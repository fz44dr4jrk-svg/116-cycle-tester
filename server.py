import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from actuator import STROKE_MM, VOLTAGES, make_actuator
from engine import (
    CYCLES_MAX,
    CYCLES_MIN,
    CYCLES_STEP,
    DWELL_MAX_S,
    DWELL_MIN_S,
    FAIL_DROP_MAX_PCT,
    FAIL_DROP_MIN_PCT,
    GOAL_MAX_KG,
    GOAL_MIN_KG,
    GOAL_STEP_KG,
    MODES,
    CycleEngine,
)
from loadcell import make_loadcell
from recorder import Recorder

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_DIR = BASE_DIR / "static"
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "l16-cycle-tester.log"


def log(message):
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json",
}


def probe_phidget():
    try:
        from Phidget22.Devices.RCServo import RCServo  # noqa: F401

        return "Phidget22 library + native phidget22 library loaded OK"
    except Exception as exc:
        return f"Phidget22 library load FAILED: {type(exc).__name__}: {exc}"


def acquire_single_instance():
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, True, "Global\\L16CycleTesterSingleInstance")
        if kernel32.GetLastError() == 183:
            kernel32.CloseHandle(handle)
            return None
        return handle
    import fcntl

    fh = (APP_DIR / ".l16-cycle-tester.lock").open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


class App:
    def __init__(self, actuator, loadcell, demo):
        self.actuator = actuator
        self.loadcell = loadcell
        self.demo = demo
        self.engine = CycleEngine(actuator, loadcell)
        self.recorder = None
        self._reconnect_lock = threading.Lock()
        self.server = None
        self.load_settings()
        try:
            self.engine.apply_config()
        except Exception:
            pass

    def load_settings(self):
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            self.engine.set_config(data.get("config", {}))
        except Exception:
            pass

    def save_settings(self):
        try:
            SETTINGS_FILE.write_text(
                json.dumps({"config": dict(self.engine.config)}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def reconnect(self):
        if self.demo:
            raise RuntimeError("demo mode uses the simulated actuator")
        if self.actuator.connected:
            return True
        if not self._reconnect_lock.acquire(blocking=False):
            return False
        try:
            log("reconnect: trying to open Phidget controller...")
            try:
                self.actuator.open()
            except Exception as exc:
                log(f"reconnect failed: {exc}")
                raise
            log(f"reconnect OK: {self.actuator.name}")
            try:
                self.engine.apply_config()
            except Exception as exc:
                log(f"apply_config after reconnect failed: {exc}")
            return True
        finally:
            self._reconnect_lock.release()

    def state(self):
        snap = self.engine.snapshot()
        snap["demo"] = self.demo
        snap["device"] = {
            "name": self.actuator.name,
            "connected": self.actuator.connected,
            "error": self.actuator.error,
            "error_owner": getattr(self.actuator, "error_owner", None),
        }
        snap["limits"] = {
            "stroke_mm": STROKE_MM,
            "dwell": [DWELL_MIN_S, DWELL_MAX_S],
            "cycles": [CYCLES_MIN, CYCLES_MAX, CYCLES_STEP],
            "voltages": list(VOLTAGES),
            "modes": list(MODES),
            "goal_kg": [GOAL_MIN_KG, GOAL_MAX_KG, GOAL_STEP_KG],
            "fail_drop_pct": [FAIL_DROP_MIN_PCT, FAIL_DROP_MAX_PCT],
        }
        lc = self.loadcell
        snap["load"] = {
            "connected": lc.connected,
            "error": lc.error,
            "name": lc.name,
            "kg": round(lc.latest_kg(), 3) if lc.connected else None,
            "history": lc.history(300) if lc.connected else [],
        }
        snap["run_file"] = self.recorder.file_name if self.recorder is not None and self.recorder.active else None
        return snap


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path):
            if path in ("/", "/index.html"):
                path = "/index.html"
            rel = path.lstrip("/")
            if rel.startswith("static/"):
                rel = rel[len("static/") :]
            file = (STATIC_DIR / rel).resolve()
            if not str(file).startswith(str(STATIC_DIR.resolve())) or not file.is_file():
                self._json(404, {"ok": False, "error": "not found"})
                return
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(file.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/state":
                self._json(200, {"ok": True, "state": app.state()})
            else:
                self._static(path)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                payload = {}
            path = self.path.split("?")[0]
            try:
                if path == "/api/config":
                    app.engine.set_config(payload.get("config", payload))
                    app.save_settings()
                    self._json(200, {"ok": True, "state": app.state()})
                elif path == "/api/start":
                    if not app.actuator.connected:
                        raise RuntimeError(
                            "controller not connected - check USB and external power, or run with --demo"
                        )
                    if app.engine.status != "running":
                        app.recorder = Recorder(APP_DIR)
                        app.engine.recorder = app.recorder
                    ok = app.engine.start()
                    self._json(200, {"ok": ok, "state": app.state()})
                elif path == "/api/stop":
                    app.engine.stop()
                    self._json(200, {"ok": True, "state": app.state()})
                elif path == "/api/reset":
                    app.engine.reset()
                    self._json(200, {"ok": True, "state": app.state()})
                elif path == "/api/tare":
                    if not app.loadcell.connected:
                        raise RuntimeError("load cell not connected")
                    app.loadcell.tare()
                    self._json(200, {"ok": True, "state": app.state()})
                elif path == "/api/reconnect":
                    app.reconnect()
                    self._json(200, {"ok": True, "state": app.state()})
                elif path == "/api/quit":
                    self._json(200, {"ok": True, "state": app.state()})

                    def quit_app():
                        time.sleep(0.5)
                        log("quit requested: shutting down")
                        try:
                            app.engine.shutdown()
                        except Exception:
                            pass
                        try:
                            app.actuator.close()
                        except Exception:
                            pass
                        try:
                            app.loadcell.close()
                        except Exception:
                            pass
                        if app.recorder is not None:
                            try:
                                app.recorder.finish("app quit")
                            except Exception:
                                pass
                        try:
                            app.server.shutdown()
                        except Exception:
                            pass
                        os._exit(0)

                    threading.Thread(target=quit_app, daemon=True).start()
                else:
                    self._json(404, {"ok": False, "error": "not found"})
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="L16 cycle tester dashboard")
    parser.add_argument("--demo", action="store_true", help="simulated actuator, no hardware needed")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--channel", type=int, default=0, help="RCC0004 servo channel (0-7)")
    parser.add_argument("--loadcell-port", default=None, help="COM port of the DSCUSB (default: auto-detect)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args()

    lock = acquire_single_instance()
    if lock is None:
        msg = (
            "L16CycleTester is already running. A Phidget controller can only be opened "
            "by one program at a time - close the other L16CycleTester instance (or the "
            "Phidget Control Panel) and start again."
        )
        log(msg)
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, msg, "L16 Cycle Tester", 0x30)
        else:
            print(msg, file=sys.stderr)
        return

    actuator = make_actuator(args.demo, channel=args.channel)
    if not args.demo:
        log(f"probe: {probe_phidget()}")
    try:
        actuator.open()
        log(f"controller opened: {actuator.name} applied_pulse={getattr(actuator, 'applied_pulse', None)} applied_position={getattr(actuator, 'applied_position', None)}")
    except Exception as exc:
        actuator.connected = False
        actuator.close()
        if actuator.error is None:
            actuator.error = f"{type(exc).__name__}: {exc}"
        log(f"could not open Phidget controller: {actuator.error}")

    loadcell = make_loadcell(args.demo, port=args.loadcell_port, position_provider=lambda: actuator.position_mm)
    try:
        loadcell.open()
        log(f"load cell opened: {loadcell.name}")
    except Exception as exc:
        loadcell.connected = False
        loadcell.error = f"{type(exc).__name__}: {exc}"
        log(f"could not open load cell: {loadcell.error}")

    app = App(actuator, loadcell, args.demo)
    stop_watcher = threading.Event()

    def watch_controller():
        while not stop_watcher.is_set():
            if not args.demo and not actuator.connected:
                try:
                    app.reconnect()
                except Exception:
                    pass
            if not args.demo and not loadcell.connected:
                try:
                    loadcell.open()
                    log(f"load cell opened: {loadcell.name}")
                except Exception:
                    pass
            stop_watcher.wait(5.0)

    watcher = threading.Thread(target=watch_controller, daemon=True)
    watcher.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    server.daemon_threads = True
    app.server = server
    url = f"http://{args.host}:{args.port}/"
    log(
        f"dashboard at {url} (demo={args.demo}, device={actuator.name}, "
        f"connected={actuator.connected}, loadcell={loadcell.name}, "
        f"loadcell_connected={loadcell.connected}) - log file: {LOG_FILE}"
    )
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_watcher.set()
        app.engine.shutdown()
        actuator.close()
        loadcell.close()
        if app.recorder is not None:
            app.recorder.finish("app quit")
        server.server_close()


if __name__ == "__main__":
    main()
