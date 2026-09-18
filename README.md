# ❄️ Crispy

> Because apparently “outside colder than inside” needed a control system.

Crispy is an opinionated Home Assistant control layer for an **Orcon HRC 400 EcoMax** using **RAMSES RF**, **MQTT** and an **ESP32-C6 Sub-GHz gateway**.

It has two jobs:

```text
HOUSE HOT + INTAKE COLDER
        ↓
USE THE COLD AIR

HOUSE WET + INTAKE DRIER
        ↓
USE THE DRY AIR
```

That is basically it.

The ridiculous part is deciding **how hard**, **for how long**, and **when to leave the Orcon alone**. Crispy uses temperature trend, recent heat gain, airflow, supply temperature, moisture rate and weather forecasts to make those decisions.

Normal ventilation and CO₂ remain the Orcon's job. If the Orcon independently asks for more ventilation, **Orcon wins**.

```text
observe → derive → decide → intervene → verify → release
```

> **Crispy decides WHEN to request HIGH. Orcon decides WHAT HIGH is.**

## 👀 See it running

**[Open the real Crispy dashboard capture](./crispy-dashboard-example.pdf)**

Actual running system, not a mock-up.

> Yes, all of this exists because outside was colder than inside.

---

# ⚠️ Before installing

Crispy controls real residential ventilation hardware. It is a **personal experimental project**, not an official Orcon product, certified HVAC controller or universal Home Assistant integration.

It was built and tested on one installation. Your hardware, RF topology, sensors, firmware, climate and entity names may differ.

Important boundaries:

- Crispy requests the Orcon's existing **LOW / MEDIUM / HIGH** modes and bypass state; it does **not** change commissioned fan calibration or ventilation balancing.
- The original Orcon remains the base controller. Invalid telemetry should make Crispy relinquish control rather than become more adventurous.
- Verify `LOW`, `MEDIUM`, `HIGH`, `BYPASS OPEN` and `BYPASS AUTO` manually before enabling automatic control.
- Only transmit to RF equipment you own or are authorized to control.
- RAMSES IDs are identifiers, not credentials. MQTT passwords, Wi-Fi credentials, API tokens and Home Assistant secrets still are.
- If in doubt: **CRISPY MODE: OFF**.

```text
SMART LAYER BROKEN
        ↓
MAKE HOUSE LESS SMART
        ↓
FAIL BORING
```

# The problem

South-facing glass + good insulation can leave the apartment warm even when useful cold air is available outside.

```text
Apartment: 30°C
Intake:    20°C

Crispy:
WE HAVE TEN DEGREES OF FREE COOLING.
OPEN THE BYPASS.
```

Same philosophy for moisture: if the apartment is wet and intake air is materially drier, use it.

# Architecture

```text
Orcon HRC
 ⇅ RAMSES RF
Elecram ESP32-C6
 ⇅ Wi-Fi / MQTT
Home Assistant
 ↓
CRISPY
 ↓
observe → derive → decide → arbitrate → intervene → verify → release
```

The Orcon remains authoritative for normal ventilation and its own higher demand.

Crispy is the annoying supervisor standing behind it with a clipboard.

---

# Hardware

## Tested setup

- **Orcon HRC 400 EcoMax**
- **Home Assistant**
- **MQTT broker**
- **Elecram ESP32-C6 WiFi/Zigbee to 855–925 MHz Wireless Bridge**
- **Open-Meteo** Home Assistant integration for forecast intelligence
- **Mushroom** cards for the supplied dashboard
- Wi-Fi
- an irresponsible amount of YAML

## RF gateway

Elecram ESP32-C6 WiFi/Zigbee to 855–925 MHz Wireless Bridge:

https://elecram.com/products/esp32-c6-wifi-zigbee-to-855-925mhz-wireless

Upstream RAMSES RF:

https://github.com/ramses-rf/ramses_rf

Home Assistant integration:

https://github.com/ramses-rf/ramses_cc

Use the current Elecram/RAMSES documentation for firmware, installation and compatibility rather than treating this README as eternal firmware documentation.

---

# Hardware architecture

The RF gateway is a small ESP32-C6/sub-GHz bridge that connects the
RAMSES-II network to MQTT over Wi-Fi.

```text
Orcon HRC
 ↕
RAMSES-II RF
 ↕
ESP32-C6 / sub-GHz gateway
 ↕
Wi-Fi / MQTT
 ↕
Home Assistant + ramses_cc
 ↕
Crispy
```

The gateway only provides the transport between the physical RAMSES
network and Home Assistant. Crispy's control logic runs in Home
Assistant.

---

# Repository layout

Recommended layout:

```text
crispy/
├── README.md
├── packages/
│ ├── crispy.yaml
│ └── crispy_weather.yaml
├── dashboards/
│ └── crispy_dashboard.yaml
└── screenshots/
 └── crispy_dashboard.png
```

There are two Home Assistant packages for a reason:

```text
crispy.yaml
 =
the actual control system

crispy_weather.yaml
 =
forecast intelligence
```

And then:

```text
crispy_dashboard.yaml
 =
the shiny control room
```

The dashboard is not the controller.

If the dashboard explodes, Crispy should continue doing Crispy things.

---

# Opinionated by design

Crispy is **not** a general-purpose ventilation-control toolkit and deliberately **not** an air-quality controller.

```text
Orcon:
normal ventilation + CO₂ + native control

Crispy:
thermal/free cooling + extra moisture handling

Orcon asks for more:
Orcon wins
```

Once the HRC and virtual remote are mapped, RF commands are verified and a comfort target is chosen:

```text
map → verify → choose target → CRISPY ON → go
```

Thermal momentum, recent heat gain, moisture-event detection, arbitration, command reconciliation, forecast correction and cooling-performance calculations are implementation details. You should not need to design the strategy yourself.

Different homes still behave differently: validate it on your installation.

If you want a framework for constructing your own ventilation strategy from reusable control features, a general-purpose project such as Ramses Extras may be a better fit.

> **Cold available → use it.**  
> **Dry air available when we're wet → use it.**  
> **Orcon wants more ventilation → respect it.**

---

# Software dependencies

## Core controller

Required:

- Home Assistant
- MQTT
- `ramses_cc`
- a compatible RAMSES RF gateway
- `packages/crispy.yaml`

## Weather intelligence

Recommended:

- Home Assistant **Open-Meteo** integration
- `packages/crispy_weather.yaml`

The supplied weather package currently expects:

```text
weather.forecast_thuis
```

If your weather entity has another ID, replace that reference in the
weather package and dashboard.

`crispy_weather.yaml` requests an **hourly forecast** using Home
Assistant's `weather.get_forecasts` action every 30 minutes.

It then derives forecast information used by Crispy, including things
such as:

```text
forecast intake offset
lowest forecast outdoor temperature
lowest predicted intake temperature
best cooling time
useful cooling hours
forecast cooling opportunity
```

The distinction is important:

```text
HRC telemetry
 =
what is actually happening

Open-Meteo
 =
what is probably about to happen
```

Crispy also compares actual HRC intake temperature with reported ambient
weather temperature to derive a rough intake/building offset for
forecasting.

Conceptually:

```text
Open-Meteo:
"Tonight will be 14°C."

Crispy:
"My intake currently runs warmer than ambient."

Crispy:
"Fine. I'll account for that."
```

Forecast corrected using observed building behaviour.

Because apparently checking tomorrow's weather wasn't complicated
enough.

The core controller can still operate from live HRC telemetry without
the weather package, but forecast-derived features will be unavailable.

Crispy loses foresight.

It does not lose consciousness.

## Dashboard

Optional:

- `dashboards/crispy_dashboard.yaml`
- **Mushroom** cards

The supplied dashboard uses custom Mushroom cards including:

```text
custom:mushroom-template-card
custom:mushroom-chips-card
```

It also uses standard Home Assistant dashboard cards such as:

```text
weather-forecast
history-graph
markdown
heading
grid
```

Mushroom is therefore a **dashboard dependency**, not a dependency of
the Crispy control engine.

Headless Crispy is completely valid.

It just looks less expensive.

---

# Installation

## 1. Get Home Assistant and MQTT working

You need functioning Home Assistant and MQTT.

If Home Assistant itself is held together with hope:

```text
fix that first
```

We are about to add RF-controlled HVAC to it.

## 2. Set up the Elecram gateway

Configure/flash the gateway using the current Elecram/RAMSES instructions.

Configure Wi-Fi, MQTT and the RAMSES RF firmware, then verify traffic reaches MQTT.

```text
ESP boots
 ↓
Wi-Fi connects
 ↓
MQTT connects
 ↓
RAMSES traffic appears
 ↓
hehe packets
```

Do not continue until this is boringly reliable.

## 3. Install `ramses_cc`

Install:

https://github.com/ramses-rf/ramses_cc

Follow the **current upstream instructions**.

If this README is five years old and upstream disagrees:

```text
trust upstream
```

## 4. Discover your HRC

Let RAMSES observe your RF environment.

Mine appears as:

```text
FAN 32:142350
```

Yours will have its own ID.

If yours is also `32:142350`, I have questions.

## 5. Identify your physical controls

Operate your real controls while watching RAMSES traffic:

```text
press HIGH → watch packets
press LOW → watch packets
```

Establish which devices are actually yours.

Do not transmit to mystery RF devices.

## 6. Create and bind a virtual remote

Crispy controls my HRC through a bound virtual RAMSES remote:

```text
REM 37:099999
```

Follow current `ramses_cc`/RAMSES documentation for emulation and binding.

Example command shape:

```yaml
action: ramses_cc.send_command
target:
 entity_id: remote.your_virtual_remote
data:
 command: high_60
```

Then observe the actual HRC state.

Boring repeatability is good engineering.

## 7. Install Open-Meteo

For Crispy's forecast intelligence, add the **Open-Meteo** integration
through Home Assistant:

```text
Settings
 → Devices & services
 → Add integration
 → Open-Meteo
```

Configure it for your Home Assistant location.

After setup, verify that you have a weather entity under:

```text
Developer Tools
 → States
```

My configuration uses:

```text
weather.forecast_thuis
```

Your entity ID may differ.

That is fine.

The YAML just needs to know what yours is called.

## 8. Install the Crispy packages

Copy both packages:

```text
packages/crispy.yaml
packages/crispy_weather.yaml
```

into your Home Assistant packages directory, typically:

```text
/config/packages/
```

Your Home Assistant configuration needs packages enabled, for example:

```yaml
homeassistant:
 packages: !include_dir_named packages
```

The exact package-loading arrangement is up to you.

`crispy.yaml` contains the core controller: helpers, derived sensors,
scripts, demand engines, arbitration and control logic.

`crispy_weather.yaml` contains the forecast layer.

The weather package currently references:

```text
weather.forecast_thuis
```

If your Open-Meteo entity is named differently, replace that reference.

Also replace installation-specific HRC and remote entity IDs with your
own where required.

Understand what you are replacing.

Do not just change random numbers until the YAML stops being red.

Then restart Home Assistant.

## 9. Verify the weather layer

After Home Assistant starts, verify that the weather package has created
its forecast entities.

The package fetches hourly forecasts at startup and every 30 minutes.

You should see entities such as Crispy's forecast intake offset, lowest
forecast outdoor/intake temperatures, best cooling time and useful
cooling hours.

If those are unavailable, check:

```text
weather entity exists
 ↓
hourly forecast works
 ↓
crispy_weather.yaml references correct weather entity
 ↓
template sensors become available
```

Do not debug predictive cooling by staring angrily at the RF gateway.

Different department.

## 10. First boot: Crispy OFF

Start with:

```text
CRISPY MODE: OFF
```

Validate temperatures, humidity, airflow, actual fan state and forecast
entities before automatic control.

If Crispy says:

```text
INTAKE: -273°C
```

do not respond with:

```text
interesting
```

Fix the sensor.

## 11. Install Mushroom

The supplied dashboard uses **Mushroom** custom cards.

Install Mushroom through HACS:

```text
HACS
 → Frontend
 → search "Mushroom"
 → Download
```

Follow HACS/Home Assistant's prompt to reload or restart the frontend as
required.

If you do not want the supplied dashboard, you do not need Mushroom.

Again:

```text
Mushroom ≠ Crispy
Mushroom = pretty buttons for Crispy
```

## 12. Install the dashboard

Install:

```text
dashboards/crispy_dashboard.yaml
```

only **after** the Crispy packages and Mushroom are working.

The dashboard expects entities created by `crispy.yaml`,
`crispy_weather.yaml`, the underlying `ramses_cc` integration and the
weather integration.

It also currently references:

```text
weather.forecast_thuis
```

Change that if your weather entity differs.

### Using the dashboard editor

Create a new Home Assistant dashboard/view and open its raw YAML
configuration editor.

Paste/import the supplied dashboard YAML and save it.

If you manage Lovelace entirely through YAML, include the supplied
dashboard using your existing YAML dashboard setup instead.

If you install the dashboard first, Home Assistant will helpfully show:

```text
Entity not found
Entity not found
Entity not found
Entity not found
```

Technically a dashboard.

Just not a useful one.

## 13. Commission incrementally

Recommended complete order:

```text
Home Assistant
 ↓
MQTT
 ↓
Elecram / RAMSES gateway
 ↓
ramses_cc
 ↓
discover HRC
 ↓
bind virtual remote
 ↓
verify RF commands manually
 ↓
Open-Meteo
 ↓
crispy.yaml
 ↓
crispy_weather.yaml
 ↓
verify all Crispy entities
 ↓
HACS + Mushroom
 ↓
crispy_dashboard.yaml
 ↓
CRISPY OFF
 ↓
commission control behaviour
 ↓
CRISPY ON
 ↓
observe
 ↓
FULL SEND only after earning the privilege
```

Commission the actual control features in stages:

```text
telemetry
 ↓
manual RF commands
 ↓
command verification
 ↓
bypass control
 ↓
basic thermal control
 ↓
external-demand arbitration
 ↓
moisture control
 ↓
thermal momentum / memory
 ↓
forecast intelligence
 ↓
FULL SEND
```

Do not install everything and immediately go on holiday.

---

# Thermal control

Version 1:

```python
if apartment_hot and outside_colder:
 fan_go_brrrr()
```

Unfortunately I discovered thermodynamics.

Crispy considers indoor, intake and supply temperature, cooling advantage, airflow, thermal momentum, recent heat accumulation, estimated cooling power, and whether the apartment is actually cooling.

Base ladder:

```text
Cooling advantage < 0.5°C NONE
0.5°C – <1.0°C LOW
1.0°C – <3.0°C MEDIUM
≥3.0°C HIGH
```

Then reality gets a vote.

---

# Thermal Momentum™

```text
Indoor: 23.3°C
Target: 21.0°C
Base demand: MEDIUM
Temperature: +0.76°C/h
```

Base demand:

```text
MEDIUM
```

Reality:

```text
apartment rapidly getting hotter
```

Crispy:

```text
WE ARE VERY CLEARLY LOSING.
```

Sustained warming can escalate ventilation.

Because the requested mode matters less than whether the building is actually cooling down.

---

# Thermal Memory™

```text
22.3°C → 23.0°C → 22.95°C
```

Simple controller:

```text
temperature falling 👍
```

Crispy:

```text
YOU HAVE ACCUMULATED 0.65°C OF BULLSHIT.
```

Meaningful recent heat accumulation can escalate:

```text
LOW → MEDIUM
MEDIUM → HIGH
HIGH → HIGH
```

One good sample does not mean the building forgot what the sun did to it for the previous two hours.

---

# Free cooling

Approximate delivered sensible cooling:

```text
P ≈ 1.2 × airflow[L/s] × (T_indoor - T_supply)
```

Example:

```text
Indoor: 30°C
Intake: 20°C
Supply: 21.5°C
Flow: 70 L/s

Cooling ≈ 714 W
```

Nature provides the cooling.

Crispy provides the unnecessary instrumentation.

```text
OUTSIDE
 ↓
INTAKE
 ↓
HRC + DUCTS
 ↓
SUPPLY
 ↓
APARTMENT
```

Intake tells us what nature offered.

Supply tells us what actually arrived.

Apparently the ducts have lore now.

---

# Moisture control

Then I took a shower.

Typical baseline during testing:

```text
~9.7 g/m³ absolute humidity
```

Observed shower peak:

```text
~12.2 g/m³
≈ +26% moisture load
```

Observed humidity rise during one test:

```text
+24 %RH/h
+32 %RH/h
+36 %RH/h
```

Crispy:

```text
oh we're doing this now

→ MOISTURE BOOST
→ HIGH
```

It can react while moisture is still being generated and try to clip the peak.

Peak-load management.

For shower steam.

---

# Drying advantage

Relative humidity is temperature-dependent, so Crispy also derives absolute humidity.

```text
DRYING ADVANTAGE =
INDOOR ABSOLUTE HUMIDITY
-
INTAKE ABSOLUTE HUMIDITY
```

Example:

```text
Indoor: 12.2 g/m³
Intake: 8.2 g/m³

Advantage: +4.0 g/m³
```

Translation:

```text
OUTSIDE AIR THIRSTY.
```

---

# Laundry Mode™

Optional moisture demand for drying clothes. Same rule, different moisture source:

```text
inside wetter + intake drier → ventilate
```

# Demand arbitration

Departments allowed to yell at the fan:

```text
THERMAL
MOISTURE
LAUNDRY
```

Each requests:

```text
NONE / LOW / MEDIUM / HIGH
```

Strongest valid Crispy demand wins.

If the Orcon independently wants more:

```text
Crispy: MEDIUM
Orcon: HIGH
```

Result:

```text
HIGH
```

Crispy:

```text
understandable, have a nice day
```

If Crispy needs MEDIUM and a lower external request appears:

```text
Crispy: MEDIUM
Remote: LOW
```

Crispy:

```text
no
```

Authority:

```text
SAFETY / INVALID STATE
 ↓
HIGHER ORCON DEMAND
 ↓
 CRISPY DEMAND
 ↓
LOWER EXTERNAL DEMAND
```

A completely normal amount of diplomacy for residential ventilation.

---

# RF control & verification

There is no lovely modern REST API.

Naturally.

```text
Home Assistant
 ↓
 MQTT
 ↓
RF gateway
 ↓
 RAMSES RF
 ↓
virtual bound remote
 ↓
 Orcon HRC
```

Sending a packet is not the same thing as controlling a building.

```text
COMMAND
 ↓
OBSERVE
 ↓
VERIFY
 ↓
RECONCILE
```

Because `"I sent HIGH"` and `"the HRC is HIGH"` are two completely different engineering statements.

This distinction has justified an unreasonable amount of YAML.

---

# Sensor health & failure handling

If required telemetry becomes unreliable:

```text
Crispy:
"I no longer know what the fuck is happening."
```

Then:

```text
stop forcing decisions
 ↓
bypass AUTO
 ↓
stop renewing temporary overrides
 ↓
Orcon continues being an Orcon
```

The smart-home layer failing should make the house less smart.

Not more exciting.

---

# Full Send™

Control philosophy:

```text
APARTMENT TOO HOT?
 │
 YES
 │
COLDER AIR AVAILABLE?
 │
 YES
 │
 ▼
 SEND IT
```

Peer reviewed by nobody.

Use accordingly.

---

# Flight Recorder

At some point debugging residential ventilation apparently required a Flight Recorder.

It tracks control-relevant state such as temperature, cooling advantage, thermal momentum, heat memory, demand and actual fan state.

So when Crispy makes a questionable decision, I can determine exactly which questionable decisions led to it.

Residential ventilation.

Obviously.

---

# What is actually in here?

The core stack includes free cooling, staged demand, thermal momentum and recent heat gain, absolute-humidity/moisture handling, demand arbitration, external-demand preservation, RF verification, sensor-health failback, weather-assisted cooling, cooling-performance telemetry and the dashboard/Flight Recorder.

```text
Original requirement:
outside colder → use it

Implementation:
a completely unreasonable amount of observability around that sentence
```

# Compatibility & support

Developed and tested around:

```text
Orcon HRC 400 EcoMax
RAMSES-II RF
Elecram ESP32-C6 Sub-GHz gateway
ramses_cc
Home Assistant
MQTT
```

**Officially supported installations: 0.**

This is a personal project that happens to be public. If it works for you too: excellent. If it does not, the YAML is there to inspect and adapt.

Issues, findings and improvements are welcome, but this repository does not promise hardware compatibility, installation support or feature requests for other ventilation systems.

# Why “Crispy”?

Because the air must be crispy.

There was no further product discovery.

---

# Engineering philosophy

Crispy is an opportunistic supervisory controller.

The Orcon remains the authoritative base controller.

Crispy just has opinions.

> **Your hardware is fine.**
>
> **I just don’t agree with your management.**

---

# Disclaimer

Use at your own risk.

This project controls real ventilation hardware. Understand your system, validate your sensors, verify your RF commands, keep the manufacturer’s controller underneath it, and do not transmit to equipment you do not own or have authorization to control.

If your ventilation system develops Thermal Memory™ and starts judging your shower habits:

```text
that's between you and your house
```
