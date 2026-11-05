import time
from datetime import datetime
from pathlib import Path

MAX_ROWS = 1000000
FLUSH_EVERY = 500
SAMPLE_DT = 1.0


class Recorder:
    def __init__(self, app_dir):
        self.dir = Path(app_dir)
        self.wb = None
        self.ws = None
        self.path = None
        self.mode = None
        self._t0 = 0.0
        self._last_sample = 0.0
        self._rows = 0
        self._pending = 0
        self._part = 1

    @property
    def active(self):
        return self.ws is not None

    @property
    def file_name(self):
        return self.path.name if self.path else None

    def begin(self, mode, cfg):
        from openpyxl import Workbook

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = self.dir / f"cyclelog_{ts}.xlsx"
        self.mode = mode
        self._t0 = time.monotonic()
        self._last_sample = 0.0
        self._rows = 1
        self._pending = 0
        self._part = 1
        self.wb = Workbook()
        self.ws = self.wb.active
        self.ws.title = "data"
        self.ws.append(["timestamp", "elapsed_s", "cycle", "position_mm", "load_kg", "event"])
        self._append([datetime.now().isoformat(timespec="milliseconds"), 0.0, 0, "", "", f"run start mode={mode} cfg={cfg}"])

    def _append(self, row):
        if self.ws is None:
            return
        self.ws.append(row)
        self._rows += 1
        self._pending += 1
        if self._pending >= FLUSH_EVERY:
            self._save()
        if self._rows > MAX_ROWS:
            self._rotate()

    def sample(self, t, cycle, pos, load):
        if self.ws is None or t - self._last_sample < SAMPLE_DT:
            return
        self._last_sample = t
        self._append([
            datetime.now().isoformat(timespec="milliseconds"),
            round(t - self._t0, 3),
            cycle,
            round(pos, 3),
            round(load, 4),
            "",
        ])

    def peak(self, t, cycle, pos, load):
        if self.ws is None:
            return
        self._append([
            datetime.now().isoformat(timespec="milliseconds"),
            round(t - self._t0, 3),
            cycle,
            round(pos, 3),
            round(load, 4),
            "PEAK",
        ])

    def event(self, text, cycle=0, pos="", load=""):
        if self.ws is None:
            return
        self._append([
            datetime.now().isoformat(timespec="milliseconds"),
            round(time.monotonic() - self._t0, 3),
            cycle,
            pos,
            load,
            text,
        ])

    def _save(self):
        if self.wb is not None and self.path is not None:
            self.wb.save(self.path)
            self._pending = 0

    def _rotate(self):
        self._save()
        self._part += 1
        base = self.path.stem
        self.path = self.dir / f"{base}_part{self._part}.xlsx"
        from openpyxl import Workbook

        self.wb = Workbook()
        self.ws = self.wb.active
        self.ws.title = "data"
        self.ws.append(["timestamp", "elapsed_s", "cycle", "position_mm", "load_kg", "event"])
        self._rows = 1

    def finish(self, reason, cycle=0, pos="", load="", run_peak=None):
        if self.ws is None:
            return None
        if run_peak is not None:
            self.event(f"run peak {run_peak:.4f} kg", cycle, pos, run_peak)
        self.event(f"run finished: {reason}", cycle, pos, load)
        self._save()
        path = self.path
        self.wb = None
        self.ws = None
        return path

    def failure(self, details):
        lines = [
            f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
        ]
        for key, value in details.items():
            lines.append(f"{key}: {value}")
        text = "\n".join(lines) + "\n\n"
        path = self.dir / "Failure.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
        self._save()
        return path
