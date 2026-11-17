import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from timing_app.config import delta_link_ms  # noqa: E402
from timing_app.db import Database  # noqa: E402
from timing_app.flash import FlashDetector, link_from_indicator  # noqa: E402
from timing_app.timing import estimate_fps, format_time, race_time_ms, round_up_hundredths  # noqa: E402


def test_rounding_up_to_the_hundredth():
    assert round_up_hundredths(11520.0) == 1152
    assert round_up_hundredths(11520.4) == 1153
    assert round_up_hundredths(11529.9) == 1153
    assert format_time(11520.0) == "11.52"
    assert format_time(9981.0) == "9.99"
    assert format_time(61230.0) == "1:01.23"
    assert format_time(None) == ""


def test_race_time_formula_and_delta():
    assert race_time_ms(1500, 100, 120.0, 0.9) == pytest.approx(11666.6667 + 0.9, abs=1e-3)
    cal = {"audio_ms": 0.5, "led_ms": 0.1, "cable_ms": 0.2, "radio_ms": 1.4}
    assert delta_link_ms(cal, "radio") == pytest.approx(1.0)
    assert delta_link_ms(cal, "cable") == pytest.approx(-0.2)


def _blinker(fps, hz, n, drop_at=None):
    t = np.arange(n) / fps
    sig = np.where((np.floor(2 * hz * t) % 2) == 0, 255.0, 55.0)
    if drop_at is not None:
        sig = np.delete(sig, drop_at)
    return sig


@pytest.mark.parametrize("fps", [119.7, 120.0, 120.4, 239.6])
def test_fps_from_blinker(fps):
    res = estimate_fps(_blinker(fps, 23.0, int(fps * 30)), 23.0, 120.0)
    assert res.measured and abs(res.fps - fps) < 0.02
    assert not res.warnings


def test_dropped_frame_is_flagged():
    res = estimate_fps(_blinker(120.0, 23.0, 3600, drop_at=1800), 23.0, 120.0)
    assert any("irregular" in w for w in res.warnings)


def test_blinker_not_visible_falls_back_to_nominal():
    res = estimate_fps(np.full(1000, 100.0), 20.0, 120.0)
    assert not res.measured and res.fps == 120.0 and res.warnings


def test_flash_detector_uses_first_of_two_frames():
    rng = np.random.default_rng(1)
    det = FlashDetector()
    found = None
    for i in range(400):
        v = 60 + rng.normal(0, 1.5)
        if i >= 250:
            v = 255
        n = det.update(i, v)
        if n is not None:
            found = (i, n)
            break
    assert found == (251, 250)


def test_flash_detector_ignores_a_single_noisy_frame():
    det = FlashDetector()
    for i in range(200):
        v = 255 if i == 100 else 60
        assert det.update(i, v) is None


def test_link_indicator():
    v = np.full(200, 55.0)
    v[101:120] = 255
    assert link_from_indicator(v, 100) == "radio"
    assert link_from_indicator(np.full(200, 55.0), 100) == "cable"


def test_startlist_import_and_csv(tmp_path):
    db = Database(tmp_path / "t.db")
    sid = db.create_session("Meet")
    csv_text = ("event;heat;lane;bib;name;club;category\n"
                "100 m;1;1;12;Anna Meier;LC Zürich;W\n100 m;1;2;7;Bea Roth;LC Bern;W\n"
                "100 m;2;1;33;Clara Frei;LC Basel;W\n")
    out = db.import_startlist(sid, csv_text)
    assert out == {"races": 2, "athletes": 3, "warnings": []}
    races = db.list_races(sid)
    assert [r["heat"] for r in races] == ["1", "2"]
    assert [e["name"] for e in races[0]["entries"]] == ["Anna Meier", "Bea Roth"]
    assert db.next_scheduled(sid) == races[0]["id"]
    assert db.export_csv(sid).splitlines()[0].startswith("event,heat,race_status,place,lane,bib")


def test_rounding_ignores_float_noise():
    assert round_up_hundredths(11200.0197) == 1120   # exact 11.20 s with 0.02 ms of fps noise
    assert round_up_hundredths(11200.5) == 1121
