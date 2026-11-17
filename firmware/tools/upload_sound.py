#!/usr/bin/env python3
"""Uploads the pistol sound to the start unit over its USB serial console.

    python upload_sound.py /dev/tty.usbmodemXXXX pistol.wav

The WAV must be mono, 16 bit, 44100 Hz and at most about 2.9 s (the sound partition is 252 KB; spec: about 2 s,
176 KB). Convert anything else first, for example:
    ffmpeg -i in.mp3 -ac 1 -ar 44100 -sample_fmt s16 -t 2 pistol.wav
Needs pyserial.
"""
from __future__ import annotations

import argparse
import sys
import time
import wave
import zlib

MAX_BYTES = 0x40000 - 4096


def load_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 44100):
            raise SystemExit(f"{path}: need mono, 16 bit, 44100 Hz; got {w.getnchannels()} ch, "
                             f"{8 * w.getsampwidth()} bit, {w.getframerate()} Hz. Convert with ffmpeg (see --help).")
        pcm = w.readframes(w.getnframes())
    if not pcm or len(pcm) > MAX_BYTES:
        raise SystemExit(f"{path}: {len(pcm)} bytes of audio, allowed 2..{MAX_BYTES}")
    return pcm


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("wav")
    a = ap.parse_args()
    pcm = load_pcm(a.wav)
    crc = zlib.crc32(pcm) & 0xFFFFFFFF
    import serial
    with serial.Serial(a.port, 115200, timeout=2) as sp:
        sp.write(b"\n")
        time.sleep(0.2)
        sp.reset_input_buffer()
        sp.write(f"sound upload {len(pcm)} {crc:08x}\n".encode())
        deadline = time.time() + 10
        while time.time() < deadline:
            if b"READY" in sp.readline():
                break
        else:
            raise SystemExit("start unit did not answer READY")
        for i in range(0, len(pcm), 512):          # small blocks: the USB buffer of the chip is small
            sp.write(pcm[i:i + 512])
            sp.flush()
            time.sleep(0.004)
        deadline = time.time() + 15
        while time.time() < deadline:
            line = sp.readline().decode(errors="replace").strip()
            if line.startswith("$SOUND"):
                print(line)
                return 0 if "stored" in line else 1
    raise SystemExit("no answer after the upload")


if __name__ == "__main__":
    sys.exit(main())
