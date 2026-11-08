"""判定トレース（どのルールのどの条件・グループが一致/不一致だったか）の表示用テキスト生成"""
from __future__ import annotations

from ..config import rule_applies_to
from ..engine import PlanItem
from ..rules import NodeResult, describe_condition, group_label
from ..util import fmt_age, fmt_datetime, fmt_size


def file_header(item: PlanItem, now: float) -> list:
    i = item.info
    chunks = [(f"{i.name}\n", "h"),
              (f"サイズ {fmt_size(i.size)}　｜　更新 {fmt_datetime(i.mtime)}（{fmt_age(now - i.mtime)}）"
               f"　｜　作成 {fmt_datetime(i.ctime)}\n", "sub")]
    urls = [u for u in (i.urls or ("", "")) if u]
    if urls:
        chunks.append((f"ダウンロード元: {' / '.join(urls)}\n", "sub"))
    return chunks


def result_line(item: PlanItem) -> list:
    tag = {"run": "ok", "ignore": "accent", "error": "err"}.get(item.status, "muted")
    s = f"\n結果: {item.label}"
    if item.rule:
        s += f"（ルール「{item.rule.get('name')}」）"
    out = [(s + "\n", tag)]
    for n, p in enumerate(item.steps, 1):
        out.append((f"  {n}. {p.text([item.folder])}\n", "ng" if p.stop else None))
    if item.reason and not item.steps:
        out.append((f"{item.reason}\n", "sub"))
    return out


def node_lines(nr: NodeResult, categories: dict, indent: int) -> list:
    pad = " " * indent
    mark = "✔" if nr.ok else "✘"
    tag = "ok" if nr.ok else "ng"
    if nr.children is not None:  # グループ
        out = [(f"{pad}{mark} ", tag), (f"［{group_label(nr.node)}］", "accent")]
        out.append(("（空のグループ）\n" if nr.error else "\n", "sub"))
        for child in nr.children:
            out += node_lines(child, categories, indent + 4)
        rest = len(nr.node.get("items") or []) - len(nr.children)
        if rest > 0:
            out.append((f"{pad}    （残り {rest} 件は結果が確定したため判定していません）\n", "muted"))
        return out
    out = [(f"{pad}{mark} ", tag), (describe_condition(nr.node, categories), None)]
    if nr.error:
        out.append((f"　⚠ {nr.error}\n", "err"))
    else:
        out.append((f"　… 実際: {nr.actual}\n" if nr.actual else "\n", "sub"))
    return out


def rules_trace(traces: list, rules: list, categories: dict, matched_rule: dict | None,
                highlight_id: str | None = None, folder: str | None = None) -> list:
    """traces は find_matching_rule(trace=True) の結果。判定されなかった残りのルールも表示する。"""
    out = [("\nルールの判定（上から順に評価し、最初に一致したルールを適用）\n", "h")]
    by_id = {t.rule.get("id"): t for t in traces}
    for n, rule in enumerate(rules, 1):
        name = rule.get("name", "")
        mark = " ◀ 編集中" if highlight_id and rule.get("id") == highlight_id else ""
        t = by_id.get(rule.get("id"))
        if t is None:
            if folder and not rule_applies_to(rule, folder):
                why = "このフォルダは対象外のルールです"
            elif rule.get("trigger") == "manual":
                why = "手動のみのルールです（クイック操作で実行）"
            elif matched_rule:
                why = f"先に「{matched_rule.get('name')}」に一致したため判定しません"
            else:
                why = "判定していません"
            out.append((f"{n:>2}. – {name}{mark}　（{why}）\n", "muted"))
            continue
        if t.note == "無効":
            out.append((f"{n:>2}. – {name}{mark}　（無効）\n", "muted"))
            continue
        if t.note:
            out.append((f"{n:>2}. ✘ {name}{mark}　（{t.note}）\n", "ng"))
            continue
        out.append((f"{n:>2}. {'✔' if t.matched else '✘'} {name}{mark}\n", "ok" if t.matched else "ng"))
        if t.root:
            out += node_lines(t.root, categories, 6)
    return out
