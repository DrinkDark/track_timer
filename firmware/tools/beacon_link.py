#!/usr/bin/env python3
"""PC side of the beacon link protocol (spec section 5) and a bench tool.

    python beacon_link.py /dev/tty.usbserial-XXXX start        # send a start packet on a USB-RS485 adapter
    python beacon_link.py /dev/tty.usbserial-XXXX ping         # ping and wait for the pong
    python beacon_link.py /dev/tty.usbserial-XXXX start --seq 7 --count 3

Needs pyserial (pip install pyserial). Mirrors firmware/common/beacon_protocol.h.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time

MAGIC = 0xA5
START, PING, PONG = 1, 2, 3
BAUD = 250000


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def encode(ptype: int, seq: int, time_us: int = 0) -> bytes:
    body = struct.pack("<BBHI", MAGIC, ptype, seq & 0xFFFF, time_us & 0xFFFFFFFF)
    return body + bytes([crc8(body)])


def decode(buf: bytes):
    """Returns (type, seq, time_us) or None."""
    if len(buf) != 9 or buf[0] != MAGIC or buf[1] not in (START, PING, PONG) or crc8(buf[:8]) != buf[8]:
        return None
    _, t, seq, tus = struct.unpack("<BBHI", buf[:8])
    return t, seq, tus


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("what", choices=["start", "ping"])
    ap.add_argument("--seq", type=int, default=int(time.time()) & 0xFFFF)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--gap", type=float, default=0.5, help="seconds between packets")
    a = ap.parse_args()
    import serial  # pyserial
    with serial.Serial(a.port, BAUD, timeout=0.3) as sp:
        for i in range(a.count):
            seq = (a.seq + i) & 0xFFFF
            pkt = encode(START if a.what == "start" else PING, seq, int(time.monotonic() * 1e6))
            sp.reset_input_buffer()
            sp.write(pkt)
            sp.flush()
            print(f"sent {a.what} seq={seq}: {pkt.hex(' ')}")
            if a.what == "ping":
                reply = decode(sp.read(18)[-9:])
                print("  pong:", reply if reply else "no reply")
            time.sleep(a.gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
