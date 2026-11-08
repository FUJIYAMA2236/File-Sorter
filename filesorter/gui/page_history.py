"""履歴（検索・元に戻す・フォルダを開く・クリア）"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from .. import actions, engine, store, winapi
from ..config import watch_folders
from ..ipc import LOCK_RUN
from ..util import display_path, fmt_datetime, fmt_size
from .page_base import Page
from .widgets import make_tree


class HistoryPage(Page):
    title = "履歴"
    subtitle = "処理したファイルの記録です。「元に戻す」で動作を逆順に取り消します（戻したファイルは再び処理されません）。"

    def build(self):
        t = self.theme
        ttk.Button(self.actions, text="履歴をクリア", command=self._clear).pack(side="left")

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, text="検索").pack(side="left")
        self.v_q = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.v_q, width=32)
        e.pack(side="left", padx=(8, 0))
        self.v_q.trace_add("write", lambda *a: self.refresh())
        self.v_action = tk.StringVar(value="すべて")
        cb = ttk.Combobox(bar, textvariable=self.v_action,
                          values=["すべて", "移動", "コピー", "名前変更", "ZIP化", "展開", "ごみ箱", "コマンド", "取消済"],
                          state="readonly", width=9)
        cb.pack(side="left", padx=(8, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(bar, text="元に戻す", style="Accent.TButton", command=self._undo).pack(side="right")
        ttk.Button(bar, text="フォルダを開く", command=self._open).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="更新", command=self.refresh).pack(side="right", padx=(0, 8))

        frame, self.tree = make_tree(self.body, [
            ("time", "日時", 140, "w", False),
            ("action", "動作", 110, "center", False),
            ("name", "ファイル名", 230, "w", True),
            ("dest", "行き先", 230, "w", True),
            ("rule", "ルール", 120, "w", False),
            ("size", "サイズ", 80, "e", False),
        ], height=16, selectmode="extended")
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("undone", foreground=t.c("muted"))
        self.tree.tag_configure("recycle", foreground=t.c("warn"))
        self.tree.bind("<Double-1>", lambda e: self._open())
        self.count = ttk.Label(self.body, text="", style="Sub.TLabel")
        self.count.pack(anchor="w", pady=(8, 0))
        self._by_id = {}

    def on_show(self):
        self.refresh()

    def on_state_changed(self):
        self.refresh()

    def refresh(self):
        hist = store.load_history()
        folders = watch_folders(self.cfg)
        q = self.v_q.get().strip().casefold()
        flt = self.v_action.get()
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self._by_id = {}
        shown = 0
        for h in reversed(hist):
            if q and q not in str(h.get("name", "")).casefold() and q not in str(h.get("rule", "")).casefold():
                continue
            undone = bool(h.get("undone"))
            action = "取消済" if undone else actions.entry_label(h)
            if flt == "取消済" and not undone or flt not in ("すべて", "取消済") and (undone or flt not in action):
                continue
            dest = actions.entry_destination(h)
            dest = display_path(dest, folders) if dest else ""
            if h.get("error"):
                dest = f"⚠ {h['error']}"
            tags = ("undone",) if undone else (("recycle",) if "ごみ箱" in action else ())
            iid = h.get("id")
            self._by_id[iid] = h
            self.tree.insert("", "end", iid=iid, tags=tags,
                             values=(fmt_datetime(h.get("time")), action, h.get("name"), dest, h.get("rule"),
                                     fmt_size(h.get("size", 0)) if h.get("size") is not None else ""))
            shown += 1
        for iid in sel:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)
        self.count.configure(text=f"{shown} 件を表示（保存件数の上限: {self.cfg.get('history_limit', 1000)} 件）")

    def _selected(self):
        return [self._by_id[i] for i in self.tree.selection() if i in self._by_id]

    def _undo(self):
        sel = self._selected()
        items = [h for h in sel if actions.entry_undoable(h)]
        if not items:
            msg = ("ごみ箱へ送ったファイルは、ごみ箱から復元してください" if any(not h.get("undone") for h in sel)
                   else "元に戻せる項目が選択されていません")
            self.app.toast(msg, "warn")
            return
        names = "\n".join(f"・{h['name']}（{actions.entry_label(h)}）" for h in items[:8])
        names += f"\n…ほか {len(items) - 8} 件" if len(items) > 8 else ""
        if not messagebox.askyesno("元に戻す", f"{len(items)} 件を元に戻しますか？\n（動作を逆の順に取り消します）\n\n{names}",
                                   parent=self.app.root):
            return
        try:
            ok, notes = engine.undo([h["id"] for h in items])
        except TimeoutError:
            self.app.toast("処理中のため、少し待ってから再度お試しください", "warn")
            return
        if notes:
            messagebox.showinfo("元に戻す", f"{ok} 件を元に戻しました。\n\n補足:\n" + "\n".join(notes[:12]),
                                parent=self.app.root)
        else:
            self.app.toast(f"{ok} 件を元に戻しました")
        self.refresh()

    def _open(self):
        items = self._selected()
        if not items:
            return
        h = items[0]
        path = h.get("restored_to") if h.get("undone") else actions.entry_destination(h)
        if path == "ごみ箱":
            path = ""
        if path and os.path.exists(path):
            subprocess.Popen(f'explorer /select,"{path}"')
        elif path and os.path.isdir(os.path.dirname(path)):
            os.startfile(os.path.dirname(path))
        else:
            self.app.toast("ファイルが見つかりません", "warn")

    def _clear(self):
        if not messagebox.askyesno("履歴をクリア", "履歴をすべて削除しますか？（ファイル自体は削除されません）\n"
                                                  "削除すると「元に戻す」は使えなくなります。", parent=self.app.root):
            return
        with winapi.NamedLock(LOCK_RUN):
            store.clear_history()
        self.refresh()
        self.app.toast("履歴をクリアしました")
