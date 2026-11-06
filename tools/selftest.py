import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actuator import ActuatorBase, SimulatedActuator
from engine import CycleEngine
from loadcell import LoadCellBase
from recorder import Recorder


class FakeLoadCell(LoadCellBase):
    name = "fake"
    connected = True

    def __init__(self, fn):
        self._fn = fn
        self.eng = None

    def open(self):
        pass

    def latest_kg(self):
        return self._fn(self.eng)

    def history(self, n=300):
        return []

    def tare(self):
        return True

    def reset_peak(self):
        pass

    def module_peak_kg(self):
        return self._fn(self.eng)


class StuckActuator(ActuatorBase):
    name = "stuck"
    connected = True

    def open(self):
        pass

    def set_voltage(self, v):
        pass

    def get_voltage(self):
        return 6.0

    def move_to_mm(self, mm, timeout_s=60.0, cancel_evt=None):
        return True

    def set_target_mm(self, mm):
        pass

    def halt(self):
        pass

    @property
    def position_mm(self):
        return 0.0


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def run_engine(mode, load_fn, actuator=None, goal=1.1, drop=30.0, max_s=12.0, pause_at=None):
    act = actuator or SimulatedActuator()
    act.open()
    lc = FakeLoadCell(load_fn)
    eng = CycleEngine(act, lc)
    lc.eng = eng
    eng.recorder = Recorder(tempfile.mkdtemp())
    eng.set_config({
        "mode": mode, "min_mm": 2.0, "max_mm": 14.0, "dwell": 0.05,
        "target": 100000, "fail_drop_pct": drop, "voltage": 6.0, "load_goal_kg": goal,
    })
    eng.start()
    t0 = time.monotonic()
    pause_ok = True
    while time.monotonic() - t0 < max_s:
        time.sleep(0.1)
        if pause_at is not None and eng.count >= pause_at and eng.status == "running":
            eng.pause()
            time.sleep(0.3)
            c1 = eng.count
            time.sleep(0.3)
            c2 = eng.count
            pause_ok = eng.status == "paused" and c1 == c2
            eng.resume()
            pause_at = None
        if eng.status not in ("running", "calibrating", "paused"):
            break
    final_status, final_count = eng.status, eng.count
    eng.shutdown()
    act.close()
    eng.status, eng.count = final_status, final_count
    return eng, pause_ok


def main():
    eng, _ = run_engine("1", lambda e: 0.5)
    check("mode1 cycles without failure", eng.status == "running" and eng.count >= 8,
          f"status={eng.status} count={eng.count}")

    noise = {"i": 0}
    def noisy(e):
        noise["i"] += 1
        return 4.0 + (0.05 if noise["i"] % 2 else -0.05)
    eng, _ = run_engine("2a", noisy, max_s=15)
    check("mode2a no false stop on noise", eng.status == "running" and eng.count >= 10,
          f"status={eng.status} count={eng.count}")

    eng, _ = run_engine("2a", lambda e: 4.0 if e.count < 7 else 1.0, max_s=15)
    check("mode2a part break detected", eng.status == "failed" and eng._failure_type == "part_break",
          f"status={eng.status} type={eng._failure_type} count={eng.count}")

    eng, _ = run_engine("2a", lambda e: 2.0, actuator=StuckActuator(), max_s=15)
    check("mode2a actuator_stuck on stall", eng.status == "failed" and eng._failure_type == "actuator_stuck",
          f"status={eng.status} type={eng._failure_type}")

    act5 = SimulatedActuator()
    eng, _ = run_engine("2b", lambda e: 0.09 * act5.position_mm, actuator=act5, max_s=25)
    check("mode2b calibrates and counts", eng._calibrated and eng.count >= 5,
          f"calibrated={eng._calibrated} count={eng.count} extent={eng._extent:.1f}")

    act6 = SimulatedActuator()
    eng, _ = run_engine("2b", lambda e: 1.5 + 0.09 * act6.position_mm, actuator=act6, max_s=25)
    check("mode2b pre-loaded still calibrates with movement", eng._calibrated and eng.count >= 3,
          f"calibrated={eng._calibrated} count={eng.count}")

    eng, pause_ok = run_engine("1", lambda e: 0.5, max_s=10, pause_at=3)
    check("pause holds count, resume continues", pause_ok and eng.count >= 5,
          f"pause_ok={pause_ok} count={eng.count} status={eng.status}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
