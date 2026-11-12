# HomeKit HAOS add-ons

Home Assistant add-on repository.

## HomeKit Rooms

A page in the Home Assistant sidebar for sorting rooms and renaming things:

- Open an export from the HomeKit Rooms iOS app and drag accessories to another room.
- Rename lights and other devices, inline or with find and replace.
- Do the same with Home Assistant's own areas and entities.

See [homekit_rooms/DOCS.md](homekit_rooms/DOCS.md) (in Dutch).

## Installing

1. In Home Assistant, go to Settings → Add-ons → Add-on store, then ⋮ → **Repositories**.
2. Add `https://github.com/timsevenants/homekit-haos`.
3. Install **HomeKit Rooms**, start it, and turn on **Show in sidebar**.

Or use this link:

[![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftimsevenants%2Fhomekit-haos)

## Updating

Raise `version` in `homekit_rooms/config.yaml` and push. Home Assistant then offers the update.
