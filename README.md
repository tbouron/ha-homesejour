# HomeSejour for Home Assistant

Custom integration that reads your bookings from the [HomeSejour](https://homesejour.fr) owner portal
(`/activite/plannings`) and exposes them as calendars and sensors, one device per property.

Entities have the same keys and attribute shapes as [ha-avantio](https://github.com/tbouron/ha-avantio),
so dashboards and templates written for Avantio carry over.

## Installation

1. HACS → Integrations → ⋮ → Custom repositories → add `https://github.com/tbouron/ha-homesejour` (Integration).
2. Install **HomeSejour** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **HomeSejour**.

## Configuration

| Field | Notes |
|---|---|
| Email / password | Your homesejour.fr owner account. |
| TOTP secret key | The base32 key behind the QR code you scanned in your authenticator app (spaces are ignored). The integration generates the 6-digit codes itself. |

- **Re-authenticate** is offered automatically when HomeSejour rejects the credentials.
- **Reconfigure** changes the password or TOTP secret of the same account.
- **Options**: check-in and check-out times (default 17:00 / 10:00). HomeSejour only gives dates.

Data refreshes every 6 hours.

## Entities (per property)

Entity ids use the property reference, e.g. `TERROIRS` → `sensor.homesejour_terroirs_total_earnings`.

| Entity | State | Attributes |
|---|---|---|
| `sensor.homesejour_<ref>_total_earnings` | Sum of "Net propriétaire" of all rental bookings (€) | `{year: "1234.56 €"}` by check-in year |
| `sensor.homesejour_<ref>_rental_days` | Total nights of rental bookings | `breakdown: {year: {month: nights}}` |
| `sensor.homesejour_<ref>_owner_days` | Total nights of owner bookings | `breakdown: {year: {month: nights}}` |
| `calendar.homesejour_<ref>_rental` | On during a rental stay | Event: booking reference, `💸 <net> €`, `Réservé via <origine>` |
| `calendar.homesejour_<ref>_owner` | On during an owner stay | Event: booking reference, `Réservé via <origine>` |

"Net propriétaire" is HomeSejour's estimate, before discounts and possible commissions.

### Which calendar a booking goes to

| État on HomeSejour | Calendar |
|---|---|
| Réservation personnelle, Période réservée à ma gestion | owner |
| La propriété est réservée, Une option est posée, Période fournie à un TO | rental |
| Ce dossier est clôturé | rental, or owner when its net is 0 € |
| Ce dossier est annulé, Information indisponible | ignored |

## New booking alerts

When a new rental booking appears, the integration creates a persistent notification and fires a
`homesejour_new_booking` event with `property_id`, `property_name`, `booking_id`, `reference`, `state`,
`start`, `end`, `nights`, `owner_net` and `origin`:

```yaml
automation:
  - alias: Phone alert on new HomeSejour booking
    triggers:
      - trigger: event
        event_type: homesejour_new_booking
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: "New booking — {{ trigger.event.data.property_name }}"
          message: "{{ trigger.event.data.start }} → {{ trigger.event.data.end }} ({{ trigger.event.data.owner_net }} €)"
```

## Moving from Avantio

The entity ids differ (`avantio_<id>_…` → `homesejour_<ref>_…`); update automations once, or rename the new
entities to the old ids in the UI after removing the Avantio integration. Totals only count bookings known to
HomeSejour; Avantio history stays in the recorder.

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

`scripts/live_check.py` logs in to the real portal (credentials prompted, never stored) and prints what the
client parses.
