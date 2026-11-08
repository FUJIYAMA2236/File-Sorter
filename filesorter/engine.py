"""仕分けエンジン：スキャン → 計画（判定）→ 実行 → 履歴 / 元に戻す

plan_* は読み取り専用（シミュレーション/テストで使用）、run_once / undo が実際にファイルを動かす。
"""
from __future__ import annotations

import fnmatch
import logging
import os
import time
from dataclasses import dataclass, field

from . import actions, store, winapi
from .config import rule_applies_to, watch_folders
from .ipc import LOCK_RUN
from .rules import EvalContext, FileInfo, actions_short, find_matching_rule

log = logging.getLogger(__name__)

STATUS_LABELS = {
    "run": "実行",
    "ignore": "除外（ルール）",
    "nomatch": "一致なし",
    "skip": "スキップ",
    "error": "エラー",
}

# 移動・ZIP 化などはスキャン対象外になるが、コピー等でファイルが監視フォルダに残る場合は
# 「処理済み」として記録し、同じ内容のまま何度も処理しないようにする。
EXCLUDE_UNDONE = "undone"
EXCLUDE_PROCESSED = "processed"


@dataclass
class PlanItem:
    folder: str
    info: FileInfo
    status: str
    rule: dict | None = None
    steps: list = field(default_factory=list)  # [actions.StepPreview]
    reason: str = ""
    traces: list = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.status == "run" and self.rule:
            return actions_short(self.rule)
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def destination(self) -> str:
        """一覧表示用：最後の動作の行き先"""
        for p in reversed(self.steps):
            if p.target:
                return p.target
        return ""


@dataclass
class RunSummary:
    entries: list = field(default_factory=list)   # 実行した履歴エントリ
    errors: list = field(default_factory=list)    # (name, message)
    skipped: int = 0

    @property
    def total(self) -> int:
        return len(self.entries)

    def to_dict(self) -> dict:
        return {"done": self.total, "errors": len(self.errors), "skipped": self.skipped,
                "error_messages": [f"{n}: {m}" for n, m in self.errors[:5]]}


def build_context(cfg: dict, now: float | None = None) -> EvalContext:
    return EvalContext(now if now is not None else time.time(), cfg.get("categories") or {})


def auto_rules(cfg: dict) -> list:
    return [r for r in cfg.get("rules", []) if r.get("trigger", "auto") == "auto"]


def rules_for_folder(rules: list, folder: str) -> list:
    """その監視フォルダを対象にするルールだけに絞る（ルールの「対象フォルダ」設定）"""
    return [r for r in rules if rule_applies_to(r, folder)]


# ------------------------------------------------------------------ スキャン / 計画
def scan_folder(folder: str, cfg: dict, now: float, exclusions: dict | None = None,
                reasons: tuple = (EXCLUDE_UNDONE, EXCLUDE_PROCESSED)) -> list[tuple[FileInfo, str]]:
    """フォルダ直下のファイルを列挙。戻り値: [(FileInfo, スキップ理由 or "")]"""
    patterns = [p.casefold() for p in cfg.get("ignore_patterns", [])]
    settle = cfg.get("settle_sec", 30)
    skip_hidden = cfg.get("skip_hidden", True)
    exclusions = exclusions or {}
    out = []
    with os.scandir(folder) as it:
        for entry in it:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                info = FileInfo.from_entry(entry)
            except OSError:
                continue
            lname = info.name.casefold()
            reason = ""
            if any(fnmatch.fnmatchcase(lname, p) for p in patterns):
                reason = "除外パターンに一致"
            elif skip_hidden and info.attrs & (winapi.FILE_ATTRIBUTE_HIDDEN | winapi.FILE_ATTRIBUTE_SYSTEM):
                reason = "隠し/システムファイル"
            elif settle and now - max(info.mtime, info.ctime) < settle:
                reason = f"更新直後のため待機中（{settle}秒）"
            elif exclusions:
                why = store.excluded_reason(exclusions, info.path, info.size, info.mtime)
                if why in reasons:
                    reason = ("元に戻したファイル（再仕分けしません）" if why == EXCLUDE_UNDONE
                              else "処理済み（内容が変わるまで再処理しません）")
            out.append((info, reason))
    out.sort(key=lambda x: x[0].name.casefold())
    return out


def plan_file(info: FileInfo, folder: str, rules: list, ctx: EvalContext, trace: bool = False) -> PlanItem:
    rule, traces = find_matching_rule(rules, info, ctx, trace)
    if rule is None:
        return PlanItem(folder, info, "nomatch", reason="どのルールにも一致しません", traces=traces)
    steps = rule.get("actions") or []
    if any(s.get("type") == "ignore" for s in steps):
        return PlanItem(folder, info, "ignore", rule, reason="除外ルールに一致（何もしません）", traces=traces)
    if not steps:
        return PlanItem(folder, info, "error", rule, reason="動作が設定されていません", traces=traces)
    pv = actions.preview(rule, info, folder, ctx.categories)
    if pv and pv[0].stop:
        return PlanItem(folder, info, "skip", rule, pv, pv[0].note, traces)
    if pv and all(p.note.endswith("何もしません") for p in pv):
        return PlanItem(folder, info, "skip", rule, pv, pv[0].note, traces)
    notes = "　".join(p.note for p in pv if p.note)
    return PlanItem(folder, info, "run", rule, pv, notes, traces)


def plan_folder(folder: str, cfg: dict, now: float | None = None, rules: list | None = None,
                trace: bool = False, exclusions: dict | None = None,
                reasons: tuple = (EXCLUDE_UNDONE, EXCLUDE_PROCESSED)) -> list[PlanItem]:
    ctx = build_context(cfg, now)
    rules = rules_for_folder(auto_rules(cfg) if rules is None else rules, folder)
    if exclusions is None:
        exclusions = store.load_exclusions()
    items = []
    for info, reason in scan_folder(folder, cfg, ctx.now, exclusions, reasons):
        if reason:
            items.append(PlanItem(folder, info, "skip", reason=reason))
        else:
            items.append(plan_file(info, folder, rules, ctx, trace))
    return items


# ------------------------------------------------------------------ 実行
def run_once(cfg: dict, rule_id: str | None = None, progress=None) -> RunSummary:
    """全監視フォルダを1回処理する（プロセス間ロック付き）。

    rule_id 指定時はそのルールだけを実行（クイック操作。手動ルール・無効ルールも実行し、
    「処理済み」の記録は無視する）。progress(done, total, name) で進捗を通知する。
    """
    summary = RunSummary()
    history: list = []
    processed: list = []
    if rule_id:
        rules = [dict(r, enabled=True) for r in cfg.get("rules", []) if r.get("id") == rule_id]
        if not rules:
            summary.errors.append(("ルール", "指定したルールが見つかりません"))
            return summary
        reasons = (EXCLUDE_UNDONE,)
    else:
        rules = auto_rules(cfg)
        reasons = (EXCLUDE_UNDONE, EXCLUDE_PROCESSED)
    cats = cfg.get("categories") or {}
    with winapi.NamedLock(LOCK_RUN):
        store.prune_exclusions()
        exclusions = store.load_exclusions()
        now = time.time()
        targets: list[PlanItem] = []
        for folder in watch_folders(cfg):
            if not os.path.isdir(folder):
                summary.errors.append((folder, "監視フォルダが見つかりません"))
                continue
            try:
                items = plan_folder(folder, cfg, now, rules=rules, exclusions=exclusions, reasons=reasons)
            except OSError as e:
                summary.errors.append((folder, str(e)))
                continue
            targets += [it for it in items if it.status == "run"]
        total = len(targets)
        if progress:
            progress(0, total, "")
        for i, it in enumerate(targets):
            if progress:
                progress(i, total, it.info.name)
            if not os.path.exists(it.info.path):
                continue
            entry = actions.execute(it.rule, it.info, it.folder, cats)
            if entry["steps"]:
                history.append(entry)
                summary.entries.append(entry)
            if entry["error"]:
                summary.errors.append((it.info.name, entry["error"]))
            elif entry["skipped"]:
                summary.skipped += 1
            else:
                final = entry["final"]
                if final and os.path.exists(final) and actions.same_dir(os.path.dirname(final), it.folder):
                    processed.append(final)
        if progress:
            progress(total, total, "")
        if history:
            store.append_history(history, cfg.get("history_limit", 1000))
        if processed:
            store.add_exclusions(processed, EXCLUDE_PROCESSED)
    return summary


# ------------------------------------------------------------------ 元に戻す
def undo(entry_ids: list[str]) -> tuple[int, list[str]]:
    """履歴を元に戻す。戻したファイルは再仕分けの対象外にする。戻り値: (件数, メッセージ)"""
    ids = set(entry_ids)
    ok = 0
    messages: list[str] = []
    restored: list[str] = []
    with winapi.NamedLock(LOCK_RUN):
        hist = store.load_history()
        for e in hist:
            if e.get("id") not in ids or e.get("undone"):
                continue
            if not actions.entry_undoable(e):
                messages.append(f"{e.get('name')}: ごみ箱へ送ったファイルはごみ箱から復元してください")
                continue
            try:
                path, notes = actions.undo_entry(e)
            except (actions.StepError, OSError, KeyError) as ex:
                messages.append(f"{e.get('name')}: {ex}")
                continue
            e["undone"] = True
            e["undone_time"] = time.time()
            if path:
                e["restored_to"] = path
                restored.append(path)
            messages += [f"{e.get('name')}: {n}" for n in dict.fromkeys(notes)]
            ok += 1
        store.save_history(hist)
        if restored:
            store.add_exclusions(restored, EXCLUDE_UNDONE)
    return ok, messages
