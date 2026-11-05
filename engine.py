import threading
import time

STROKE_MM = 50.0
DWELL_MIN_S, DWELL_MAX_S = 0.0, 10.0
CYCLES_MIN, CYCLES_MAX, CYCLES_STEP = 10000, 400000, 5000
VOLTAGES = (5.0, 6.0, 7.4)
MODES = ("1", "2a", "2b", "3")
GOAL_MIN_KG, GOAL_MAX_KG, GOAL_STEP_KG = 0.1, 6.0, 0.2
FAIL_DROP_MIN_PCT, FAIL_DROP_MAX_PCT = 10.0, 50.0
PEAK_EPS_KG = 0.1

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def cycles_to_index(value):
    return int(round((value - CYCLES_MIN) / CYCLES_STEP))


def index_to_cycles(index):
    return CYCLES_MIN + int(index) * CYCLES_STEP


class CycleEngine:
    def __init__(self, actuator, loadcell=None):
        self.actuator = actuator
        self.loadcell = loadcell
        self.recorder = None
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._fail_evt = threading.Event()
        self._fail_reason = None
        self._thread = None
        self._monitor_thread = None
        self.config = {
            "dwell": 2.0,
            "min_mm": 0.0,
            "max_mm": 50.0,
            "target": CYCLES_MIN,
            "voltage": 6.0,
            "mode": "1",
            "load_goal_kg": 4.0,
            "fail_drop_pct": 30.0,
        }
        self.count = 0
        self.status = STATUS_IDLE
        self.error = None
        self._cycle_peak = 0.0
        self._prev_peak = None
        self._fail_prev_peak = None
        self._run_peak = 0.0

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "count": self.count,
                "error": self.error,
                "fail_reason": self._fail_reason,
                "config": dict(self.config),
                "position_mm": self.actuator.position_mm,
                "voltage": self.actuator.get_voltage(),
                "run_peak_kg": self._run_peak,
                "prev_cycle_peak_kg": self._prev_peak,
            }

    def set_config(self, cfg):
        with self._lock:
            if self.status == STATUS_RUNNING:
                raise RuntimeError("stop the run before changing settings")
            merged = dict(self.config)
            for key in merged:
                if key in cfg:
                    merged[key] = cfg[key]
            merged["dwell"] = float(merged["dwell"])
            merged["min_mm"] = float(merged["min_mm"])
            merged["max_mm"] = float(merged["max_mm"])
            merged["target"] = int(merged["target"])
            merged["voltage"] = float(merged["voltage"])
            merged["mode"] = str(merged["mode"])
            merged["fail_drop_pct"] = float(merged["fail_drop_pct"])
            merged["load_goal_kg"] = round(
                GOAL_MIN_KG
                + round((float(merged["load_goal_kg"]) - GOAL_MIN_KG) / GOAL_STEP_KG) * GOAL_STEP_KG,
                3,
            )
            if not DWELL_MIN_S <= merged["dwell"] <= DWELL_MAX_S:
                raise ValueError(f"interval must be {DWELL_MIN_S}-{DWELL_MAX_S} s")
            if not 0.0 <= merged["min_mm"] <= STROKE_MM - 1.0:
                raise ValueError(f"position min must be 0-{STROKE_MM - 1.0} mm")
            if not 1.0 <= merged["max_mm"] <= STROKE_MM:
                raise ValueError(f"position max must be 1-{STROKE_MM} mm")
            if merged["min_mm"] >= merged["max_mm"]:
                raise ValueError("position min must be below position max")
            if (
                merged["target"] < CYCLES_MIN
                or merged["target"] > CYCLES_MAX
                or (merged["target"] - CYCLES_MIN) % CYCLES_STEP != 0
            ):
                raise ValueError(f"target must be {CYCLES_MIN}-{CYCLES_MAX} in {CYCLES_STEP} steps")
            if merged["voltage"] not in VOLTAGES:
                raise ValueError(f"voltage must be one of {VOLTAGES}")
            if merged["mode"] not in MODES:
                raise ValueError(f"mode must be one of {MODES}")
            if not GOAL_MIN_KG <= merged["load_goal_kg"] <= GOAL_MAX_KG:
                raise ValueError(f"load goal must be {GOAL_MIN_KG}-{GOAL_MAX_KG} kg")
            if not FAIL_DROP_MIN_PCT <= merged["fail_drop_pct"] <= FAIL_DROP_MAX_PCT:
                raise ValueError(
                    f"failure drop must be {FAIL_DROP_MIN_PCT}-{FAIL_DROP_MAX_PCT} %"
                )
            self.config = merged
        if self.actuator.connected:
            self.actuator.set_voltage(merged["voltage"])

    def apply_config(self):
        cfg = self._cfg()
        if self.actuator.connected:
            self.actuator.set_voltage(cfg["voltage"])

    def start(self):
        with self._lock:
            if self.status == STATUS_RUNNING:
                return False
            mode = self.config["mode"]
            can_move = self.actuator is not None and getattr(self.actuator, "connected", False)
            has_load = self.loadcell is not None and self.loadcell.connected
            if mode != "1" and not has_load:
                raise RuntimeError(
                    f"mode {mode} needs the load cell - connect the DSCUSB "
                    "(or run with --demo)"
                )
            if not can_move and not has_load:
                raise RuntimeError("neither the RCC0004 controller nor the load cell is connected")
            if self.count >= self.config["target"]:
                self.count = 0
            self.error = None
            self._fail_reason = None
            self._cycle_peak = 0.0
            self._prev_peak = None
            self._fail_prev_peak = None
            self._run_peak = 0.0
            self._fail_evt.clear()
            if self.loadcell is not None:
                try:
                    self.loadcell.reset_peak()
                except Exception:
                    pass
            self.status = STATUS_RUNNING
            self._stop_evt.clear()
            if self.recorder is not None:
                self.recorder.begin(self.config["mode"], dict(self.config))
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
            self._monitor_thread.start()
            return True

    def stop(self):
        self._stop_evt.set()
        for thread in (self._thread, self._monitor_thread):
            if thread is not None:
                thread.join(timeout=10)
        return True

    def reset(self):
        with self._lock:
            self.count = 0
        return True

    def shutdown(self):
        self._stop_evt.set()
        for thread in (self._thread, self._monitor_thread):
            if thread is not None:
                thread.join(timeout=10)

    def _cfg(self):
        with self._lock:
            return dict(self.config)

    def _load(self):
        if self.loadcell is None:
            return None
        try:
            return self.loadcell.latest_kg()
        except Exception:
            return None

    def _monitor(self):
        while not self._stop_evt.is_set():
            load = self._load()
            now = time.monotonic()
            if load is not None:
                if load > self._cycle_peak:
                    self._cycle_peak = load
                if load > self._run_peak:
                    self._run_peak = load
                if self.recorder is not None:
                    with self._lock:
                        current = self.count
                    self.recorder.sample(now, current, self.actuator.position_mm, load)
            time.sleep(0.05)

    def _check_cycle_peak(self):
        cfg = self._cfg()
        if cfg["mode"] == "1":
            return
        drop = cfg["fail_drop_pct"] / 100.0
        peak = self._cycle_peak
        prev = self._prev_peak
        if prev is not None and prev > PEAK_EPS_KG and peak < (1.0 - drop) * prev:
            self._fail_reason = (
                f"cycle peak load dropped {cfg['fail_drop_pct']:.0f}% below the previous "
                f"cycle peak: {peak:.3f} kg vs {prev:.3f} kg"
            )
            self._fail_evt.set()
            self._fail_prev_peak = prev
        self._prev_peak = peak

    def _sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_evt.is_set() or self._fail_evt.is_set():
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return not self._stop_evt.is_set() and not self._fail_evt.is_set()

    def _extend_until_load(self, target_load, cfg, cap_mm=None):
        cap = cap_mm if cap_mm is not None else cfg["max_mm"]
        pos = max(cfg["min_mm"], self.actuator.position_mm)
        while (
            pos < cap
            and not self._stop_evt.is_set()
            and not self._fail_evt.is_set()
        ):
            load = self._load()
            if load is not None:
                if load > self._cycle_peak:
                    self._cycle_peak = load
                if load >= target_load:
                    break
                remaining = target_load - load
                step = 1.0 if remaining > 0.5 else (0.5 if remaining > 0.2 else 0.1)
            else:
                step = 1.0
            target = min(cap, pos + step)
            self.actuator.set_target_mm(target)
            while not self._stop_evt.is_set() and not self._fail_evt.is_set():
                load = self._load()
                if load is not None:
                    if load > self._cycle_peak:
                        self._cycle_peak = load
                    if load >= target_load:
                        self.actuator.halt()
                        return self.actuator.position_mm
                if self.actuator.position_mm >= target - 0.05:
                    break
                time.sleep(0.01)
            pos = self.actuator.position_mm
        return pos

    def _run(self):
        done = False
        try:
            cfg = self._cfg()
            can_move = self.actuator is not None and getattr(self.actuator, "connected", False)
            if can_move:
                self.actuator.set_voltage(cfg["voltage"])
                self.actuator.move_to_mm(cfg["min_mm"], timeout_s=120, cancel_evt=self._stop_evt)
            while (
                not self._stop_evt.is_set()
                and not self._fail_evt.is_set()
            ):
                self._cycle_peak = 0.0
                if can_move:
                    if cfg["mode"] == "2b":
                        self._extend_until_load(cfg["load_goal_kg"], cfg, cap_mm=STROKE_MM)
                    else:
                        if not self.actuator.move_to_mm(cfg["max_mm"], timeout_s=120, cancel_evt=self._stop_evt):
                            if self._stop_evt.is_set() or self._fail_evt.is_set():
                                break
                            raise RuntimeError("actuator did not reach max extent in time")
                        load = self._load()
                        if load is not None and load > self._cycle_peak:
                            self._cycle_peak = load
                if self._stop_evt.is_set() or self._fail_evt.is_set():
                    break
                self._check_cycle_peak()
                if self._fail_evt.is_set():
                    break
                if can_move:
                    if not self.actuator.move_to_mm(cfg["min_mm"], timeout_s=120, cancel_evt=self._stop_evt):
                        if self._stop_evt.is_set() or self._fail_evt.is_set():
                            break
                        raise RuntimeError("actuator did not reach min extent in time")
                with self._lock:
                    self.count += 1
                    current = self.count
                    target = self.config["target"]
                if self.recorder is not None:
                    self.recorder.event(
                        f"cycle {current} complete",
                        current,
                        round(self.actuator.position_mm, 3) if can_move else "",
                        round(self._load() or 0.0, 4) if self._load() is not None else "",
                    )
                if current >= target:
                    done = True
                    break
                dwell = cfg["dwell"] if can_move else max(cfg["dwell"], 1.0)
                if not self._sleep(dwell):
                    break
        except Exception as exc:
            with self._lock:
                self.error = str(exc)
        finally:
            failed = self._fail_evt.is_set()
            with self._lock:
                if self.status == STATUS_RUNNING:
                    if failed:
                        self.status = STATUS_FAILED
                    else:
                        self.status = STATUS_DONE if done else STATUS_STOPPED
            self._stop_evt.set()
            if self._monitor_thread is not None:
                self._monitor_thread.join(timeout=5)
            if self.recorder is not None:
                with self._lock:
                    final_count = self.count
                if failed:
                    self.recorder.failure({
                        "mode": cfg.get("mode"),
                        "cycles_completed": final_count,
                        "target_cycles": cfg.get("target"),
                        "last_cycle_peak_kg": round(self._cycle_peak, 4),
                        "previous_cycle_peak_kg": round(self._fail_prev_peak or 0.0, 4),
                        "load_at_failure_kg": round(self._load() or 0.0, 4),
                        "position_mm": round(self.actuator.position_mm, 3),
                        "reason": self._fail_reason,
                        "run_file": self.recorder.file_name,
                    })
                self.recorder.finish(
                    "failed" if failed else ("done" if done else "stopped"),
                    final_count,
                    round(self.actuator.position_mm, 3),
                    round(self._load() or 0.0, 4),
                )
            try:
                if can_move:
                    cfg = self._cfg()
                    self.actuator.move_to_mm(cfg["min_mm"], timeout_s=60)
            except Exception:
                pass
