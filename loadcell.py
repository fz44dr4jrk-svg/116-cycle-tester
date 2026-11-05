import random
import threading
import time
from collections import deque

READ_CMD = b"!001:SYS?\r"
BAUD = 115200
POLL_HZ = 100.0
HIST_MAX = 180000
WINDOW_MAX_S = 1800.0
POINTS_MAX = 1200


class LoadCellBase:
    name = "abstract"
    connected = False
    error = None

    def open(self):
        raise NotImplementedError

    def close(self):
        pass

    def latest_kg(self):
        raise NotImplementedError

    def history(self, max_points=300):
        raise NotImplementedError

    def history_window(self, window_s=10.0, max_points=300):
        raise NotImplementedError

    def reset_peak(self):
        pass

    def module_peak_kg(self):
        return None

    def tare(self):
        raise NotImplementedError


class SimulatedLoadCell(LoadCellBase):
    name = "Simulated load cell (demo mode)"

    def __init__(self, position_provider=None):
        self._provider = position_provider
        self._lock = threading.Lock()
        self._gross = 0.0
        self._tare = 0.0
        self._alltime_peak = 0.0
        self._hist = deque(maxlen=HIST_MAX)
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
        while not self._stop.is_set():
            pos = self._provider() if self._provider else 0.0
            gross = max(0.0, 0.09 * pos) + random.uniform(-0.01, 0.01)
            with self._lock:
                self._gross = gross
                net = gross - self._tare
                if net > self._alltime_peak:
                    self._alltime_peak = net
                self._hist.append((time.monotonic(), net))
            time.sleep(1.0 / POLL_HZ)

    def latest_kg(self):
        with self._lock:
            return self._gross - self._tare

    def history(self, max_points=300):
        return self.history_window(1800.0, max_points)

    def history_window(self, window_s=10.0, max_points=300):
        with self._lock:
            now = time.monotonic()
            items = [(t, v) for t, v in self._hist if t >= now - min(window_s, WINDOW_MAX_S)]
        return _decimate(items, max_points)

    def tare(self):
        with self._lock:
            self._tare = self._gross
        return True

    def reset_peak(self):
        with self._lock:
            self._alltime_peak = self._gross - self._tare

    def module_peak_kg(self):
        with self._lock:
            return self._alltime_peak


class DscUsbLoadCell(LoadCellBase):
    def __init__(self, port=None):
        self.port = port
        self._ser = None
        self._lock = threading.Lock()
        self._gross = 0.0
        self._tare = 0.0
        self._mod_peak = None
        self._hist = deque(maxlen=HIST_MAX)
        self._stop = threading.Event()
        self._thread = None

    def _cmd(self, payload):
        self._ser.reset_input_buffer()
        self._ser.write(payload)
        line = self._ser.read_until(b"\r", 64).strip()
        if not line or line == b"?":
            return None
        try:
            return float(line)
        except ValueError:
            return None

    def _probe(self, device):
        import serial

        s = serial.Serial(device, BAUD, timeout=0.3)
        try:
            s.reset_input_buffer()
            s.write(READ_CMD)
            line = s.read_until(b"\r", 64).strip()
            if line and line != b"?":
                float(line)
                return True
        finally:
            s.close()
        return False

    def _autodetect(self):
        from serial.tools import list_ports

        for p in list_ports.comports():
            try:
                if self._probe(p.device):
                    return p.device
            except Exception:
                continue
        return None

    def open(self):
        import serial

        port = self.port or self._autodetect()
        if not port:
            raise RuntimeError(
                "no DSCUSB found on any COM port - install the DSC Toolkit / FTDI drivers "
                "(SW-DSCUSBDRV) so the DSCUSB appears as a virtual COM port, or pass --loadcell-port COMx"
            )
        self._ser = serial.Serial(port, BAUD, timeout=0.05)
        for payload in (b"!001:DP=6\r", b"!001:FFST=0\r", b"!001:RATE=100\r"):
            try:
                self._cmd(payload)
            except Exception:
                pass
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        self.connected = True
        self.name = f"DSCUSB (DBBSMM-50KG) on {port}"

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self.connected = False

    def _reader(self):
        tick = 0
        while not self._stop.is_set():
            try:
                gross = self._cmd(READ_CMD)
                if gross is not None:
                    tick += 1
                    if tick % 5 == 0:
                        peak = self._cmd(b"!001:PEAK?\r")
                        if peak is not None:
                            with self._lock:
                                self._mod_peak = peak - self._tare
                    with self._lock:
                        self._gross = gross
                        self._hist.append((time.monotonic(), gross - self._tare))
            except Exception:
                time.sleep(0.02)
            time.sleep(0.005)

    def reset_peak(self):
        try:
            self._cmd(b"!001:RSPT\r")
        except Exception:
            pass
        with self._lock:
            self._mod_peak = None

    def module_peak_kg(self):
        with self._lock:
            return self._mod_peak

    def latest_kg(self):
        with self._lock:
            return self._gross - self._tare

    def history(self, max_points=300):
        return self.history_window(1800.0, max_points)

    def history_window(self, window_s=10.0, max_points=300):
        with self._lock:
            now = time.monotonic()
            items = [(t, v) for t, v in self._hist if t >= now - min(window_s, WINDOW_MAX_S)]
        return _decimate(items, max_points)

    def tare(self):
        with self._lock:
            self._tare = self._gross
        return True


def _decimate(items, max_points):
    max_points = max(2, min(int(max_points), POINTS_MAX))
    if len(items) > max_points:
        n = len(items)
        bucket = -(-n // max_points)
        picked = []
        for i in range(0, n, bucket):
            chunk = items[i : i + bucket]
            picked.append(max(chunk, key=lambda p: p[1]))
        if picked and picked[-1] is not items[-1]:
            picked.append(items[-1])
        items = picked
    return [[t, round(v, 4)] for t, v in items]


def make_loadcell(demo, port=None, position_provider=None):
    if demo:
        return SimulatedLoadCell(position_provider=position_provider)
    return DscUsbLoadCell(port=port)
