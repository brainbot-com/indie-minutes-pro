"""Draws the app icons (a white record dot on the Swiss red) without any image library."""
import struct
import zlib
from pathlib import Path


def png(size, path):
    bg, dot = (225, 37, 27, 255), (255, 255, 255, 255)
    c, r = size / 2, size * 0.22   # inside the maskable safe zone
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            d = ((x + 0.5 - c) ** 2 + (y + 0.5 - c) ** 2) ** 0.5
            a = max(0.0, min(1.0, r - d + 0.5))  # anti-aliased edge
            row += bytes(round(bg[i] * (1 - a) + dot[i] * a) for i in range(4))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(rows)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    here = Path(__file__).parent
    for n in (192, 512):
        png(n, here / f"icon-{n}.png")
