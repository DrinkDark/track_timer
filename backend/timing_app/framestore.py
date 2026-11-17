"""Stores the recorded JPEG frames of one race in a single file with an index."""
from __future__ import annotations

import json
from pathlib import Path


class FrameWriter:
    def __init__(self, rec_dir: Path, first_idx: int):
        self.dir = Path(rec_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.first_idx = first_idx
        self._f = open(self.dir / "frames.bin", "wb")
        self._pos = 0
        self.offsets: list[int] = []
        self.sizes: list[int] = []

    def append(self, jpeg: bytes) -> None:
        self.offsets.append(self._pos)
        self.sizes.append(len(jpeg))
        self._f.write(jpeg)
        self._pos += len(jpeg)

    def append_duplicate_of_last(self) -> None:
        self.offsets.append(self.offsets[-1])
        self.sizes.append(self.sizes[-1])

    def close(self) -> None:
        self._f.close()
        (self.dir / "frames.json").write_text(json.dumps({
            "first_idx": self.first_idx,
            "count": len(self.offsets),
            "offsets": self.offsets,
            "sizes": self.sizes,
        }))


class FrameReader:
    def __init__(self, rec_dir: Path):
        self.dir = Path(rec_dir)
        idx = json.loads((self.dir / "frames.json").read_text())
        self.first_idx = idx["first_idx"]
        self.count = idx["count"]
        self.offsets = idx["offsets"]
        self.sizes = idx["sizes"]

    def get(self, frame_idx: int) -> bytes:
        i = frame_idx - self.first_idx
        if not 0 <= i < self.count:
            raise IndexError(frame_idx)
        with open(self.dir / "frames.bin", "rb") as f:
            f.seek(self.offsets[i])
            return f.read(self.sizes[i])
