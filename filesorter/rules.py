"""ルール（条件・動作）の定義と判定ロジック

条件はグループ（すべて一致/いずれか一致/否定）を入れ子にできる木構造。
GUI / 常駐プロセス / テスト機能のすべてがこのモジュールを共有するため、
「テストで確認した結果」と「実際の仕分け結果」が必ず一致する。
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple
from urllib.parse import urlsplit

from .util import fmt_age, fmt_datetime, fmt_size

# ------------------------------------------------------------------ 条件の定義
# (キー, 表示名, 種別)
FIELDS = [
    ("name", "ファイル名（拡張子を含む）", "text"),
    ("stem", "ファイル名（拡張子を除く）", "text"),
    ("ext", "拡張子", "ext"),
    ("category", "種類", "category"),
    ("size", "サイズ", "size"),
    ("mtime", "更新日時", "age"),
    ("ctime", "作成日時（ダウンロード日時）", "age"),
    ("url", "ダウンロード元URL", "text"),
    ("all", "すべてのファイル", "always"),
]
FIELD_LABELS = {k: label for k, label, _ in FIELDS}
FIELD_KINDS = {k: kind for k, _, kind in FIELDS}
FIELD_SHORT = {
    "name": "ファイル名", "stem": "ファイル名(拡張子除く)", "ext": "拡張子", "category": "種類",
    "size": "サイズ", "mtime": "更新日時", "ctime": "作成日時", "url": "ダウンロード元URL", "all": "すべて",
}

OPS = {
    "text": [("contains", "を含む"), ("startswith", "で始まる"), ("endswith", "で終わる"),
             ("equals", "と一致する"), ("wildcard", "ワイルドカードに一致"), ("regex", "正規表現に一致")],
    "ext": [("in", "のいずれか")],
    "category": [("is", "である")],
    "size": [("gte", "以上"), ("lte", "以下"), ("between", "の範囲内")],
    "age": [("older", "以上前"), ("newer", "以内")],
    "always": [("always", "（常に一致）")],
}
SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
AGE_UNITS = {"min": ("分", 60), "hour": ("時間", 3600), "day": ("日", 86400)}
DEFAULT_UNITS = {"size": "MB", "age": "day"}
MAX_DEPTH = 6  # グループの入れ子の上限

# ------------------------------------------------------------------ 動作の定義
# (キー, 表示名, 短い表示名)
STEP_TYPES = [
    ("move", "フォルダへ移動", "移動"),
    ("copy", "フォルダへコピー", "コピー"),
    ("rename", "名前を変更", "名前変更"),
    ("zip", "ZIPにまとめる", "ZIP化"),
    ("extract", "ZIPを展開", "展開"),
    ("recycle", "ごみ箱へ送る", "ごみ箱"),
    ("command", "コマンドを実行", "コマンド"),
    ("ignore", "何もしない（除外）", "除外"),
]
STEP_LABELS = {k: label for k, label, _ in STEP_TYPES}
STEP_SHORT = {k: short for k, _, short in STEP_TYPES}
STEP_DEFAULTS = {
    "move": {"target": "", "conflict": "rename"},
    "copy": {"target": "", "conflict": "rename"},
    "rename": {"target": "{yyyy}{mm}{dd}_{name}", "conflict": "rename"},
    # group: ZIP_GROUPS のキー。custom のときだけ target（保存先のテンプレート）を使う
    "zip": {"group": "month", "folder": "アーカイブ", "prefix": "", "target": "", "remove_original": True},
    "extract": {"target": r"{stem}", "remove_original": False},
    "recycle": {},
    "command": {"target": '"C:\\path\\to\\app.exe" "{path}"'},
    "ignore": {},
}
# ZIP のまとめ方: (キー, 表示名, ZIP ファイル名に使う差し込み)。条件に合ったファイルを、この単位ごとの ZIP に入れる
ZIP_GROUPS = [
    ("month", "更新月ごと", "{yyyy}-{mm}"),
    ("cmonth", "作成月ごと", "{cyyyy}-{cmm}"),
    ("day", "更新日ごと", "{yyyy}-{mm}-{dd}"),
    ("year", "更新年ごと", "{yyyy}"),
    ("ext", "拡張子ごと", "{ext}"),
    ("category", "種類ごと", "{category}"),
    ("all", "すべてを1つのZIPに", ""),
    ("each", "1ファイルずつ別のZIPに", "{stem}"),
    ("custom", "自由に指定（差し込みで）", None),
]
ZIP_GROUP_LABELS = {k: label for k, label, _ in ZIP_GROUPS}
ZIP_GROUP_KEYS = {k: key for k, _, key in ZIP_GROUPS}


def zip_template(step: dict) -> str:
    """ZIP 動作の保存先（差し込みを含むテンプレート。相対パスは監視フォルダ基準）"""
    group = step.get("group") or "custom"
    if group not in ZIP_GROUP_KEYS or group == "custom":
        return str(step.get("target") or "").strip()
    folder = str(step.get("folder") or "").strip().rstrip("\\/")
    name = str(step.get("prefix") or "") + ZIP_GROUP_KEYS[group]
    name = name.strip() or "{rule}"
    return (folder + "\\" if folder else "") + name + ".zip"


def zip_step_from_template(target: str) -> dict:
    """旧形式（保存先テンプレートのみ）の ZIP 動作を、まとめ方の設定に読み替える。読み替えられなければ custom。"""
    t = str(target or "").strip()
    m = re.fullmatch(r"(?:(?P<folder>.*)[\\/])?(?P<name>[^\\/]*)\.zip", t, re.IGNORECASE)
    if m:
        folder, name = m.group("folder") or "", m.group("name")
        for key, _, ph in ZIP_GROUPS:
            if ph and name.endswith(ph) and "{" not in folder:
                return {"group": key, "folder": folder, "prefix": name[: -len(ph)], "target": ""}
        if name and "{" not in name and "{" not in folder:
            return {"group": "all", "folder": folder, "prefix": name, "target": ""}
    return {"group": "custom", "folder": "", "prefix": "", "target": t}


CONFLICTS = [("rename", "連番を付ける"), ("overwrite", "上書き（既存はごみ箱へ）"), ("skip", "処理しない")]
CONFLICT_LABELS = dict(CONFLICTS)
TRIGGERS = [("auto", "自動（定期実行・今すぐ仕分け）"), ("manual", "手動のみ（クイック操作）")]

PLACEHOLDERS = [
    ("{name}", "ファイル名（拡張子を含む）"),
    ("{stem}", "ファイル名（拡張子を除く）"),
    ("{ext}", "拡張子"),
    ("{category}", "種類名（画像・文書など）"),
    ("{yyyy}", "更新日時の年"), ("{mm}", "更新日時の月"), ("{dd}", "更新日時の日"),
    ("{cyyyy}", "作成日時の年"), ("{cmm}", "作成日時の月"), ("{cdd}", "作成日時の日"),
    ("{today}", "今日の日付（YYYY-MM-DD）"),
    ("{host}", "ダウンロード元のドメイン"),
    ("{rule}", "ルール名"),
]
COMMAND_PLACEHOLDERS = [("{path}", "ファイルのフルパス"), ("{dir}", "ファイルのあるフォルダ")] + PLACEHOLDERS


def ops_for(field: str) -> list[tuple[str, str]]:
    return OPS[FIELD_KINDS.get(field, "text")]


def is_group(node: dict) -> bool:
    return isinstance(node, dict) and node.get("type") == "group"


def iter_leaves(node: dict):
    if is_group(node):
        for it in node.get("items") or []:
            yield from iter_leaves(it)
    elif isinstance(node, dict):
        yield node


def new_group(match: str = "all", items=None) -> dict:
    return {"type": "group", "match": match, "negate": False, "items": list(items or [])}


def new_condition(field="name", op="contains", value="") -> dict:
    return {"type": "cond", "field": field, "op": op, "value": value, "value2": "", "unit": "", "negate": False}


# ------------------------------------------------------------------ ファイル情報
def read_zone_identifier(path: str) -> tuple[str, str]:
    """ブラウザが付与する Zone.Identifier（代替データストリーム）から URL を読む。"""
    if not path:
        return ("", "")
    try:
        with open(path + ":Zone.Identifier", "rb") as fh:
            data = fh.read(16384)
    except OSError:
        return ("", "")
    text = data.decode("utf-16", "replace") if data.startswith(b"\xff\xfe") else data.decode("utf-8", "replace")
    host = ref = ""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip().lower()
        if key == "hosturl":
            host = value.strip()
        elif key == "referrerurl":
            ref = value.strip()
    return host, ref


@dataclass
class FileInfo:
    path: str
    name: str
    size: int = 0
    mtime: float = 0.0
    ctime: float = 0.0
    attrs: int = 0
    urls: tuple[str, str] | None = None  # (HostUrl, ReferrerUrl)。None = 未読込（遅延読込）

    @classmethod
    def from_entry(cls, entry: os.DirEntry) -> "FileInfo":
        st = entry.stat(follow_symlinks=False)
        return cls(entry.path, entry.name, st.st_size, st.st_mtime,
                   getattr(st, "st_birthtime", st.st_ctime), getattr(st, "st_file_attributes", 0))

    @classmethod
    def from_path(cls, path: str) -> "FileInfo":
        st = os.stat(path)
        return cls(path, os.path.basename(path), st.st_size, st.st_mtime,
                   getattr(st, "st_birthtime", st.st_ctime), getattr(st, "st_file_attributes", 0))

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1][1:].lower()

    def get_urls(self) -> tuple[str, str]:
        if self.urls is None:
            self.urls = read_zone_identifier(self.path)
        return self.urls

    def host(self) -> str:
        host_url, ref_url = self.get_urls()
        for u in (ref_url, host_url):
            if u:
                try:
                    h = urlsplit(u).hostname
                except ValueError:
                    h = None
                if h:
                    return h[4:] if h.startswith("www.") else h
        return ""


@dataclass
class EvalContext:
    now: float
    categories: dict  # key -> {"label": str, "exts": [str]}


class NodeResult(NamedTuple):
    node: dict
    ok: bool          # 否定を反映した最終結果
    actual: str       # 条件: 実際の値（表示用）
    error: str        # エラー内容（空=正常）
    children: list | None  # グループの場合は子の結果（短絡評価で未評価の子は含まれない）


class RuleResult(NamedTuple):
    rule: dict
    matched: bool
    root: NodeResult | None
    note: str         # 「無効」「条件がありません」など


# ------------------------------------------------------------------ 補助
def split_alternatives(value) -> list[str]:
    return [v.strip() for v in str(value or "").split("|") if v.strip()]


def parse_ext_list(value) -> list[str]:
    parts = re.split(r"[,\s;|、，]+", str(value or ""))
    return [p.strip().lstrip(".").lower() for p in parts if p.strip().lstrip(".")]


def parse_number(value) -> float:
    s = str(value if value is not None else "").strip().replace(",", "").replace("，", "")
    if not s:
        raise ValueError("empty")
    v = float(s)
    if v != v or v < 0:  # NaN / 負数
        raise ValueError("invalid")
    return v


@lru_cache(maxsize=512)
def compile_regex(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def ext_matches(info: FileInfo, exts: list[str]) -> bool:
    lname = info.name.lower()
    ext = info.ext
    for e in exts:
        if "." in e:
            if lname.endswith("." + e):
                return True
        elif e == ext:
            return True
    return False


def category_of(info: FileInfo, categories: dict) -> str | None:
    for key, cat in categories.items():
        if ext_matches(info, [str(e).lower().lstrip(".") for e in cat.get("exts", [])]):
            return key
    return None


def text_matches(op: str, subject: str, value: str) -> bool:
    if op == "regex":
        return compile_regex(str(value)).search(subject) is not None
    s = subject.casefold()
    for alt in split_alternatives(value):
        a = alt.casefold()
        if op == "contains" and a in s:
            return True
        if op == "startswith" and s.startswith(a):
            return True
        if op == "endswith" and s.endswith(a):
            return True
        if op == "equals" and s == a:
            return True
        if op == "wildcard" and fnmatch.fnmatchcase(s, a):
            return True
    return False


# ------------------------------------------------------------------ 判定
def evaluate_condition(cond: dict, info: FileInfo, ctx: EvalContext) -> NodeResult:
    field = cond.get("field", "name")
    op = cond.get("op", "")
    kind = FIELD_KINDS.get(field)
    value = cond.get("value", "")
    actual = ""
    try:
        if kind == "always":
            raw = True
        elif kind == "text":
            if field == "url":
                subjects = [u for u in info.get_urls() if u]
                actual = " / ".join(subjects) if subjects else "（URL情報なし）"
                raw = any(text_matches(op, s, value) for s in subjects)
            else:
                subject = info.name if field == "name" else info.stem
                actual = subject
                raw = text_matches(op, subject, value)
        elif kind == "ext":
            actual = info.ext or "（なし）"
            raw = ext_matches(info, parse_ext_list(value))
        elif kind == "category":
            cat = ctx.categories.get(value) or {}
            key = category_of(info, ctx.categories)
            actual = ctx.categories[key]["label"] if key else "その他"
            raw = ext_matches(info, [str(e).lower().lstrip(".") for e in cat.get("exts", [])])
        elif kind == "size":
            actual = fmt_size(info.size)
            mul = SIZE_UNITS.get(cond.get("unit") or "MB", SIZE_UNITS["MB"])
            v1 = parse_number(value) * mul
            if op == "gte":
                raw = info.size >= v1
            elif op == "lte":
                raw = info.size <= v1
            else:
                lo, hi = sorted((v1, parse_number(cond.get("value2")) * mul))
                raw = lo <= info.size <= hi
        elif kind == "age":
            t = info.mtime if field == "mtime" else info.ctime
            age = ctx.now - t
            actual = f"{fmt_datetime(t)}（{fmt_age(age)}）"
            sec = AGE_UNITS.get(cond.get("unit") or "day", AGE_UNITS["day"])[1]
            threshold = parse_number(value) * sec
            raw = age >= threshold if op == "older" else age < threshold
        else:
            return NodeResult(cond, False, "", "不明な条件です", None)
    except re.error as e:
        return NodeResult(cond, False, actual, f"正規表現エラー: {e}", None)
    except (ValueError, TypeError):
        return NodeResult(cond, False, actual, "数値が正しくありません", None)
    ok = (not raw) if cond.get("negate") else raw
    return NodeResult(cond, ok, actual, "", None)


def evaluate_node(node: dict, info: FileInfo, ctx: EvalContext, full: bool = False) -> NodeResult:
    """full=True なら短絡評価せずすべて判定する（テスト表示用）。"""
    if not is_group(node):
        return evaluate_condition(node, info, ctx)
    items = node.get("items") or []
    if not items:
        return NodeResult(node, False, "", "空のグループ", [])
    any_mode = node.get("match") == "any"
    children = []
    raw = None
    for it in items:
        r = evaluate_node(it, info, ctx, full)
        children.append(r)
        if not full:
            if any_mode and r.ok:
                raw = True
                break
            if not any_mode and not r.ok:
                raw = False
                break
    if raw is None:
        raw = any(c.ok for c in children) if any_mode else all(c.ok for c in children)
    ok = (not raw) if node.get("negate") else raw
    return NodeResult(node, ok, "", "", children)


def evaluate_rule(rule: dict, info: FileInfo, ctx: EvalContext, full: bool = False) -> RuleResult:
    root = rule.get("conditions") or new_group()
    if not any(True for _ in iter_leaves(root)):
        return RuleResult(rule, False, None, "条件がありません")
    r = evaluate_node(root, info, ctx, full)
    return RuleResult(rule, r.ok, r, "")


def find_matching_rule(rules: list, info: FileInfo, ctx: EvalContext, trace: bool = False):
    """上から順に評価し、最初に一致したルールを返す。戻り値: (rule|None, [RuleResult])"""
    traces = []
    for rule in rules:
        if not rule.get("enabled", True):
            if trace:
                traces.append(RuleResult(rule, False, None, "無効"))
            continue
        rr = evaluate_rule(rule, info, ctx, full=trace)
        if trace:
            traces.append(rr)
        if rr.matched:
            return rule, traces
    return None, traces


# ------------------------------------------------------------------ 検証
def _validate_condition(c: dict, categories: dict, prefix: str) -> list[str]:
    kind = FIELD_KINDS.get(c.get("field"))
    v = c.get("value", "")
    if kind == "text":
        if not str(v).strip():
            return [prefix + "値を入力してください。"]
        if c.get("op") == "regex":
            try:
                re.compile(str(v))
            except re.error as e:
                return [prefix + f"正規表現が正しくありません（{e}）。"]
    elif kind == "ext":
        if not parse_ext_list(v):
            return [prefix + "拡張子を入力してください（例: pdf, docx）。"]
    elif kind == "category":
        if v not in categories:
            return [prefix + "種類を選択してください。"]
    elif kind in ("size", "age"):
        try:
            parse_number(v)
            if kind == "size" and c.get("op") == "between":
                parse_number(c.get("value2"))
        except ValueError:
            return [prefix + "0以上の数値を入力してください。"]
    return []


def validate_rule(rule: dict, categories: dict) -> list[str]:
    errors = []
    if not str(rule.get("name", "")).strip():
        errors.append("ルール名を入力してください。")
    root = rule.get("conditions") or new_group()
    leaves = list(iter_leaves(root))
    if not leaves:
        errors.append("条件を1つ以上追加してください。")
    for i, c in enumerate(leaves, 1):
        errors += _validate_condition(c, categories, f"条件{i}: ")

    def check_empty(node, depth):
        if is_group(node):
            if not node.get("items") and depth > 0:
                errors.append("空のグループがあります（条件を追加するか削除してください）。")
            if depth >= MAX_DEPTH:
                errors.append(f"グループの入れ子は {MAX_DEPTH} 段までです。")
            for it in node.get("items") or []:
                check_empty(it, depth + 1)
    check_empty(root, 0)

    steps = rule.get("actions") or []
    if not steps:
        errors.append("動作を1つ以上追加してください。")
    types = [s.get("type") for s in steps]
    if "ignore" in types and len(steps) > 1:
        errors.append("「何もしない（除外）」は他の動作と組み合わせられません。")
    for i, s in enumerate(steps, 1):
        t = s.get("type")
        prefix = f"動作{i}（{STEP_SHORT.get(t, t)}）: "
        if t in ("move", "copy", "rename", "command") and not str(s.get("target", "")).strip():
            errors.append(prefix + {"move": "移動先フォルダ", "copy": "コピー先フォルダ", "rename": "新しい名前",
                                    "command": "コマンド"}[t] + "を入力してください。")
        if t == "zip":
            if s.get("group", "custom") == "custom":
                if not str(s.get("target", "")).strip():
                    errors.append(prefix + "ZIPファイルの保存先を入力してください。")
            elif not str(s.get("folder", "")).strip():
                errors.append(prefix + "ZIPの保存先フォルダを入力してください（監視フォルダ直下には作れません）。")
        if t == "rename" and re.search(r"[\\/]", str(s.get("target", ""))):
            errors.append(prefix + "新しい名前にフォルダ区切り（\\ /）は使えません。")
        if step_is_terminal(s) and i < len(steps):
            errors.append(prefix + "この動作のあとはファイルが無くなるため、後ろに動作を追加できません。")
    return errors


def step_is_terminal(step: dict) -> bool:
    t = step.get("type")
    return t in ("recycle", "ignore") or (t in ("zip", "extract") and bool(step.get("remove_original")))


# ------------------------------------------------------------------ 表示用の説明文
def describe_condition(c: dict, categories: dict | None = None) -> str:
    field = c.get("field", "name")
    kind = FIELD_KINDS.get(field, "text")
    label = FIELD_SHORT.get(field, field)
    op = c.get("op")
    v = c.get("value", "")
    if kind == "always":
        s = "すべてのファイル"
    elif kind == "text":
        phrase = {
            "contains": f"「{v}」を含む", "startswith": f"「{v}」で始まる", "endswith": f"「{v}」で終わる",
            "equals": f"「{v}」と一致", "wildcard": f"パターン「{v}」に一致", "regex": f"正規表現「{v}」に一致",
        }.get(op, str(v))
        s = f"{label}が{phrase}"
    elif kind == "ext":
        s = f"拡張子が {', '.join(parse_ext_list(v)) or '?'} のいずれか"
    elif kind == "category":
        cat_label = (categories or {}).get(v, {}).get("label", v)
        s = f"種類が「{cat_label}」"
    elif kind == "size":
        unit = c.get("unit") or "MB"
        if op == "between":
            s = f"サイズが {v}〜{c.get('value2', '')} {unit}"
        else:
            s = f"サイズが {v} {unit} {'以上' if op == 'gte' else '以下'}"
    elif kind == "age":
        unit = AGE_UNITS.get(c.get("unit") or "day", AGE_UNITS["day"])[0]
        s = f"{label}が {v} {unit}{'以上前' if op == 'older' else '以内'}"
    else:
        s = str(c)
    return f"NOT［{s}］" if c.get("negate") else s


def group_label(node: dict) -> str:
    s = "いずれかに一致" if node.get("match") == "any" else "すべてに一致"
    return f"NOT［{s}］" if node.get("negate") else s


def describe_node(node: dict, categories: dict | None = None, top: bool = True) -> str:
    if not is_group(node):
        return describe_condition(node, categories)
    items = node.get("items") or []
    if not items:
        return "（条件なし）"
    joiner = " または " if node.get("match") == "any" else " かつ "
    s = joiner.join(describe_node(it, categories, False) for it in items)
    if node.get("negate"):
        return f"NOT（{s}）"
    return s if top or len(items) == 1 else f"（{s}）"


def describe_conditions(rule: dict, categories: dict | None = None) -> str:
    return describe_node(rule.get("conditions") or new_group(), categories)


def describe_step(step: dict) -> str:
    t = step.get("type")
    target = step.get("target", "")
    if t in ("move", "copy"):
        return f"{STEP_SHORT[t]} → {target}"
    if t == "rename":
        return f"名前変更 → {target}"
    if t == "zip":
        group = step.get("group", "custom")
        how = "" if group == "custom" else f"（{ZIP_GROUP_LABELS.get(group, group)}）"
        return f"ZIP化{how} → {zip_template(step)}" + ("（元はごみ箱）" if step.get("remove_original") else "")
    if t == "extract":
        return f"展開 → {target or '{stem}'}" + ("（ZIPはごみ箱）" if step.get("remove_original") else "")
    if t == "command":
        return f"コマンド: {target}"
    return STEP_LABELS.get(t, t)


def describe_actions(rule: dict) -> str:
    steps = rule.get("actions") or []
    return "　＞　".join(describe_step(s) for s in steps) if steps else "（動作なし）"


def actions_short(rule: dict) -> str:
    return "→".join(STEP_SHORT.get(s.get("type"), "?") for s in rule.get("actions") or [])
