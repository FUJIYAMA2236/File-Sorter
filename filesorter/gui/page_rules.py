"""ルール一覧（追加・編集・複製・削除・並べ替え・有効化・インポート/エクスポート）"""
from __future__ import annotations

import copy
import json
from tkinter import filedialog, messagebox, ttk

from ..config import new_rule_id, normalize_rule
from ..rules import describe_actions, describe_conditions
from .page_base import Page
from .widgets import Tooltip, make_tree


class RulesPage(Page):
    title = "ルール"
    subtitle = "ルールは上から順に判定され、最初に一致したルールの動作が適用されます。ダブルクリックで編集できます。"

    def build(self):
        t = self.theme
        ttk.Button(self.actions, text="インポート", command=self._import).pack(side="left")
        ttk.Button(self.actions, text="エクスポート", command=self._export).pack(side="left", padx=(8, 0))

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Button(bar, text="＋ ルールを追加", style="Accent.TButton", command=self._add).pack(side="left")
        ttk.Button(bar, text="編集", command=self._edit).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="複製", command=self._duplicate).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="有効 / 無効", command=self._toggle).pack(side="left", padx=(8, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=12, pady=4)
        up = ttk.Button(bar, text=t.icon("up", "↑"), style="Icon.TButton", command=lambda: self._move(-1))
        up.pack(side="left")
        Tooltip(up, "上へ移動（優先度を上げる）", t)
        down = ttk.Button(bar, text=t.icon("down", "↓"), style="Icon.TButton", command=lambda: self._move(1))
        down.pack(side="left", padx=(6, 0))
        Tooltip(down, "下へ移動（優先度を下げる）", t)
        ttk.Button(bar, text="削除", command=self._delete).pack(side="right")
        run = ttk.Button(bar, text="▶ 選択したルールを今すぐ実行", command=self._run_selected)
        run.pack(side="right", padx=(0, 8))
        self.app.register_run_button(run, keep_text=True)
        Tooltip(run, "選択したルールだけを、監視フォルダのファイルに今すぐ実行します（手動ルール・無効なルールも実行できます）", t)

        frame, self.tree = make_tree(self.body, [
            ("no", "#", 40, "center", False),
            ("state", "状態", 72, "center", False),
            ("trigger", "実行", 64, "center", False),
            ("name", "ルール名", 170, "w", False),
            ("conds", "条件", 380, "w", True),
            ("action", "動作", 240, "w", True),
        ], height=14)
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("disabled", foreground=t.c("muted"))
        self.tree.bind("<Double-1>", lambda e: self._edit())
        self.tree.bind("<Return>", lambda e: self._edit())
        self.tree.bind("<Delete>", lambda e: self._delete())
        self.tree.bind("<space>", lambda e: self._toggle())

        ttk.Label(self.body, text="ヒント: 「何もしない（除外）」のルールを上に置くと、特定のファイルを対象から外せます。"
                                  "「手動」のルールは自動では動かず、ホーム画面やトレイのクイック操作から実行します。"
                                  "　Space で有効/無効、Delete で削除。", wraplength=900,
                  style="Sub.TLabel").pack(anchor="w", pady=(10, 0))

    # ---------------------------------------------------------------- 表示
    def on_show(self):
        self.refresh()

    def refresh(self, select_id: str | None = None):
        sel = select_id or self._selected_id()
        self.tree.delete(*self.tree.get_children())
        cats = self.cfg.get("categories", {})
        for i, r in enumerate(self.cfg.get("rules", []), 1):
            self.tree.insert("", "end", iid=r["id"], tags=() if r.get("enabled") else ("disabled",),
                             values=(i, "● 有効" if r.get("enabled") else "○ 無効",
                                     "手動" if r.get("trigger") == "manual" else "自動", r.get("name"),
                                     describe_conditions(r, cats), describe_actions(r)))
        if sel and self.tree.exists(sel):
            self.tree.selection_set(sel)
            self.tree.focus(sel)
            self.tree.see(sel)

    def _selected_id(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _find(self, rid):
        for i, r in enumerate(self.cfg.get("rules", [])):
            if r["id"] == rid:
                return i, r
        return None, None

    # ---------------------------------------------------------------- 操作
    def _save_rules(self, fn, select_id=None):
        def mut(c):
            fn(c["rules"])
        self.app.update_config(mut)
        self.refresh(select_id)

    def _run_selected(self):
        rid = self._selected_id()
        if not rid:
            self.app.toast("実行するルールを選択してください", "warn")
            return
        _, rule = self._find(rid)
        if rule and messagebox.askyesno("ルールを実行", f"ルール「{rule['name']}」を今すぐ実行しますか？\n"
                                                      "（実行前に「テスト」画面や編集画面のテストで結果を確認できます）",
                                        parent=self.app.root):
            self.app.run_now(rid)

    def _add(self):
        from .rule_editor import RuleEditor
        RuleEditor(self.app, None, self._on_editor_saved)

    def _edit(self):
        rid = self._selected_id()
        if not rid:
            return
        _, rule = self._find(rid)
        if rule:
            from .rule_editor import RuleEditor
            RuleEditor(self.app, copy.deepcopy(rule), self._on_editor_saved)

    def _on_editor_saved(self, rule: dict, is_new: bool):
        def fn(rules):
            if is_new:
                sel = self._selected_id()
                idx = next((i for i, r in enumerate(rules) if r["id"] == sel), None)
                if idx is None:
                    rules.append(rule)
                else:
                    rules.insert(idx + 1, rule)
            else:
                for i, r in enumerate(rules):
                    if r["id"] == rule["id"]:
                        rules[i] = rule
                        break
                else:
                    rules.append(rule)
        self._save_rules(fn, rule["id"])
        self.app.toast(f"ルール「{rule['name']}」を保存しました")

    def _duplicate(self):
        rid = self._selected_id()
        if not rid:
            return
        new_id = new_rule_id()

        def fn(rules):
            for i, r in enumerate(rules):
                if r["id"] == rid:
                    dup = copy.deepcopy(r)
                    dup["id"] = new_id
                    dup["name"] = r["name"] + "（コピー）"
                    dup["enabled"] = False
                    rules.insert(i + 1, dup)
                    break
        self._save_rules(fn, new_id)
        self.app.toast("ルールを複製しました（無効状態）")

    def _toggle(self):
        rid = self._selected_id()
        if not rid:
            return

        def fn(rules):
            for r in rules:
                if r["id"] == rid:
                    r["enabled"] = not r.get("enabled")
        self._save_rules(fn, rid)

    def _move(self, delta):
        rid = self._selected_id()
        if not rid:
            return

        def fn(rules):
            for i, r in enumerate(rules):
                if r["id"] == rid:
                    j = i + delta
                    if 0 <= j < len(rules):
                        rules[i], rules[j] = rules[j], rules[i]
                    break
        self._save_rules(fn, rid)

    def _delete(self):
        rid = self._selected_id()
        if not rid:
            return
        _, rule = self._find(rid)
        if not rule or not messagebox.askyesno("ルールの削除", f"ルール「{rule['name']}」を削除しますか？",
                                               parent=self.app.root):
            return
        def fn(rules):
            rules[:] = [r for r in rules if r["id"] != rid]
        self._save_rules(fn)
        self.app.toast("ルールを削除しました")

    def _export(self):
        path = filedialog.asksaveasfilename(parent=self.app.root, title="ルールをエクスポート",
                                            defaultextension=".json", initialfile="仕分けルール.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"app": "FileSortKun", "rules": self.cfg.get("rules", [])}, f, ensure_ascii=False, indent=2)
        self.app.toast("ルールをエクスポートしました")

    def _import(self):
        path = filedialog.askopenfilename(parent=self.app.root, title="ルールをインポート",
                                          filetypes=[("JSON", "*.json"), ("すべて", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            items = data.get("rules") if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise ValueError("ルールの一覧が見つかりません")
            imported = []
            for r in items:
                if isinstance(r, dict):
                    nr = normalize_rule(r)
                    nr["id"] = new_rule_id()
                    imported.append(nr)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            messagebox.showerror("インポート", f"読み込めませんでした。\n{e}", parent=self.app.root)
            return
        if not imported:
            return
        cmds = [r["name"] for r in imported if any(s["type"] == "command" for s in r["actions"])]
        warn = (f"⚠ コマンドを実行するルールが含まれています: {', '.join(cmds[:3])}\n"
                "　内容を確認してから有効にしてください（インポートしたルールは無効状態で追加されます）。\n\n"
                if cmds else "")
        for r in imported:
            if any(s["type"] == "command" for s in r["actions"]):
                r["enabled"] = False
        ans = messagebox.askyesnocancel(
            "インポート", f"{len(imported)} 件のルールを読み込みました。\n\n{warn}"
                          "「はい」… 既存のルールの後ろに追加\n「いいえ」… 既存のルールを置き換え",
            parent=self.app.root)
        if ans is None:
            return

        def fn(rules):
            if ans:
                rules.extend(imported)
            else:
                rules[:] = imported
        self._save_rules(fn)
        self.app.toast(f"{len(imported)} 件のルールをインポートしました")
