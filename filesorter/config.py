"""設定ファイル（config.json）の読み書き・既定値・正規化・保存先（通常/ポータブル）"""
from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

from . import APP_ID
from .rules import (AGE_UNITS, CONFLICTS, DEFAULT_UNITS, FIELD_KINDS, MAX_DEPTH, OPS, SIZE_UNITS, STEP_DEFAULTS,
                    TRIGGERS, ZIP_GROUP_KEYS, is_group, new_condition, new_group, zip_step_from_template)

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ 場所
FROZEN = bool(getattr(sys, "frozen", False))  # PyInstaller で exe 化されているか
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
PROJECT_ROOT = APP_DIR
ASSETS_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "assets"
PORTABLE_MARKER = APP_DIR / "portable.txt"
PORTABLE_DATA_DIR = APP_DIR / "data"
DOWNLOADS_TOKEN = "{downloads}"
CONFIG_VERSION = 2
DATA_FILES = ("config.json", "history.json", "state.json", "exclusions.json")


def is_portable() -> bool:
    """アプリと同じフォルダに portable.txt があればポータブルモード（設定は APP_DIR\\data）。"""
    return PORTABLE_MARKER.exists()


def normal_data_dir() -> Path:
    return Path(os.environ.get("APPDATA") or str(Path.home())) / APP_ID


def data_dir() -> Path:
    """設定・履歴の保存先。優先順位: 環境変数 FILESORTKUN_DATA ＞ ポータブル ＞ %APPDATA%\\FileSortKun"""
    env = os.environ.get("FILESORTKUN_DATA")
    if env:
        p = Path(env)
    elif is_portable():
        p = PORTABLE_DATA_DIR
    else:
        p = normal_data_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return data_dir() / "config.json"


def switch_storage(portable: bool) -> Path:
    """保存先を切り替え、現在のデータを新しい場所へコピーする。戻り値: 新しい保存先。"""
    src = data_dir()
    if portable:
        dst = PORTABLE_DATA_DIR
        dst.mkdir(parents=True, exist_ok=True)
        PORTABLE_MARKER.write_text("このファイルがあると、設定と履歴をこのフォルダの data に保存します（ポータブルモード）。\n",
                                   encoding="utf-8")
    else:
        dst = normal_data_dir()
        dst.mkdir(parents=True, exist_ok=True)
        try:
            PORTABLE_MARKER.unlink()
        except FileNotFoundError:
            pass
    if src.resolve() != dst.resolve():
        for name in DATA_FILES:
            if (src / name).exists():
                shutil.copy2(src / name, dst / name)
    return dst


def resolve_folder(path: str) -> str:
    """監視フォルダの表記を実際のパスにする（{downloads}・環境変数・アプリフォルダからの相対パス）。"""
    p = str(path or "").strip()
    if DOWNLOADS_TOKEN in p:
        from .winapi import downloads_folder
        p = p.replace(DOWNLOADS_TOKEN, downloads_folder())
    p = os.path.expandvars(os.path.expanduser(p))
    if p and not os.path.isabs(p):
        p = str(APP_DIR / p)
    return os.path.normpath(p) if p else p


def watch_folders(cfg: dict) -> list[str]:
    return [resolve_folder(f) for f in cfg.get("watch_folders", [])]


def folder_key(path: str) -> str:
    """フォルダ比較用のキー（表記を実際のパスに解決して大文字小文字を無視）"""
    return os.path.normcase(resolve_folder(path))


def rule_applies_to(rule: dict, folder: str) -> bool:
    """ルールがその監視フォルダ（実際のパス）を対象にするか。folders が空ならすべて対象。"""
    scope = rule.get("folders") or []
    if not scope:
        return True
    key = os.path.normcase(os.path.normpath(folder))
    return any(folder_key(f) == key for f in scope)


def folder_label(entry: str) -> str:
    """監視フォルダの表示名（短い名前）"""
    if entry.strip() == DOWNLOADS_TOKEN:
        return "ダウンロード"
    return os.path.basename(resolve_folder(entry)) or entry


def missing_rule_folders(cfg: dict, rule: dict) -> list[str]:
    """ルールが対象に指定しているが、監視フォルダに無いもの"""
    keys = {folder_key(f) for f in cfg.get("watch_folders", [])}
    return [f for f in rule.get("folders") or [] if folder_key(f) not in keys]


def folder_token_for(path: str) -> str:
    """フォルダ選択結果を、可搬性の高い表記（{downloads} / アプリフォルダからの相対）に変換する。"""
    from .winapi import downloads_folder
    np = os.path.normcase(os.path.normpath(path))
    if np == os.path.normcase(os.path.normpath(downloads_folder())):
        return DOWNLOADS_TOKEN
    if is_portable():
        try:
            rel = os.path.relpath(path, APP_DIR)
            if not rel.startswith(".."):
                return rel
        except ValueError:
            pass
    return os.path.normpath(path)


# ------------------------------------------------------------------ 既定値
DEFAULT_IGNORE = [
    "*.crdownload", "*.part", "*.partial", "*.download", "*.opdownload", "*.tmp", "*.temp",
    "*.!ut", "*.!qb", "*.aria2", "~$*", ".~lock.*", "desktop.ini", "thumbs.db",
]

DEFAULT_CATEGORIES = {
    "image": {"label": "画像", "exts": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "heif", "tif", "tiff",
                                        "svg", "ico", "avif", "psd", "raw", "cr2", "cr3", "nef", "arw", "dng"]},
    "video": {"label": "動画", "exts": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg",
                                        "ts", "m2ts", "3gp"]},
    "audio": {"label": "音声", "exts": ["mp3", "wav", "flac", "aac", "m4a", "ogg", "wma", "opus", "aiff", "mid", "midi"]},
    "document": {"label": "文書", "exts": ["pdf", "doc", "docx", "xls", "xlsx", "xlsm", "ppt", "pptx", "txt", "rtf",
                                           "odt", "ods", "odp", "csv", "md", "epub", "pages", "numbers", "key"]},
    "archive": {"label": "圧縮ファイル", "exts": ["zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "lzh",
                                                  "cab", "iso", "img"]},
    "installer": {"label": "インストーラ", "exts": ["exe", "msi", "msix", "msixbundle", "appx", "appxbundle",
                                                    "dmg", "pkg", "deb", "rpm", "apk"]},
    "code": {"label": "コード", "exts": ["py", "js", "ts", "html", "css", "json", "xml", "yml", "yaml", "c", "cpp",
                                          "h", "cs", "java", "go", "rs", "php", "rb", "sh", "bat", "ps1", "sql", "ipynb"]},
    "font": {"label": "フォント", "exts": ["ttf", "otf", "woff", "woff2"]},
}


def new_rule_id() -> str:
    return "r" + secrets.token_hex(4)


def _sample_rules() -> list[dict]:
    """初回起動時のサンプル（安全のため全て無効状態）。"""
    rules = []
    for key in ["installer", "image", "video", "audio", "document", "archive"]:
        label = DEFAULT_CATEGORIES[key]["label"]
        rules.append(normalize_rule({
            "name": label, "enabled": False,
            "conditions": new_group("all", [new_condition("category", "is", key)]),
            "actions": [{"type": "move", "target": label, "conflict": "rename"}],
        }))
    rules.append(normalize_rule({
        "name": "古いスクリーンショットを月ごとにZIP（例）", "enabled": False, "trigger": "manual",
        "conditions": new_group("all", [
            new_condition("category", "is", "image"),
            new_group("any", [new_condition("name", "contains", "screenshot|スクリーンショット"),
                              new_condition("name", "startswith", "IMG_")]),
            {**new_condition("mtime", "older", "30"), "unit": "day"},
        ]),
        "actions": [{"type": "zip", "group": "month", "folder": "アーカイブ", "prefix": "スクリーンショット_",
                     "remove_original": True}],
    }))
    return rules


def default_config() -> dict:
    return {
        "version": CONFIG_VERSION,
        "enabled": True,
        "watch_folders": [DOWNLOADS_TOKEN],
        "interval_sec": 300,
        "settle_sec": 30,
        "watch_changes": False,
        "ignore_patterns": list(DEFAULT_IGNORE),
        "skip_hidden": True,
        "notify": True,
        "theme": "system",
        "history_limit": 1000,
        "categories": copy.deepcopy(DEFAULT_CATEGORIES),
        "rules": _sample_rules(),
    }


# ------------------------------------------------------------------ 正規化（旧形式の読み替えを含む）
def _clamp_int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return default


def normalize_condition(c: dict) -> dict:
    field = c.get("field") if c.get("field") in FIELD_KINDS else "name"
    kind = FIELD_KINDS[field]
    valid_ops = [k for k, _ in OPS[kind]]
    op = c.get("op") if c.get("op") in valid_ops else valid_ops[0]
    unit = str(c.get("unit") or "")
    if kind == "size" and unit not in SIZE_UNITS:
        unit = DEFAULT_UNITS["size"]
    elif kind == "age" and unit not in AGE_UNITS:
        unit = DEFAULT_UNITS["age"]
    elif kind not in ("size", "age"):
        unit = ""
    return {
        "type": "cond", "field": field, "op": op,
        "value": str(c.get("value") if c.get("value") is not None else ""),
        "value2": str(c.get("value2") if c.get("value2") is not None else ""),
        "unit": unit, "negate": bool(c.get("negate", False)),
    }


def normalize_node(n: dict, depth: int = 0) -> dict:
    if is_group(n):
        items = n.get("items") if isinstance(n.get("items"), list) else []
        if depth >= MAX_DEPTH:
            items = []
        return {"type": "group", "match": "any" if n.get("match") == "any" else "all",
                "negate": bool(n.get("negate", False)),
                "items": [normalize_node(it, depth + 1) for it in items if isinstance(it, dict)]}
    return normalize_condition(n)


def normalize_step(s: dict) -> dict:
    t = s.get("type") if s.get("type") in STEP_DEFAULTS else "move"
    d = STEP_DEFAULTS[t]
    out = {"type": t}
    if "target" in d:
        out["target"] = str(s.get("target") if s.get("target") is not None else d["target"])
    if "conflict" in d:
        out["conflict"] = s.get("conflict") if s.get("conflict") in dict(CONFLICTS) else d["conflict"]
    if "remove_original" in d:
        out["remove_original"] = bool(s.get("remove_original", d["remove_original"]))
    if t == "zip":
        if s.get("group") in ZIP_GROUP_KEYS:
            out.update(group=s["group"], folder=str(s.get("folder") or ""), prefix=str(s.get("prefix") or ""))
        elif "group" not in s and s.get("target"):
            out.update(zip_step_from_template(s["target"]))  # 旧形式（保存先テンプレートのみ）からの読み替え
        else:
            out.update(group=d["group"], folder=d["folder"], prefix=d["prefix"])
    return out


def normalize_rule(r: dict) -> dict:
    # --- v1 形式（conditions がリスト / action・destination・conflict）からの読み替え
    conds = r.get("conditions")
    if isinstance(conds, list):
        conds = {"type": "group", "match": r.get("match", "all"), "negate": False, "items": conds}
    if not is_group(conds or {}):
        conds = new_group()
    actions = r.get("actions")
    if not isinstance(actions, list):
        old = r.get("action", "move")
        if old == "move":
            actions = [{"type": "move", "target": r.get("destination", ""), "conflict": r.get("conflict", "rename")}]
        else:
            actions = [{"type": old if old in ("recycle", "ignore") else "move"}]
    return {
        "id": str(r.get("id") or new_rule_id()),
        "name": str(r.get("name") or "新しいルール"),
        "enabled": bool(r.get("enabled", True)),
        "trigger": r.get("trigger") if r.get("trigger") in dict(TRIGGERS) else "auto",
        "conditions": normalize_node(conds),
        "actions": [normalize_step(s) for s in actions if isinstance(s, dict)],
        # 対象の監視フォルダ（監視フォルダの表記のまま保存。空 = すべての監視フォルダ）
        "folders": list(dict.fromkeys(str(f).strip() for f in (r.get("folders") or [])
                                      if isinstance(f, str) and f.strip())),
    }


def normalize_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        return default_config()
    out = {"version": CONFIG_VERSION}
    out["enabled"] = bool(cfg.get("enabled", True))
    folders = [str(f) for f in (cfg.get("watch_folders") or []) if str(f).strip()]
    if not folders:
        folders = [DOWNLOADS_TOKEN]
    if cfg.get("version", 1) < 2:  # v1 は絶対パス → ダウンロードフォルダなら {downloads} に
        from .winapi import downloads_folder
        dl = os.path.normcase(os.path.normpath(downloads_folder()))
        folders = [DOWNLOADS_TOKEN if os.path.normcase(os.path.normpath(f)) == dl else f for f in folders]
    out["watch_folders"] = folders
    out["interval_sec"] = _clamp_int(cfg.get("interval_sec"), 10, 86400, 300)
    out["settle_sec"] = _clamp_int(cfg.get("settle_sec"), 0, 3600, 30)
    out["watch_changes"] = bool(cfg.get("watch_changes", False))
    pats = cfg.get("ignore_patterns")
    out["ignore_patterns"] = [str(p).strip() for p in pats if str(p).strip()] if isinstance(pats, list) else list(DEFAULT_IGNORE)
    out["skip_hidden"] = bool(cfg.get("skip_hidden", True))
    out["notify"] = bool(cfg.get("notify", True))
    out["theme"] = cfg.get("theme") if cfg.get("theme") in ("system", "light", "dark") else "system"
    out["history_limit"] = _clamp_int(cfg.get("history_limit"), 50, 100000, 1000)
    cats = cfg.get("categories")
    if isinstance(cats, dict) and cats:
        out["categories"] = {
            str(k): {"label": str(v.get("label") or k), "exts": [str(e).lower().lstrip(".") for e in v.get("exts", [])]}
            for k, v in cats.items() if isinstance(v, dict)
        }
    else:
        out["categories"] = copy.deepcopy(DEFAULT_CATEGORIES)
    out["rules"] = [normalize_rule(r) for r in (cfg.get("rules") or []) if isinstance(r, dict)]
    return out


# ------------------------------------------------------------------ 読み書き
def atomic_write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        backup = path.with_name(f"config.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
        log.error("設定ファイルを読み込めません (%s)。%s に退避して既定値で起動します。", e, backup.name)
        try:
            os.replace(path, backup)
        except OSError:
            pass
        cfg = default_config()
        save_config(cfg)
        return cfg
    cfg = normalize_config(raw)
    if isinstance(raw, dict) and raw.get("version", 1) < CONFIG_VERSION:
        try:  # 旧形式は移行前の内容を残してから新形式で保存
            shutil.copy2(path, path.with_name(f"config.v{raw.get('version', 1)}.bak.json"))
            save_config(cfg)
            log.info("設定ファイルを v%s から v%s に移行しました", raw.get("version", 1), CONFIG_VERSION)
        except OSError:
            pass
    return cfg


def save_config(cfg: dict) -> dict:
    cfg = normalize_config(cfg)
    atomic_write_json(config_path(), cfg)
    return cfg


def update_config(mutator) -> dict:
    """最新の設定を読み直してから変更を適用・保存する（他プロセスとの上書き競合を防ぐ）。"""
    cfg = load_config()
    mutator(cfg)
    return save_config(cfg)


def config_mtime() -> float:
    try:
        return config_path().stat().st_mtime
    except OSError:
        return 0.0
