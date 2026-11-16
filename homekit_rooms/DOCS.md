# HomeKit Rooms (add-on)

Een webpagina in de zijbalk van Home Assistant. Je beheert er de JSON-exports van de HomeKit Rooms-app en kiest welke entiteiten de HomeKit Bridge in Apple Woning toont. Bovenaan kies je de bron.

## Bronnen

**Een JSON-bestand in `/share` of `/share/homekit-rooms/`**
- Dat is een export uit de HomeKit Rooms-app.
- Sleep accessoires naar een andere ruimte. Sleep ruimtes aan ⠿ in de gewenste volgorde. Wijzig namen met ✎.
- *Opslaan* overschrijft het bestand. De vorige inhoud wordt eerst als versie bewaard (zie *Versies*).
- Met **Ook in Home Assistant** worden dezelfde wijzigingen ook toegepast op entiteiten waarvan het HomeKit-serienummer een entity_id is. Dat is zo bij accessoires van de HomeKit Bridge.
- Het aangepaste bestand importeer je daarna in de iOS-app. Die toont eerst de verschillen en maakt een backup van de woning.

**Home Assistant**
- De gebieden en entiteiten van deze Home Assistant.
- Sleep een entiteit naar een ander gebied of tik op ✎ om haar naam te wijzigen.
- *Opslaan* past dit rechtstreeks in Home Assistant aan, via het entiteitenregister.
- Standaard zie je alleen bedienbare domeinen, zoals lampen, schakelaars en screens. *Alle entiteiten* toont ook sensoren, verborgen entiteiten en configuratie-entiteiten.

## Importeren en exporteren

- **Importeren** uploadt een JSON-bestand van je computer of iPad naar `/share/homekit-rooms/`. Bestaat het bestand al, dan vraagt de pagina eerst of je het wilt overschrijven. De oude inhoud blijft als versie bewaard.
- **Exporteren** downloadt het geopende bestand, inclusief wijzigingen die nog niet opgeslagen zijn. Dat bestand open je in de iOS-app met *Importeren uit JSON*.

## Versies

Bij elke schrijfactie wordt de vorige inhoud eerst bewaard: opslaan, importeren, terugzetten en wijzigingen via de API. Onder **Versies** kun je per versie:
- het verschil met nu bekijken;
- de versie terugzetten (de huidige inhoud wordt dan eerst zelf een versie);
- de versie downloaden of verwijderen.

Met *Huidige versie bewaren* leg je zelf een versie vast, met een notitie. Bij *Opslaan* kun je ook een notitie meegeven.

De versies staan in `/share/homekit-rooms/backups/<bestand>/`. Per bestand worden de laatste 200 bewaard.

## Entiteiten toevoegen en HomeKit Bridge

Bovenaan kies je de **HomeKit-bridge** van Home Assistant (integratie *HomeKit Bridge*). Elke entiteit heeft dan een knop **HomeKit**:
- met ✓ en blauw: de bridge toont deze entiteit in Apple Woning;
- zonder vinkje: niet zichtbaar.

Tik op de knop om dat te wijzigen. *Opslaan* past de filter van de bridge aan, via dezelfde opties als in Instellingen → Apparaten & diensten → HomeKit Bridge → Configureren. De bridge herstart daarna vanzelf.

- Zit een heel domein in de bridge (bijvoorbeeld "alle schakelaars") en verberg je één entiteit, dan zet de add-on de andere entiteiten van dat domein apart in de lijst. Wat zichtbaar was, blijft zichtbaar.
- Bridges in accessoire-modus of uit YAML kunnen alleen gelezen worden.
- Eén bridge toont maximaal 150 accessoires. De add-on waarschuwt als je daarboven gaat.

**+ Entiteit** (bij een JSON-bestand) voegt entiteiten uit Home Assistant toe aan het bestand:
- kies een ruimte, of laat *Gebied uit Home Assistant* staan;
- met *Zichtbaar maken in HomeKit* komen ze ook in de bridge.

Zo werkt het geheel:
1. Voeg in de add-on de entiteiten toe en sla op. De bridge herstart en de nieuwe accessoires verschijnen in Apple Woning, in de standaardruimte.
2. Importeer het bestand in de iOS-app. Die zet de accessoires in de juiste ruimte en geeft ze de juiste naam. Ze worden herkend op serienummer, en dat is bij de HomeKit Bridge het entity_id.

## Voor agents (API)

Alles wat de pagina doet, kan ook via een JSON-API. `GET api` geeft een beschrijving van alle endpoints met een voorbeeld. Het belangrijkste endpoint is `POST api/agent`: één aanroep die het bestand en de HomeKit-bridge aanpast.
- Standaard is dat een **dry-run**: het antwoord toont `changes` en `warnings`, maar wijzigt niets.
- Met `"dry_run": false` en een `note` wordt het toegepast, met eerst een versie.

**Toegang:**
1. Vul onder *Configuratie* van de add-on een lange willekeurige `api_token` in.
2. Zet onder *Netwerk* een poort open, bijvoorbeeld 8099.
3. Herstart de add-on.

Elke aanvraag op die poort moet dan meesturen: `Authorization: Bearer <api_token>`. Zonder token blijft de poort dicht. Via Ingress, de zijbalk, is geen token nodig.

```sh
curl -s http://homeassistant.local:8099/api/agent \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"file": "homekit-rooms/Thuis.json",
       "add_entities": [{"entity_id": "light.bureau", "room": "Bureau"}],
       "accessories": [{"match": "light.keuken_spots", "name": "Spots"}],
       "homekit": {"hide": ["switch.test"]},
       "dry_run": true}'
```

Zie [AGENTS.md](https://github.com/timsevenants/homekit-haos/blob/main/AGENTS.md) voor de volledige beschrijving.

## Goed om te weten

- Home Assistant kent geen volgorde van gebieden. De volgorde van ruimtes wordt alleen in een JSON bewaard.
- Entiteiten zonder unique_id, bijvoorbeeld uit YAML, kun je niet hernoemen of verplaatsen. Ze zijn grijs.
- Een nieuwe naam in Home Assistant verandert de naam in Apple Woning niet: de HomeKit Bridge geeft namen alleen door bij het eerste koppelen. Gebruik daarvoor de JSON en de iOS-app.
- De pagina laadt SortableJS van cdn.jsdelivr.net. De browser heeft dus internet nodig.
