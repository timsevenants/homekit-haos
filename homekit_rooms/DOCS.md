# HomeKit Rooms (add-on)

Een webpagina in de zijbalk van Home Assistant om ruimtes te sorteren en namen te wijzigen. Bovenaan kies je de bron.

## Bronnen

**Home Assistant**
- De gebieden en entiteiten van deze Home Assistant.
- Sleep een entiteit naar een ander gebied, of tik op ✎ om haar naam te wijzigen.
- *Opslaan* past dit rechtstreeks in Home Assistant aan, via het entiteitenregister.
- Standaard zie je alleen bedienbare domeinen, zoals lampen, schakelaars en screens. *Alle entiteiten* toont ook de sensoren.

**Een JSON-bestand in `/share` of `/share/homekit-rooms/`**
- Dat is een export uit de HomeKit Rooms-app.
- Sleep accessoires naar een andere ruimte, sleep ruimtes aan ⠿ in de gewenste volgorde, en wijzig namen.
- *Opslaan* overschrijft het bestand. De vorige versie komt eerst in `/share/homekit-rooms/backups/`.
- Met **Ook in Home Assistant** worden dezelfde wijzigingen ook toegepast op entiteiten waarvan het HomeKit-serienummer een entity_id is. Dat is zo bij accessoires van de HomeKit Bridge.
- Het aangepaste bestand importeer je daarna in de iOS-app. Die toont eerst de verschillen en maakt een backup van de woning.

**Zoek en vervang** werkt op alle zichtbare namen. Leeg *Vervang door* om bijvoorbeeld "Keuken " voor elke naam weg te halen.

Elke wijziging is geel gemarkeerd. *Opslaan* toont eerst de lijst van wijzigingen.

## Goed om te weten

- Home Assistant kent geen volgorde van gebieden. De volgorde van ruimtes wordt alleen in een JSON bewaard.
- Entiteiten zonder unique_id, bijvoorbeeld uit YAML, kun je niet hernoemen of verplaatsen. Ze zijn grijs.
- Een nieuwe naam in Home Assistant verandert de naam in Apple Woning niet: de HomeKit Bridge geeft namen alleen door bij het eerste koppelen. Gebruik daarvoor de JSON en de iOS-app.
- De pagina laadt SortableJS van cdn.jsdelivr.net. De browser heeft dus internet nodig.
