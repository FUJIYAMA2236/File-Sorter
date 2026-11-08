"""ルール編集ダイアログ（入れ子の条件グループ・複数の動作・その場でテスト）"""
from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, ttk

from .. import actions, engine, store
from ..config import folder_key, folder_label, normalize_rule, resolve_folder, rule_applies_to, watch_folders
from ..rules import (AGE_UNITS, COMMAND_PLACEHOLDERS, CONFLICTS, FIELD_KINDS, FIELD_LABELS, FIELDS, MAX_DEPTH,
                     PLACEHOLDERS, SIZE_UNITS, STEP_DEFAULTS, STEP_TYPES, TRIGGERS, ZIP_GROUP_LABELS, ZIP_GROUPS,
                     FileInfo, RuleResult,
                     describe_condition, evaluate_rule, group_label, is_group, new_condition, new_group, ops_for,
                     validate_rule, zip_template)
from ..util import display_path, elide_middle
from . import trace
from .widgets import ScrollFrame, Tooltip, card, make_text, make_tree, set_text

AGE_LABELS = {k: v[0] for k, v in AGE_UNITS.items()}
AGE_KEYS = {v[0]: k for k, v in AGE_UNITS.items()}
DEPTH_COLORS = ["accent", "#a78bfa", "#f59e0b", "#34d399", "#f472b6", "#60a5fa"]
STEP_KEYS = [k for k, _, _ in STEP_TYPES]
STEP_NAMES = [label for _, label, _ in STEP_TYPES]
CONFLICT_KEYS = {label: k for k, label in CONFLICTS}
ZIP_GROUP_BY_LABEL = {label: k for k, label, _ in ZIP_GROUPS}
# ZIP のファイル名の例（まとめ方ごと）。名前欄の右に「＋ 2026-09.zip」のように表示する
ZIP_NAME_EXAMPLES = {"month": "2026-09", "cmonth": "2026-09", "day": "2026-09-15", "year": "2026", "ext": "png",
                     "category": "画像", "all": "", "each": "(元のファイル名)"}
ZIP_HINTS = {
    "month": "更新日時の年月ごとに別のZIPへ入れます。ダウンロードやコピーをしても変わりにくい日時なので、通常はこれがおすすめです。",
    "cmonth": "作成日時の年月ごとに別のZIPへ入れます。別のドライブからコピーしたファイルは、コピーした日が作成日時になります。",
    "day": "更新日時の日付ごとに別のZIPへ入れます。",
    "year": "更新日時の年ごとに別のZIPへ入れます。",
    "ext": "拡張子ごとに別のZIPへ入れます（png.zip、pdf.zip など）。",
    "category": "設定の「種類」ごとに別のZIPへ入れます（画像.zip、文書.zip など）。",
    "all": "条件に合ったファイルをすべて1つのZIPに入れます。名前が空ならルール名を使います。",
    "each": "ファイルごとに、同じ名前のZIPを1つずつ作ります。",
    "custom": "保存先のパスを差し込みで自由に指定します（例: アーカイブ\\{yyyy}\\{mm}.zip）。",
}


def tree_text(node: dict, categories: dict, prefix: str = "", last: bool = True, top: bool = True) -> list[str]:
    """条件の構造をツリー状の文字列にする（プレビュー用）"""
    branch = "" if top else ("└ " if last else "├ ")
    if is_group(node):
        lines = [prefix + branch + group_label(node)]
        items = node.get("items") or []
        child_prefix = prefix + ("" if top else ("   " if last else "│  "))
        if not items:
            lines.append(child_prefix + "└ （空のグループ）")
        for i, it in enumerate(items):
            lines += tree_text(it, categories, child_prefix, i == len(items) - 1, False)
        return lines
    return [prefix + branch + describe_condition(node, categories)]


# ======================================================================== 条件
class ConditionRow(ttk.Frame):
    """1つの条件： [項目] が [値] [演算子]  □否定  ✕"""

    def __init__(self, master, editor: "RuleEditor", parent: "GroupBlock", cond: dict):
        super().__init__(master)
        self.editor = editor
        self.parent_block = parent
        t = editor.theme
        self.cats = editor.categories
        self.cat_keys = list(self.cats)
        self.cat_labels = [self.cats[k]["label"] for k in self.cat_keys]
        self.field_keys = [k for k, _, _ in FIELDS]
        self.field_labels = [label for _, label, _ in FIELDS]
        self.v_field = tk.StringVar()
        self.v_op = tk.StringVar()
        self.v_value = tk.StringVar()
        self.v_value2 = tk.StringVar()
        self.v_unit = tk.StringVar()
        self.v_neg = tk.BooleanVar()
        self.field = "name"

        self.cb_field = ttk.Combobox(self, textvariable=self.v_field, values=self.field_labels,
                                     state="readonly", width=24)
        self.val = ttk.Frame(self)
        self.cb_op = ttk.Combobox(self, textvariable=self.v_op, state="readonly", width=17)
        chk = ttk.Checkbutton(self, text="否定", variable=self.v_neg, command=editor.changed)
        Tooltip(chk, "チェックすると「この条件に一致しない」ファイルが対象になります", t)
        btn = ttk.Button(self, text=t.icon("close", "✕"), style="Icon.TButton", command=lambda: parent.remove_child(self))
        Tooltip(btn, "この条件を削除", t)
        self.cb_field.grid(row=0, column=0, sticky="w")
        ttk.Label(self, text="が").grid(row=0, column=1, padx=6)
        self.val.grid(row=0, column=2, sticky="ew")
        self.cb_op.grid(row=0, column=3, padx=(8, 0))
        chk.grid(row=0, column=4, padx=(10, 0))
        btn.grid(row=0, column=5, padx=(6, 0))
        self.columnconfigure(2, weight=1)
        self.cb_field.bind("<<ComboboxSelected>>", self._on_field)
        self.cb_op.bind("<<ComboboxSelected>>", self._on_op)
        for v in (self.v_value, self.v_value2):
            v.trace_add("write", lambda *a: editor.changed())
        self.set(cond)

    def set(self, c: dict):
        self.field = c.get("field", "name")
        self.v_field.set(FIELD_LABELS.get(self.field, self.field_labels[0]))
        self._set_ops(c.get("op"))
        self.v_value.set(c.get("value", ""))
        self.v_value2.set(c.get("value2", ""))
        self.v_unit.set(c.get("unit", ""))
        self.v_neg.set(bool(c.get("negate")))
        self._build_value()

    def get(self) -> dict:
        kind = FIELD_KINDS[self.field]
        ops = ops_for(self.field)
        labels = [label for _, label in ops]
        op = ops[labels.index(self.v_op.get())][0] if self.v_op.get() in labels else ops[0][0]
        return {"type": "cond", "field": self.field, "op": op, "value": self.v_value.get().strip(),
                "value2": self.v_value2.get().strip() if kind == "size" and op == "between" else "",
                "unit": self.v_unit.get() if kind in ("size", "age") else "", "negate": bool(self.v_neg.get())}

    def _set_ops(self, op_key):
        ops = ops_for(self.field)
        self.cb_op["values"] = [label for _, label in ops]
        self.v_op.set(dict(ops).get(op_key, ops[0][1]))
        self.cb_op.configure(state="disabled" if len(ops) == 1 else "readonly")

    def _on_field(self, _e=None):
        old_kind = FIELD_KINDS.get(self.field)
        self.field = self.field_keys[self.field_labels.index(self.v_field.get())]
        kind = FIELD_KINDS[self.field]
        self._set_ops(None)
        if kind != old_kind:
            self.v_value2.set("")
            if kind == "category":
                self.v_value.set(self.cat_keys[0] if self.cat_keys else "")
            elif kind == "size":
                self.v_value.set("10")
                self.v_unit.set("MB")
            elif kind == "age":
                self.v_value.set("30")
                self.v_unit.set("day")
            else:
                self.v_value.set("")
        self._build_value()
        self.editor.changed()

    def _on_op(self, _e=None):
        self._build_value()
        self.editor.changed()

    def _build_value(self):
        for w in self.val.winfo_children():
            w.destroy()
        kind = FIELD_KINDS[self.field]
        op = self.get()["op"]
        if kind in ("text", "ext"):
            e = ttk.Entry(self.val, textvariable=self.v_value)
            e.pack(fill="x", expand=True)
            hint = {"ext": "カンマ区切り（例: pdf, docx, tar.gz）",
                    "text": "| で区切るといずれかに一致（例: 請求書|invoice）"}[kind]
            if op == "regex":
                hint = "Python の正規表現（大文字小文字は区別しません）"
            elif op == "wildcard":
                hint = "* = 任意の文字列、? = 任意の1文字（例: IMG_*.jpg）"
            Tooltip(e, hint, self.editor.theme)
        elif kind == "category":
            v = tk.StringVar(value=self.cats.get(self.v_value.get(), {}).get("label", ""))
            cb = ttk.Combobox(self.val, textvariable=v, values=self.cat_labels, state="readonly", width=16)
            cb.pack(side="left")

            def pick(_e=None):
                self.v_value.set(self.cat_keys[self.cat_labels.index(v.get())])
            cb.bind("<<ComboboxSelected>>", pick)
        elif kind == "size":
            ttk.Entry(self.val, textvariable=self.v_value, width=8).pack(side="left")
            if op == "between":
                ttk.Label(self.val, text="〜").pack(side="left", padx=4)
                ttk.Entry(self.val, textvariable=self.v_value2, width=8).pack(side="left")
            if self.v_unit.get() not in SIZE_UNITS:
                self.v_unit.set("MB")
            cb = ttk.Combobox(self.val, textvariable=self.v_unit, values=list(SIZE_UNITS), state="readonly", width=5)
            cb.pack(side="left", padx=(6, 0))
            cb.bind("<<ComboboxSelected>>", lambda e: self.editor.changed())
        elif kind == "age":
            ttk.Entry(self.val, textvariable=self.v_value, width=8).pack(side="left")
            if self.v_unit.get() not in AGE_UNITS:
                self.v_unit.set("day")
            v = tk.StringVar(value=AGE_LABELS[self.v_unit.get()])
            cb = ttk.Combobox(self.val, textvariable=v, values=list(AGE_KEYS), state="readonly", width=6)
            cb.pack(side="left", padx=(6, 0))

            def pick_unit(_e=None):
                self.v_unit.set(AGE_KEYS[v.get()])
                self.editor.changed()
            cb.bind("<<ComboboxSelected>>", pick_unit)
        else:
            ttk.Label(self.val, text="（すべてのファイルに一致します）", style="Sub.TLabel").pack(side="left")


class GroupBlock(ttk.Frame):
    """条件グループ：［すべて/いずれか］に一致（否定可）。中に条件と子グループを並べる。"""

    def __init__(self, master, editor: "RuleEditor", node: dict, depth: int = 0, parent: "GroupBlock" = None):
        super().__init__(master)
        self.editor = editor
        self.depth = depth
        self.parent_block = parent
        t = editor.theme
        color = DEPTH_COLORS[depth % len(DEPTH_COLORS)]
        color = t.c(color) if color == "accent" else color
        tk.Frame(self, bg=color, width=3).pack(side="left", fill="y", padx=(0, 10))
        body = ttk.Frame(self)
        body.pack(side="left", fill="x", expand=True)

        head = ttk.Frame(body)
        head.pack(fill="x")
        self.v_match = tk.StringVar(value="いずれか" if node.get("match") == "any" else "すべて")
        cb = ttk.Combobox(head, textvariable=self.v_match, values=["すべて", "いずれか"], state="readonly", width=8)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: editor.changed())
        ttk.Label(head, text="の条件に一致" if depth == 0 else "に一致するグループ").pack(side="left", padx=(6, 0))
        self.v_neg = tk.BooleanVar(value=bool(node.get("negate")))
        chk = ttk.Checkbutton(head, text="否定", variable=self.v_neg, command=editor.changed)
        chk.pack(side="left", padx=(12, 0))
        Tooltip(chk, "グループ全体の結果を反転します（例: NOT（A または B））", t)
        if parent is not None:
            btn = ttk.Button(head, text=t.icon("close", "✕"), style="Icon.TButton",
                             command=lambda: parent.remove_child(self))
            btn.pack(side="right")
            Tooltip(btn, "このグループを削除", t)

        self.items = ttk.Frame(body)
        self.items.pack(fill="x", pady=(6, 0))
        self.children_: list = []
        for it in node.get("items") or []:
            if is_group(it):
                self.add_group(it, notify=False)
            else:
                self.add_condition(it, notify=False)

        foot = ttk.Frame(body)
        foot.pack(fill="x", pady=(4, 2))
        ttk.Button(foot, text="＋ 条件", style="Small.TButton", command=self.add_condition).pack(side="left")
        gbtn = ttk.Button(foot, text="＋ グループ", style="Small.TButton", command=self.add_group)
        gbtn.pack(side="left", padx=(6, 0))
        if depth + 1 >= MAX_DEPTH:
            gbtn.configure(state="disabled")
        Tooltip(gbtn, "「すべて/いずれか」を切り替えられる入れ子のグループを追加します", t)

    def add_condition(self, cond: dict | None = None, notify: bool = True):
        if not isinstance(cond, dict):
            cond = new_condition()
        row = ConditionRow(self.items, self.editor, self, cond)
        row.pack(fill="x", pady=3)
        self.children_.append(row)
        if notify:
            self.editor.changed()

    def add_group(self, node: dict | None = None, notify: bool = True):
        if not isinstance(node, dict):
            node = new_group("any", [new_condition()])
        block = GroupBlock(self.items, self.editor, node, self.depth + 1, self)
        block.pack(fill="x", pady=(6, 6))
        self.children_.append(block)
        if notify:
            self.editor.changed()

    def remove_child(self, child):
        if child in self.children_:
            self.children_.remove(child)
            child.destroy()
            self.editor.changed()

    def get(self) -> dict:
        return {"type": "group", "match": "any" if self.v_match.get() == "いずれか" else "all",
                "negate": bool(self.v_neg.get()), "items": [c.get() for c in self.children_]}


# ======================================================================== 動作
class ActionRow(ttk.Frame):
    def __init__(self, master, editor: "RuleEditor", step: dict):
        super().__init__(master, style="Card.TFrame", padding=(12, 10))
        self.editor = editor
        t = editor.theme
        self.v_type = tk.StringVar()
        self.v_target = tk.StringVar()
        self.v_conflict = tk.StringVar()
        self.v_remove = tk.BooleanVar()
        self.v_group = tk.StringVar()    # ZIP: まとめ方（表示名）
        self.v_folder = tk.StringVar()   # ZIP: 保存先フォルダ
        self.v_prefix = tk.StringVar()   # ZIP: ファイル名の先頭
        head = ttk.Frame(self)
        head.pack(fill="x")
        self.num = ttk.Label(head, text="", style="Strong.TLabel", width=3)
        self.num.pack(side="left")
        cb = ttk.Combobox(head, textvariable=self.v_type, values=STEP_NAMES, state="readonly", width=20)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_type())
        for icon, fb, tip, cmd in (("close", "✕", "この動作を削除", lambda: editor.remove_action(self)),
                                   ("down", "↓", "後ろへ", lambda: editor.move_action(self, 1)),
                                   ("up", "↑", "前へ", lambda: editor.move_action(self, -1))):
            b = ttk.Button(head, text=t.icon(icon, fb), style="Icon.TButton", command=cmd)
            b.pack(side="right", padx=(4, 0))
            Tooltip(b, tip, t)
        self.params = ttk.Frame(self)
        self.params.pack(fill="x", pady=(8, 0))
        self.preview = ttk.Label(self, text="", style="Sub.TLabel", wraplength=700, justify="left")
        self.preview.pack(anchor="w", pady=(6, 0))
        for v in (self.v_target, self.v_conflict, self.v_folder, self.v_prefix):
            v.trace_add("write", lambda *a: editor.changed())
        self.set(step)

    @property
    def zip_group(self) -> str:
        return ZIP_GROUP_BY_LABEL.get(self.v_group.get(), "month")

    @property
    def type(self) -> str:
        name = self.v_type.get()
        return STEP_KEYS[STEP_NAMES.index(name)] if name in STEP_NAMES else "move"

    def set(self, step: dict):
        t = step.get("type", "move")
        self.v_type.set(STEP_NAMES[STEP_KEYS.index(t)])
        d = STEP_DEFAULTS[t]
        self.v_target.set(step.get("target", d.get("target", "")))
        self.v_conflict.set(dict(CONFLICTS)[step.get("conflict", d.get("conflict", "rename"))])
        self.v_remove.set(bool(step.get("remove_original", d.get("remove_original", False))))
        self._set_zip_fields(step if t == "zip" else STEP_DEFAULTS["zip"])
        self._build()

    def _set_zip_fields(self, step: dict):
        d = STEP_DEFAULTS["zip"]
        self.v_group.set(ZIP_GROUP_LABELS.get(step.get("group") or d["group"], ZIP_GROUP_LABELS[d["group"]]))
        self.v_folder.set(step.get("folder", d["folder"]))
        self.v_prefix.set(step.get("prefix", d["prefix"]))

    def get(self) -> dict:
        t = self.type
        d = STEP_DEFAULTS[t]
        out = {"type": t}
        if "target" in d:
            out["target"] = self.v_target.get().strip()
        if "conflict" in d:
            out["conflict"] = CONFLICT_KEYS.get(self.v_conflict.get(), "rename")
        if "remove_original" in d:
            out["remove_original"] = bool(self.v_remove.get())
        if t == "zip":
            out.update(group=self.zip_group, folder=self.v_folder.get().strip(), prefix=self.v_prefix.get())
        return out

    def _on_type(self):
        d = STEP_DEFAULTS[self.type]
        self.v_target.set(d.get("target", ""))
        self.v_remove.set(bool(d.get("remove_original", False)))
        self._set_zip_fields(d if self.type == "zip" else STEP_DEFAULTS["zip"])
        self._build()
        self.editor.changed()

    def _on_zip_group(self):
        group = self.zip_group
        prev = getattr(self, "_zip_group_shown", group)
        if group == "custom" and prev != "custom" and not self.v_target.get().strip():
            # 自由指定に切り替えたら、それまでの設定で決まる保存先を初期値にする
            self.v_target.set(zip_template({"group": prev, "folder": self.v_folder.get(),
                                            "prefix": self.v_prefix.get()}))
        self.after_idle(self._build)  # コンボボックス自身も作り直すため、イベント処理の後で
        self.editor.changed()

    def _placeholder_button(self, parent, entry, items):
        mb = ttk.Menubutton(parent, text="差し込み")
        menu = tk.Menu(mb, tearoff=False)
        for ph, desc in items:
            menu.add_command(label=f"{ph}　{desc}", command=lambda p=ph: (entry.insert("insert", p), entry.focus_set()))
        mb["menu"] = menu
        return mb

    def _build(self):
        for w in self.params.winfo_children():
            w.destroy()
        t = self.type
        p = self.params
        if t == "zip":
            self._build_zip(p)
            return
        label = {"move": "移動先フォルダ", "copy": "コピー先フォルダ", "rename": "新しい名前",
                 "extract": "展開先フォルダ", "command": "コマンド"}.get(t)
        if label:
            ttk.Label(p, text=label, width=12).grid(row=0, column=0, sticky="w")
            e = ttk.Entry(p, textvariable=self.v_target)
            e.grid(row=0, column=1, sticky="ew", padx=(8, 0))
            col = 2
            if t in ("move", "copy", "extract"):
                ttk.Button(p, text="参照…", command=self._browse).grid(row=0, column=col, padx=(6, 0))
                col += 1
            self._placeholder_button(p, e, COMMAND_PLACEHOLDERS if t == "command" else PLACEHOLDERS).grid(
                row=0, column=col, padx=(6, 0))
            p.columnconfigure(1, weight=1)
        if t in ("move", "copy", "rename"):
            ttk.Label(p, text="同名ファイル", width=12).grid(row=1, column=0, sticky="w", pady=(6, 0))
            ttk.Combobox(p, textvariable=self.v_conflict, values=[lbl for _, lbl in CONFLICTS], state="readonly",
                         width=24).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        if t == "extract":
            ttk.Checkbutton(p, text="展開した後、元のZIPをごみ箱へ送る", variable=self.v_remove,
                            command=self.editor.changed).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        hints = {
            "move": "相対パスは監視フォルダからの位置（例: 画像、文書\\{yyyy}）。",
            "copy": "元のファイルは残し、同じ内容のまま再処理しないよう記録します。",
            "rename": "例: {yyyy}{mm}{dd}_{name}（拡張子も含めて指定。{stem}.{ext} でも可）",
            "extract": "ZIP形式のみ対応。例: {stem} で「ZIPと同じ名前のフォルダ」に展開。",
            "recycle": "一致したファイルをごみ箱へ送ります（ごみ箱から復元できます）。",
            "command": '例: "C:\\Program Files\\7-Zip\\7z.exe" x "{path}" -o"{dir}\\{stem}"　（最大5分、ウィンドウ非表示で実行）',
            "ignore": "一致したファイルは何もせず、以降のルールも適用しません（例外の指定に使います）。",
        }
        ttk.Label(p, text=hints[t], style="Sub.TLabel", wraplength=700, justify="left").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_zip(self, p):
        """ZIP: 何を（条件に合ったファイル）・どの単位で・どこへ まとめるか"""
        group = self._zip_group_shown = self.zip_group
        ttk.Label(p, text="対象", width=12).grid(row=0, column=0, sticky="w")
        ttk.Label(p, text="このルールの条件に合ったファイル（1回の実行で見つかった分をまとめて入れます）").grid(
            row=0, column=1, columnspan=3, sticky="w", padx=(8, 0))
        ttk.Label(p, text="まとめ方", width=12).grid(row=1, column=0, sticky="w", pady=(6, 0))
        cb = ttk.Combobox(p, textvariable=self.v_group, values=[lbl for _, lbl, _ in ZIP_GROUPS], state="readonly",
                          width=24)
        cb.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_zip_group())
        if group == "custom":
            ttk.Label(p, text="ZIPファイル", width=12).grid(row=2, column=0, sticky="w", pady=(6, 0))
            e = ttk.Entry(p, textvariable=self.v_target)
            e.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
            ttk.Button(p, text="参照…", command=self._browse).grid(row=2, column=2, padx=(6, 0), pady=(6, 0))
            self._placeholder_button(p, e, PLACEHOLDERS).grid(row=2, column=3, padx=(6, 0), pady=(6, 0))
            row = 3
        else:
            ttk.Label(p, text="保存先フォルダ", width=12).grid(row=2, column=0, sticky="w", pady=(6, 0))
            e = ttk.Entry(p, textvariable=self.v_folder)
            e.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
            ttk.Button(p, text="参照…", command=lambda: self._browse(self.v_folder)).grid(
                row=2, column=2, padx=(6, 0), pady=(6, 0))
            self._placeholder_button(p, e, PLACEHOLDERS).grid(row=2, column=3, padx=(6, 0), pady=(6, 0))
            ttk.Label(p, text="ZIPの名前", width=12).grid(row=3, column=0, sticky="w", pady=(6, 0))
            nf = ttk.Frame(p)
            nf.grid(row=3, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(6, 0))
            e2 = ttk.Entry(nf, textvariable=self.v_prefix, width=24)
            e2.pack(side="left")
            example = ZIP_NAME_EXAMPLES.get(group, "")
            ttk.Label(nf, text=(f"＋ {example}.zip" if example else ".zip（空ならルール名）") +
                      ("　← 先頭に付ける文字（空でも可）" if example else ""), style="Sub.TLabel").pack(side="left", padx=(6, 0))
            row = 4
        ttk.Checkbutton(p, text="ZIPに入れた後、元のファイルをごみ箱へ送る", variable=self.v_remove,
                        command=self.editor.changed).grid(row=row, column=1, columnspan=3, sticky="w", padx=(8, 0),
                                                          pady=(6, 0))
        ttk.Label(p, text=ZIP_HINTS.get(group, "") + "　既にあるZIPには追加します。", style="Sub.TLabel",
                  wraplength=700, justify="left").grid(row=row + 1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        p.columnconfigure(1, weight=1)

    def _browse(self, var: tk.StringVar | None = None):
        base = self.editor.base_folder()
        if self.type == "zip" and var is None:
            path = filedialog.asksaveasfilename(parent=self.editor, title="ZIPファイル", defaultextension=".zip",
                                                initialdir=base or None, filetypes=[("ZIP", "*.zip")],
                                                confirmoverwrite=False)
        else:
            path = filedialog.askdirectory(parent=self.editor, title="フォルダを選択", initialdir=base or None)
        if not path:
            return
        path = os.path.normpath(path)
        try:
            rel = os.path.relpath(path, base)
            if base and not rel.startswith(".."):
                path = rel
        except ValueError:
            pass
        (var or self.v_target).set(path)

    def set_number(self, n: int):
        self.num.configure(text=f"{n}.")

    def update_preview(self, text: str):
        self.preview.configure(text=text)


# ======================================================================== ダイアログ
class RuleEditor(tk.Toplevel):
    def __init__(self, app, rule: dict | None, on_save):
        super().__init__(app.root)
        self.withdraw()
        self.app = app
        self.theme = t = app.theme
        self.on_save = on_save
        self.is_new = rule is None
        self.rule = rule or normalize_rule({
            "name": "新しいルール", "enabled": True,
            "conditions": new_group("all", [new_condition("ext", "in", "")]),
            "actions": [{"type": "move", "target": ""}],
        })
        self.categories = app.cfg.get("categories", {})
        self.action_rows: list[ActionRow] = []
        self._test_rows = {}
        self._after = None
        self._ready = False

        self.title("ルールの追加" if self.is_new else f"ルールの編集 — {self.rule['name']}")
        self.configure(bg=t.c("bg"))
        self.transient(app.root)

        ttk.Separator(self).pack(side="bottom", fill="x")
        footer = ttk.Frame(self, padding=(24, 12))
        footer.pack(side="bottom", fill="x")
        self.err_label = ttk.Label(footer, text="", style="Err.TLabel", wraplength=620, justify="left")
        self.err_label.pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="保存", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(footer, text="キャンセル", command=self.destroy).pack(side="right", padx=(0, 8))

        sf = ScrollFrame(self, t)
        sf.pack(fill="both", expand=True)
        c = ttk.Frame(sf.body, padding=(28, 22, 28, 22))
        c.pack(fill="both", expand=True)
        ttk.Label(c, text="ルールの追加" if self.is_new else "ルールの編集", style="Title.TLabel").pack(anchor="w")

        # ---- 基本
        c1 = card(c)
        c1.pack(fill="x", pady=(16, 0))
        ttk.Label(c1, text="ルール名").grid(row=0, column=0, sticky="w")
        self.v_name = tk.StringVar(value=self.rule["name"])
        self.e_name = ttk.Entry(c1, textvariable=self.v_name, width=40)
        self.e_name.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.v_enabled = tk.BooleanVar(value=self.rule["enabled"])
        ttk.Checkbutton(c1, text="このルールを有効にする", variable=self.v_enabled,
                        style="Switch.TCheckbutton").grid(row=0, column=2, sticky="e", padx=(24, 0))
        ttk.Label(c1, text="実行のしかた").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.v_trigger = tk.StringVar(value=self.rule.get("trigger", "auto"))
        tr = ttk.Frame(c1)
        tr.grid(row=1, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(10, 0))
        for key, label in TRIGGERS:
            ttk.Radiobutton(tr, text=label, value=key, variable=self.v_trigger, command=self.changed).pack(
                side="left", padx=(0, 18))
        self._build_scope(c1, row=2)
        c1.columnconfigure(2, weight=1)

        # ---- 条件
        c2 = card(c)
        c2.pack(fill="x", pady=(12, 0))
        ttk.Label(c2, text="条件", style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(c2, text="「＋ グループ」で（A または B）のような入れ子の条件を作れます。大文字・小文字は区別しません。",
                  style="Sub.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        self.root_group = GroupBlock(c2, self, self.rule["conditions"], 0)
        self.root_group.grid(row=2, column=0, sticky="new")
        pv = ttk.Frame(c2, style="Card.TFrame", padding=(12, 10))
        pv.grid(row=2, column=1, sticky="nsew", padx=(16, 0))
        ttk.Label(pv, text="条件の構造", style="Strong.TLabel").pack(anchor="w")
        self.structure = tk.Label(pv, text="", justify="left", anchor="nw", bg=t.c("bg"), fg=t.c("sub"),
                                  font=t.font("App.Small"), wraplength=300)
        self.structure.pack(fill="both", expand=True, pady=(6, 0))
        c2.columnconfigure(0, weight=3)
        c2.columnconfigure(1, weight=1, minsize=280)

        # ---- 動作
        c3 = card(c)
        c3.pack(fill="x", pady=(12, 0))
        ah = ttk.Frame(c3)
        ah.pack(fill="x")
        ttk.Label(ah, text="動作", style="Subtitle.TLabel").pack(side="left")
        ttk.Label(ah, text="　上から順に実行します（例: 名前を変更 → フォルダへ移動）", style="Sub.TLabel").pack(side="left")
        self.actions_box = ttk.Frame(c3)
        self.actions_box.pack(fill="x", pady=(10, 0))
        for step in self.rule.get("actions") or []:
            self.add_action(step, notify=False)
        mb = ttk.Menubutton(c3, text="＋ 動作を追加")
        menu = tk.Menu(mb, tearoff=False)
        for key, label, _ in STEP_TYPES:
            menu.add_command(label=label, command=lambda k=key: self.add_action({"type": k, **STEP_DEFAULTS[k]}))
        menu.add_separator()
        menu.add_command(label="例: 種類ごとに ZIP 化（元のファイルは残す）",
                         command=lambda: self.add_action({**STEP_DEFAULTS["zip"], "type": "zip", "group": "category",
                                                          "remove_original": False}))
        menu.add_command(label="例: 日付を先頭に付けて名前を変更",
                         command=lambda: self.add_action({"type": "rename", "target": "{yyyy}-{mm}-{dd}_{name}"}))
        mb["menu"] = menu
        mb.pack(anchor="w", pady=(10, 0))

        # ---- テスト
        c4 = card(c)
        c4.pack(fill="both", expand=True, pady=(12, 0))
        th = ttk.Frame(c4)
        th.pack(fill="x")
        ttk.Label(th, text="テスト", style="Subtitle.TLabel").pack(side="left")
        ttk.Button(th, text="このルールを試す", style="Accent.TButton", command=self.run_test).pack(side="left", padx=(14, 0))
        self.v_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(th, text="一致したファイルのみ表示", variable=self.v_only,
                        command=self._render_test).pack(side="left", padx=(14, 0))
        self.v_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(th, text="編集のたびに自動で再テスト", variable=self.v_auto).pack(side="left", padx=(14, 0))
        self.test_summary = ttk.Label(c4, text="監視フォルダ内の実際のファイルに対して判定します（ファイルは動かしません）。",
                                      style="Sub.TLabel")
        self.test_summary.pack(anchor="w", pady=(8, 6))
        tf, self.test_tree = make_tree(c4, [
            ("name", "ファイル名", 230, "w", True),
            ("this", "このルール", 90, "center", False),
            ("applied", "実際に適用されるルール", 200, "w", False),
            ("dest", "このルールでの処理内容 / 理由", 320, "w", True),
        ], height=7)
        tf.pack(fill="both", expand=True)
        self.test_tree.tag_configure("match", foreground=t.c("ok"))
        self.test_tree.tag_configure("other", foreground=t.c("warn"))
        self.test_tree.tag_configure("muted", foreground=t.c("muted"))
        self.test_tree.bind("<<TreeviewSelect>>", self._on_test_select)
        xf, self.trace_text = make_text(c4, t, height=10)
        xf.pack(fill="both", expand=True, pady=(8, 0))

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-Return>", lambda e: self.run_test())

        w, h = 1080, min(920, self.winfo_screenheight() - 90)
        px, py = app.root.winfo_rootx(), app.root.winfo_rooty()
        pw, ph = app.root.winfo_width(), app.root.winfo_height()
        self.geometry(f"{w}x{h}+{max(0, px + (pw - w) // 2)}+{max(0, py + (ph - h) // 2 - 20)}")
        self.minsize(920, 600)
        self.deiconify()
        t.titlebar(self)
        self.grab_set()
        self.e_name.focus_set()
        self.e_name.select_range(0, "end")
        self._ready = True
        self.changed()

    # ---------------------------------------------------------------- 対象フォルダ
    def _build_scope(self, parent, row: int):
        """どの監視フォルダのファイルを対象にするか（監視フォルダが2つ以上のときに選択肢を表示）"""
        entries = list(self.app.cfg.get("watch_folders", []))
        current = self.rule.get("folders") or []
        cur_keys = {folder_key(f) for f in current}
        entry_keys = {folder_key(e) for e in entries}
        extra = [f for f in current if folder_key(f) not in entry_keys]  # 監視フォルダから外れたもの
        self.v_scope = tk.StringVar(value="some" if current else "all")
        self.scope_vars: list[tuple[str, tk.BooleanVar]] = []
        ttk.Label(parent, text="対象フォルダ").grid(row=row, column=0, sticky="nw", pady=(12, 0))
        box = ttk.Frame(parent)
        box.grid(row=row, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(12, 0))
        if len(entries) <= 1 and not current:
            ttk.Label(box, text="すべての監視フォルダ（監視フォルダを2つ以上設定すると、ルールごとに対象を選べます）",
                      style="Sub.TLabel").pack(anchor="w")
            return
        radios = ttk.Frame(box)
        radios.pack(anchor="w")
        for key, label in (("all", "すべての監視フォルダ"), ("some", "選択したフォルダのみ")):
            ttk.Radiobutton(radios, text=label, value=key, variable=self.v_scope,
                            command=self._on_scope).pack(side="left", padx=(0, 18))
        self.scope_checks = []
        for entry in entries + extra:
            v = tk.BooleanVar(value=folder_key(entry) in cur_keys)
            text = f"{folder_label(entry)}　—　{elide_middle(resolve_folder(entry), 70)}"
            if entry in extra:
                text += "　⚠ 監視フォルダにありません"
            chk = ttk.Checkbutton(box, text=text, variable=v, command=self.changed)
            chk.pack(anchor="w", padx=(24, 0), pady=(4, 0))
            self.scope_vars.append((entry, v))
            self.scope_checks.append(chk)
        self._on_scope(notify=False)

    def _on_scope(self, notify: bool = True):
        state = "normal" if self.v_scope.get() == "some" else "disabled"
        for chk in getattr(self, "scope_checks", []):
            chk.configure(state=state)
        if notify:
            self.changed()

    def _scope_folders(self) -> list[str]:
        if self.v_scope.get() != "some":
            return []
        return [entry for entry, v in self.scope_vars if v.get()]

    def _scope_errors(self) -> list[str]:
        if self.v_scope.get() == "some" and not self._scope_folders():
            return ["対象フォルダを1つ以上選択してください。"]
        return []

    # ---------------------------------------------------------------- 動作の並び
    def add_action(self, step: dict, notify: bool = True):
        row = ActionRow(self.actions_box, self, step)
        row.pack(fill="x", pady=(0, 8))
        self.action_rows.append(row)
        self._renumber()
        if notify:
            self.changed()

    def remove_action(self, row: ActionRow):
        if row in self.action_rows:
            self.action_rows.remove(row)
            row.destroy()
            self._renumber()
            self.changed()

    def move_action(self, row: ActionRow, delta: int):
        i = self.action_rows.index(row)
        j = i + delta
        if not 0 <= j < len(self.action_rows):
            return
        self.action_rows[i], self.action_rows[j] = self.action_rows[j], self.action_rows[i]
        for r in self.action_rows:
            r.pack_forget()
        for r in self.action_rows:
            r.pack(fill="x", pady=(0, 8))
        self._renumber()
        self.changed()

    def _renumber(self):
        for n, r in enumerate(self.action_rows, 1):
            r.set_number(n)

    # ---------------------------------------------------------------- 変更 → プレビュー・自動テスト
    def base_folder(self) -> str:
        scope = self._scope_folders() if hasattr(self, "v_scope") else []
        if scope:
            return resolve_folder(scope[0])
        folders = watch_folders(self.app.cfg) or [""]
        return folders[0]

    def changed(self):
        if not self._ready:
            return
        draft = self.collect()
        self.structure.configure(text="\n".join(tree_text(draft["conditions"], self.categories)))
        sample = FileInfo(path="", name="sample.pdf", mtime=time.time(), ctime=time.time(),
                          urls=("", "https://example.com/"))
        pv = actions.preview(draft, sample, self.base_folder(), self.categories)
        for n, row in enumerate(self.action_rows):
            text = ""
            if n < len(pv) and pv[n].type == row.type:
                tgt = pv[n].target
                text = f"例（sample.pdf の場合）: {display_path(tgt, [self.base_folder()]) if tgt else ''}"
                if pv[n].note:
                    text += f"　{pv[n].note}"
            row.update_preview(text)
        if not self.v_auto.get():
            return
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(450, self.run_test)

    # ---------------------------------------------------------------- 収集・保存
    def collect(self) -> dict:
        return {
            "id": self.rule["id"], "name": self.v_name.get().strip(), "enabled": bool(self.v_enabled.get()),
            "trigger": self.v_trigger.get(), "conditions": self.root_group.get(),
            "actions": [r.get() for r in self.action_rows], "folders": self._scope_folders(),
        }

    def save(self):
        raw = self.collect()
        errs = self._scope_errors() + validate_rule(raw, self.categories)
        if errs:
            self.err_label.configure(text="\n".join(errs[:6]))
            self.bell()
            return
        self.on_save(normalize_rule(raw), self.is_new)
        self.destroy()

    def destroy(self):
        if self._after:
            try:
                self.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        super().destroy()

    # ---------------------------------------------------------------- テスト
    def run_test(self):
        self._after = None
        raw = self.collect()
        errs = self._scope_errors() + validate_rule(raw, self.categories)
        self.err_label.configure(text="\n".join(errs[:6]) if errs else "")
        draft = normalize_rule(raw)
        cfg = self.app.reload_config()
        rules = list(cfg.get("rules", []))
        for i, r in enumerate(rules):
            if r["id"] == draft["id"]:
                rules[i] = draft
                break
        else:
            rules.append(draft)
        auto = [r for r in rules if r.get("trigger", "auto") == "auto"]
        self._rules = rules
        ctx = engine.build_context(cfg)
        exclusions = store.load_exclusions()
        self._test_rows = {}
        n_total = n_match = n_applied = n_out = 0
        manual = draft["trigger"] == "manual"
        for folder in watch_folders(cfg):
            if not os.path.isdir(folder):
                continue
            try:
                scanned = engine.scan_folder(folder, cfg, ctx.now, exclusions)
            except OSError:
                continue
            in_scope = rule_applies_to(draft, folder)
            folder_rules = engine.rules_for_folder(auto, folder)
            for info, reason in scanned:
                n_total += 1
                if in_scope:
                    rr = evaluate_rule(draft, info, ctx, full=True)
                else:  # このルールの対象外のフォルダ
                    rr = RuleResult(draft, False, None, "対象外のフォルダ")
                    n_out += 1
                if reason:
                    applied = engine.PlanItem(folder, info, "skip", reason=reason)
                elif manual:  # 手動ルールはこのルール単体で実行される
                    applied = engine.plan_file(info, folder, engine.rules_for_folder([dict(draft, enabled=True)], folder),
                                               ctx, trace=True)
                else:
                    applied = engine.plan_file(info, folder, folder_rules, ctx, trace=True)
                if rr.matched:
                    n_match += 1
                    if applied.rule is not None and applied.rule.get("id") == draft["id"] and applied.status == "run":
                        n_applied += 1
                self._test_rows[f"r{len(self._test_rows)}"] = (folder, info, rr, applied)
        self._ctx = ctx
        self._draft = draft
        msg = f"{n_total} 件中 {n_match} 件がこのルールの条件に一致（うち {n_applied} 件で実際に実行）"
        if manual:
            msg += "。手動ルールのため、クイック操作で実行したときの結果です"
        if n_match > n_applied:
            msg += "。黄色の行は、より上のルールが先に適用されるか、待機中などでスキップされます"
        if n_out:
            msg += f"。対象外のフォルダのファイル {n_out} 件は判定しません"
        mine = [a for _, _, _, a in self._test_rows.values()
                if a.rule is not None and a.rule.get("id") == draft["id"]]
        zips = engine.zip_summary_text(mine, watch_folders(cfg))
        self.test_summary.configure(text=msg + "。" + (f"\n{zips}" if zips else ""))
        self._render_test()

    def _render_test(self):
        tree = self.test_tree
        tree.delete(*tree.get_children())
        draft_id = self.rule["id"]
        for iid, (folder, info, rr, applied) in self._test_rows.items():
            if self.v_only.get() and not rr.matched:
                continue
            if applied.rule:
                applied_text = f"{applied.label}（{applied.rule.get('name')}）"
            elif applied.status == "skip":
                applied_text = f"スキップ"
            else:
                applied_text = applied.label
            if rr.matched:
                mine = applied.rule is not None and applied.rule.get("id") == draft_id and applied.status == "run"
                tag = "match" if mine else "other"
                if mine:
                    dest = "　＞　".join(p.text([folder]) for p in applied.steps)
                else:
                    dest = applied.reason
            else:
                tag, dest = "muted", ""
            this = "✔ 一致" if rr.matched else ("– 対象外" if rr.note == "対象外のフォルダ" else "✘ 不一致")
            tree.insert("", "end", iid=iid, tags=(tag,), values=(info.name, this, applied_text, dest))
        set_text(self.trace_text, [("行を選択すると、条件ごとの判定結果と処理内容を表示します。", "muted")])

    def _on_test_select(self, _e=None):
        sel = self.test_tree.selection()
        if not sel or sel[0] not in self._test_rows:
            return
        folder, info, rr, applied = self._test_rows[sel[0]]
        chunks = trace.file_header(applied, self._ctx.now)
        chunks += [("\nこのルールの判定: ", "h"), ("✔ 一致\n" if rr.matched else "✘ 不一致\n", "ok" if rr.matched else "ng")]
        if rr.note:
            chunks.append((f"  （{rr.note}）\n", "sub"))
        if rr.root:
            chunks += trace.node_lines(rr.root, self.categories, 3)
        if rr.matched:
            chunks.append(("\nこのルールでの処理内容:\n", "h"))
            for n, p in enumerate(actions.preview(self._draft, info, folder, self.categories), 1):
                chunks.append((f"  {n}. {p.text([folder])}\n", "ng" if p.stop else None))
        chunks += trace.result_line(applied)
        if applied.traces:
            chunks += trace.rules_trace(applied.traces, self._rules, self.categories, applied.rule, self.rule["id"],
                                        folder)
        set_text(self.trace_text, chunks)
