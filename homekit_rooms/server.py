"""HomeKit Rooms add-on: a small web app (via Ingress) to sort rooms and rename things.

Two sources:
- a JSON file in /share: an export of the HomeKit Rooms app (format "homekit-rooms-snapshot")
  or a room plan ({"rooms": [...], "accessories": [{"name", "room"}]});
- Home Assistant itself: areas and entities, through the WebSocket API.

Outside Home Assistant (for testing): set SHARE_DIR, and optionally HA_URL + HA_TOKEN.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

SHARE = Path(os.environ.get("SHARE_DIR", "/share")).resolve()
APP_DIR = SHARE / "homekit-rooms"
BACKUP_DIR = APP_DIR / "backups"
STATIC = Path(__file__).parent / "static"
PORT = int(os.environ.get("PORT", "8099"))

if os.environ.get("SUPERVISOR_TOKEN"):
    HA_WS = "ws://supervisor/core/websocket"
    HA_TOKEN = os.environ["SUPERVISOR_TOKEN"]
else:
    base = os.environ.get("HA_URL", "").rstrip("/")
    HA_WS = (base.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket") if base else ""
    HA_TOKEN = os.environ.get("HA_TOKEN", "")


class HAError(Exception):
    pass


class HomeAssistant:
    """One WebSocket connection; commands run one after another."""

    async def __aenter__(self):
        if not HA_WS or not HA_TOKEN:
            raise HAError("Geen verbinding met Home Assistant ingesteld")
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(HA_WS, max_msg_size=0)
        await self._ws.receive_json()  # auth_required
        await self._ws.send_json({"type": "auth", "access_token": HA_TOKEN})
        reply = await self._ws.receive_json()
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


# ---------- files ----------

def safe_path(rel: str) -> Path:
    path = (SHARE / rel).resolve()
    if SHARE not in path.parents or path.suffix.lower() != ".json":
        raise web.HTTPBadRequest(text="Ongeldig pad")
    return path


def file_kind(data) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("format") == "homekit-rooms-snapshot":
        return "snapshot"
    if isinstance(data.get("accessories"), list) and isinstance(data.get("rooms"), list):
        return "plan"
    return None


async def list_files(request):
    files = []
    for folder in (SHARE, APP_DIR):
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.json")):
            try:
                kind = file_kind(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if kind:
                files.append({
                    "path": str(p.relative_to(SHARE)).replace("\\", "/"),
                    "kind": kind,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes"),
                })
    return web.json_response(files)


async def read_file(request):
    path = safe_path(request.query.get("path", ""))
    if not path.is_file():
        raise web.HTTPNotFound(text="Bestand niet gevonden")
    return web.json_response(json.loads(path.read_text(encoding="utf-8")))


async def write_file(request):
    body = await request.json()
    path = safe_path(body.get("path", ""))
    data = body.get("data")
    if not file_kind(data):
        raise web.HTTPBadRequest(text="Geen geldig HomeKit Rooms-bestand")
    backup = None
    if path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        backup = BACKUP_DIR / f"{path.stem} {stamp}.json"
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return web.json_response({
        "path": str(path.relative_to(SHARE)).replace("\\", "/"),
        "backup": str(backup.relative_to(SHARE)).replace("\\", "/") if backup else None,
    })


# ---------- Home Assistant ----------

async def ha_data(request):
    try:
        async with HomeAssistant() as ha:
            areas = await ha.cmd({"type": "config/area_registry/list"})
            entities = await ha.cmd({"type": "config/entity_registry/list"})
            devices = await ha.cmd({"type": "config/device_registry/list"})
            states = await ha.cmd({"type": "get_states"})
    except (HAError, aiohttp.ClientError) as e:
        return web.json_response({"error": str(e)}, status=502)

    device_area = {d["id"]: d.get("area_id") for d in devices}
    friendly = {s["entity_id"]: s.get("attributes", {}).get("friendly_name") for s in states}
    out, registered = [], set()
    for e in entities:
        registered.add(e["entity_id"])
        if e.get("disabled_by") or e.get("hidden_by") or e.get("entity_category"):
            continue
        out.append({
            "entity_id": e["entity_id"],
            "name": friendly.get(e["entity_id"]) or e.get("name") or e.get("original_name") or e["entity_id"],
            "area_id": e.get("area_id") or device_area.get(e.get("device_id")),
            "editable": True,
        })
    # Entities without a unique_id (e.g. from YAML) cannot be renamed or moved.
    for s in states:
        if s["entity_id"] not in registered:
            out.append({"entity_id": s["entity_id"], "name": friendly.get(s["entity_id"]) or s["entity_id"],
                        "area_id": None, "editable": False})
    return web.json_response({
        "areas": [{"id": a["area_id"], "name": a["name"]} for a in areas],
        "entities": out,
    })


async def ha_apply(request):
    """Body: {createAreas: [name], renameAreas: [{area_id, name}], deleteAreas: [area_id],
    entities: [{entity_id, name?, area?}]} where area is an area name. Runs every step, reports each."""
    body = await request.json()
    log = []
    try:
        async with HomeAssistant() as ha:
            areas = {a["name"].casefold(): a["area_id"] for a in await ha.cmd({"type": "config/area_registry/list"})}
            for name in body.get("createAreas", []):
                if name.casefold() in areas:
                    continue
                try:
                    area = await ha.cmd({"type": "config/area_registry/create", "name": name})
                    areas[name.casefold()] = area["area_id"]
                    log.append(f"➕ Ruimte {name}")
                except HAError as e:
                    log.append(f"⚠️ Ruimte {name}: {e}")
            for r in body.get("renameAreas", []):
                try:
                    await ha.cmd({"type": "config/area_registry/update", "area_id": r["area_id"], "name": r["name"]})
                    areas[r["name"].casefold()] = r["area_id"]
                    log.append(f"✏️ Ruimte → {r['name']}")
                except HAError as e:
                    log.append(f"⚠️ Ruimte {r['name']}: {e}")
            for e in body.get("entities", []):
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
                except HAError as err:
                    log.append(f"⚠️ {e['entity_id']}: {err}")
            for area_id in body.get("deleteAreas", []):
                try:
                    await ha.cmd({"type": "config/area_registry/delete", "area_id": area_id})
                    log.append(f"➖ Ruimte {area_id} verwijderd")
                except HAError as e:
                    log.append(f"⚠️ Ruimte {area_id}: {e}")
    except (HAError, aiohttp.ClientError) as e:
        return web.json_response({"error": str(e), "log": log}, status=502)
    return web.json_response({"log": log})


async def index(request):
    return web.FileResponse(STATIC / "index.html")


def main():
    app = web.Application(client_max_size=50 * 1024 * 1024)
    app.add_routes([
        web.get("/", index),
        web.get("/api/files", list_files),
        web.get("/api/file", read_file),
        web.post("/api/file", write_file),
        web.get("/api/ha", ha_data),
        web.post("/api/ha/apply", ha_apply),
    ])
    print(f"HomeKit Rooms add-on op poort {PORT}, share: {SHARE}, HA: {'ja' if HA_WS else 'nee'}")
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
