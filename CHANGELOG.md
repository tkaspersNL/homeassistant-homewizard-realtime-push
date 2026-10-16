# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Power from a HomeWizard P1 meter, pushed over the local API v2 WebSocket.
- Per-phase power, voltage and current entities, disabled by default and only created for phases the meter reports.
- Update speed option: Realtime (every telegram), Very fast (2s, default), Fast (3s) or Normal (5s). Entities always show the latest reading, never an average.
- Automatic backoff to a slower interval when Home Assistant lags, telegrams arrive late or the connection drops, and a step back after 60s without problems.
- Diagnostic entity *Effective update interval*, with the reason and transport as attributes.
- Reconnect with exponential backoff and HTTPS polling as a fallback while the WebSocket is down.
- Entities go unavailable when no data arrives for 10s or 3 intervals, whichever is longer.
- Setup through discovery or IP address, with a button press on the meter; reauthentication and reconfiguration flows.
- Diagnostic *Problem* binary sensor that turns on when there is no fresh data (no measurements or a rejected token), so automations can pause control and send alerts.
- Repair issues that clear themselves: no measurements for 5 minutes, WebSocket not working for 15 minutes, or slower than the configured speed for 30 minutes.
- Warnings in the log when measurements stop and when a repair issue is raised; info lines when they are back, on WebSocket drops and on every throttle step.
- README section on safety, with an example automation that pauses a battery controller when the data stops.
- Redacted diagnostics.
- English and Dutch translations.
- Integration icon, with a separate version for dark mode (Home Assistant 2026.3 and later load it from the integration folder).
