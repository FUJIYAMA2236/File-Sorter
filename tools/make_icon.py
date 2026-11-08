"""アイコン生成スクリプト（開発用・標準ライブラリのみ）

assets/icon.ico        … 通常時（青→紫グラデーション）
assets/icon_paused.ico … 一時停止時（グレー）
assets/icon_64.png     … GUI サイドバー用ロゴ

使い方:  python tools/make_icon.py
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def _sd_round_box(px, py, cx, cy, hw, hh, r):
    """角丸矩形の符号付き距離（負=内側）。座標は 0..1 の正規化座標。"""
    qx = abs(px - cx) - hw + r
    qy = abs(py - cy) - hh + r
    ox = max(qx, 0.0)
    oy = max(qy, 0.0)
    return math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r


def _box(x0, y0, x1, y1, r):
    return ((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2, r)


def _lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _over(dst, src_rgb, src_a):
    """dst=(r,g,b,a) に src を重ねる（ストレートアルファ）。"""
    dr, dg, db, da = dst
    out_a = src_a + da * (1 - src_a)
    if out_a <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    r = (src_rgb[0] * src_a + dr * da * (1 - src_a)) / out_a
    g = (src_rgb[1] * src_a + dg * da * (1 - src_a)) / out_a
    b = (src_rgb[2] * src_a + db * da * (1 - src_a)) / out_a
    return (r, g, b, out_a)


def _shade(u, v, paused, small):
    if paused:
        c0, c1, bar = (156, 163, 175), (95, 103, 116), (95, 103, 116)
    else:
        c0, c1, bar = (79, 140, 255), (139, 92, 246), (88, 92, 230)
    px = (0.0, 0.0, 0.0, 0.0)
    bg = _box(0.04, 0.04, 0.96, 0.96, 0.22)
    if _sd_round_box(u, v, *bg) <= 0:
        t = min(max((u + v) / 2, 0.0), 1.0)
        px = _over(px, _lerp(c0, c1, t), 1.0)
    white = (255, 255, 255)
    tab = _box(0.20, 0.25, 0.47, 0.40, 0.05)
    if _sd_round_box(u, v, *tab) <= 0:
        px = _over(px, white, 0.72)
    body = _box(0.20, 0.33, 0.80, 0.76, 0.07)
    if _sd_round_box(u, v, *body) <= 0:
        px = _over(px, white, 1.0)
    if not small:
        for (x0, y0, x1, y1) in ((0.31, 0.44, 0.69, 0.50), (0.31, 0.53, 0.61, 0.59), (0.31, 0.62, 0.52, 0.68)):
            if _sd_round_box(u, v, *_box(x0, y0, x1, y1, 0.03)) <= 0:
                px = _over(px, bar, 1.0)
    return px


def render(size: int, paused: bool = False) -> bytes:
    """RGBA（上から下）のピクセル列を返す。"""
    ss = 4 if size <= 64 else 3
    small = size < 24
    out = bytearray()
    n = ss * ss
    for y in range(size):
        for x in range(size):
            ar = ag = ab = aa = 0.0
            for sy in range(ss):
                for sx in range(ss):
                    r, g, b, a = _shade((x + (sx + 0.5) / ss) / size, (y + (sy + 0.5) / ss) / size, paused, small)
                    ar += r * a
                    ag += g * a
                    ab += b * a
                    aa += a
            if aa > 0:
                out += bytes((round(ar / aa), round(ag / aa), round(ab / aa), round(aa / n * 255)))
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def png_bytes(rgba: bytes, size: int) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def dib_bytes(rgba: bytes, size: int) -> bytes:
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = []
    for y in range(size - 1, -1, -1):  # ボトムアップ
        row = bytearray()
        for x in range(size):
            i = (y * size + x) * 4
            r, g, b, a = rgba[i:i + 4]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    mask_row = ((size + 31) // 32) * 4
    return header + b"".join(rows) + b"\x00" * (mask_row * size)


def write_ico(path: Path, paused: bool):
    sizes = [16, 20, 24, 32, 40, 48, 64, 256]
    images = []
    for s in sizes:
        rgba = render(s, paused)
        images.append((s, png_bytes(rgba, s) if s >= 256 else dib_bytes(rgba, s)))
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    data = b""
    for s, blob in images:
        dim = 0 if s >= 256 else s
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    path.write_bytes(out + data)


def main():
    ASSETS.mkdir(exist_ok=True)
    write_ico(ASSETS / "icon.ico", paused=False)
    write_ico(ASSETS / "icon_paused.ico", paused=True)
    (ASSETS / "icon_64.png").write_bytes(png_bytes(render(64), 64))
    (ASSETS / "icon_32.png").write_bytes(png_bytes(render(32), 32))
    print("generated:", ", ".join(p.name for p in ASSETS.iterdir()))


if __name__ == "__main__":
    main()
