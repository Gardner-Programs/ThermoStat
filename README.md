# ThermoStat

An ESP32-based smart thermostat built with MicroPython. Controls a Daikin mini-split AC and a backup Pelonis oil heater via IR, reads room temperature from a DHT22 sensor, and hosts a single-page web UI served directly from the ESP32.

## Features

**Thermostat logic**
- Own heat/cool decision engine — avoids the Daikin's unreliable built-in auto mode
- Dead-band control, season guard, dynamic fan speed, heat boost, 20-min short-cycle lockout
- Heartbeat resend every 20 min to keep Daikin in sync; boot resend on restart

**Schedule**
- Weekly schedule: away windows (protect against extremes) and home windows (target temp)
- Optional pre-conditioning: starts heating N minutes before you arrive home
- Adaptive learning: measures actual heating rate daily and auto-adjusts lead time

**Outdoor temperature**
- Polls Open-Meteo (free, no key) every 30 min for outdoor °F
- Used by season guard, heat boost scaling, backup heater thresholds, and history logging

**Web interface** (served from ESP32 on port 80)
- Dashboard: live sensor readings, target temp ±1°F (1-hour override), AC state, away/override cards
- AC Control: full manual IR command — mode, fan speed, setpoint, Pelonis on/off
- Schedule: Calendar Gantt view + list editor; add/edit/delete windows with pre-con timing
- History: canvas chart of indoor/outdoor/humidity over 6h–7d; heating/cooling background bands; system log viewer

**IR protocols**
- Daikin ARC433: stateful 19-byte frame — power, mode (heat/cool/fan/dry), fan speed, setpoint
- Pelonis PSHO06MR6ASB: NEC protocol, power toggle only (repeat=1 mandatory)

**Deployment**
- WebREPL on port 8266: wireless OTA upload, no USB needed after initial flash
- `remote.py`: deploy all files and reboot in one command
- `fetch_logs.py`: archive system.log and history.csv locally
- Watchdog timer (90s): reboots if thermostat thread stalls

## Hardware

| Component | Detail |
|---|---|
| Microcontroller | ESP32 running MicroPython |
| Temperature/humidity | DHT22 on GPIO 26 |
| IR transmitter | GPIO 23 → NPN transistor → 22Ω → 2× 940nm IR LEDs in parallel |
| Primary HVAC | Daikin mini-split (ARC433 protocol) |
| Backup heat | Pelonis oil radiator (NEC protocol) |

## Setup

1. Flash MicroPython to your ESP32
2. Copy `secrets.py.example` → `secrets.py` and fill in your WiFi credentials and lat/lon
3. Edit `remote.py` and `fetch_logs.py` to set your device's IP address
4. Upload all device files:
   ```bash
   python3 remote.py deploy
   ```

## Project structure

```
Device files (MicroPython):
  main.py          — entry point; thermostat + web server threads
  boot.py          — WiFi connect, WebREPL start
  ac_mgr.py        — smart thermostat brain and IR dispatcher
  ir_daikin.py     — Daikin ARC433 frame builder and IR transmitter
  ir_pelonis.py    — Pelonis NEC IR transmitter
  sched_mgr.py     — schedule storage and evaluation
  learn_mgr.py     — adaptive pre-conditioning optimizer
  outdoor_mgr.py   — Open-Meteo outdoor temp fetcher with 30-min cache
  data_mgr.py      — 5-min history logger (history.csv)
  log_mgr.py       — rolling system.log capture
  state_mgr.py     — target temp persistence (state.json)
  time_mgr.py      — NTP sync + US Eastern DST handling
  wifi_mgr.py      — WiFi connect/reconnect
  web_ui.py        — dynamic JS var block injected into web page
  _web_p1.html     — web app HTML + CSS (static, served in 2 KB chunks)
  _web_p2.html     — web app JavaScript (static, served in 2 KB chunks)

PC-side tools:
  remote.py        — OTA deploy via WebREPL; reboot
  fetch_logs.py    — pull system.log and history.csv to local archive
  ir_angle_test.py — IR LED pointing calibration tool
  webrepl_cli.py   — MicroPython WebREPL client (upstream project)

Development:
  .dev/            — capture and decode IR signals, OTA helpers
  docs/            — full project documentation and changelog
```

## API

The ESP32 exposes a REST API on port 80:

| Endpoint | Method | Description |
|---|---|---|
| `/api/data` | GET | Live sensor + state JSON |
| `/api/schedule` | GET/POST | Read or save schedule |
| `/api/ac` | POST | Send manual IR command (sets 1-hour override) |
| `/api/command?cmd=set&val=<F>` | GET | Set target temp + 1-hour override |
| `/api/command?cmd=override_clear` | GET | Resume schedule immediately |
| `/api/history` | GET | Stream history.csv |
| `/api/log` | GET | Stream system.log |
| `/api/log/clear` | POST | Delete system.log |
| `/api/reboot` | POST | Reboot ESP32 |
