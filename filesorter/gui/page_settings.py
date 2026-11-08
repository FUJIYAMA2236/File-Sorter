"""設定"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import APP_ID, APP_NAME, __version__, ipc, store, winapi
from ..config import (APP_DIR, DEFAULT_IGNORE, DOWNLOADS_TOKEN, data_dir, folder_token_for, is_portable,
                      resolve_folder, switch_storage)
from .page_base import Page
from .widgets import ScrollFrame, card

UNITS = [("秒", 1), ("分", 60), ("時間", 3600)]


def _split_interval(sec: int) -> tuple[int, str]:
    for label, mul in reversed(UNITS):
        if sec % mul == 0 and sec >= mul:
            return sec // mul, label
    return sec, "秒"


class SettingsPage(Page):
    title = "設定"

    def build(self):
        t = self.theme
        self.dirty_label = ttk.Label(self.actions, text="", style="Warn.TLabel")
        self.dirty_label.pack(side="left", padx=(0, 12))
        ttk.Button(self.actions, text="変更を破棄", command=self.load).pack(side="left")
        ttk.Button(self.actions, text="保存", style="Accent.TButton", command=self.save).pack(side="left", padx=(8, 0))

        sf = ScrollFrame(self.body, t)
        sf.pack(fill="both", expand=True)
        b = ttk.Frame(sf.body, padding=(0, 0, 14, 10))
        b.pack(fill="both", expand=True)

        def section(title, desc=""):
            c = card(b, padding=(20, 16))
            c.pack(fill="x", pady=(0, 12))
            ttk.Label(c, text=title, style="Subtitle.TLabel").pack(anchor="w")
            if desc:
                ttk.Label(c, text=desc, style="Sub.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(2, 0))
            inner = ttk.Frame(c)
            inner.pack(fill="x", pady=(12, 0))
            return inner

        # ---- 監視フォルダ
        s = section("監視フォルダ", "フォルダ直下のファイルのみが対象です（サブフォルダの中身は対象外）。"
                                  "ダウンロードフォルダは PC ごとの場所に自動で読み替えます。")
        lf = ttk.Frame(s, style="Card.TFrame", padding=2)
        lf.pack(side="left", fill="x", expand=True)
        self.lb = tk.Listbox(lf, height=4, bd=0, highlightthickness=0, activestyle="none", bg=t.c("text_bg"),
                             fg=t.c("fg"), selectbackground="#2f60d8", selectforeground="#ffffff",
                             font=t.font("App.Body"))
        self.lb.pack(fill="both", expand=True, padx=8, pady=6)
        bx = ttk.Frame(s)
        bx.pack(side="left", anchor="n", padx=(10, 0))
        ttk.Button(bx, text="追加…", command=self._add_folder).pack(fill="x")
        ttk.Button(bx, text="削除", command=self._remove_folder).pack(fill="x", pady=(6, 0))
        ttk.Button(bx, text="開く", command=self._open_folder).pack(fill="x", pady=(6, 0))

        # ---- 実行タイミング
        s = section("実行タイミング")
        g = ttk.Frame(s)
        g.pack(fill="x")
        self.v_interval = tk.StringVar()
        self.v_interval_unit = tk.StringVar()
        ttk.Label(g, text="仕分けの間隔").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Spinbox(g, from_=1, to=86400, textvariable=self.v_interval, width=8).grid(row=0, column=1, sticky="w", padx=(14, 0))
        ttk.Combobox(g, textvariable=self.v_interval_unit, values=[u for u, _ in UNITS], state="readonly",
                     width=6).grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Label(g, text="ごと（10秒以上）", style="Sub.TLabel").grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.v_settle = tk.StringVar()
        ttk.Label(g, text="待機時間").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(g, from_=0, to=3600, textvariable=self.v_settle, width=8).grid(row=1, column=1, sticky="w", padx=(14, 0))
        ttk.Label(g, text="秒", ).grid(row=1, column=2, sticky="w", padx=(6, 0))
        ttk.Label(s, text="作成・更新からこの時間が経っていないファイルは、ダウンロード中・コピー中の可能性があるため次回に回します。",
                  style="Sub.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(2, 8))
        self.v_watch = tk.BooleanVar()
        ttk.Checkbutton(s, text="フォルダに新しいファイルが現れたら、待機時間の経過後すぐに仕分ける", variable=self.v_watch,
                        style="Switch.TCheckbutton").pack(anchor="w")
        ttk.Label(s, text="定期実行に加えて Windows のフォルダ変更通知を使います（待機中の負荷はほぼありません）。",
                  style="Sub.TLabel").pack(anchor="w", padx=(48, 0))

        # ---- 除外
        s = section("除外", "次のファイルは、ルールに関係なく仕分けの対象外になります。")
        ttk.Label(s, text="除外パターン（1行に1つ。* と ? が使えます）").pack(anchor="w")
        tf = ttk.Frame(s, style="Card.TFrame", padding=2)
        tf.pack(fill="x", pady=(6, 0))
        self.t_ignore = tk.Text(tf, height=6, bd=0, highlightthickness=0, wrap="none", bg=t.c("text_bg"),
                                fg=t.c("fg"), insertbackground=t.c("fg"), font=t.font("App.Body"), padx=8, pady=6,
                                selectbackground="#2f60d8", selectforeground="#ffffff", undo=True)
        self.t_ignore.pack(fill="x")
        self.t_ignore.bind("<<Modified>>", self._on_text_modified)
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="既定のパターンに戻す", style="Small.TButton", command=self._reset_ignore).pack(side="left")
        self.v_hidden = tk.BooleanVar()
        ttk.Checkbutton(s, text="隠しファイル・システムファイルを無視する", variable=self.v_hidden,
                        style="Switch.TCheckbutton").pack(anchor="w", pady=(10, 0))
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(10, 0))
        self.ex_label = ttk.Label(row, text="", style="Sub.TLabel")
        self.ex_label.pack(side="left")
        ttk.Button(row, text="処理済みの記録をリセット", style="Small.TButton",
                   command=lambda: self._clear_exclusions("processed")).pack(side="right")
        ttk.Button(row, text="元に戻したファイルの除外を解除", style="Small.TButton",
                   command=lambda: self._clear_exclusions("undone")).pack(side="right", padx=(0, 6))

        # ---- 通知と起動
        s = section("通知と起動")
        self.v_notify = tk.BooleanVar()
        ttk.Checkbutton(s, text="ファイルを仕分けたら通知する", variable=self.v_notify,
                        style="Switch.TCheckbutton").pack(anchor="w")
        self.v_startup = tk.BooleanVar()
        ttk.Checkbutton(s, text="Windows へのサインイン時に自動で起動する（通知領域に常駐）", variable=self.v_startup,
                        style="Switch.TCheckbutton").pack(anchor="w", pady=(8, 0))

        # ---- 外観
        s = section("外観")
        self.v_theme = tk.StringVar()
        row = ttk.Frame(s)
        row.pack(anchor="w")
        for key, label in (("system", "システム設定に合わせる"), ("light", "ライト"), ("dark", "ダーク")):
            ttk.Radiobutton(row, text=label, value=key, variable=self.v_theme).pack(side="left", padx=(0, 18))

        # ---- データ
        s = section("データと持ち運び（ポータブルモード）")
        row = ttk.Frame(s)
        row.pack(fill="x")
        self.storage_label = ttk.Label(row, text="", wraplength=640, justify="left")
        self.storage_label.pack(side="left", fill="x", expand=True)
        self.storage_btn = ttk.Button(row, text="", command=self._toggle_storage)
        self.storage_btn.pack(side="right")
        ttk.Label(s, text="ポータブルモードでは、設定と履歴をアプリと同じフォルダの data に保存します。"
                          "フォルダごと USB メモリや別の PC にコピーして使えます（exe 版なら Python も不要）。",
                  style="Sub.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(6, 10))
        g = ttk.Frame(s)
        g.pack(fill="x")
        self.v_hist = tk.StringVar()
        ttk.Label(g, text="履歴の保存件数").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(g, from_=50, to=100000, increment=50, textvariable=self.v_hist, width=8).grid(row=0, column=1, padx=(14, 0))
        ttk.Label(g, text="件", ).grid(row=0, column=2, padx=(6, 0))
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="設定フォルダを開く", command=lambda: os.startfile(str(data_dir()))).pack(side="left")
        ttk.Button(row, text="ログを開く", command=self._open_log).pack(side="left", padx=(8, 0))

        # ---- 常駐プロセス
        s = section("常駐プロセス", "仕分けは通知領域に常駐する軽量なプロセスが行います。この画面を閉じても動作を続けます。")
        row = ttk.Frame(s)
        row.pack(fill="x")
        self.res_label = ttk.Label(row, text="")
        self.res_label.pack(side="left")
        ttk.Button(row, text="常駐を停止", command=self._stop_resident).pack(side="right")
        ttk.Button(row, text="常駐を開始", command=self._start_resident).pack(side="right", padx=(0, 8))
        ttk.Label(b, text=f"{APP_NAME}  v{__version__}", style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

        for v in (self.v_interval, self.v_interval_unit, self.v_settle, self.v_watch, self.v_hidden, self.v_notify,
                  self.v_startup, self.v_theme, self.v_hist):
            v.trace_add("write", lambda *a: self._mark_dirty())
        self._loading = False
        self._dirty = False

    # ---------------------------------------------------------------- 読込/保存
    def on_show(self):
        if not self._dirty:
            self.load()
        self._update_resident()

    def on_state_changed(self):
        self._update_resident()

    def _update_resident(self):
        running = self.app.resident
        self.res_label.configure(text="● 動作中" if running else "● 停止中",
                                 style="Ok.TLabel" if running else "Err.TLabel")
        ex = store.load_exclusions().values()
        undone = sum(1 for v in ex if v.get("reason", "undone") == "undone")
        processed = sum(1 for v in ex if v.get("reason") == "processed")
        self.ex_label.configure(text=f"元に戻したため再処理しない: {undone} 件　｜　コピー等で処理済み: {processed} 件")
        if is_portable():
            self.storage_label.configure(text=f"現在: ポータブルモード（{data_dir()}）")
            self.storage_btn.configure(text="通常モードに戻す")
        else:
            self.storage_label.configure(text=f"現在: 通常モード（{data_dir()}）")
            self.storage_btn.configure(text="ポータブルモードにする")

    def _show_folders(self):
        self.lb.delete(0, "end")
        for f in self._folders:
            if f == DOWNLOADS_TOKEN:
                self.lb.insert("end", f"ダウンロードフォルダ（{resolve_folder(f)}）")
            elif os.path.isabs(os.path.expandvars(f)):
                self.lb.insert("end", f)
            else:
                self.lb.insert("end", f"{f}　（→ {resolve_folder(f)}）")

    def load(self):
        self._loading = True
        cfg = self.app.reload_config()
        self._folders = list(cfg["watch_folders"])
        self._show_folders()
        n, unit = _split_interval(cfg["interval_sec"])
        self.v_interval.set(str(n))
        self.v_interval_unit.set(unit)
        self.v_settle.set(str(cfg["settle_sec"]))
        self.v_watch.set(cfg["watch_changes"])
        self.t_ignore.delete("1.0", "end")
        self.t_ignore.insert("1.0", "\n".join(cfg["ignore_patterns"]))
        self.t_ignore.edit_modified(False)
        self.v_hidden.set(cfg["skip_hidden"])
        self.v_notify.set(cfg["notify"])
        self.v_startup.set(winapi.get_startup_command(APP_ID) is not None)
        self.v_theme.set(cfg["theme"])
        self.v_hist.set(str(cfg["history_limit"]))
        self._loading = False
        self._set_dirty(False)

    def _mark_dirty(self):
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, v: bool):
        self._dirty = v
        self.dirty_label.configure(text="未保存の変更があります" if v else "")

    def _on_text_modified(self, _e=None):
        if self.t_ignore.edit_modified():
            self._mark_dirty()

    def save(self):
        folders = list(self._folders)
        missing = [resolve_folder(f) for f in folders if not os.path.isdir(resolve_folder(f))]
        if not folders:
            messagebox.showerror("設定", "監視フォルダを1つ以上指定してください。", parent=self.app.root)
            return
        if missing and not messagebox.askyesno("設定", f"存在しないフォルダがあります:\n{missing[0]}\n\nこのまま保存しますか？",
                                               parent=self.app.root):
            return
        try:
            interval = int(float(self.v_interval.get())) * dict(UNITS)[self.v_interval_unit.get() or "秒"]
            settle = int(float(self.v_settle.get()))
            hist = int(float(self.v_hist.get()))
        except (ValueError, KeyError):
            messagebox.showerror("設定", "数値の入力を確認してください。", parent=self.app.root)
            return
        if interval < 10:
            messagebox.showerror("設定", "仕分けの間隔は10秒以上にしてください。", parent=self.app.root)
            return
        patterns = [ln.strip() for ln in self.t_ignore.get("1.0", "end").splitlines() if ln.strip()]
        old_theme = self.cfg.get("theme")
        new_theme = self.v_theme.get()

        def mut(c):
            c.update(watch_folders=folders, interval_sec=interval, settle_sec=max(0, settle),
                     watch_changes=bool(self.v_watch.get()), ignore_patterns=patterns,
                     skip_hidden=bool(self.v_hidden.get()), notify=bool(self.v_notify.get()),
                     theme=new_theme, history_limit=hist)
        self.app.update_config(mut)
        try:
            winapi.set_startup_command(APP_ID, ipc.startup_command_line() if self.v_startup.get() else None)
        except OSError as e:
            messagebox.showwarning("設定", f"自動起動の設定に失敗しました: {e}", parent=self.app.root)
        self._set_dirty(False)
        if new_theme != old_theme:
            self.app.rebuild()
            self.app.toast("設定を保存しました")
            return
        self.load()
        self.app.toast("設定を保存しました")

    # ---------------------------------------------------------------- 個別操作
    def _add_folder(self):
        p = filedialog.askdirectory(parent=self.app.root, title="監視するフォルダを選択")
        if p:
            token = folder_token_for(os.path.normpath(p))
            if token not in self._folders:
                self._folders.append(token)
                self._show_folders()
                self._mark_dirty()

    def _remove_folder(self):
        for i in reversed(self.lb.curselection()):
            del self._folders[i]
            self._mark_dirty()
        self._show_folders()

    def _open_folder(self):
        sel = self.lb.curselection()
        if sel:
            p = resolve_folder(self._folders[sel[0]])
            if os.path.isdir(p):
                os.startfile(p)

    def _toggle_storage(self):
        to_portable = not is_portable()
        if to_portable:
            msg = (f"ポータブルモードにしますか？\n\n設定と履歴を次の場所にコピーし、以後はそこに保存します:\n{APP_DIR / 'data'}\n\n"
                   "常駐プロセスは自動で再起動します。")
        else:
            msg = "通常モードに戻しますか？\n\n設定と履歴を %APPDATA%\\FileSortKun にコピーし、以後はそこに保存します。\n常駐プロセスは自動で再起動します。"
        if not messagebox.askyesno("保存先の切り替え", msg, parent=self.app.root):
            return
        try:
            with winapi.NamedLock(ipc.LOCK_RUN):
                dst = switch_storage(to_portable)
        except (OSError, TimeoutError) as e:
            messagebox.showerror("保存先の切り替え", f"切り替えに失敗しました: {e}", parent=self.app.root)
            return
        self.app.restart_resident()
        self.load()
        self._update_resident()
        self.app.toast(f"保存先を切り替えました: {dst}")

    def _reset_ignore(self):
        self.t_ignore.delete("1.0", "end")
        self.t_ignore.insert("1.0", "\n".join(DEFAULT_IGNORE))
        self._mark_dirty()

    def _clear_exclusions(self, reason: str):
        with winapi.NamedLock(ipc.LOCK_RUN):
            store.clear_exclusions_by_reason(reason)
        self._update_resident()
        self.app.toast("記録をリセットしました（次回の実行から対象になります）")

    def _open_log(self):
        p = data_dir() / "app.log"
        if p.exists():
            os.startfile(str(p))

    def _start_resident(self):
        self.app.start_resident()
        self.after(1500, self._update_resident)

    def _stop_resident(self):
        if self.app.stop_resident():
            self.after(800, self._update_resident)
