# HomeWizard Realtime Push

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/dark_icon.svg">
  <img src="assets/brand/icon.svg" alt="" width="96" align="right">
</picture>

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Release](https://img.shields.io/github/v/release/tkaspersnl/homeassistant-homewizard-realtime-push?include_prereleases)](https://github.com/tkaspersnl/homeassistant-homewizard-realtime-push/releases)
[![Validate](https://github.com/tkaspersnl/homeassistant-homewizard-realtime-push/actions/workflows/validate.yml/badge.svg)](https://github.com/tkaspersnl/homeassistant-homewizard-realtime-push/actions/workflows/validate.yml)
[![Tests](https://github.com/tkaspersnl/homeassistant-homewizard-realtime-push/actions/workflows/tests.yml/badge.svg)](https://github.com/tkaspersnl/homeassistant-homewizard-realtime-push/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Fast power readings from a HomeWizard P1 meter in Home Assistant, pushed by the meter over its local API v2 WebSocket instead of polled. You choose an update rate between every telegram (about 1s) and 5s, and the integration slows down on its own when Home Assistant is busy.

It is built for things that need to react quickly to the grid connection, such as a home battery controller or a load balancer for an EV charger. It runs **next to** the official HomeWizard integration and does not replace it.

> **Unofficial.** This project is not affiliated with or endorsed by HomeWizard.

## Why

- **No polling.** The meter sends each new telegram itself. There are no HTTP requests every second, no timeouts piling up, and less load on the meter's Wi-Fi.
- **Fresh data.** A value is about as old as the telegram (1s at most on a DSMR 5 meter), not "up to one poll interval".
- **Current API.** API v2 is HomeWizard's current local API: it needs a token and is encrypted (HTTPS/WSS). The old v1 API is open plain HTTP.
- **Resilient.** It reconnects with backoff, falls back to HTTPS polling while the WebSocket is down, throttles its update rate when Home Assistant is busy, and turns its entities unavailable when data stops. An automation never acts on a frozen value.

The official integration polls every 5s. Calling `homeassistant.update_entity` from an automation does not make it faster, because refresh requests are debounced with a 10s cooldown.

## Designed to run next to the official integration

| | This integration | Official HomeWizard integration |
|---|---|---|
| Power, per-phase power, voltage and current | Fast (1–5s, pushed) | Every 5s (polled) |
| Energy totals, tariffs, gas, water | No | Yes |
| Energy dashboard, long-term history | Not needed | Yes |

Keep the official integration for history, statistics and the Energy dashboard. Use the fast entities from this integration only where speed matters.

The fast entities live on their own device, named after the meter with "Realtime" added (for example *P1 Meter Realtime*). Entity IDs therefore do not clash with the official ones, for example `sensor.p1_meter_realtime_power` next to `sensor.p1_meter_power`.

If the device is in an area, Home Assistant may put the area name in front of entity IDs, for example `binary_sensor.hallway_p1_meter_realtime_status`. The examples below use IDs without an area; check yours under *Settings → Devices & services → Entities*.

## Requirements

- A HomeWizard **P1 meter** on **firmware 6 or newer** (API v2). Other HomeWizard devices are not supported.
- To get the most out of it, a smart meter that sends a reading every second (DSMR 5). Older meters send one every 10s. The integration works with them, but a value can be up to 10s old at every speed, so it gains little over the official integration.
- Physical access to the meter: setup needs one press of its button.
- Home Assistant 2026.10 or newer.

## Install

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tkaspersnl&repository=homeassistant-homewizard-realtime-push&category=integration)

1. Select the button above, or in HACS add `https://github.com/tkaspersnl/homeassistant-homewizard-realtime-push` as a custom repository of type *Integration*.
2. Download **HomeWizard Realtime Push**.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/homewizard_realtime_push` into the `custom_components` folder of your Home Assistant configuration.
2. Restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=homewizard_realtime_push)

The meter is usually discovered automatically. Otherwise add **HomeWizard Realtime Push** under *Settings → Devices & services* and enter the meter's IP address.

1. Confirm the meter.
2. Press the button on the P1 meter once, then select **Submit**. Home Assistant waits up to 30s for the press.
3. The integration gets its own access token from the meter. It shows up in the meter's user list as `local/ha-realtime-push#…`, separate from the official integration's token.

With the default settings there is nothing else to configure. If you run Home Assistant on an SD card or plan to enable the phase entities, read [Storage and load estimates](#storage-and-load-estimates) first.

If the token is later removed, Home Assistant asks you to authenticate again (one more button press). If the meter gets a new IP address, it is picked up through discovery, or you can change it with **Reconfigure**.

### Remove

1. *Settings → Devices & services → HomeWizard Realtime Push → ⋮ → Delete*.
2. Optionally remove the `ha-realtime-push` user from the meter in the HomeWizard app.
3. If you installed through HACS, remove the repository there as well.

## Update speed options

Change this under *Settings → Devices & services → HomeWizard Realtime Push → Configure*. The integration reloads automatically.

| Option | Interval | Notes |
|---|---|---|
| Realtime | every reading (~1s on DSMR 5) | fastest, highest load |
| Very fast | 2s | **default** |
| Fast | 3s | |
| Normal | 5s | same rate as the official integration, but pushed |

### What "realtime" means

The connection is realtime at every speed: the meter pushes each reading over the WebSocket the moment it measures it. The update speed only sets how often Home Assistant takes a copy, and that copy is always the **latest** reading, never an average. Each value is published as soon as its reading arrives, so it is fresher than polling at the same rate, even at 5s (calculated; see [How old is the value?](#how-old-is-the-value)).

The *Realtime* option is the one that passes on every reading.

### How old is the value?

The integration publishes the first telegram that arrives once the interval is up, so a value is as fresh as the telegram itself when it reaches Home Assistant. It then stays until the next publish. When an automation reads the value, it is this old (calculated for a DSMR 5 meter that sends a telegram every second, not measured):

| Option | On average | At most |
|---|---|---|
| Realtime | 0.5s | 1s |
| Very fast (2s) | 1s | 2s |
| Fast (3s) | 1.5s | 3s |
| Normal (5s) | 2.5s | 5s |
| Official integration (polls every 5s) | ≈ 3s | ≈ 6s |

The meter's own processing and the Wi-Fi delay come on top of this, the same for every option. A meter that sends a telegram every 10s gets each telegram published as it arrives, at every speed, so the value can be up to 10s old.

### Adaptive speed (throttling)

The integration measures how late its own timer runs (event loop lag). It steps one level slower (1 → 2 → 3 → 5s) when, over 10s:

- the median timer lag is above 250ms (Home Assistant is busy), or
- telegrams arrive late at least twice (more than twice the usual gap), or
- the WebSocket connection dropped.

After 30s without any of these it steps one level back, and again after every further 30s, back to the option you chose. It never goes faster than that. The *Current update interval* entity shows the interval in use and why.

## Storage and load estimates

> These figures are **estimates** based on a typical SQLite row size. They will be replaced with measured values before version 1.0.0.

A Home Assistant state row takes roughly 200 bytes including indexes. Power, voltage and current change on almost every update, so nearly every update becomes a row. Home Assistant keeps 10 days of history by default and then deletes the oldest rows, so the database levels off at about the size below instead of growing forever.

| Speed | Rows/day per entity | Power only (default) | Power + all 9 phase entities |
|---|---|---|---|
| Realtime (1s) | 86,400 | ≈ 170MB | ≈ 1.7GB |
| Very fast (2s) | 43,200 | ≈ 85MB | ≈ 850MB |
| Fast (3s) | 28,800 | ≈ 60MB | ≈ 600MB |
| Normal (5s) | 17,280 | ≈ 35MB | ≈ 350MB |

- Sizes are for 10 days of history. A single-phase connection has 3 phase entities instead of 9, so 4 entities in total.
- Long-term statistics (5-minute and hourly) are the same size at every speed.
- **Load:** a few state changes per second is light work for Home Assistant. The real cost comes from what listens to the entities: automations and template sensors that trigger on every change, and open dashboards that show them. Trigger only on what you need, and use the adaptive speed above as a safety net.

### Optional: stop saving the fast entities' history

With the default settings (Power only, every 2s) the extra storage is small, and most installs don't need to do anything. Consider leaving the fast entities out of the recorder if:

- Home Assistant runs on an SD card, for example in a Raspberry Pi (constant writes wear it out faster),
- you enable the phase entities, or
- you use Realtime.

Excluded entities add nothing to the database. What changes:

- Automations and dashboards keep working: they use the live value, not the saved history.
- The official integration still keeps history and statistics for the same values every 5s.
- The fast entities themselves get no history graph and no long-term statistics, so the second-by-second detail is not saved.

An integration can't switch this off for its own entities; only you can, in `configuration.yaml`. Add this and restart Home Assistant:

```yaml
# configuration.yaml
recorder:
  exclude:
    entity_globs:
      - sensor.*p1_meter_realtime_*
```

If you already have a `recorder:` section, add the pattern to its `exclude`. The leading `*` also matches IDs with an area name in front. Adjust the pattern if you renamed the device. It also excludes the *Current update interval* entity.

## Entities

| Entity | Unit | Enabled by default |
|---|---|---|
| Power | W | yes |
| Power phase 1/2/3 | W | no |
| Voltage phase 1/2/3 | V | no |
| Current phase 1/2/3 | A | no |
| Current update interval (diagnostic) | s | yes |
| Status (diagnostic) | OK / Problem | yes |

- **Power** uses the same sign as the official integration: positive when you import from the grid, negative when you export.
- Phase entities are only created for phases the meter reports. A single-phase connection gets phase 1 only.
- **Current update interval** changes only when the interval or connection changes, so it costs almost no storage. Its attributes:
  - `configured_interval`: the option you chose, in seconds
  - `reason`: `configured`, `loop_lag`, `late_frames` or `reconnect`
  - `transport`: `websocket`, `polling` (fallback) or `connecting`
- **Status** shows *Problem* (state `on`) when there is no fresh data, and *OK* (state `off`) otherwise. Its `reason` attribute says why: `no_data` (nothing arrived for 10s or more) or `token_rejected` (the meter no longer accepts the token). It stays available itself, so automations can rely on it. See [Safety](#safety-when-the-data-stops).

There are no energy totals, tariffs or gas entities on purpose: the official integration covers those, and they don't need fast updates.

## Safety: when the data stops

If something controls a battery or a load from these entities, decide what it should do when the data stops. Otherwise it keeps acting on the last value it saw.

The integration helps in three ways:

- **Unavailable entities.** When no value arrives for 10s or more, the fast entities become *unavailable*. They never keep showing an old value.
- **The Status sensor** switches to *Problem* at the same moment and back to *OK* when fresh data returns. Use it to pause control and to alert yourself.
- **Repairs.** If a problem lasts, a message appears under *Settings → System → Repairs*. It disappears by itself when the problem is over.

  | Message | Appears after | Meaning |
  |---|---|---|
  | No measurements | 5 minutes | Nothing arrives from the meter. |
  | Not pushing measurements | 15 minutes | The WebSocket doesn't work; values come in by HTTPS polling instead. |
  | Updates slower than configured | 30 minutes | The integration has slowed down for a long time (see [Adaptive speed](#adaptive-speed-throttling)). |

  When the meter rejects the token, Home Assistant asks you to authenticate again instead.

Integrations can't send phone notifications themselves. Use an automation for that. This example pauses a battery controller and notifies you when the data stops for 30s, and resumes when it is back. Replace the entity and notify names with your own:

```yaml
alias: Pause battery control when grid data stops
mode: restart
triggers:
  - trigger: state
    entity_id: binary_sensor.p1_meter_realtime_status
    to: "on"
    for: "00:00:30"
    id: stopped
  - trigger: state
    entity_id: binary_sensor.p1_meter_realtime_status
    from: "on"
    to: "off"
    id: back
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: stopped
        sequence:
          - action: switch.turn_off
            target:
              entity_id: switch.home_battery_control
          - action: notify.mobile_app_your_phone
            data:
              title: Battery control paused
              message: >-
                No grid data from the P1 meter
                ({{ state_attr('binary_sensor.p1_meter_realtime_status', 'reason') }}).
      - conditions:
          - condition: trigger
            id: back
        sequence:
          - action: switch.turn_on
            target:
              entity_id: switch.home_battery_control
          - action: notify.mobile_app_your_phone
            data:
              title: Battery control resumed
              message: Grid data from the P1 meter is back.
```

A Home Assistant restart doesn't count as a problem either: while Home Assistant shuts down, the entities keep their last state instead of reporting a gap. Polling and slowing down don't set Status to *Problem*: the data is still fresh then, and pausing for that would do more harm than good.

## How data reaches Home Assistant

1. The integration opens `wss://<meter>/api/ws`, authenticates with its token and subscribes to measurements. TLS is verified against HomeWizard's own certificate authority.
2. Every telegram the meter sends is kept as the *latest* value. The first telegram that arrives once the chosen interval is up is published to the entities straight away. If that telegram is late, the latest one is published 0.5s after the interval instead. In Realtime mode each telegram is published as it arrives.
3. If the WebSocket fails, it reconnects with backoff (1, 2, 4… up to 30s, with jitter). Meanwhile it polls `https://<meter>/api/measurement` at the current interval, so values keep coming.
4. When no value arrives for 10s (or 3 intervals, or 3 telegram gaps on meters that send less often, whichever is longest), the entities go unavailable. They come back with the next value.

## Troubleshooting and diagnostics

- **Entities are unavailable / Status shows Problem.** The meter sent nothing for a while. Check that it is online in the HomeWizard app. The integration reconnects by itself. The log has a warning when values stop and an info line when they are back.
- **A message under Repairs.** See [Safety](#safety-when-the-data-stops) for what each one means. They clear themselves when the problem is over.
- **`transport` stays on `polling`.** The WebSocket could not be used and the integration fell back to HTTPS polling. Values still arrive, but less efficiently. Please open an issue with diagnostics and debug logs.
- **`reason` is `loop_lag`.** Home Assistant was too busy to keep up. Look for heavy automations or integrations, or choose a slower option.
- **Setup says no button press was detected.** Press the button once, then select Submit within 30s.
- **Setup says the firmware is too old.** The meter only speaks API v1. Update it through the HomeWizard app, or keep using the official integration.

**Diagnostics:** *Settings → Devices & services → HomeWizard Realtime Push → ⋮ → Download diagnostics*. Besides the settings and the latest measurement, it shows the live event loop lag, the time between telegrams, and how old the published values are since they arrived (on average and at most, since the last restart). The telegram delay compares the arrival time with the meter's clock, so it includes any offset of that clock. The token, serial number and IP address are redacted.

**Debug logs:**

```yaml
logger:
  logs:
    custom_components.homewizard_realtime_push: debug
```

## Known limitations

- HomeWizard documents the WebSocket handshake but not every detail of the measurement messages. If the meter sends something unexpected, the integration falls back to polling and logs it at debug level.
- One P1 meter per config entry. Other HomeWizard devices (kWh meters, sockets, batteries) are not supported.
- The update speed applies to all entities of a meter.

## FAQ

**Can I use this instead of the official integration?**
You can, but you lose energy totals, tariffs, gas and the Energy dashboard. It is meant to run next to it.

**Does it put more load on the meter?**
Less than fast polling: there is one long-lived connection instead of a request per second. It uses its own token, alongside the official integration's.

**Why is the default 2s and not realtime?**
2s is fast enough for most control loops and halves the state writes. The value is on average 1s old instead of 0.5s (see [How old is the value?](#how-old-is-the-value)). Choose Realtime if your use case benefits from every telegram.

**Why do I see a second device for my meter?**
The fast entities have their own device, so their entity IDs don't collide with the official ones and you can exclude them from the recorder with one pattern.

## Contributing

Issues and pull requests are welcome.

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements_test.txt
```

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest --cov
```

- Branches follow Gitflow: `main` holds releases, work happens on `develop` and `feature/*` branches.
- Every change comes with tests, and the checks above must pass.
- User-facing changes get a line under *Unreleased* in [CHANGELOG.md](CHANGELOG.md).

## Disclaimer

This is an unofficial, community project. It is not affiliated with, endorsed by or supported by HomeWizard. "HomeWizard" is a trademark of its owner. Use at your own risk; do not rely on it for safety-critical control.

Licensed under the [MIT License](LICENSE).
