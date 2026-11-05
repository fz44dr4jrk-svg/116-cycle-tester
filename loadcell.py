import random
import threading
import time
from collections import deque

READ_CMD = b"!001:SYS?\r"
BAUD = 115200
POLL_HZ = 20.0
HIST_MAX = 36000
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

    def tare(self):
        raise NotImplementedError


class SimulatedLoadCell(LoadCellBase):
    name = "Simulated load cell (demo mode)"

    def __init__(self, position_provider=None):
        self._provider = position_provider
        self._lock = threading.Lock()
        self._gross = 0.0
        self._tare = 0.0
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
                self._hist.append((time.monotonic(), gross - self._tare))
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


class DscUsbLoadCell(LoadCellBase):
    def __init__(self, port=None):
        self.port = port
        self._ser = None
        self._lock = threading.Lock()
        self._gross = 0.0
        self._tare = 0.0
        self._hist = deque(maxlen=HIST_MAX)
        self._stop = threading.Event()
        self._thread = None

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
        self._ser = serial.Serial(port, BAUD, timeout=0.2)
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
        while not self._stop.is_set():
            try:
                self._ser.reset_input_buffer()
                self._ser.write(READ_CMD)
                line = self._ser.read_until(b"\r", 64).strip()
                if line and line != b"?":
                    gross = float(line)
                    with self._lock:
                        self._gross = gross
                        self._hist.append((time.monotonic(), gross - self._tare))
            except Exception:
                time.sleep(0.1)
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


def _decimate(items, max_points):
    max_points = max(2, min(int(max_points), POINTS_MAX))
    if len(items) > max_points:
        step = -(-len(items) // max_points)
        picked = items[::step]
        if picked and picked[-1] is not items[-1]:
            picked.append(items[-1])
        items = picked
    return [[t, round(v, 4)] for t, v in items]


def make_loadcell(demo, port=None, position_provider=None):
    if demo:
        return SimulatedLoadCell(position_provider=position_provider)
    return DscUsbLoadCell(port=port)
