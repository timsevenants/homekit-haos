# Working with the HomeKit Rooms add-on (for agents)

The add-on manages JSON exports of the HomeKit Rooms iOS app (format `homekit-rooms-snapshot`) stored in `/share`, and the entity filter of Home Assistant's **HomeKit Bridge** integration. Everything the web page does is available as a JSON API.

## Access

| Route | Auth |
|---|---|
| Ingress (sidebar, `/api/hassio_ingress/<token>/…`) | Home Assistant session, no extra token |
| Direct port (opt-in; set a host port under the add-on's *Network* tab) | `Authorization: Bearer <api_token>`. Set the token in the add-on options. Without a token the port refuses every request |

`GET /api` returns a machine-readable description of every endpoint, with an example.

## Recommended flow

1. `GET /api/files`: pick a file. Each file has `path`, `kind`, `home`, counts and number of `versions`.
2. `GET /api/homekit`: list the bridges (read from Home Assistant's `.storage/core.config_entries`, read-only; a bridge with `mode: null` could not be read, see `reason`). `exposed` maps each `entity_id` to `"entity"` (listed explicitly) or `"domain"` (its whole domain is included).
3. `POST /api/agent` with `"dry_run": true` (the default). Read `file.changes`, `homekit.changes` and `warnings`.
4. Repeat with `"dry_run": false` and a short `"note"` to apply. The previous file content is saved as a version first. The bridge reloads automatically.
5. Tell the user to open the file in the iOS app (*Importeren uit JSON*). Only that step changes Apple Home: names, rooms, scenes and automations. The add-on only changes the file and which entities the bridge exposes.

## `POST /api/agent`

All keys are optional.

```json
{
  "file": "homekit-rooms/Thuis.json",
  "bridge": "HASS Bridge",
  "dry_run": true,
  "note": "why this change",
  "rooms": {
    "add": ["Bureau"],
    "rename": {"Living": "Woonkamer"},
    "remove": ["Oud"],
    "order": ["Woonkamer", "Keuken", "Bureau"]
  },
  "accessories": [{"match": "light.keuken_spots", "name": "Spots", "room": "Keuken"}],
  "add_entities": [{"entity_id": "light.bureau", "name": "Bureaulamp", "room": "Bureau", "expose": true}],
  "remove_accessories": ["switch.oud"],
  "homekit": {"expose": ["cover.screen_zuid"], "hide": ["switch.test"]}
}
```

How each key is handled:
- **`file`**: may be left out when only one file exists.
- **`bridge`**: an entry_id or a title. It may be left out when only one bridge can be edited.
- **`match`**: an entity_id (the serial number of a HomeKit Bridge accessory), an accessory `key`, or a name. Names are compared case- and accent-insensitively. An ambiguous match is skipped and produces a warning.
- **Rooms**:
  - `room: ""` means the home's default room.
  - Unknown rooms named in `accessories` or `add_entities` are created.
  - `rooms.remove` only removes empty rooms.
  - `order` lists the rooms that go first. The others keep their order.
- **`add_entities`**:
  - The name and room default to the name and area in Home Assistant.
  - `expose` defaults to `true`, which also adds the entity to the bridge.
- **`homekit`**:
  - The add-on changes as little of the bridge filter as possible. Entities that were visible stay visible.
  - If you hide one entity from a domain that was included as a whole, the add-on lists that domain's other entities explicitly instead.

The response looks like this:

```json
{
  "dry_run": true,
  "warnings": ["..."],
  "file": {"path": "...", "changes": [["add|change|remove", "text"]], "version": "2026-11-16 233225"},
  "homekit": {"bridge": "...", "changes": [...], "applied": false, "exposed_count": 42,
              "filter": {"mode": "include", "domains": [...], "entities": [...]}}
}
```

## Other endpoints

| Method and path | Body or query | Purpose |
|---|---|---|
| `GET /api/file` | `?path=` (`&download=1`) | Read a file |
| `POST /api/file` | `{path, data, note?}` | Replace a file. A version is saved first |
| `POST /api/import` | `{name, data, overwrite?, note?}` | Store an uploaded export in `/share/homekit-rooms/`. Returns 409 if it exists |
| `GET /api/versions` | `?path=` | Versions, newest first: `id`, `reason`, `note`, counts |
| `GET /api/version` | `?path=&id=` (`&compare=1` or `&download=1`) | A version, or its differences from the current file |
| `POST /api/versions` | `{path, note}` | Save the current content as a version |
| `POST /api/versions/restore` | `{path, id, note?}` | Restore a version. The current content is saved first |
| `POST /api/versions/delete` | `{path, id}` | Delete a version |
| `GET /api/ha` | | Areas and entities (`listed`, `eligible`, `area`) |
| `POST /api/ha/apply` | `{createAreas, renameAreas, deleteAreas, entities}` | Change Home Assistant areas and entity names |
| `GET /api/homekit` | | Bridges and their exposed entities |
| `POST /api/homekit/apply` | `{bridge?, expose, hide, dry_run?}` | Change only the bridge filter |

## Limits

- The iOS app cannot pair or remove accessories. An entity appears in Apple Home only after the bridge exposes it.
- A bridge in accessory mode, or one configured in YAML, is read-only.
- A bridge holds at most 150 accessories. The response warns above that limit.
- Error bodies are `{"error": "..."}`: status 400 for bad input, 401 for auth, 502 for Home Assistant errors.
