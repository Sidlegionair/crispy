# ❄️ Crispy

> Because apparently “outside colder than inside” needed a supervisory control system.

Crispy is an opinionated Home Assistant controller for an **Orcon HRC 400 EcoMax** connected through **RAMSES-II RF**, **MQTT**, `ramses_cc`, and an ESP32-C6 sub-GHz gateway.

It adds three demand engines on top of the Orcon:

- free cooling from colder intake air;
- moisture removal when intake air is genuinely drier;
- an optional laundry-drying cycle.

The Orcon remains the base controller. Crispy only sends the existing `LOW`, `MEDIUM`, `HIGH`, fan `AUTO`, `BYPASS OPEN`, and `BYPASS AUTO` commands. It does not alter commissioned fan calibration or ventilation balancing, and a detected higher native or physical-control demand is preserved.

**[Open the real Crispy dashboard capture](./crispy-dashboard-example.pdf)**

## Architecture

```mermaid
flowchart TD
    H["Orcon HRC 400 EcoMax"] <-->|"RAMSES-II RF"| G["ESP32-C6 gateway"]
    G <-->|MQTT| R[ramses_cc]
    R <--> C["Home Assistant + Crispy"]
    C --> D["Dashboard + telemetry"]
    W["Open-Meteo (optional)"] --> F["24-hour heat and cooling-window policy"]
    F --> C
```

Forecasts can lower the target and increase fan aggression, but they never bypass the live safety gates. Intake and supply telemetry retain final authority over whether free cooling is actually useful.

## Decision model

Crispy calculates independent thermal, moisture, and laundry demand. Thermal demand combines the live temperature ladder, momentum, recent heat gain, and the rolling forecast. Auto then moves through explicit Attack, Cruise, and Release phases.

```mermaid
flowchart TD
    F["Forecast risk + window"] --> T["Thermal request"]
    H["Live HRC telemetry"] --> T
    T --> P["Attack / Cruise / Release"]
    P --> A["Strongest Crispy demand"]
    M["Moisture + laundry"] --> A
    A --> Q{"Guest Quiet enabled?"}
    Q -->|No| R["Applied request"]
    Q -->|"Yes: MEDIUM or HIGH"| C["Cap to LOW"]
    C --> R
    R --> E{"Detected external demand higher?"}
    E -->|Yes| V["Preserve Orcon demand"]
    E -->|No| X["Send, observe, verify"]
```

Higher demand is immediate. Ordinary reductions retain the 12-minute anti-hunting dwell, but a deliberate Cruise transition and a cooling-watchdog stop step down immediately. A useful run-on keeps the bypass open. When demand ends, Crispy sends fan `AUTO` instead of abandoning a timed boost.

### Thermal demand

Thermal cooling requires Crispy to be enabled, critical telemetry to be healthy, intake temperature to remain above the configured cold-intake guard, indoor temperature to be above target, and intake air to be cooler than indoor air.

| Indoor − intake | Base demand |
|---:|:---|
| `< 0.5°C` | None |
| `0.5–<1.0°C` | Low |
| `1.0–<3.0°C` | Medium |
| `≥ 3.0°C` | High |

Momentum inputs must remain plausible for five minutes after startup; another ten minutes of sustained warming then arms thermal pressure. Warming pressure can step the base request up once, while genuine cooling suppresses it. Recent heat gain provides separate two-hour memory. **Full Send** requests High whenever the apartment is above target and intake air is slightly cooler; it never enters Cruise.

### Predictive attack and adaptive cruise

The optional weather package evaluates the next 24 hours, including maximum temperature, a sunny-hours proxy, total useful cooling hours, and the remaining contiguous cooling window. High heat risk lowers the target by 0.5°C; extreme risk lowers it by 1.0°C. The temperature gap divided by remaining window hours determines whether predictive demand is Low, Medium, or High.

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Attack: Useful cooling needed
    Attack --> Cruise: 10 min heat removal and stable or falling room
    Cruise --> Attack: 8 min warming rebound or lost cooling
    Attack --> Release: Target or watchdog
    Cruise --> Release: Target reached
    Release --> Idle: Fan AUTO confirmed
```

Cruise tests Low, or Medium when the forecast window is urgent. Holding the room steady against solar gain counts as success: entry requires ten minutes of ≥50 W heat removal, supply ≥0.3°C cooler, and room trend ≤+0.03°C/h. Cruise records the entry trend; warming above +0.03°C/h and ≥0.05°C/h worse than that baseline for eight minutes returns to Attack. Lost cooling also re-attacks. This is a trend heuristic, not a measurement of solar gain.

The watchdog allows five minutes of continuous open-bypass Attack to settle, then requires three minutes of supply at/above room temperature or negligible airflow (≤5 L/s). A flat or rising room and low wattage alone never trip it. Retry normally waits ten minutes, but intake ≥1°C colder than at failure for two minutes ends the wait early if live cooling/guard conditions allow it. Thermal cooling opens the bypass; standalone moisture or laundry demand leaves it in Auto and remains allowed during a thermal lockout.

Every meaningful phase, fan, bypass, forecast, or reason change is written to `input_text.crispy_decision_trace` and the Home Assistant Logbook.

If bypass OPEN stays unconfirmed for three minutes while supply is no cooler (or airflow is negligible), the same thermal retry timer stops the boost. `input_text.crispy_watchdog_reason` distinguishes bypass failure from failed cooling through an open bypass. Moisture/laundry and detected external demand remain independently eligible.

Forecast influence expires 90 minutes after the last accepted fetch, or sooner when no numeric forecast point remains in the next hour. Predictive target offsets and aggression fall back to live control; manual Heatwave still works. The dashboard shows freshness and the accepted-fetch timestamp. Cached forecast graphs remain visible. This checks fetch age and time coverage, not the provider's internal model age.

Tap **Capture diagnostic snapshot** to create a copyable Home Assistant notification with temperatures, trends, demand, ownership, bypass/retry state, forecast freshness, and the latest command/decision. It captures values at tap time and replaces the previous snapshot; use Logbook for earlier decisions. It sends no RF commands or external messages.

### Moisture and laundry

Moisture control compares absolute humidity rather than relative humidity alone. It latches on when indoor RH is at least 60% or rising quickly, provided intake air is usefully drier. It releases when the drying advantage disappears or indoor RH has returned to 55% and stabilised.

Laundry mode records the starting absolute humidity and ventilates according to drying advantage. A rise of 0.5 g/m³ confirms that a wet load was seen; after that, the mode completes when humidity returns close to baseline and remains stable.

## Guest Quiet

`input_boolean.crispy_quiet_mode` is a real noise cap for having people over:

- dashboard shortcuts run it for 2, 4, or 6 hours, then restore normal policy;
- tapping the main Guest Quiet control starts four hours by default; tapping again cancels it;
- Crispy still calculates and exposes the uncapped demand;
- every held Crispy-originated Medium or High request—thermal, moisture, laundry, or Full Send—is applied as Low immediately;
- thermal cooling may still keep the bypass open, so useful cold air is not thrown away;
- a detected higher request from the Orcon or a physical remote still wins;
- `script.crispy_max` turns Guest Quiet off before engaging Full Send.

Directly toggling the helper remains an indefinite mode. Timed sessions survive a Home Assistant restart; startup reconciliation also clears a session that expired while Home Assistant was offline.

Guest Quiet is a comfort mode, not a humidity strategy. Leaving it enabled will make moisture removal and laundry drying slower. That is the trade: guests can hear each other; the towels lose the drag race.

## Failure behaviour

Crispy is designed to fail boring:

- missing or stale critical telemetry marks the controller unhealthy;
- missing forecast data disables predictive influence without blocking live control;
- implausible or newly restarted derivative data cannot arm momentum pressure;
- intake below the configured guard stops Crispy control;
- sustained absent delivered cooling trips a retry timer, with an early retry when intake improves;
- fault, frost, or disabled states return a Crispy-forced bypass to Auto;
- ended, disabled, faulted, or no-longer-useful demand sends fan `AUTO`, returning authority immediately;
- fan commands are checked against reported HRC state and retried once;
- higher external demand is latched and preserved until the HRC steps back down.

## Repository layout

| Path | Purpose |
|---|---|
| `packages/crispy.yaml` | Core helpers, derived telemetry, demand engines, arbitration, RF scripts, and controller |
| `packages/crispy_weather.yaml` | Optional 30-minute forecast fetch, rolling heat risk, target offset, and cooling-window policy |
| `dashboards/crispy_dashboard.yaml` | Optional Home Assistant dashboard; requires Mushroom cards |
| `crispy-dashboard-example.pdf` | Capture of the running dashboard |

## Requirements

- Home Assistant with packages enabled
- MQTT
- [`ramses_cc`](https://github.com/ramses-rf/ramses_cc)
- a compatible RAMSES-II RF gateway
- an Orcon HRC and a verified, bound virtual remote
- Open-Meteo for forecast cards (optional)
- Mushroom cards for the supplied dashboard (optional)

Tested hardware uses the [Elecram ESP32-C6 855–925 MHz bridge](https://elecram.com/products/esp32-c6-wifi-zigbee-to-855-925mhz-wireless).

## Installation

1. Get the HRC visible in `ramses_cc` and verify `low_60`, `medium_60`, `high_60`, `auto`, `bypass_open`, and `bypass_auto` manually.
2. Create these two Home Assistant helpers, which are intentionally expected rather than declared by the package:
   - `input_boolean.crispy_mode`
   - `input_number.crispy_target`
3. Copy `packages/crispy.yaml` into your packages directory.
4. Optionally copy `packages/crispy_weather.yaml` and the dashboard.
5. Map the installation-specific entities in the adapter blocks below.
6. Restart Home Assistant with Crispy **off**, validate every sensor and command, then enable it.

Example package loading:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

### Installation-specific references

| Configuration point | Replace with |
|---|---|
| `INSTALLATION ADAPTER` in `packages/crispy.yaml` | Your HRC temperature, humidity, flow, fan, filter, and bypass entities |
| `input_text.crispy_remote_entity` initial value | Your bound virtual remote |
| `input_text.crispy_weather_entity` initial value | Your weather entity, if using forecasts |
| Weather card in `dashboards/crispy_dashboard.yaml` | The same weather entity; Lovelace entity fields are not templated |

The HRC entity called `outdoor_temperature` is treated by Crispy as **intake temperature at the unit**, not a perfect ambient outdoor reading.

## Control modes

| Control | Effect |
|---|---|
| Crispy Mode | Enables or releases the supervisory controller |
| Auto | Uses predictive Attack/Cruise/Release control |
| Full Send | Requests High whenever useful colder intake air is available |
| Predictive | Uses the rolling 24-hour heat risk and cooling window; safely falls back when unavailable |
| Guest Quiet | Caps Crispy-originated demand at Low; dashboard presets run 2/4/6 hours |
| Heatwave | Manually lowers the target by 0.5°C; the strongest manual/forecast offset wins |
| Laundry | Runs the humidity-baseline drying lifecycle |
| MAX CRISPY | Target 19°C, Guest Quiet off, Heatwave off, Full Send on |
| NORMAL | Disables Crispy and returns fan and bypass control to the Orcon |

## Cooling telemetry

Delivered sensible cooling is estimated as:

```text
P ≈ 1.2 × airflow[L/s] × (T_indoor − T_supply)
```

This powers the live cooling estimate, accumulated cooling energy, session progress, equivalent AC runtime, and forecast opportunity cards. These are engineering estimates, not calibrated energy measurements.

## Boundaries

This is a personal experimental controller, not an official Orcon product, certified HVAC controller, or universal integration. It was built and tested on one installation.

Keep the manufacturer controller underneath it, validate every mapped entity and RF command, and only transmit to equipment you own or are authorised to control. RAMSES IDs are identifiers, not credentials; protect MQTT, Wi-Fi, Home Assistant, and API secrets normally.

If the telemetry makes no sense: turn Crispy off. The house should become less smart, not more exciting.

## License

[MIT](./LICENSE)
