# STIB/MIVB — Home Assistant HACS Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Monitor real-time bus/tram/metro waiting times from the **Brussels public transport network (STIB/MIVB)** directly in Home Assistant.

---

## Features

- **One device per stop** — clearly grouped in the device registry  
- **One sensor per line per direction** at each stop — shows minutes until the next arrival  
- **French or Dutch** display language (chosen at setup)  
- **Rich attributes** — coordinates, stop names in both languages, direction, destination  
- **Configurable polling interval** (default 30 s)  
- **Add more stops at any time** via the integration options  

---

## Installation via HACS

1. Open **HACS → Integrations → ⋮ → Custom repositories**  
2. Paste `https://github.com/danito/hacs-stib-mivb` and select category **Integration**  
3. Click **Download**  
4. Restart Home Assistant  

---

## Setup

1. Go to **Settings → Devices & Services → Add integration → STIB/MIVB**  
2. Choose your preferred **display language** (French or Dutch)  
3. Enter a **line number** (e.g. `54`) — the integration fetches all stops on that line  
4. Select one or more **stops** from the list  
5. Repeat step 3-4 for as many lines/stops as you need  
6. Click **Finish**  

---

## Sensors

Each sensor is named **`sensor.line_<LINE>_<STOP_NAME>_<DIRECTION>`** and reports:

Because the same line can pass through the same stop in two directions (e.g. towards the city centre and away from it), the direction is included in both the sensor name and its unique ID to avoid collisions. For example, line 54 at JUPITER produces two sensors:

- `sensor.line_54_jupiter_city`
- `sensor.line_54_jupiter_suburb`

Both sensors appear under the same **JUPITER** device in the device registry.

| State | Meaning |
|---|---|
| `number` | Minutes until the next vehicle arrives |
| `unavailable` | API unreachable or no service |

### Attributes

| Attribute | Description |
|---|---|
| `next_passage` | ISO timestamp of the **second** upcoming vehicle |
| `latitude` / `longitude` | Stop GPS coordinates |
| `stop_name_fr` | Stop name in French |
| `stop_name_nl` | Stop name in Dutch |
| `direction` | `City` or `Suburb` |
| `destination` | Final destination (in your chosen language) |
| `line_id` | Line number |
| `stop_id` | Internal STIB/MIVB stop ID |

---

## Example Lovelace card

```yaml
type: entities
title: "🚌 Stop Jupiter – Line 54"
entities:
  - entity: sensor.line_54_jupiter_city
    name: "→ City centre"
    icon: mdi:tram
  - entity: sensor.line_54_jupiter_suburb
    name: "→ Suburb"
    icon: mdi:tram
```

---

## Data sources

- **Static data** (stops, names, GPS): [STIB/MIVB Open Data](https://api-management-discovery-production.azure-api.net)  
- **Real-time data** (waiting times): same API, `/rt/WaitingTimes` endpoint  

No API key required — the endpoints are publicly accessible.

---

## Options

After setup, go to **Settings → Devices & Services → STIB/MIVB → Configure** to:

- Adjust the **polling interval**  
- Add **additional stops**  

---

## Contributing

Pull requests are welcome! Please open an issue first to discuss major changes.

## License

MIT
