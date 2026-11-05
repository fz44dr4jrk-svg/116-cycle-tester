import re
import threading
import time

STROKE_MM = 50.0
VOLTAGES = (5.0, 6.0, 7.4)
SIM_MAX_MM_PER_S = 60.0


class ActuatorBase:
    name = "abstract"
    connected = False
    error = None

    def open(self):
        raise NotImplementedError

    def close(self):
        pass

    def set_voltage(self, volts):
        raise NotImplementedError

    def get_voltage(self):
        raise NotImplementedError

    def set_speed_pct(self, pct):
        raise NotImplementedError

    def move_to_mm(self, mm, timeout_s=60.0):
        raise NotImplementedError

    @property
    def position_mm(self):
        raise NotImplementedError


class SimulatedActuator(ActuatorBase):
    name = "Simulated L16-50-35-6-R (demo mode)"

    def __init__(self):
        self._lock = threading.Lock()
        self._pos = 0.0
        self._target = 0.0
        self._speed_pct = 50.0
        self._voltage = 6.0
        self._stop = threading.Event()
        self._thread = None
        self.connected = True

    def open(self):
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()
        self.connected = True

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _tick(self):
        last = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            dt = now - last
            last = now
            with self._lock:
                rate = self._speed_pct / 100.0 * SIM_MAX_MM_PER_S
                diff = self._target - self._pos
                step = rate * dt
                if abs(diff) <= step:
                    self._pos = self._target
                elif diff > 0:
                    self._pos += step
                else:
                    self._pos -= step
            time.sleep(0.02)

    def set_voltage(self, volts):
        with self._lock:
            self._voltage = float(volts)

    def get_voltage(self):
        with self._lock:
            return self._voltage

    def set_speed_pct(self, pct):
        with self._lock:
            self._speed_pct = float(pct)

    def move_to_mm(self, mm, timeout_s=60.0):
        with self._lock:
            self._target = max(0.0, min(STROKE_MM, float(mm)))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if abs(self._pos - self._target) < 0.05:
                    return True
            time.sleep(0.02)
        return False

    @property
    def position_mm(self):
        with self._lock:
            return self._pos


class PhidgetActuator(ActuatorBase):
    def __init__(self, channel=0, attach_timeout_ms=8000):
        self.channel = channel
        self.attach_timeout_ms = attach_timeout_ms
        self.error_owner = None
        self._dev = None
        self._pos_min = 0.0
        self._pos_max = 250.0
        self._vel_min = 1.0
        self._vel_max = 250.0
        self._voltage = 6.0
        self._reached = threading.Event()
        self._lock = threading.Lock()

    def open(self):
        self.error = None
        dev = None
        try:
            try:
                from Phidget22.Devices.AdvancedServo import AdvancedServo as ServoCls
            except ImportError:
                from Phidget22.Devices.RCServo import RCServo as ServoCls
            dev = ServoCls()
            dev.setChannel(self.channel)
            dev.openWaitForAttachment(self.attach_timeout_ms)
            self._dev = dev
            dev.setEngaged(True)
            try:
                self._pos_min = float(dev.getMinPosition())
                self._pos_max = float(dev.getMaxPosition())
            except Exception:
                pass
            try:
                self._vel_min = float(dev.getMinVelocityLimit())
                self._vel_max = float(dev.getMaxVelocityLimit())
            except Exception:
                pass
            try:
                dev.setOnTargetPositionReachedHandler(self._on_reached)
            except Exception:
                pass
            self.connected = True
            self.name = f"PhidgetAdvancedServo 8-Motor RCC0004_0, channel {self.channel}"
            try:
                self.name += f", serial {dev.getSerialNumber()}"
            except Exception:
                pass
        except Exception as exc:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
            self._dev = None
            self.connected = False
            self.error_owner = None
            detail = f"{type(exc).__name__}: {exc}"
            match = re.search(r'in use by:.*path: "([^"]+)"\s*-\s*pid:\s*(\d+)', str(exc), re.S)
            if match:
                self.error_owner = f"{match.group(1)} (pid {match.group(2)})"
                detail += (
                    f" | HELD BY: {self.error_owner} - close that program, then press"
                    " RECONNECT here (the app retries automatically every 5 s)."
                )
            self.error = (
                f"RCC0004 not usable on USB channel {self.channel} ({detail}). "
                "Otherwise check: USB cable, external 8-30V supply on the board "
                "(correct polarity), Windows driver. Details in l16-cycle-tester.log."
            )
            raise RuntimeError(self.error) from exc

    def _on_reached(self, e):
        self._reached.set()

    @property
    def _units_per_mm(self):
        return (self._pos_max - self._pos_min) / STROKE_MM

    def _mm_to_position(self, mm):
        mm = max(0.0, min(STROKE_MM, float(mm)))
        return self._pos_min + mm * self._units_per_mm

    def set_voltage(self, volts):
        volts = float(volts)
        if volts not in VOLTAGES:
            raise ValueError(f"voltage must be one of {VOLTAGES}")
        applied = False
        try:
            from Phidget22.RCServoVoltage import RCServoVoltage

            mapping = {}
            for member in RCServoVoltage:
                if "5V" in member.name:
                    mapping[5.0] = member
                elif "6V" in member.name:
                    mapping[6.0] = member
                elif "7_4V" in member.name:
                    mapping[7.4] = member
            self._dev.setVoltage(mapping[volts])
            applied = True
        except Exception:
            pass
        if not applied:
            try:
                self._dev.setVoltage(volts)
                applied = True
            except Exception:
                pass
        if not applied:
            raise RuntimeError("controller does not expose API-selectable voltage")
        self._voltage = volts

    def get_voltage(self):
        return self._voltage

    def set_speed_pct(self, pct):
        pct = max(1.0, min(100.0, float(pct)))
        velocity = self._vel_min + (pct / 100.0) * (self._vel_max - self._vel_min)
        self._dev.setVelocityLimit(velocity)

    def move_to_mm(self, mm, timeout_s=60.0):
        with self._lock:
            target = self._mm_to_position(mm)
        self._reached.clear()
        self._dev.setTargetPosition(target)
        return self._reached.wait(timeout_s)

    @property
    def position_mm(self):
        try:
            pos = float(self._dev.getPosition())
        except Exception:
            return 0.0
        mm = (pos - self._pos_min) / self._units_per_mm
        return max(0.0, min(STROKE_MM, mm))

    def close(self):
        try:
            if self._dev is not None:
                self._dev.close()
        except Exception:
            pass
        self.connected = False


def make_actuator(demo, channel=0):
    if demo:
        return SimulatedActuator()
    return PhidgetActuator(channel=channel)
