"""camera_count.py logic with a fake camera and a fake start unit (no hardware)."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "firmware" / "tools"))

import camera_count as cc  # noqa: E402


def test_clean_stream_has_no_gaps():
    ts = [i / 120 for i in range(1200)]
    r = cc.analyze_intervals(ts, 120)
    assert r["frames"] == 1200 and abs(r["fps"] - 120) < 0.01
    assert r["gaps"] == 0 and r["lost_frames_estimate"] == 0


def test_dropped_frames_are_counted():
    ts = [i / 120 for i in range(1200) if i not in (300, 301, 700)]      # a double drop and a single drop
    r = cc.analyze_intervals(ts, 120)
    assert r["gaps"] == 2 and r["lost_frames_estimate"] == 3


def test_too_few_frames():
    assert cc.analyze_intervals([0.0, 0.1], 120)["frames"] == 2


def test_burst_verdicts():
    assert cc.burst_verdict(100, 100, 0).startswith("PASS")
    assert "FAIL: 2 pulse" in cc.burst_verdict(100, 98, 0)
    assert "FAIL: 1 frame" in cc.burst_verdict(100, 101, 0)
    assert "NOT IN TRIGGER MODE" in cc.burst_verdict(100, 100, 7)


class FakeReader:
    """Frames appear only while a burst runs, like a camera in trigger mode."""

    def __init__(self, extra_idle=0, lose=0):
        self.n, self.stop_flag, self.t = extra_idle, False, []
        self.lose = lose

    def count(self):
        return self.n

    def times(self):
        return [i / 120 for i in range(self.n)]


class FakeSerial:
    def __init__(self, reader, pulses, lose=0):
        self.reader, self.pulses, self.lose = reader, pulses, lose
        self.sent = False

    def write(self, b):
        if b"camtrig burst" in b:
            self.sent = True

    def readline(self):
        if self.sent:
            self.reader.n += self.pulses - self.lose
            self.sent = False
            return b"$CAMTRIG done pulses=%d\n" % self.pulses
        return b""


def fast():
    return dict(sleep=lambda s: None, clock=iter(i * 0.05 for i in range(10**6)).__next__)


def test_burst_pass_and_fail():
    r = FakeReader()
    out = cc.run_burst(r, FakeSerial(r, 600), 600, 120, **fast())
    assert out["verdict"].startswith("PASS") and out["received"] == 600
    r = FakeReader()
    out = cc.run_burst(r, FakeSerial(r, 600, lose=2), 600, 120, **fast())
    assert "FAIL: 2 pulse" in out["verdict"]


def test_burst_detects_free_running_camera():
    r = FakeReader(extra_idle=5)
    out = cc.run_burst(r, FakeSerial(r, 100), 100, 120, **fast())
    assert "NOT IN TRIGGER MODE" in out["verdict"]
