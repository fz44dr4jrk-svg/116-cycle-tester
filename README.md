# L16 Cycle Tester

Standalone touch dashboard for cycle-testing the **Actuonics L16-50-35-6-R** micro linear
actuator through a **PhidgetAdvancedServo 8-Motor (RCC0004_0)** connected by USB to a
Windows 10 Pro tablet. It replaces the MAX patch: everything runs from one local app,
fully offline, deployable from a USB key.

## Features

- **Servo voltage selector: 5.0 V / 6.0 V / 7.4 V** — set through the RCC0004 API
  (`RCServoVoltage`), applied globally to all servo outputs. Use **6.0 V** for the
  L16-...-6-R. Only use 7.4 V with a 7.4 V-rated actuator.
- **Speed slider** (1–100 %) — mapped onto the controller's velocity limit.
- **Pause between cycles slider** (0–60 s).
- **Extent min / max sliders** in **1 mm increments** over the 50 mm stroke
  (min 0–49 mm, max 1–50 mm).
- **Number of cycles slider: 10,000–400,000 in 5,000 steps.**
- **Cycle counter** with progress bar and a **RESET COUNTER** button.
- **Extent indication**: numeric readout in mm plus a numbered 0–50 mm gauge with a
  live marker and a band showing the configured min/max range.
- **START / STOP** buttons. STOP (and automatic finish) returns the actuator to the
  min extent. One cycle = extend to max extent, retract to min extent; the pause is
  applied after each completed cycle.
- Settings are persisted (`settings.json` next to the EXE) and restored on restart.
- **Demo mode** (`--demo`): simulated actuator, no hardware needed — see `demo/`.

## Hardware setup

1. Feed the RCC0004_0 an **external 8–30 V DC supply** (barrel jack or terminal block,
   observe polarity). Its switching regulator then powers the servo rail at the
   voltage you select in the dashboard.
2. Plug the L16-R into **servo channel 0** of the RCC0004 (3-pin servo header:
   signal / +V / GND — the board supplies the regulated voltage).
3. Connect the RCC0004 to the tablet via USB.
4. If Windows does not see the device, install the Phidget22 drivers
   (phidgets.com → docs → OS - Windows).

Safety: match the selected voltage to the actuator rating (6.0 V for the
L16-50-35-6-R). Respect the L16 datasheet duty cycle during long runs (use the pause
between cycles); 400k cycles is a long test — mount the actuator in your frame so a
failure cannot damage the test parts.

## Install on the tablet from a USB key

1. Get `L16CycleTester.exe`:
   - download the `L16CycleTester-windows` artifact from the repo's **Actions** tab
     (the `build-exe` workflow builds it on every push), or
   - build it yourself on any Windows PC with Python 3.11+: `build.bat`
     (runs `pip install -r requirements.txt` + PyInstaller, output `dist\L16CycleTester.exe`).
2. Copy the single EXE to a USB key, plug it into the tablet, double-click it
   (SmartScreen: *More info → Run anyway*). The app starts a local server on
   `http://127.0.0.1:8080` and opens the dashboard in the default browser.
   No installation, no internet needed.

   **Yes, the browser showing `127.0.0.1:8080` is correct.** That is the loopback
   address — the app serves the dashboard only to the tablet itself. Nothing is
   sent over the network; the tablet does not need internet access.

### "Controller not connected" on the tablet

The dashboard shows this when the RCC0004 is not detected over USB. The app
retries automatically every 5 s and there is a **RECONNECT CONTROLLER** button in
the header. **Only one instance of L16CycleTester may run at a time** — a Phidget
controller can be opened by a single program only. If the error says
`open failed because device is in use by ... pid ...`, another copy of the app
(or the Phidget Control Panel) holds the controller: close it in Task Manager,
then press RECONNECT. The app now also refuses to start a second instance.

Then check, in this order:

1. USB cable between the RCC0004 and the tablet (try another cable/port).
2. The RCC0004 needs its **external 8–30 V DC supply** connected (barrel jack or
   terminal block, correct polarity) — without it the board can misbehave.
3. Windows driver: on Windows 10 the RCC0004 normally uses WinUSB automatically.
   If not, install the Phidget22 drivers (phidgets.com → docs → OS - Windows) or
   use Zadig to bind WinUSB to the Phidget device.
4. Read the exact error: the app writes `l16-cycle-tester.log` next to the EXE
   (same folder the EXE was started from) and shows the error text in the
   dashboard. Copy that log file back via USB if you need help interpreting it.

To run from source instead: `pip install -r requirements.txt`, then `run.bat`
(hardware) or `run_demo.bat` (simulated actuator).

## Demo

`python server.py --demo` (or `run_demo.bat`) runs the full dashboard against a
simulated actuator. Captured demo output is in [`demo/`](demo/):

- `01_idle.png` — dashboard before starting
- `02_running.png`, `03_running_later.png` — running, counter and extent advancing
- `04_stopped.png` — stopped, actuator homed to min extent
- `dashboard-demo.gif` — animated capture

Regenerate with `python tools/capture_demo.py` (needs `playwright` + `pillow`).

## Project layout

```
server.py       local HTTP server + JSON API + static file serving (stdlib only)
engine.py       cycle engine (background thread): extend/retract/dwell/count
actuator.py     actuator abstraction: PhidgetActuator (RCC0004) + SimulatedActuator
static/         dashboard UI (index.html, style.css, app.js) - no external assets
build.bat       PyInstaller one-file EXE build for Windows
run.bat / run_demo.bat
.github/workflows/build-exe.yml   CI build of the Windows EXE
tools/capture_demo.py             regenerates demo/ screenshots
```

### API

- `GET /api/state` — full state snapshot (status, count, config, position, device, limits)
- `POST /api/config` — `{config: {speed, dwell, min_mm, max_mm, target, voltage}}` (only while stopped)
- `POST /api/start` · `POST /api/stop` · `POST /api/reset`

## Calibration notes

Position mapping assumes the controller's full pulse range equals the full 50 mm
stroke (`STROKE_MM` in `actuator.py`/`engine.py`, position range is read from the
device at connect). If your endpoints do not line up, adjust `MinPulseWidth` /
`MaxPulseWidth` on the channel (Phidget Control Panel) or the constants in the code.
The RCC0004 reports commanded position (no feedback input); the L16-R feedback wire
can be wired to a Phidget analog input later for true closed-loop verification.
