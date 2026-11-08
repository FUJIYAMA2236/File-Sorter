"""動作（ステップ）の展開・プレビュー・実行・元に戻す

1つのルールに複数の動作を順に並べられる（例: 名前を変更 → フォルダへ移動、ZIPにまとめる 等）。
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass

from . import winapi
from .rules import STEP_SHORT, FileInfo, category_of, zip_template

log = logging.getLogger(__name__)

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
# 既に圧縮済みの形式は ZIP 化の際に再圧縮しない（CPU 節約）
STORED_EXTS = {"zip", "7z", "rar", "gz", "tgz", "bz2", "xz", "zst", "jpg", "jpeg", "png", "gif", "webp", "heic",
               "avif", "mp4", "mkv", "mov", "webm", "m4v", "mp3", "m4a", "aac", "ogg", "opus", "flac", "docx",
               "xlsx", "pptx", "epub", "apk", "msix", "jar"}
COMMAND_TIMEOUT_SEC = 300


class StepError(Exception):
    pass


class StopChain(Exception):
    """同名ファイルがあり「処理しない」設定のときなど、以降の動作を行わずに終える。"""


# ------------------------------------------------------------------ パス・差し込み
def same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def sanitize(value: str) -> str:
    v = _INVALID_CHARS.sub("_", str(value)).strip(" .")
    return v or "_"


def expand(template: str, info: FileInfo, rule: dict, categories: dict, name: str | None = None,
           path: str | None = None, raw: bool = False) -> str:
    """{name} などの差し込みを展開する。raw=True はコマンド用（値を加工せず {path} {dir} も使える）。"""
    name = name or info.name
    stem, dot_ext = os.path.splitext(name)
    lt = time.localtime(info.mtime or time.time())
    ct = time.localtime(info.ctime or info.mtime or time.time())
    cache: dict = {}

    def lookup(m: re.Match) -> str:
        key = m.group(1).lower()
        if key in cache:
            return cache[key]
        if key == "name":
            v = name
        elif key == "stem":
            v = stem
        elif key == "ext":
            v = dot_ext[1:].lower() or "拡張子なし"
        elif key == "category":
            ck = category_of(FileInfo(path="", name=name), categories)
            v = categories[ck]["label"] if ck else "その他"
        elif key in ("yyyy", "mm", "dd"):
            v = time.strftime({"yyyy": "%Y", "mm": "%m", "dd": "%d"}[key], lt)
        elif key in ("cyyyy", "cmm", "cdd"):
            v = time.strftime({"cyyyy": "%Y", "cmm": "%m", "cdd": "%d"}[key], ct)
        elif key == "today":
            v = time.strftime("%Y-%m-%d")
        elif key == "host":
            v = info.host() or "不明"
        elif key == "rule":
            v = rule.get("name", "rule")
        elif raw and key == "path":
            v = path or info.path
        elif raw and key == "dir":
            v = os.path.dirname(path or info.path)
        else:
            return m.group(0)  # 未知の差し込みはそのまま
        cache[key] = v if raw else sanitize(v)
        return cache[key]

    return _PLACEHOLDER.sub(lookup, template)


def resolve_path(template: str, info: FileInfo, rule: dict, categories: dict, base: str,
                 name: str | None = None) -> str:
    """フォルダ/ファイルのテンプレートを絶対パスにする。相対パスは監視フォルダ基準。"""
    t = str(template or "").strip()
    if not t:
        return ""
    s = expand(t, info, rule, categories, name=name)
    s = os.path.expandvars(os.path.expanduser(s))
    if not os.path.isabs(s):
        s = os.path.join(base, s)
    return os.path.normpath(s)


def next_free_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    d, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        cand = os.path.join(d, f"{stem} ({n}){ext}")
        if not os.path.exists(cand):
            return cand
        n += 1


def move_file(src: str, dst: str) -> None:
    """同一ドライブは rename、別ドライブはコピー後に元を削除（失敗時はコピーを消して中断）。"""
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if getattr(e, "winerror", None) != 17:  # ERROR_NOT_SAME_DEVICE 以外はそのまま失敗
            raise
    shutil.copy2(src, dst)
    try:
        os.remove(src)
    except OSError:
        try:
            os.remove(dst)
        except OSError:
            pass
        raise


def zip_archive_path(step: dict, info, rule, categories, base, name=None) -> str:
    """ZIP 動作で、このファイルを入れる ZIP の絶対パス（まとめ方の設定から決まる）"""
    p = resolve_path(zip_template(step), info, rule, categories, base, name)
    if p and not p.lower().endswith(".zip"):
        p += ".zip"
    return p


def unique_zip_entry(names: set, entry: str) -> str:
    if entry not in names:
        return entry
    stem, ext = os.path.splitext(entry)
    n = 1
    while f"{stem} ({n}){ext}" in names:
        n += 1
    return f"{stem} ({n}){ext}"


def open_zip_for_read(path: str) -> zipfile.ZipFile:
    """Windows で作られた ZIP（ファイル名が cp932）も文字化けしないように開く。"""
    try:
        z = zipfile.ZipFile(path, metadata_encoding="cp932")
        z.namelist()
        return z
    except (UnicodeDecodeError, ValueError):
        return zipfile.ZipFile(path)


# ------------------------------------------------------------------ プレビュー（テスト表示用・ファイルは動かさない）
@dataclass
class StepPreview:
    type: str
    target: str = ""
    note: str = ""
    stop: bool = False

    def text(self, bases: list[str] | None = None) -> str:
        s = STEP_SHORT.get(self.type, self.type)
        if self.target:
            from .util import display_path
            s += f" → {display_path(self.target, bases) if bases and self.type != 'command' else self.target}"
        if self.note:
            s += f"（{self.note}）"
        return s


def preview(rule: dict, info: FileInfo, base: str, categories: dict) -> list[StepPreview]:
    out: list[StepPreview] = []
    path = info.path or os.path.join(base, info.name)
    name = info.name
    for step in rule.get("actions") or []:
        t = step.get("type")
        tgt = step.get("target", "")
        if t == "ignore":
            out.append(StepPreview(t, note="移動しません"))
            break
        if t in ("move", "copy"):
            d = resolve_path(tgt, info, rule, categories, base, name)
            if t == "move" and same_dir(d, os.path.dirname(path)):
                out.append(StepPreview(t, d, "移動先が元と同じため何もしません"))
                continue
            target, note, stop = _preview_conflict(os.path.join(d, name), step)
            out.append(StepPreview(t, target, note, stop))
            if stop:
                break
            if t == "move":
                path, name = target, os.path.basename(target)
        elif t == "rename":
            new = sanitize(expand(tgt, info, rule, categories, name=name))
            target = os.path.join(os.path.dirname(path), new)
            if same_dir(target, path):
                out.append(StepPreview(t, new, "名前が同じため何もしません"))
                continue
            target, note, stop = _preview_conflict(target, step)
            out.append(StepPreview(t, os.path.basename(target), note, stop))
            if stop:
                break
            path, name = target, os.path.basename(target)
        elif t == "zip":
            arc = zip_archive_path(step, info, rule, categories, base, name)
            note = "元のファイルはごみ箱へ" if step.get("remove_original") else "元のファイルは残します"
            if same_dir(os.path.dirname(arc), base):
                note = "保存先が監視フォルダ直下のため実行できません"
            out.append(StepPreview(t, arc, note))
            if step.get("remove_original"):
                break
        elif t == "extract":
            if not name.lower().endswith(".zip"):
                out.append(StepPreview(t, "", "ZIPファイルではないため展開しません", True))
                break
            d = resolve_path(tgt or "{stem}", info, rule, categories, base, name)
            out.append(StepPreview(t, d, "展開後に元のZIPはごみ箱へ" if step.get("remove_original") else ""))
            if step.get("remove_original"):
                break
        elif t == "recycle":
            out.append(StepPreview(t))
            break
        elif t == "command":
            out.append(StepPreview(t, expand(tgt, info, rule, categories, name=name, path=path, raw=True)))
    return out


def _preview_conflict(target: str, step: dict) -> tuple[str, str, bool]:
    if not os.path.exists(target):
        return target, "", False
    c = step.get("conflict", "rename")
    if c == "skip":
        return target, "同名ファイルがあるため処理しません", True
    if c == "overwrite":
        return target, "同名ファイルを上書き（既存はごみ箱へ）", False
    return next_free_path(target), "同名ファイルがあるため連番を付けます", False


# ------------------------------------------------------------------ 実行
def _resolve_conflict(target: str, step: dict) -> tuple[str, bool]:
    """戻り値: (実際の保存先, 既存を置き換えたか)"""
    if not os.path.exists(target):
        return target, False
    c = step.get("conflict", "rename")
    if c == "skip":
        raise StopChain(f"同名ファイルがあるため処理しませんでした: {os.path.basename(target)}")
    if c == "overwrite":
        winapi.send_to_recycle_bin(target)
        return target, True
    return next_free_path(target), False


def _run_step(step: dict, path: str, info: FileInfo, rule: dict, categories: dict, base: str):
    """1ステップを実行する。戻り値: (実行後のファイルパス or None, 履歴用の記録 or None)"""
    t = step.get("type")
    tgt = step.get("target", "")
    name = os.path.basename(path)
    if t in ("move", "copy"):
        d = resolve_path(tgt, info, rule, categories, base, name)
        if not d:
            raise StepError("移動先が設定されていません")
        if t == "move" and same_dir(d, os.path.dirname(path)):
            return path, None
        os.makedirs(d, exist_ok=True)
        target, replaced = _resolve_conflict(os.path.join(d, name), step)
        if t == "move":
            move_file(path, target)
            return target, {"type": "move", "from": path, "to": target, "replaced": replaced}
        shutil.copy2(path, target)
        return path, {"type": "copy", "from": path, "to": target, "replaced": replaced}
    if t == "rename":
        new = sanitize(expand(tgt, info, rule, categories, name=name))
        target = os.path.join(os.path.dirname(path), new)
        if same_dir(target, path):
            return path, None
        target, replaced = _resolve_conflict(target, step)
        os.rename(path, target)
        return target, {"type": "rename", "from": path, "to": target, "replaced": replaced}
    if t == "zip":
        arc = zip_archive_path(step, info, rule, categories, base, name)
        if not arc:
            raise StepError("ZIPファイルの保存先が設定されていません")
        if same_dir(os.path.dirname(arc), base):
            raise StepError("ZIPの保存先が監視フォルダ直下です（サブフォルダを指定してください）")
        if same_dir(arc, path):
            raise StepError("ZIPファイル自身は追加できません")
        os.makedirs(os.path.dirname(arc), exist_ok=True)
        ext = os.path.splitext(name)[1][1:].lower()
        with zipfile.ZipFile(arc, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            entry = unique_zip_entry(set(z.namelist()), name)
            z.write(path, entry, compress_type=zipfile.ZIP_STORED if ext in STORED_EXTS else zipfile.ZIP_DEFLATED)
        removed = bool(step.get("remove_original"))
        if removed:
            winapi.send_to_recycle_bin(path)
        return (None if removed else path), {"type": "zip", "from": path, "archive": arc, "entry": entry,
                                             "removed": removed}
    if t == "extract":
        if not zipfile.is_zipfile(path):
            raise StepError("ZIP形式ではないため展開できません")
        d = resolve_path(tgt or "{stem}", info, rule, categories, base, name)
        created = not os.path.exists(d)
        os.makedirs(d, exist_ok=True)
        root = os.path.realpath(d)
        count = 0
        with open_zip_for_read(path) as z:
            for zi in z.infolist():
                dest = os.path.realpath(os.path.join(d, zi.filename))
                if os.path.commonpath([root, dest]) != root:
                    continue  # ZIP 外へのパス（Zip Slip）は無視
                if zi.is_dir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                dest = next_free_path(dest)
                with z.open(zi) as src_f, open(dest, "wb") as out_f:
                    shutil.copyfileobj(src_f, out_f, 1024 * 1024)
                count += 1
        removed = bool(step.get("remove_original"))
        if removed:
            winapi.send_to_recycle_bin(path)
        return (None if removed else path), {"type": "extract", "from": path, "to": d, "created": created,
                                             "files": count, "removed": removed}
    if t == "recycle":
        winapi.send_to_recycle_bin(path)
        return None, {"type": "recycle", "from": path}
    if t == "command":
        cmd = expand(tgt, info, rule, categories, name=name, path=path, raw=True)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=COMMAND_TIMEOUT_SEC,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise StepError(f"コマンドが {COMMAND_TIMEOUT_SEC} 秒以内に終わりませんでした")
        except OSError as e:
            raise StepError(f"コマンドを実行できません: {e}")
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or b"").decode("cp932", "replace").strip()[:200]
            raise StepError(f"コマンドが終了コード {r.returncode} で終了しました {msg}")
        # コマンドがファイルを移動/削除した場合に備える
        return (path if os.path.exists(path) else None), {"type": "command", "from": path, "command": cmd}
    if t == "ignore":
        return path, None
    raise StepError(f"不明な動作です: {t}")


def execute(rule: dict, info: FileInfo, base: str, categories: dict) -> dict:
    """ルールの動作を順に実行し、履歴エントリを返す（例外は投げず error / skipped に記録）。"""
    entry = {
        "id": secrets.token_hex(6), "time": time.time(), "name": info.name, "src": info.path,
        "rule": rule.get("name", ""), "rule_id": rule.get("id", ""), "size": info.size,
        "steps": [], "final": info.path, "undone": False, "error": "", "skipped": "",
    }
    path = info.path
    for step in rule.get("actions") or []:
        try:
            new_path, rec = _run_step(step, path, info, rule, categories, base)
        except StopChain as e:
            entry["skipped"] = str(e)
            break
        except PermissionError:
            entry["error"] = "使用中のため処理できません（次回再試行します）"
            break
        except (StepError, OSError, zipfile.BadZipFile) as e:
            entry["error"] = str(e)
            log.warning("動作に失敗: %s (%s): %s", info.path, step.get("type"), e)
            break
        if rec:
            entry["steps"].append(rec)
            log.info("%s: %s (ルール: %s)", STEP_SHORT.get(rec["type"], rec["type"]), _rec_text(rec), rule.get("name"))
        path = new_path
        if path is None:
            break
    entry["final"] = path or ""
    return entry


def _rec_text(rec: dict) -> str:
    if rec["type"] == "zip":
        return f"{rec['from']} -> {rec['archive']}"
    return f"{rec.get('from')} -> {rec.get('to', '')}"


# ------------------------------------------------------------------ 履歴の表示・元に戻す
# 同じ実行で同じ ZIP にまとめたファイルは、履歴1件（ZIP 1つにつき1件）にまとめて記録する。
# その場合、各ファイルの記録は items に入る（items の各要素は通常の履歴と同じ name/src/size/steps/final を持つ）。
def merge_zip_entries(entries: list[dict]) -> list[dict]:
    order: list = []          # ("one", entry) or ("zip", key)
    groups: dict = {}
    for e in entries:
        steps = e.get("steps") or []
        last = steps[-1] if steps else {}
        if e.get("error") or e.get("skipped") or last.get("type") != "zip":
            order.append(("one", e))
            continue
        key = (e.get("rule_id"), os.path.normcase(last.get("archive", "")))
        if key not in groups:
            groups[key] = []
            order.append(("zip", key))
        groups[key].append(e)
    out = []
    for kind, x in order:
        if kind == "one":
            out.append(x)
            continue
        items = groups[x]
        if len(items) == 1:
            out.append(items[0])
            continue
        first = items[0]
        out.append({
            "id": secrets.token_hex(6), "time": first.get("time", time.time()),
            "name": f"{first.get('name', '')} ほか {len(items) - 1} 件", "src": os.path.dirname(first.get("src", "")),
            "rule": first.get("rule", ""), "rule_id": first.get("rule_id", ""),
            "size": sum(int(i.get("size") or 0) for i in items),
            "items": [{k: i.get(k) for k in ("name", "src", "size", "steps", "final")} for i in items],
            "final": "", "undone": False, "error": "", "skipped": "",
        })
    return out


def entry_items(e: dict) -> list[dict]:
    """履歴1件に含まれるファイルごとの記録（まとめていない履歴はそれ自体）"""
    items = e.get("items")
    return items if isinstance(items, list) and items else [e]


def entry_count(e: dict) -> int:
    return len(entry_items(e))


def entry_names(e: dict) -> list[str]:
    return [str(i.get("name", "")) for i in entry_items(e)]


def entry_steps(e: dict) -> list[dict]:
    """旧形式（v1: action/src/dst）の履歴も steps 形式で返す。まとめた履歴は先頭のファイルの動作。"""
    if isinstance(e.get("items"), list) and e["items"]:
        return entry_steps(e["items"][0])
    if isinstance(e.get("steps"), list):
        return e["steps"]
    if e.get("action") == "move":
        return [{"type": "move", "from": e.get("src", ""), "to": e.get("dst", "")}]
    if e.get("action") == "recycle":
        return [{"type": "recycle", "from": e.get("src", "")}]
    return []


def entry_label(e: dict) -> str:
    steps = entry_steps(e)
    return "→".join(STEP_SHORT.get(s.get("type"), "?") for s in steps) or ("エラー" if e.get("error") else "—")


def entry_destination(e: dict) -> str:
    """一覧表示用：最後の動作の行き先"""
    for s in reversed(entry_steps(e)):
        if s.get("type") == "zip":
            return s.get("archive", "")
        if s.get("type") in ("move", "copy", "rename", "extract"):
            return s.get("to", "")
        if s.get("type") == "recycle":
            return "ごみ箱"
    return ""


def entry_undoable(e: dict) -> bool:
    return not e.get("undone") and any(
        not i.get("undone") and any(s.get("type") in ("move", "rename", "copy", "zip", "extract")
                                    for s in entry_steps(i))
        for i in entry_items(e))


def undo_entry(e: dict) -> tuple[list[str], list[str], list[str]]:
    """1件の履歴を取り消す（ファイルごとに動作を逆順に）。
    戻り値: (元に戻ったファイルのパス, 注意事項, 戻せなかったファイルのメッセージ)。
    まとめていない履歴で失敗した場合は例外を投げる。まとめた履歴は戻せたファイルに undone を付ける。"""
    items = entry_items(e)
    batch = items[0] is not e
    restored: list[str] = []
    notes: list[str] = []
    failed: list[str] = []
    zips: dict = {}
    try:
        for item in items:
            if batch and item.get("undone"):
                continue
            try:
                path, n = _undo_steps(entry_steps(item), zips)
            except (StepError, OSError, KeyError, zipfile.BadZipFile) as ex:
                if not batch:
                    raise
                failed.append(f"{item.get('name')}: {ex}")
                continue
            if batch:
                item["undone"] = True
            if path:
                restored.append(path)
            notes += n
    finally:
        for z in zips.values():
            z.close()
    return restored, notes, failed


def _undo_steps(steps: list[dict], zips: dict) -> tuple[str | None, list[str]]:
    """1ファイル分の動作を逆順に取り消す。zips は開いた ZIP の使い回し用（まとめて戻すときに速くする）"""
    notes: list[str] = []
    restored = None
    for s in reversed(steps):
        t = s.get("type")
        if t in ("move", "rename"):
            if not os.path.exists(s["to"]):
                raise StepError(f"{os.path.basename(s['to'])} が見つかりません")
            dest = next_free_path(s["from"])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            move_file(s["to"], dest)
            restored = dest
            if s.get("replaced"):
                notes.append("上書きされた既存ファイルはごみ箱から復元してください")
        elif t == "copy":
            if os.path.exists(s["to"]):
                winapi.send_to_recycle_bin(s["to"])
        elif t == "zip":
            if s.get("removed"):
                key = os.path.normcase(s["archive"])
                if key not in zips:
                    zips[key] = zipfile.ZipFile(s["archive"])
                z = zips[key]
                zi = z.getinfo(s["entry"])
                dest = next_free_path(s["from"])
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(zi) as src_f, open(dest, "wb") as out_f:
                    shutil.copyfileobj(src_f, out_f, 1024 * 1024)
                try:  # 更新日時を ZIP に記録された日時に戻す（「更新月ごと」などの判定が変わらないように）
                    ts = time.mktime(zi.date_time + (0, 0, -1))
                    os.utime(dest, (ts, ts))
                except (OverflowError, ValueError, OSError):
                    pass
                restored = dest
            notes.append("ZIP 内のファイルはそのまま残ります")
        elif t == "extract":
            if s.get("created") and os.path.isdir(s["to"]):
                winapi.send_to_recycle_bin(s["to"])
            if s.get("removed"):
                notes.append("元の ZIP はごみ箱から復元してください")
        elif t == "recycle":
            notes.append("ごみ箱へ送ったファイルはごみ箱から復元してください")
        elif t == "command":
            notes.append("コマンドの実行結果は元に戻せません")
    return restored, notes
