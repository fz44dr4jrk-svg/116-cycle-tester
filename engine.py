import threading
import time

STROKE_MM = 50.0
SPEED_MIN, SPEED_MAX = 1.0, 100.0
DWELL_MIN_S, DWELL_MAX_S = 0.0, 600.0
CYCLES_MIN, CYCLES_MAX, CYCLES_STEP = 10000, 400000, 5000
VOLTAGES = (5.0, 6.0, 7.4)

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_DONE = "done"


def cycles_to_index(value):
    return int(round((value - CYCLES_MIN) / CYCLES_STEP))


def index_to_cycles(index):
    return CYCLES_MIN + int(index) * CYCLES_STEP


class CycleEngine:
    def __init__(self, actuator):
        self.actuator = actuator
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread = None
        self.config = {
            "speed": 50.0,
            "dwell": 2.0,
            "min_mm": 0.0,
            "max_mm": 50.0,
            "target": CYCLES_MIN,
            "voltage": 6.0,
        }
        self.count = 0
        self.status = STATUS_IDLE
        self.error = None

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "count": self.count,
                "error": self.error,
                "config": dict(self.config),
                "position_mm": self.actuator.position_mm,
                "voltage": self.actuator.get_voltage(),
            }

    def set_config(self, cfg):
        with self._lock:
            if self.status == STATUS_RUNNING:
                raise RuntimeError("stop the run before changing settings")
            merged = dict(self.config)
            for key in merged:
                if key in cfg:
                    merged[key] = cfg[key]
            merged["speed"] = float(merged["speed"])
            merged["dwell"] = float(merged["dwell"])
            merged["min_mm"] = float(merged["min_mm"])
            merged["max_mm"] = float(merged["max_mm"])
            merged["target"] = int(merged["target"])
            merged["voltage"] = float(merged["voltage"])
            if not SPEED_MIN <= merged["speed"] <= SPEED_MAX:
                raise ValueError(f"speed must be {SPEED_MIN}-{SPEED_MAX}")
            if not DWELL_MIN_S <= merged["dwell"] <= DWELL_MAX_S:
                raise ValueError(f"pause must be {DWELL_MIN_S}-{DWELL_MAX_S} s")
            if not 0.0 <= merged["min_mm"] <= STROKE_MM - 1.0:
                raise ValueError(f"min extent must be 0-{STROKE_MM - 1.0} mm")
            if not 1.0 <= merged["max_mm"] <= STROKE_MM:
                raise ValueError(f"max extent must be 1-{STROKE_MM} mm")
            if merged["min_mm"] >= merged["max_mm"]:
                raise ValueError("min extent must be below max extent")
            if (
                merged["target"] < CYCLES_MIN
                or merged["target"] > CYCLES_MAX
                or (merged["target"] - CYCLES_MIN) % CYCLES_STEP != 0
            ):
                raise ValueError(
                    f"target must be {CYCLES_MIN}-{CYCLES_MAX} in {CYCLES_STEP} steps"
                )
            if merged["voltage"] not in VOLTAGES:
                raise ValueError(f"voltage must be one of {VOLTAGES}")
            self.config = merged
        if self.actuator.connected:
            self.actuator.set_voltage(merged["voltage"])
            self.actuator.set_speed_pct(merged["speed"])

    def apply_config(self):
        cfg = self._cfg()
        if self.actuator.connected:
            self.actuator.set_voltage(cfg["voltage"])
            self.actuator.set_speed_pct(cfg["speed"])

    def start(self):
        with self._lock:
            if self.status == STATUS_RUNNING:
                return False
            if self.count >= self.config["target"]:
                self.count = 0
            self.error = None
            self.status = STATUS_RUNNING
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop_evt.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10)
        return True

    def reset(self):
        with self._lock:
            self.count = 0
        return True

    def shutdown(self):
        self._stop_evt.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10)

    def _cfg(self):
        with self._lock:
            return dict(self.config)

    def _sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_evt.is_set():
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return not self._stop_evt.is_set()

    def _run(self):
        done = False
        try:
            cfg = self._cfg()
            self.actuator.set_voltage(cfg["voltage"])
            self.actuator.set_speed_pct(cfg["speed"])
            self.actuator.move_to_mm(cfg["min_mm"], timeout_s=120, cancel_evt=self._stop_evt)
            while not self._stop_evt.is_set():
                if not self.actuator.move_to_mm(cfg["max_mm"], timeout_s=120, cancel_evt=self._stop_evt):
                    if self._stop_evt.is_set():
                        break
                    raise RuntimeError("actuator did not reach max extent in time")
                if self._stop_evt.is_set():
                    break
                if not self.actuator.move_to_mm(cfg["min_mm"], timeout_s=120, cancel_evt=self._stop_evt):
                    if self._stop_evt.is_set():
                        break
                    raise RuntimeError("actuator did not reach min extent in time")
                with self._lock:
                    self.count += 1
                    current = self.count
                    target = self.config["target"]
                if current >= target:
                    done = True
                    break
                if not self._sleep(cfg["dwell"]):
                    break
        except Exception as exc:
            with self._lock:
                self.error = str(exc)
        finally:
            with self._lock:
                if self.status == STATUS_RUNNING:
                    self.status = STATUS_DONE if done else STATUS_STOPPED
            try:
                cfg = self._cfg()
                self.actuator.move_to_mm(cfg["min_mm"], timeout_s=60)
            except Exception:
                pass
