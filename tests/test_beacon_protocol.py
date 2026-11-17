"""The PC tool, the C header and the spec must agree on the 9-byte link packet."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "firmware"
sys.path.insert(0, str(ROOT / "tools"))

import beacon_link as bl  # noqa: E402

VECTORS = [  # the same vectors are hard-coded in firmware/test/test_protocol.c
    ((1, 1, 0), "a50101000000000078"),
    ((1, 0x1234, 0xDEADBEEF), "a5013412efbeadde33"),
    ((2, 65535, 1), "a502ffff01000000b7"),
    ((3, 7, 123456789), "a503070015cd5b078c"),
]


def test_crc8_check_value():
    assert bl.crc8(b"123456789") == 0xF4          # CRC-8, poly 0x07, init 0


def test_vectors_round_trip():
    for (t, seq, tus), hexs in VECTORS:
        raw = bytes.fromhex(hexs)
        assert bl.encode(t, seq, tus) == raw
        assert bl.decode(raw) == (t, seq, tus)


def test_corruption_is_rejected():
    raw = bytearray(bl.encode(1, 42, 1000))
    for i in range(9):
        for bit in range(8):
            bad = bytearray(raw)
            bad[i] ^= 1 << bit
            assert bl.decode(bytes(bad)) is None
    assert bl.decode(bytes(raw[:8])) is None


def test_c_header_matches(tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        return
    exe = tmp_path / "tp"
    subprocess.run([cc, "-Wall", "-Wextra", "-I", str(ROOT / "common"), str(ROOT / "test" / "test_protocol.c"),
                    "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
