import threading
import time
from collections import deque

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
        self._extent = None
        self._goal_reached = False
        self._flat_count = 0
        self._failure_type = None
        self._cycle_maxpos = 0.0
        self._prev_maxpos = None
        self._peak_hist = deque(maxlen=5)

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
            self._extent = None
            self._goal_reached = False
            self._flat_count = 0
            self._failure_type = None
            self._cycle_maxpos = 0.0
            self._prev_maxpos = None
            self._peak_hist.clear()
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
            pos = self.actuator.position_mm
            if pos > self._cycle_maxpos:
                self._cycle_maxpos = pos
            time.sleep(0.05)

    def _check_cycle_peak(self):
        cfg = self._cfg()
        peak = self._cycle_peak
        prev = self._prev_peak
        if cfg["mode"] == "2b" and peak >= cfg["load_goal_kg"]:
            self._goal_reached = True
        if cfg["mode"] != "1":
            check_failure = not (cfg["mode"] == "2b" and not self._goal_reached)
            check_failure = check_failure and len(self._peak_hist) >= 3
            baseline = None
            if self._peak_hist:
                recent = list(self._peak_hist)[-3:]
                baseline = sum(recent) / len(recent)
            drop = cfg["fail_drop_pct"] / 100.0
            if (
                check_failure
                and baseline is not None
                and baseline > PEAK_EPS_KG
                and peak < (1.0 - drop) * baseline
                and not (cfg["mode"] == "2b" and peak >= cfg["load_goal_kg"] * 0.8)
            ):
                actuator_moved = (self._cycle_maxpos - cfg["min_mm"]) > 1.0
                if actuator_moved:
                    self._failure_type = "part_break"
                    self._fail_reason = (
                        f"cycle peak load dropped {cfg['fail_drop_pct']:.0f}% below the expected "
                        f"peak (avg of last cycles) while the actuator kept moving: {peak:.3f} kg "
                        f"vs ~{baseline:.3f} kg (part break)"
                    )
                else:
                    self._failure_type = "actuator_stuck"
                    self._fail_reason = (
                        f"load dropped {peak:.3f} kg vs ~{baseline:.3f} kg and the actuator stalled "
                        f"(max pos {self._cycle_maxpos:.1f} mm vs {self._prev_maxpos:.1f} mm) "
                        f"- actuator may be dead"
                    )
                self._fail_evt.set()
                self._fail_prev_peak = baseline
            if cfg["mode"] in ("2a", "3") and prev is not None and prev > 0.2:
                if abs(peak - prev) <= 0.01 * prev:
                    self._flat_count += 1
                else:
                    self._flat_count = 0
                if self._flat_count >= 5 and not self._fail_evt.is_set():
                    self._fail_reason = (
                        f"cycle load unchanged for {self._flat_count} consecutive cycles "
                        f"({peak:.3f} kg) - actuator may be dead/stuck"
                    )
                    self._failure_type = "actuator_stuck"
                    self._fail_evt.set()
                    self._fail_prev_peak = prev

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
            last_pos = self.actuator.position_mm
            last_change = time.monotonic()
            while not self._stop_evt.is_set() and not self._fail_evt.is_set():
                load = self._load()
                if load is not None:
                    if load > self._cycle_peak:
                        self._cycle_peak = load
                    if load >= target_load:
                        break
                pos_now = self.actuator.position_mm
                if abs(pos_now - last_pos) > 0.05:
                    last_pos = pos_now
                    last_change = time.monotonic()
                if time.monotonic() - last_change > 2.0:
                    break
                if pos_now >= target - 0.05:
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
                self._cycle_maxpos = 0.0
                if self.loadcell is not None:
                    try:
                        self.loadcell.reset_peak()
                    except Exception:
                        pass
                if can_move:
                    if cfg["mode"] == "2b":
                        if self._extent is None:
                            self._extent = cfg["min_mm"] + 1.0
                        limiter = cfg["load_goal_kg"] * 1.15
                        self._extend_until_load(limiter, cfg, cap_mm=self._extent)
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
                if self.loadcell is not None:
                    try:
                        mp = self.loadcell.module_peak_kg()
                        if mp is not None and mp > self._cycle_peak:
                            self._cycle_peak = mp
                    except Exception:
                        pass
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
                if can_move and self.loadcell is not None:
                    try:
                        mp = self.loadcell.module_peak_kg()
                        if mp is not None and mp > self._cycle_peak:
                            self._cycle_peak = mp
                    except Exception:
                        pass
                peak = self._cycle_peak
                self._peak_hist.append(peak)
                self._prev_peak = peak
                self._prev_maxpos = self._cycle_maxpos
                if self.recorder is not None:
                    self.recorder.event(
                        f"cycle {current} peak",
                        current,
                        round(self._cycle_maxpos, 3) if can_move else "",
                        round(peak, 4),
                    )
                if current >= target:
                    done = True
                    break
                if cfg["mode"] == "2b" and can_move:
                    goal = cfg["load_goal_kg"]
                    peak = self._cycle_peak
                    if peak < goal:
                        if (
                            (self._cycle_maxpos - cfg["min_mm"]) < 0.5
                            and peak < goal * 0.5
                            and not self._fail_evt.is_set()
                        ):
                            self._fail_reason = (
                                f"actuator cannot extend further (max pos {self._cycle_maxpos:.1f} mm) "
                                f"but cycle peak {peak:.3f} kg below goal {goal} - actuator may be dead "
                                f"or the part is blocking"
                            )
                            self._failure_type = "actuator_stuck"
                            self._fail_evt.set()
                            note = "actuator stalled below goal"
                        else:
                            delta = min(1.0, max(0.1, (goal - peak) * 3.0))
                            self._extent = min(STROKE_MM, self._extent + delta)
                            note = f"cycle peak {peak:.3f} kg below goal {goal}: extent +{delta:.1f} -> {self._extent:.1f} mm"
                        if self._extent >= STROKE_MM and not self._fail_evt.is_set() and (self._cycle_maxpos - cfg["min_mm"]) >= 0.5:
                            self._fail_reason = (
                                f"actuator reached full stroke ({STROKE_MM} mm) but cycle peak "
                                f"{peak:.3f} kg still below goal {goal} - no resistance: part "
                                f"likely failed"
                            )
                            self._failure_type = "part_break"
                            self._fail_evt.set()
                    elif peak > goal * 1.05:
                        delta = min(0.5, max(0.1, (peak - goal) * 3.0))
                        self._extent = max(cfg["min_mm"] + 0.5, self._extent - delta)
                        note = f"cycle peak {peak:.3f} kg above goal: extent -{delta:.1f} -> {self._extent:.1f} mm"
                    else:
                        note = f"cycle peak {peak:.3f} kg at goal {goal}; extent held at {self._extent:.1f} mm"
                    if self.recorder is not None:
                        self.recorder.event(note, current, round(self._cycle_maxpos, 3), round(peak, 4))
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
                        "failure_type": self._failure_type,
                        "run_file": self.recorder.file_name,
                    })
                self.recorder.finish(
                    "failed" if failed else ("done" if done else "stopped"),
                    final_count,
                    round(self.actuator.position_mm, 3),
                    round(self._load() or 0.0, 4),
                    run_peak=self._run_peak,
                )
            try:
                if can_move:
                    cfg = self._cfg()
                    self.actuator.move_to_mm(cfg["min_mm"], timeout_s=60)
            except Exception:
                pass
