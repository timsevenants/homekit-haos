# HomeKit HAOS add-ons

Home Assistant add-on repository.

## HomeKit Rooms

A page in the Home Assistant sidebar for managing exports of the HomeKit Rooms iOS app:

- Import and export the JSON. Every change keeps a version, so you can compare and restore.
- Drag accessories to another room and rename them, inline or with find and replace.
- Add Home Assistant entities to the file, and choose which entities the HomeKit Bridge exposes.
- Do the same with Home Assistant's own areas and entities.
- Agents can do all of this through a JSON API with dry runs. See [AGENTS.md](AGENTS.md).

See [homekit_rooms/DOCS.md](homekit_rooms/DOCS.md) (in Dutch).

## Installing

1. In Home Assistant, go to Settings → Add-ons → Add-on store, then ⋮ → **Repositories**.
2. Add `https://github.com/timsevenants/homekit-haos`.
3. Install **HomeKit Rooms**, start it, and turn on **Show in sidebar**.

Or use this link:

[![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftimsevenants%2Fhomekit-haos)

## Updating

Raise `version` in `homekit_rooms/config.yaml` and push. Home Assistant then offers the update.
