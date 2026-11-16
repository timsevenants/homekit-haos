"""HomeKit Rooms add-on: a small web app (via Ingress) and a JSON API to sort rooms, rename things,
keep versions of the JSON files and choose which entities the HomeKit Bridge exposes.

Two sources:
- a JSON file in /share: an export of the HomeKit Rooms app (format "homekit-rooms-snapshot")
  or a room plan ({"rooms": [...], "accessories": [{"name", "room"}]});
- Home Assistant itself: areas and entities, through the WebSocket API.

GET /api describes every endpoint (meant for agents). Outside Ingress, every request needs
"Authorization: Bearer <api_token>" (add-on option); without a token the direct port is closed.

Outside Home Assistant (for testing): set SHARE_DIR, and optionally HA_URL + HA_TOKEN.
"""

import copy
import hmac
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

SHARE = Path(os.environ.get("SHARE_DIR", "/share")).resolve()
APP_DIR = SHARE / "homekit-rooms"
BACKUP_DIR = APP_DIR / "backups"
STATIC = Path(__file__).parent / "static"
PORT = int(os.environ.get("PORT", "8099"))
KEEP_VERSIONS = 200
INGRESS_IP = "172.30.32.2"

IN_ADDON = bool(os.environ.get("SUPERVISOR_TOKEN"))
if IN_ADDON:
    HA_WS = "ws://supervisor/core/websocket"
    HA_API = "http://supervisor/core/api"
    HA_TOKEN = os.environ["SUPERVISOR_TOKEN"]
else:
    base = os.environ.get("HA_URL", "").rstrip("/")
    HA_WS = (base.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket") if base else ""
    HA_API = base + "/api" if base else ""
    HA_TOKEN = os.environ.get("HA_TOKEN", "")


def load_options():
    try:
        return json.loads(Path("/data/options.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


API_TOKEN = str(load_options().get("api_token") or os.environ.get("API_TOKEN", ""))


def norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def rel(p: Path) -> str:
    return str(p.relative_to(SHARE)).replace("\\", "/")


class HAError(Exception):
    pass


class UserError(Exception):
    """Bad input: reported as HTTP 400 with the message."""


class HomeAssistant:
    """One WebSocket connection (commands run one after another) plus an HTTP session for the REST API."""

    async def __aenter__(self):
        if not HA_WS or not HA_TOKEN:
            raise HAError("Geen verbinding met Home Assistant ingesteld")
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(HA_WS, max_msg_size=0)
            await self._ws.receive_json()  # auth_required
            await self._ws.send_json({"type": "auth", "access_token": HA_TOKEN})
            reply = await self._ws.receive_json()
        except Exception:
            await self._session.close()
            raise
        if reply.get("type") != "auth_ok":
            await self.__aexit__()
            raise HAError("Aanmelden bij Home Assistant mislukt")
        self._id = 0
        return self

    async def __aexit__(self, *exc):
        await self._ws.close()
        await self._session.close()

    async def cmd(self, msg):
        self._id += 1
        await self._ws.send_json({"id": self._id, **msg})
        while True:
            reply = await self._ws.receive_json()
            if reply.get("id") == self._id and reply.get("type") == "result":
                break
        if not reply.get("success"):
            raise HAError(reply.get("error", {}).get("message", "onbekende fout"))
        return reply.get("result")

    async def rest(self, method, path, body=None):
        async with self._session.request(method, HA_API + path, json=body,
                                         headers={"Authorization": f"Bearer {HA_TOKEN}"}) as r:
            text = await r.text()
            if r.status >= 400:
                raise HAError(f"{method} {path}: {r.status} {text[:300]}")
            return json.loads(text) if text else None

    async def entities(self):
        """Every entity with a state: {entity_id: {name, area, domain, editable, eligible, listed}}.
        eligible: HomeKit includes it with its domain (not hidden, no entity category).
        listed: shown by default in the board (also not disabled)."""
        areas = await self.cmd({"type": "config/area_registry/list"})
        registry = await self.cmd({"type": "config/entity_registry/list"})
        devices = await self.cmd({"type": "config/device_registry/list"})
        states = await self.cmd({"type": "get_states"})
        area_name = {a["area_id"]: a["name"] for a in areas}
        device_area = {d["id"]: d.get("area_id") for d in devices}
        reg = {e["entity_id"]: e for e in registry}
        out = {}
        for s in states:
            eid = s["entity_id"]
            e = reg.get(eid)
            area_id = (e.get("area_id") or device_area.get(e.get("device_id"))) if e else None
            out[eid] = {
                "entity_id": eid,
                "name": s.get("attributes", {}).get("friendly_name") or (e and (e.get("name") or e.get("original_name"))) or eid,
                "domain": eid.split(".")[0],
                "area_id": area_id,
                "area": area_name.get(area_id),
                "editable": e is not None,
                "eligible": not (e and (e.get("hidden_by") or e.get("entity_category"))),
                "listed": not (e and (e.get("hidden_by") or e.get("entity_category") or e.get("disabled_by"))),
            }
        return areas, out


# ---------- files and versions ----------

def safe_path(rel_path: str) -> Path:
    path = (SHARE / (rel_path or "")).resolve()
    if SHARE not in path.parents or path.suffix.lower() != ".json" or BACKUP_DIR in path.parents:
        raise UserError("Ongeldig pad: alleen .json-bestanden in /share, buiten de backups")
    return path


def file_kind(data) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("format") == "homekit-rooms-snapshot":
        return "snapshot"
    if isinstance(data.get("accessories"), list) and isinstance(data.get("rooms"), list):
        return "plan"
    return None


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def summary(data) -> dict:
    return {
        "rooms": len(data.get("rooms") or []),
        "accessories": len(data.get("accessories") or []),
        "scenes": len(data.get("scenes") or []),
        "automations": len(data.get("automations") or []),
    }


def version_dir(path: Path) -> Path:
    return BACKUP_DIR / rel(path)[:-5].replace("/", "__")


def version_index(d: Path) -> dict:
    try:
        return read_json(d / "_index.json")
    except (OSError, ValueError):
        return {}


def save_version(path: Path, note: str = "", reason: str = "") -> str | None:
    """Copies the current file into its version folder. Returns the version id."""
    if not path.exists():
        return None
    d = version_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    base = vid = datetime.now().strftime("%Y-%m-%d %H%M%S")
    n = 1
    while (d / f"{vid}.json").exists():
        n += 1
        vid = f"{base}-{n}"
    shutil.copy2(path, d / f"{vid}.json")
    index = version_index(d)
    index[vid] = {"note": note or "", "reason": reason or ""}
    for old in sorted(p.stem for p in d.glob("*.json") if p.name != "_index.json")[:-KEEP_VERSIONS]:
        (d / f"{old}.json").unlink(missing_ok=True)
        index.pop(old, None)
    write_json(d / "_index.json", index)
    return vid


def list_versions(path: Path) -> list:
    d = version_dir(path)
    index = version_index(d)
    out = []
    for p in sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []:
        if p.name == "_index.json":
            continue
        try:
            counts = summary(read_json(p))
        except (OSError, ValueError):
            counts = {}
        out.append({"id": p.stem, **index.get(p.stem, {"note": "", "reason": ""}), **counts,
                    "size": p.stat().st_size})
    return out


def version_path(path: Path, vid: str) -> Path:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{6}(-[0-9]+)?", vid or ""):
        raise UserError("Ongeldige versie")
    p = version_dir(path) / f"{vid}.json"
    if not p.is_file():
        raise web.HTTPNotFound(text="Versie niet gevonden")
    return p


def save_file(path: Path, data, note="", reason="Opgeslagen") -> dict:
    if not file_kind(data):
        raise UserError("Geen geldig HomeKit Rooms-bestand (export uit de app of plan)")
    vid = save_version(path, note, reason)
    write_json(path, data)
    return {"path": rel(path), "version": vid}


def migrate_old_backups():
    """0.2 kept backups as backups/<name> <stamp>.json; move them into their version folder."""
    if not BACKUP_DIR.is_dir():
        return
    for p in BACKUP_DIR.glob("*.json"):
        m = re.fullmatch(r"(.+) ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{6})", p.stem)
        if not m:
            continue
        stem, stamp = m.groups()
        target = next((c for c in (APP_DIR / f"{stem}.json", SHARE / f"{stem}.json") if c.exists()),
                      APP_DIR / f"{stem}.json")
        d = version_dir(target)
        d.mkdir(parents=True, exist_ok=True)
        if not (d / f"{stamp}.json").exists():
            shutil.move(str(p), d / f"{stamp}.json")
            index = version_index(d)
            index[stamp] = {"note": "", "reason": "Backup uit versie 0.2"}
            write_json(d / "_index.json", index)


def all_files() -> list:
    files = []
    for folder in (SHARE, APP_DIR):
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.json")):
            try:
                data = read_json(p)
            except (OSError, ValueError):
                continue
            kind = file_kind(data)
            if kind:
                files.append({
                    "path": rel(p), "kind": kind, "home": data.get("home"),
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes"),
                    "versions": len(list_versions(p)), **summary(data),
                })
    return files


def resolve_file(rel_path: str | None) -> Path:
    if rel_path:
        return safe_path(rel_path)
    files = all_files()
    if len(files) != 1:
        raise UserError("Geef 'file' op; beschikbaar: " + ", ".join(f["path"] for f in files))
    return safe_path(files[0]["path"])


# ---------- editing a file (shared by the agent API) ----------

def find_accessory(data, match: str) -> list[int]:
    accs = data.get("accessories") or []
    hits = [i for i, a in enumerate(accs) if match and (a.get("serial") == match or a.get("key") == match)]
    return hits or [i for i, a in enumerate(accs) if norm(a.get("name")) == norm(match)]


def edit_file(data, ops: dict, ha_entities: dict | None):
    """Applies declarative operations to a file. Returns (new data, changes, warnings, entities added)."""
    data = copy.deepcopy(data)
    snapshot = file_kind(data) == "snapshot"
    rooms = data.setdefault("rooms", [])
    accs = data.setdefault("accessories", [])
    changes, warnings, added = [], [], []

    def room_index(name):
        return next((i for i, r in enumerate(rooms) if norm(r) == norm(name)), None)

    def ensure_room(name):
        if not name:
            return ""
        i = room_index(name)
        if i is None:
            rooms.append(name)
            changes.append(["add", f"Nieuwe ruimte: {name}"])
            return name
        return rooms[i]

    def one(match):
        hits = find_accessory(data, match)
        if len(hits) != 1:
            warnings.append(f"Accessoire '{match}': {'niet gevonden' if not hits else 'meerdere gevonden, gebruik het entity_id of de key'}")
            return None
        return accs[hits[0]]

    r = ops.get("rooms") or {}
    for name in r.get("add") or []:
        if room_index(name) is None:
            ensure_room(name)
    for old, new in (r.get("rename") or {}).items():
        i = room_index(old)
        if i is None:
            warnings.append(f"Ruimte '{old}' niet gevonden")
            continue
        was = rooms[i]
        if was == new:
            continue
        rooms[i] = new
        for a in accs:
            if norm(a.get("room")) == norm(was):
                a["room"] = new
        for z in data.get("zones") or []:
            z["rooms"] = [new if norm(x) == norm(was) else x for x in z.get("rooms", [])]
        changes.append(["change", f"Ruimte {was} → {new}"])

    for e in ops.get("add_entities") or []:
        eid = e.get("entity_id") if isinstance(e, dict) else e
        e = e if isinstance(e, dict) else {}
        if not eid or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", eid):
            warnings.append(f"Ongeldig entity_id: {eid}")
            continue
        if any(a.get("serial") == eid for a in accs):
            warnings.append(f"{eid} staat al in het bestand")
            continue
        info = (ha_entities or {}).get(eid)
        if ha_entities is not None and info is None:
            warnings.append(f"{eid} bestaat niet in Home Assistant")
            continue
        name = e.get("name") or (info or {}).get("name") or eid
        room = ensure_room(e["room"] if "room" in e else (info or {}).get("area") or "")
        acc = {"key": f"serial:{eid}", "name": name, "room": room, "serial": eid,
               "manufacturer": "Home Assistant", "model": eid.split(".")[0], "services": []}
        if not snapshot:
            acc = {"name": name, "room": room, "serial": eid}
        accs.append(acc)
        added.append(eid)
        changes.append(["add", f"Accessoire {name} ({eid})" + (f" in {room}" if room else "")])

    for u in ops.get("accessories") or []:
        a = one(u.get("match", ""))
        if a is None:
            continue
        if u.get("name") and u["name"] != a["name"]:
            for s in a.get("services") or []:
                if s.get("name") == a["name"]:
                    s["name"] = u["name"]
            changes.append(["change", f"{a['name']} → {u['name']}"])
            a["name"] = u["name"]
        if "room" in u:
            room = ensure_room(u["room"] or "")
            if norm(room) != norm(a.get("room")):
                changes.append(["change", f"{a['name']}: {a.get('room') or 'zonder ruimte'} → {room or 'zonder ruimte'}"])
                a["room"] = room

    for m in ops.get("remove_accessories") or []:
        a = one(m)
        if a is not None:
            accs.remove(a)
            changes.append(["remove", f"Accessoire uit het bestand: {a['name']}"])

    for name in r.get("remove") or []:
        i = room_index(name)
        if i is None:
            warnings.append(f"Ruimte '{name}' niet gevonden")
        elif any(norm(a.get("room")) == norm(rooms[i]) for a in accs):
            warnings.append(f"Ruimte '{rooms[i]}' is niet leeg; verplaats eerst de accessoires")
        else:
            was = rooms.pop(i)
            for z in data.get("zones") or []:
                z["rooms"] = [x for x in z.get("rooms", []) if norm(x) != norm(was)]
            changes.append(["remove", f"Ruimte verwijderd: {was}"])

    if r.get("order"):
        first = [rooms[i] for i in (room_index(n) for n in r["order"]) if i is not None]
        unknown = [n for n in r["order"] if room_index(n) is None]
        if unknown:
            warnings.append("Onbekende ruimtes in 'order': " + ", ".join(unknown))
        new = list(dict.fromkeys(first)) + [x for x in rooms if x not in first]
        if new != rooms:
            rooms[:] = new
            changes.append(["change", "Volgorde van de ruimtes: " + ", ".join(new)])

    if not snapshot:
        data["accessories"] = [a for a in accs if a.get("room")]
    return data, changes, warnings, added


def diff_files(old, new) -> list:
    """What changed from old to new, as [kind, text]."""
    out = []
    o_rooms, n_rooms = old.get("rooms") or [], new.get("rooms") or []
    for x in n_rooms:
        if norm(x) not in map(norm, o_rooms):
            out.append(["add", f"Ruimte {x}"])
    for x in o_rooms:
        if norm(x) not in map(norm, n_rooms):
            out.append(["remove", f"Ruimte {x}"])
    common = [norm(x) for x in o_rooms if norm(x) in map(norm, n_rooms)]
    if common != [norm(x) for x in n_rooms if norm(x) in common]:
        out.append(["change", "Andere volgorde van de ruimtes"])

    def key(a):
        return a.get("key") or a.get("serial") or norm(a.get("name"))
    o_acc = {key(a): a for a in old.get("accessories") or []}
    n_acc = {key(a): a for a in new.get("accessories") or []}
    for k, a in n_acc.items():
        b = o_acc.get(k)
        if b is None:
            out.append(["add", f"Accessoire {a.get('name')}"])
            continue
        if a.get("name") != b.get("name"):
            out.append(["change", f"{b.get('name')} → {a.get('name')}"])
        if norm(a.get("room")) != norm(b.get("room")):
            out.append(["change", f"{a.get('name')}: {b.get('room') or 'zonder ruimte'} → {a.get('room') or 'zonder ruimte'}"])
    for k, b in o_acc.items():
        if k not in n_acc:
            out.append(["remove", f"Accessoire {b.get('name')}"])
    for part, label in (("zones", "Zone"), ("serviceGroups", "Groep"), ("scenes", "Scène"), ("automations", "Automatisatie")):
        o = {x.get("name"): x for x in old.get(part) or []}
        n = {x.get("name"): x for x in new.get(part) or []}
        out += [["add", f"{label} {k}"] for k in n if k not in o]
        out += [["remove", f"{label} {k}"] for k in o if k not in n]
        out += [["change", f"{label} {k} gewijzigd"] for k in n if k in o and n[k] != o[k]]
    return out


# ---------- HomeKit Bridge ----------

FLOW = "/config/config_entries/options/flow"


def form_defaults(step) -> dict:
    return {f["name"]: f["default"] for f in step.get("data_schema") or [] if "default" in f}


def form_options(step, name) -> list:
    field = next((f for f in step.get("data_schema") or [] if f["name"] == name), {})
    opts = field.get("options") or []
    return list(opts) if isinstance(opts, dict) else [o[0] if isinstance(o, list) else o for o in opts]


async def read_bridges(ha: HomeAssistant) -> list:
    """Reads each HomeKit entry's filter by opening its options flow and closing it without saving."""
    bridges = []
    for entry in await ha.cmd({"type": "config_entries/get", "domain": "homekit"}):
        b = {"entry_id": entry["entry_id"], "title": entry.get("title"), "state": entry.get("state"),
             "editable": False, "reason": None, "mode": None, "include_exclude_mode": None,
             "domains": [], "entities": [], "supported_domains": []}
        bridges.append(b)
        if entry.get("disabled_by"):
            b["reason"] = "uitgeschakeld"
            continue
        try:
            step = await ha.rest("POST", FLOW, {"handler": entry["entry_id"]})
        except HAError as e:
            b["reason"] = str(e)
            continue
        fid = step.get("flow_id")
        try:
            if step.get("step_id") != "init":
                b["reason"] = "beheerd via YAML"
                continue
            init = form_defaults(step)
            b["mode"] = init.get("mode")
            b["include_exclude_mode"] = init.get("include_exclude_mode")
            b["domains"] = sorted(set(init.get("domains") or []))
            b["supported_domains"] = form_options(step, "domains")
            step = await ha.rest("POST", f"{FLOW}/{fid}", init)
            entities = form_defaults(step).get("entities") or []
            b["entities"] = sorted([entities] if isinstance(entities, str) else entities)
            if b["mode"] != "bridge":
                b["reason"] = "accessoire-modus (één entiteit)"
            else:
                b["editable"] = True
        finally:
            try:
                await ha.rest("DELETE", f"{FLOW}/{fid}")
            except HAError:
                pass
    return bridges


def exposed(bridge, entities: dict) -> dict:
    """{entity_id: "entity" | "domain"} for what this bridge shows in HomeKit."""
    out = {}
    listed = set(bridge["entities"])
    domains = set(bridge["domains"])
    if bridge["mode"] == "accessory":
        return {e: "entity" for e in listed}
    if bridge["include_exclude_mode"] == "include":
        explicit_domains = {e.split(".")[0] for e in listed}
        for e in listed:
            out[e] = "entity"
        for eid, info in entities.items():
            d = info["domain"]
            if d in domains and d not in explicit_domains and info["eligible"]:
                out[eid] = "domain"
    elif bridge["include_exclude_mode"] == "exclude":
        for eid, info in entities.items():
            if info["domain"] in domains and eid not in listed and info["eligible"]:
                out[eid] = "domain"
    return out


def plan_bridge(bridge, entities: dict, expose: list, hide: list):
    """New (domains, entities) for the bridge so that `expose` is shown and `hide` is not, keeping
    everything else as it is. Returns (domains, entities, changes, warnings)."""
    before = exposed(bridge, entities)
    want = set(before)
    changes, warnings = [], []
    supported = set(bridge["supported_domains"])
    for eid in expose:
        if eid not in entities:
            warnings.append(f"{eid} bestaat niet in Home Assistant")
        elif supported and entities[eid]["domain"] not in supported:
            warnings.append(f"{eid}: domein {entities[eid]['domain']} wordt niet door HomeKit ondersteund")
        elif eid not in want:
            want.add(eid)
            changes.append(["add", f"Zichtbaar in HomeKit: {entities[eid]['name']} ({eid})"])
    for eid in hide:
        if eid in want:
            want.discard(eid)
            changes.append(["remove", f"Niet meer in HomeKit: {entities.get(eid, {}).get('name', eid)} ({eid})"])

    mode = bridge["include_exclude_mode"]
    domains, listed = set(bridge["domains"]), set(bridge["entities"])
    by_domain = {}
    for eid in want:
        by_domain.setdefault(eid.split(".")[0], set()).add(eid)
    touched = {e.split(".")[0] for e in expose + hide if e in entities}
    for d in touched:
        want_d = by_domain.get(d, set())
        eligible_d = {e for e, i in entities.items() if i["domain"] == d and i["eligible"]}
        listed -= {e for e in listed if e.split(".")[0] == d}
        if not want_d:
            domains.discard(d)
            continue
        domains.add(d)
        if mode == "include":
            whole_before = any(v == "domain" and k.split(".")[0] == d for k, v in before.items())
            if not (whole_before and want_d == eligible_d):
                listed |= want_d
        else:
            for e in want_d - eligible_d:
                warnings.append(f"{e} is verborgen of een configuratie-entiteit en kan in exclude-modus niet getoond worden")
            listed |= eligible_d - want_d
    return sorted(domains), sorted(listed), changes, warnings


async def write_bridge(ha: HomeAssistant, bridge, domains, entities):
    """Saves the filter through the same options flow as the UI; other steps keep their current values."""
    step = await ha.rest("POST", FLOW, {"handler": bridge["entry_id"]})
    fid = step["flow_id"]
    try:
        init = form_defaults(step)
        step = await ha.rest("POST", f"{FLOW}/{fid}", {**init, "include_exclude_mode": bridge["include_exclude_mode"],
                                                       "domains": domains})
        if step.get("step_id") not in ("include", "exclude"):
            raise HAError(f"Onverwachte stap {step.get('step_id')}")
        step = await ha.rest("POST", f"{FLOW}/{fid}", {"entities": entities})
        for _ in range(10):
            if step.get("type") != "form":
                break
            if step.get("errors"):
                raise HAError(f"Stap {step.get('step_id')}: {step['errors']}")
            step = await ha.rest("POST", f"{FLOW}/{fid}", form_defaults(step))
        if step.get("type") != "create_entry":
            raise HAError(f"HomeKit-opties niet bewaard: {step.get('type')} {step.get('reason') or ''}")
        fid = None
    finally:
        if fid:
            try:
                await ha.rest("DELETE", f"{FLOW}/{fid}")
            except HAError:
                pass


def pick_bridge(bridges, wanted):
    if wanted:
        b = next((b for b in bridges if wanted == b["entry_id"] or norm(b["title"]) == norm(wanted)), None)
        if not b:
            raise UserError(f"HomeKit-bridge '{wanted}' niet gevonden")
        return b
    editable = [b for b in bridges if b["editable"]]
    if len(editable) != 1:
        raise UserError("Geef 'bridge' op (entry_id of titel); bewerkbaar: " + (", ".join(b["title"] for b in editable) or "geen"))
    return editable[0]


async def change_exposure(ha, entities, bridge_name, expose, hide, dry_run):
    bridges = await read_bridges(ha)
    bridge = pick_bridge(bridges, bridge_name)
    if not bridge["editable"]:
        raise UserError(f"{bridge['title']} kan niet aangepast worden: {bridge['reason']}")
    domains, listed, changes, warnings = plan_bridge(bridge, entities, expose, hide)
    count = len(exposed({**bridge, "domains": domains, "entities": listed}, entities))
    if count > 150:
        warnings.append(f"Een HomeKit-bridge toont maximaal 150 accessoires; dit zouden er {count} zijn")
    applied = False
    if changes and not dry_run:
        await write_bridge(ha, bridge, domains, listed)
        applied = True
    return {"bridge": bridge["title"], "entry_id": bridge["entry_id"], "changes": changes, "warnings": warnings,
            "applied": applied, "exposed_count": count,
            "filter": {"mode": bridge["include_exclude_mode"], "domains": domains, "entities": listed}}


# ---------- HTTP ----------

def err(e, status):
    return web.json_response({"error": str(e)}, status=status)


@web.middleware
async def guard(request, handler):
    if IN_ADDON and request.remote != INGRESS_IP:
        sent = request.headers.get("Authorization", "")
        if not API_TOKEN or not hmac.compare_digest(sent.encode(), f"Bearer {API_TOKEN}".encode()):
            return err("Niet toegelaten: stuur 'Authorization: Bearer <api_token>' (add-on optie)", 401)
    try:
        return await handler(request)
    except UserError as e:
        return err(e, 400)
    except (HAError, aiohttp.ClientError) as e:
        return err(f"Home Assistant: {e}", 502)
    except json.JSONDecodeError as e:
        return err(f"Ongeldige JSON: {e}", 400)


async def body(request) -> dict:
    data = await request.json()
    if not isinstance(data, dict):
        raise UserError("Verwacht een JSON-object")
    return data


async def api_files(request):
    return web.json_response(all_files())


async def api_file_get(request):
    path = safe_path(request.query.get("path", ""))
    if not path.is_file():
        raise web.HTTPNotFound(text="Bestand niet gevonden")
    if request.query.get("download"):
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{path.name}"'})
    return web.json_response(read_json(path))


async def api_file_post(request):
    b = await body(request)
    return web.json_response(save_file(safe_path(b.get("path", "")), b.get("data"), b.get("note", "")))


async def api_import(request):
    """{name, data, overwrite?, note?}: stores an uploaded export in /share/homekit-rooms/."""
    b = await body(request)
    name = re.sub(r"[^\w .()-]", "_", Path(str(b.get("name") or "import")).stem).strip() or "import"
    path = APP_DIR / f"{name}.json"
    if path.exists() and not b.get("overwrite"):
        return web.json_response({"exists": True, "path": rel(path)}, status=409)
    return web.json_response(save_file(path, b.get("data"), b.get("note", ""), "Voor import"))


async def api_versions(request):
    path = safe_path(request.query.get("path", ""))
    return web.json_response(list_versions(path))


async def api_version_get(request):
    path = safe_path(request.query.get("path", ""))
    v = version_path(path, request.query.get("id", ""))
    if request.query.get("download"):
        return web.FileResponse(v, headers={"Content-Disposition": f'attachment; filename="{path.stem} {v.stem}.json"'})
    data = read_json(v)
    if request.query.get("compare"):
        current = read_json(path) if path.exists() else {}
        return web.json_response({"changes": diff_files(data, current), **summary(data)})
    return web.json_response(data)


async def api_version_save(request):
    b = await body(request)
    path = safe_path(b.get("path", ""))
    if not path.exists():
        raise web.HTTPNotFound(text="Bestand niet gevonden")
    return web.json_response({"version": save_version(path, b.get("note", ""), "Handmatig bewaard")})


async def api_version_restore(request):
    b = await body(request)
    path = safe_path(b.get("path", ""))
    v = version_path(path, b.get("id", ""))
    return web.json_response(save_file(path, read_json(v), b.get("note", ""), f"Voor herstel van {v.stem}"))


async def api_version_delete(request):
    b = await body(request)
    path = safe_path(b.get("path", ""))
    v = version_path(path, b.get("id", ""))
    v.unlink()
    index = version_index(v.parent)
    index.pop(v.stem, None)
    write_json(v.parent / "_index.json", index)
    return web.json_response({"deleted": v.stem})


async def api_ha(request):
    async with HomeAssistant() as ha:
        areas, entities = await ha.entities()
    return web.json_response({
        "areas": [{"id": a["area_id"], "name": a["name"]} for a in areas],
        "entities": list(entities.values()),
    })


async def api_ha_apply(request):
    """Body: {createAreas: [name], renameAreas: [{area_id, name}], deleteAreas: [area_id],
    entities: [{entity_id, name?, area?}]} where area is an area name. Runs every step, reports each."""
    b = await body(request)
    log = []
    async with HomeAssistant() as ha:
        areas = {a["name"].casefold(): a["area_id"] for a in await ha.cmd({"type": "config/area_registry/list"})}
        for name in b.get("createAreas", []):
            if name.casefold() in areas:
                continue
            try:
                area = await ha.cmd({"type": "config/area_registry/create", "name": name})
                areas[name.casefold()] = area["area_id"]
                log.append(f"➕ Ruimte {name}")
            except HAError as e:
                log.append(f"⚠️ Ruimte {name}: {e}")
        for r in b.get("renameAreas", []):
            try:
                await ha.cmd({"type": "config/area_registry/update", "area_id": r["area_id"], "name": r["name"]})
                areas[r["name"].casefold()] = r["area_id"]
                log.append(f"✏️ Ruimte → {r['name']}")
            except HAError as e:
                log.append(f"⚠️ Ruimte {r['name']}: {e}")
        for e in b.get("entities", []):
            msg = {"type": "config/entity_registry/update", "entity_id": e["entity_id"]}
            if "name" in e:
                msg["name"] = e["name"]
            if "area" in e:
                area_id = areas.get(e["area"].casefold()) if e["area"] else None
                if e["area"] and not area_id:
                    log.append(f"⚠️ {e['entity_id']}: ruimte {e['area']} bestaat niet")
                    continue
                msg["area_id"] = area_id
            try:
                await ha.cmd(msg)
                log.append(f"✅ {e['entity_id']}" + (f" → {e['name']}" if "name" in e else "") +
                           (f" in {e['area'] or 'geen ruimte'}" if "area" in e else ""))
            except HAError as err_:
                log.append(f"⚠️ {e['entity_id']}: {err_}")
        for area_id in b.get("deleteAreas", []):
            try:
                await ha.cmd({"type": "config/area_registry/delete", "area_id": area_id})
                log.append(f"➖ Ruimte {area_id} verwijderd")
            except HAError as e:
                log.append(f"⚠️ Ruimte {area_id}: {e}")
    return web.json_response({"log": log})


async def api_homekit(request):
    async with HomeAssistant() as ha:
        _, entities = await ha.entities()
        bridges = await read_bridges(ha)
    for b in bridges:
        b["exposed"] = exposed(b, entities) if b["mode"] else {}
    return web.json_response({"bridges": bridges})


async def api_homekit_apply(request):
    """{bridge?, expose: [entity_id], hide: [entity_id], dry_run?}"""
    b = await body(request)
    async with HomeAssistant() as ha:
        _, entities = await ha.entities()
        result = await change_exposure(ha, entities, b.get("bridge"), list(b.get("expose") or []),
                                       list(b.get("hide") or []), bool(b.get("dry_run")))
    return web.json_response(result)


async def api_agent(request):
    """One call for agents: edit a file and/or the HomeKit Bridge. dry_run defaults to true."""
    b = await body(request)
    dry_run = b.get("dry_run", True) is not False
    result = {"dry_run": dry_run, "warnings": []}
    file_ops = {k: b[k] for k in ("rooms", "accessories", "add_entities", "remove_accessories") if b.get(k)}
    homekit = dict(b.get("homekit") or {})
    expose, hide = list(homekit.get("expose") or []), list(homekit.get("hide") or [])
    keep_hidden = {e.get("entity_id") for e in b.get("add_entities") or [] if isinstance(e, dict) and e.get("expose") is False}
    need_ha = bool(expose or hide or file_ops.get("add_entities"))

    async def run(ha, entities):
        if file_ops:
            path = resolve_file(b.get("file"))
            old = read_json(path)
            new, changes, warnings, added = edit_file(old, file_ops, entities)
            result["file"] = {"path": rel(path), "changes": changes, "version": None}
            result["warnings"] += warnings
            expose.extend(e for e in added if e not in keep_hidden and e not in expose)
            if changes and not dry_run:
                result["file"].update(save_file(path, new, b.get("note", ""), "Via API"))
        if expose or hide:
            hk = await change_exposure(ha, entities, b.get("bridge"), expose, hide, dry_run)
            result["warnings"] += hk.pop("warnings")
            result["homekit"] = hk

    if need_ha:
        async with HomeAssistant() as ha:
            _, entities = await ha.entities()
            await run(ha, entities)
    else:
        await run(None, None)
    return web.json_response(result)


API_DOC = {
    "about": "HomeKit Rooms add-on. Bewerkt JSON-exports van de HomeKit Rooms iOS-app en de HomeKit Bridge "
             "van Home Assistant. Alle paden zijn relatief t.o.v. /share. Elke schrijfactie bewaart eerst een versie.",
    "auth": "Via Ingress: geen. Via de directe poort: header 'Authorization: Bearer <api_token>' (add-on optie).",
    "workflow": [
        "GET api/files → kies een bestand",
        "GET api/homekit → bridges en welke entiteiten zichtbaar zijn",
        "POST api/agent met dry_run=true → controleer 'changes' en 'warnings'",
        "POST api/agent met dry_run=false en een 'note' → toepassen",
        "Daarna in de iOS-app: Importeren uit JSON (de app toont de verschillen en maakt een backup)",
    ],
    "endpoints": {
        "GET api/files": "JSON-bestanden in /share en /share/homekit-rooms met tellingen",
        "GET api/file?path=&download=1": "Inhoud van een bestand (download=1 als bijlage)",
        "POST api/file": "{path, data, note?}: overschrijven (eerst een versie)",
        "POST api/import": "{name, data, overwrite?, note?}: bewaren in /share/homekit-rooms/<name>.json; 409 als het bestaat",
        "GET api/versions?path=": "Versies van een bestand, nieuwste eerst",
        "GET api/version?path=&id=&compare=1|download=1": "Een versie, of de verschillen tussen die versie en het huidige bestand",
        "POST api/versions": "{path, note}: huidige toestand als versie bewaren",
        "POST api/versions/restore": "{path, id, note?}: versie terugzetten (de huidige wordt eerst bewaard)",
        "POST api/versions/delete": "{path, id}",
        "GET api/ha": "Gebieden en entiteiten van Home Assistant",
        "POST api/ha/apply": "{createAreas, renameAreas:[{area_id,name}], deleteAreas, entities:[{entity_id,name?,area?}]}",
        "GET api/homekit": "HomeKit-bridges: filter en 'exposed' {entity_id: 'entity'|'domain'}",
        "POST api/homekit/apply": "{bridge?, expose:[entity_id], hide:[entity_id], dry_run?}",
        "POST api/agent": "Alles in één keer, zie agent_example. dry_run is standaard true.",
    },
    "agent_example": {
        "file": "homekit-rooms/Thuis.json",
        "bridge": "HASS Bridge",
        "dry_run": True,
        "note": "Keukenverlichting toegevoegd",
        "rooms": {"add": ["Bureau"], "rename": {"Living": "Woonkamer"}, "remove": ["Oud"],
                  "order": ["Woonkamer", "Keuken", "Bureau"]},
        "accessories": [{"match": "light.keuken_spots", "name": "Spots", "room": "Keuken"}],
        "add_entities": [{"entity_id": "light.bureau", "name": "Bureaulamp", "room": "Bureau", "expose": True}],
        "remove_accessories": ["switch.oud"],
        "homekit": {"expose": ["cover.screen_zuid"], "hide": ["switch.test"]},
    },
    "notes": [
        "match: entity_id (serienummer bij de HomeKit Bridge), key of naam van het accessoire",
        "room '' = zonder ruimte (standaardruimte van de woning)",
        "add_entities: naam en ruimte vallen terug op de naam en het gebied in Home Assistant; expose staat standaard aan",
        "HomeKit toont nieuwe entiteiten pas na het herstarten van de bridge (gebeurt automatisch bij opslaan)",
        "De iOS-app kan geen accessoires koppelen: een entiteit moet eerst zichtbaar zijn in HomeKit",
    ],
}


async def api_doc(request):
    return web.json_response(API_DOC)


async def index(request):
    return web.FileResponse(STATIC / "index.html")


def main():
    migrate_old_backups()
    app = web.Application(client_max_size=50 * 1024 * 1024, middlewares=[guard])
    app.add_routes([
        web.get("/", index),
        web.get("/api", api_doc),
        web.get("/api/files", api_files),
        web.get("/api/file", api_file_get),
        web.post("/api/file", api_file_post),
        web.post("/api/import", api_import),
        web.get("/api/versions", api_versions),
        web.get("/api/version", api_version_get),
        web.post("/api/versions", api_version_save),
        web.post("/api/versions/restore", api_version_restore),
        web.post("/api/versions/delete", api_version_delete),
        web.get("/api/ha", api_ha),
        web.post("/api/ha/apply", api_ha_apply),
        web.get("/api/homekit", api_homekit),
        web.post("/api/homekit/apply", api_homekit_apply),
        web.post("/api/agent", api_agent),
    ])
    print(f"HomeKit Rooms add-on op poort {PORT}, share: {SHARE}, HA: {'ja' if HA_WS else 'nee'}, "
          f"API-token: {'ja' if API_TOKEN else 'nee'}")
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
