"""表示用フォーマットなど小物ユーティリティ"""
from __future__ import annotations

import time


def fmt_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}秒前"
    if s < 3600:
        return f"{s // 60}分前"
    if s < 86400:
        return f"{s // 3600}時間前"
    return f"{s // 86400}日前"


def fmt_datetime(ts: float | None, seconds: bool = False) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y/%m/%d %H:%M:%S" if seconds else "%Y/%m/%d %H:%M", time.localtime(ts))


def fmt_clock(ts: float | None) -> str:
    if not ts:
        return "—"
    lt = time.localtime(ts)
    if time.strftime("%Y%m%d", lt) == time.strftime("%Y%m%d"):
        return time.strftime("%H:%M:%S", lt)
    return time.strftime("%m/%d %H:%M", lt)


def fmt_number(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def display_path(path: str, bases: list[str]) -> str:
    """監視フォルダ配下なら相対パス（例: 文書\\a.pdf）で返す。"""
    import os
    if not path:
        return ""
    np = os.path.normcase(os.path.normpath(path))
    for b in bases:
        nb = os.path.normcase(os.path.normpath(b))
        if np.startswith(nb + os.sep):
            return os.path.normpath(path)[len(os.path.normpath(b)) + 1:]
    return path


def elide_middle(s: str, max_len: int = 72) -> str:
    if len(s) <= max_len:
        return s
    keep = (max_len - 1) // 2
    return s[:keep] + "…" + s[-(max_len - 1 - keep):]
