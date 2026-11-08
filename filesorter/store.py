"""履歴（history.json）・状態（state.json）・再仕分け除外（exclusions.json）の保存

書き込みは常駐プロセスと GUI の両方から行われるため、
履歴・除外リストの更新は呼び出し側で RunLock（プロセス間ロック）を取得してから行うこと。
"""
from __future__ import annotations

import json
import os
import time

from .config import atomic_write_json, data_dir


def _read_json(name: str, default):
    try:
        with open(data_dir() / name, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(name: str, data) -> None:
    atomic_write_json(data_dir() / name, data)


# ------------------------------------------------------------------ 履歴
def load_history() -> list[dict]:
    data = _read_json("history.json", [])
    return data if isinstance(data, list) else []


def save_history(entries: list[dict], limit: int | None = None) -> None:
    if limit:
        entries = entries[-limit:]
    _write_json("history.json", entries)


def append_history(new_entries: list[dict], limit: int) -> None:
    if not new_entries:
        return
    hist = load_history()
    hist.extend(new_entries)
    save_history(hist, limit)


def clear_history() -> None:
    save_history([])


# ------------------------------------------------------------------ 状態（常駐プロセス → GUI）
def read_state() -> dict:
    data = _read_json("state.json", {})
    return data if isinstance(data, dict) else {}


def write_state(**updates) -> None:
    st = read_state()
    st.update(updates)
    st["updated"] = time.time()
    try:
        _write_json("state.json", st)
    except OSError:
        pass


def clear_exclusions_by_reason(reason: str) -> None:
    ex = {k: v for k, v in load_exclusions().items() if v.get("reason", "undone") != reason}
    _write_json("exclusions.json", ex)


# ------------------------------------------------------------------ 元に戻したファイルの除外
def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def load_exclusions() -> dict:
    data = _read_json("exclusions.json", {})
    return data if isinstance(data, dict) else {}


def add_exclusions(paths: list[str], reason: str = "undone") -> None:
    ex = load_exclusions()
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        ex[_key(p)] = {"size": st.st_size, "mtime": st.st_mtime, "reason": reason}
    _write_json("exclusions.json", ex)


def excluded_reason(exclusions: dict, path: str, size: int, mtime: float) -> str:
    """除外対象なら理由（undone / processed）、そうでなければ空文字。内容が変われば対象外になる。"""
    e = exclusions.get(_key(path))
    if e and e.get("size") == size and abs(float(e.get("mtime", 0)) - mtime) < 1.0:
        return e.get("reason", "undone")
    return ""


# ------------------------------------------------------------------ クイック操作の依頼（GUI → 常駐）
def write_request(rule_id: str) -> None:
    _write_json("request.json", {"rule_id": rule_id, "time": time.time()})


def take_request(max_age: float = 60.0) -> str | None:
    """依頼を取り出して削除する。古い依頼は無視。"""
    data = _read_json("request.json", None)
    if data is None:
        return None
    try:
        os.remove(data_dir() / "request.json")
    except OSError:
        pass
    if isinstance(data, dict) and time.time() - float(data.get("time", 0)) <= max_age:
        return data.get("rule_id") or None
    return None


def prune_exclusions() -> None:
    ex = load_exclusions()
    if not ex:
        return
    alive = {k: v for k, v in ex.items() if os.path.exists(k)}
    if len(alive) != len(ex):
        _write_json("exclusions.json", alive)


def clear_exclusions() -> None:
    _write_json("exclusions.json", {})
