# STIB/MIVB — Home Assistant HACS Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Monitor real-time bus/tram/metro waiting times from the **Brussels public transport network (STIB/MIVB)** directly in Home Assistant.

---

## Features

- 🚌 **One device per stop name** — all platforms of the same stop are grouped together  
- ⏱️ **One sensor per line at each stop** — shows minutes until the next arrival  
- 🔍 **Search stops by name** — type "forest" to find all stops containing that word  
- 🌍 **French or Dutch** display language (chosen at setup)  
- 📍 **Rich attributes** — coordinates, stop names in both languages, destination, platform IDs  
- 🔄 **Configurable polling interval** (default 30 s)  
- ➕ **Add more stops at any time** via the integration options  
- 🛑 **Short-turn proof** — sensor names always use the canonical end-of-line destination, not a temporary short-turn destination  

---

## Prerequisites — API Key

This integration requires a free API key from the STIB/MIVB open data portal.

1. Go to **[api-management-opendata-production.developer.azure-api.net](https://api-management-opendata-production.developer.azure-api.net/)**  
2. Log in or create a free account  
3. Go to your **Profile** page  
4. Subscribe to the **"Standard"** product  
5. Copy your **primary or secondary key** — you will need it during setup  

---

## Installation via HACS

1. Open **HACS → Integrations → ⋮ → Custom repositories**  
2. Paste `https://github.com/danito/hacs-stib-mivb` and select category **Integration**  
3. Click **Download**  
4. Restart Home Assistant  

---

## Setup

1. Go to **Settings → Devices & Services → Add integration → STIB/MIVB**  
2. Choose your preferred **display language** (French or Dutch) and enter your **API key**  
3. Wait a few seconds while the integration downloads the full stop catalogue (~2 400 stops)  
4. **Search** for a stop by typing part of its name (e.g. `forest`)  
5. **Select** the stop name from the results — all physical platforms are grouped automatically  
6. Repeat steps 4–5 to add more stops, then click **Finish**  

---

## Sensors

Each sensor is named **`sensor.line_<LINE>_<STOP_NAME>_<DESTINATION>`** and belongs to a device named after the stop.

The sensor name includes both the **stop name** and the **canonical destination**, so sensors remain unique and readable even when the same line passes through multiple monitored stops.

For example, monitoring both **FOREST NATIONAL** and **SAINT-DENIS** on line 54 produces:

- `sensor.line_54_forest_national_forest_bervoets` — Line 54 at Forest National, towards Forest (Bervoets)
- `sensor.line_54_saint_denis_forest_bervoets` — Line 54 at Saint-Denis, towards Forest (Bervoets)

Both sensors appear under their respective devices (**FOREST NATIONAL** and **SAINT-DENIS**) in the device registry.

### Short-turn handling

When a line is temporarily short-turning (e.g. line 54 terminating at WIELS instead of FOREST (BERVOETS)), the sensor name and unique ID are always based on the **canonical end-of-line destination** fetched from the static timetable. This prevents phantom sensors being created during disruptions. The real-time destination is still visible as the `destination` attribute.

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
| `destination` | Current real-time destination (in your chosen language) |
| `line_id` | Line number |
| `point_ids` | List of all physical platform IDs grouped under this stop |

---

## Example Lovelace card

```yaml
type: entities
title: "🚌 Forest National"
entities:
  - entity: sensor.line_54_forest_national_forest_bervoets
    name: "Line 54 → Forest (Bervoets)"
    icon: mdi:tram
  - entity: sensor.line_97_forest_national_stalle
    name: "Line 97 → Stalle"
    icon: mdi:bus
```

---

## Data sources

- **Static data** (stops, names, GPS): STIB/MIVB Open Data — `StopDetails` endpoint  
- **Static timetable** (canonical destinations): STIB/MIVB Open Data — `stopsByLine` endpoint  
- **Real-time data** (waiting times): STIB/MIVB Open Data — `WaitingTimes` endpoint  

All endpoints require the `bmc-partner-key` header with your API key.

---

## Options

After setup, go to **Settings → Devices & Services → STIB/MIVB → Configure** to:

- Adjust the **polling interval**  
- **Add additional stops** using the same name-search flow  

---

## Contributing

Pull requests are welcome! Please open an issue first to discuss major changes.

## License

MIT
