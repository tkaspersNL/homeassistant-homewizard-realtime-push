# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.2] - 2026-10-19

### Changed

- Passes the full HACS validation, including the brand check, so it can be added to the HACS default list.

## [1.0.1] - 2026-10-19

### Fixed

- The README shows the logo and the license badge in HACS. Before, HACS showed the logo's HTML as text and the license badge as a broken image.
- Fixed HACS author username.

## [1.0.0] - 2026-10-19

First stable release.

### Changed

- The README's known limitations say that only IPv4 addresses work, and explain more clearly that one update speed applies to all entities.

## [0.3.2] - 2026-10-18

### Changed

- The README shows measured storage figures instead of estimates, and explains setup and throttling more clearly.

### Fixed

- An answer from the meter that is not a measurement, for example while it boots, no longer stops the integration. Before, the next WebSocket reconnect could end all updates until Home Assistant was restarted.
- During an outage longer than 15 minutes, the *not pushing measurements* repair issue no longer appears next to *no measurements*. It said that values still arrive by polling, which was not true.

## [0.3.1] - 2026-10-18

### Added

- An info line in the log when a repair issue clears because the problem is over.

### Changed

- The safety example in the README also pauses and resumes when the integration reloads during an outage. Before, a reload in the middle of an outage kept it from doing either.

### Fixed

- A discovery with only an IPv6 address no longer replaces the meter's IPv4 address. This happened after a router restart, while the meter rejoined the network: the integration reloaded and kept retrying an address it can't use until the meter announced its IPv4 address again.

## [0.3.0] - 2026-10-17

### Added

- German translation.
- Diagnostics show how old the published values are since they arrived (on average and at most), how often a late telegram held a value back, and how long telegrams take to arrive.
- README explains what "realtime" means at each update speed, and that the full benefit needs a meter that sends a reading every second (DSMR 5).

### Changed

- The update speed dialog is shorter: it says what a faster speed gives and costs, and that the speed temporarily steps down (to 5s at most) and recovers automatically. The README has the details.
- 2s is labelled as the default instead of recommended.
- The update speed steps back up after 30s without problems instead of 60s, so a rare slowdown recovers sooner.
- The diagnostic entity is renamed to *Current update interval* (nl: *Actueel update-interval*).

## [0.2.0] - 2026-10-16

### Changed

- Values are published the moment a telegram arrives once the interval is up, instead of on a fixed timer. At 2s a value is now on average 1s old instead of 1.5s, and at most 2s instead of 3s (calculated).
- Clearer update speed labels: "reading" instead of the DSMR term "telegram", 2s marked as recommended and 5s as the rate of the official integration.

### Added

- README section on how old a value is at each update speed.

## [0.1.0] - 2026-10-16

### Added

- Power from a HomeWizard P1 meter, pushed over the local API v2 WebSocket.
- Per-phase power, voltage and current entities, disabled by default and only created for phases the meter reports.
- Update speed option: Realtime (every reading, ~1s on DSMR 5), Very fast (2s, default and recommended), Fast (3s) or Normal (5s). Entities always show the latest reading, never an average.
- Automatic backoff to a slower interval when Home Assistant lags, telegrams arrive late or the connection drops, and a step back after 60s without problems.
- Diagnostic entity *Effective update interval*, with the reason and transport as attributes.
- Reconnect with exponential backoff and HTTPS polling as a fallback while the WebSocket is down.
- Entities go unavailable when no data arrives for 10s or 3 intervals, whichever is longer.
- Setup through discovery or IP address, with a button press on the meter; reauthentication and reconfiguration flows.
- Diagnostic *Status* binary sensor (OK / Problem) that shows *Problem* when there is no fresh data (no measurements or a rejected token, not during Home Assistant restarts), so automations can pause control and send alerts.
- Repair issues that clear themselves: no measurements for 5 minutes, WebSocket not working for 15 minutes, or slower than the configured speed for 30 minutes.
- Warnings in the log when measurements stop and when a repair issue is raised; info lines when they are back, on WebSocket drops and on every throttle step.
- README section on safety, with an example automation that pauses a battery controller when the data stops.
- Redacted diagnostics.
- English and Dutch translations.
- Integration icon, with a separate version for dark mode (Home Assistant 2026.3 and later load it from the integration folder).
