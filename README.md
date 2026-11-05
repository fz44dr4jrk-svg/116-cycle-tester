# L16 Cycle Tester

Standalone touch dashboard for cycle-testing the **Actuonics L16-50-35-6-R** micro linear
actuator through a **PhidgetAdvancedServo 8-Motor (RCC0004_0)** connected by USB to a
Windows 10 Pro tablet. It replaces the MAX patch: everything runs from one local app,
fully offline, deployable from a USB key.

## Features

- **Servo voltage selector: 5.0 V / 6.0 V / 7.4 V** as a 3-position slider — set
  through the RCC0004 API (`RCServoVoltage`), applied globally to all servo
  outputs. Use **6.0 V** for the L16-...-6-R (see the voltage/extent note below).
- **Number of cycles slider: 10,000–400,000 in 5,000 steps**, with the **cycle
  counter + progress bar directly above it**.
- **Position min / max sliders** in **1 mm increments** over the 50 mm stroke
  (min 0–49 mm, max 1–50 mm).
- **Interval between cycles slider: 0–10 s in 0.5 s steps** (sticky detents).
- Every slider has a **numeric input box** for keyboard entry, synced both ways;
  sliders snap (stick) to their step — 1 mm for the position sliders, 5,000 for
  the cycle target, 0.5 s for the interval, one position for the voltage.
- **Horizontal extent gauge** below the sliders: numbered 0–50 mm scale, live
  marker, band showing the configured min/max range, plus a numeric mm readout.
- **START / STOP / RESET** buttons under the sliders. STOP (and automatic finish)
  returns the actuator to the min extent. One cycle = extend to max extent,
  retract to min extent; the interval is applied after each completed cycle.
- The whole control surface occupies the **left half** of the screen; the right
  half is reserved for future functions.
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
(or the Phidget Control Panel, or MAX) holds the controller: close it in Task
Manager, then press RECONNECT. The app now also refuses to start a second
instance. Note: every instance opens the same address `127.0.0.1:8080`, so if
several browser tabs are open you may be looking at the instance that does NOT
own the controller — close extra tabs and use the tab of the single running
instance (its header pill shows `Connected: ... serial ...`).

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

Note: in Task Manager a one-file EXE shows **two** processes with the same name —
a small bootloader parent and the unpacked child. That is normal, it is still a
single instance (one browser window).

### Closing the app

Closing the browser tab does **not** stop the program — the EXE keeps running and
keeps holding the RCC0004. To close it cleanly, press **QUIT APP** in the
dashboard header: it stops any running cycle, releases the controller, and exits
the EXE (both Task Manager entries disappear). If it ever stays alive, end both
`L16CycleTester` processes in Task Manager — that also releases the controller so
MAX / the Phidget Control Panel can use it again.

To run from source instead: `pip install -r requirements.txt`, then `run.bat`
(hardware) or `run_demo.bat` (simulated actuator).

## macOS build (prepared, currently paused)

The app is portable to macOS and the build supports it (CI matrix entry ready in
`.github/workflows/build-exe.yml`, commented; `build_exe.py` bundles
`libphidget22.dylib`). The macOS CI build is paused on request — re-enable the
commented matrix entry when needed. To build locally on a Mac:
`pip install -r requirements.txt && python3 build_exe.py`
→ `dist/L16CycleTester` (run it by double-clicking or `./dist/L16CycleTester`;
if Gatekeeper blocks it: right-click → Open, or
`xattr -d com.apple.quarantine dist/L16CycleTester`).

macOS prerequisites:

- `brew install libusb` — the bundled `libphidget22.dylib` links against libusb-1.0
  for the RCC0004.
- The FTDI VCP driver for the DSCUSB (from the vendor's site) so it appears as
  `/dev/cu.usbserial-…`; the app auto-detects it (or `--loadcell-port /dev/cu.usbserial-XXX`).
- The single-instance guard uses a lock file next to the binary on macOS/Linux
  (named mutex on Windows); settings/log/xlsx land next to the launched binary.

## Load cell (Applied Measurements DBBSMM-50KG + DSCUSB)

The right half of the dashboard is the load-cell panel: live value in kg
(0.000 kg), a scrolling bar chart (X = time, last 10 s), a **TARE / NET**
button (software tare: current reading becomes 0.000 kg), and the **control
mode** selector.

Hardware: DBBSMM-50KG into the DSCUSB 9-way D socket, DSCUSB micro-USB to the
tablet. Install the DSC Toolkit / FTDI drivers (SW-DSCUSBDRV) once so the
DSCUSB appears as a **virtual COM port (115200 8N1)**; calibrate the module to
kg in the Toolkit (auto or table calibration). The app talks the MantraASCII
protocol directly over the COM port — read command `!001:SYS?<CR>` returns the
main output (kg) as `123.456<CR>`; NAK is `?<CR>`. The port is auto-detected, or
pass `--loadcell-port COMx`.

Control modes (options 1–4):

- **1 — independent**: actuator cycles between position min/max as before; the
  load cell is only displayed/logged. Works with **only one device connected**:
  actuator without load cell cycles normally; load cell without actuator runs
  as a monitor-only logging session (one "cycle" per interval). Modes 2–4
  require the load cell and refuse to start with a clear message otherwise.
- **2 — failure mode**: each cycle's peak load is compared with the previous
  cycle's peak; if it drops by more than the **user-defined failure drop
  (10–50%, default 30%)**, the actuator stops, the cycle count and all data are
  written to `Failure.log`, and the run file is saved. (The load naturally falls
  to ~0 while retracting between cycles — that is not a failure; only the
  per-cycle peak-to-peak drop is evaluated.)
- **3 — constant load**: set a **load goal 0.1–6.0 kg**. Position **min** is the
  user-set start point; position **max is greyed out** — the app decides it
  (capped at the 50 mm stroke). The run **self-calibrates over the first cycles**:
  each cycle extends 1 mm further than the last, and after each completed cycle
  the measured cycle peak is compared with the goal — below goal → extent
  increases by 1 mm next cycle; above goal+5% → extent decreases by 0.5 mm;
  otherwise held. A mid-move limiter stops extension at 115% of the goal as a
  safety. The DSCUSB runs at its max 100 Hz with its dynamic filter disabled
  (`FFST=0`, `RATE=100`, `DP=6` set by the app at connect) so the readout tracks
  fast changes. All samples are logged. Same failure stop as mode 2.
  (Note: the standard DSCUSB hardware tops out at 100 Hz; the DSCUSB-FQ variant
  does 4800 Hz if you ever need more.)
- **4 — constant path**: fixed min/max extent; the load decline over time is
  logged. Same failure stop.

The load panel also shows a **PEAK** readout (far right) and the chart's time
**window** (last 10 s / 1 min / 10 min / 30 min) and **resolution** (max points)
are selectable; the server decimates the samples accordingly.

Logging: each run writes a **single** `cyclelog_<timestamp>.xlsx` (opens in
Excel; columns timestamp, elapsed_s, cycle, position_mm, load_kg, event) with
**one row per cycle carrying that cycle's peak load** — the DSCUSB hardware
`PEAK` (reset per cycle with `RSPT`), i.e. the true highest reading of each
cycle — plus run start/finish rows. Even a 200k–400k cycle run stays well
under Excel's 1,048,576-row limit in one file. `Failure.log` is written on
failure with the `failure_type`. All files land
next to `L16CycleTester.exe` — copy them off via USB.

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

At connect, the app sets the RCC0004 pulse window to **950–2368 µs**
(`PULSE_MIN_US` / `PULSE_MAX_US` in `actuator.py`, clamped to the device limits;
the applied values are written to `l16-cycle-tester.log`) and position range
0–180, and enables speed ramping. Measured on the reference unit: 950 µs =
0 mm (fully retracted), 2028 µs = 38 mm, so linear extrapolation puts 50 mm at
≈ 2368 µs (950 + 50 × (2028−950)/38). With the pulse window set to the
actuator's own stroke, the **whole** position range maps 1:1 to 0–50 mm (the
0–180 scale itself is irrelevant; only the pulse width matters). If your unit's
endpoints differ, measure the pulse widths at 0 mm and 50 mm with a ruler in the
Phidget Control Panel and update the two constants, then rebuild.

**Achieved extent depends on servo voltage under load.** Measured on the
reference setup at full commanded extent (slider 50 mm): 5.0 V → 40 mm,
6.0 V → 45 mm, **7.4 V → exactly 50 mm**. The app always commands the correct
pulse width; how far the rod physically travels at the top end depends on the
selected voltage and the load in your frame. Select **7.4 V** in the dashboard
for the full 50 mm stroke (note: the L16-...-6-R is a 6 V-rated motor — at 7.4 V
it runs faster/hotter, so respect the datasheet duty cycle during long cycle
tests), or stay at 6.0 V and cap the extent-max slider at the reachable value
(e.g. 45 mm).

The RCC0004 reports commanded position (no feedback input); the L16-R feedback
wire can be wired to a Phidget analog input later for true closed-loop verification.
