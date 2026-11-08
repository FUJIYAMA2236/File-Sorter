"""ホーム（状態・常駐の開始/停止・クイック操作・統計・最近の処理）"""
from __future__ import annotations

import os
import subprocess
import time
from tkinter import ttk

from .. import actions, config, store
from ..util import display_path, elide_middle, fmt_clock, fmt_datetime
from .page_base import Page
from .widgets import Banner, Tooltip, card, make_tree


def _interval_text(sec: int) -> str:
    if sec % 3600 == 0:
        return f"{sec // 3600} 時間ごと"
    if sec % 60 == 0:
        return f"{sec // 60} 分ごと"
    return f"{sec} 秒ごと"


class HomePage(Page):
    title = "ホーム"
    subtitle = "監視の状態、クイック操作、最近処理したファイルを確認できます。"

    def build(self):
        t = self.theme
        b = self.body

        # ---- 状態カード
        sc = card(b, padding=(20, 18))
        sc.pack(fill="x")
        right = ttk.Frame(sc)
        right.pack(side="right", anchor="n")
        left = ttk.Frame(sc)
        left.pack(side="left", fill="x", expand=True)
        top = ttk.Frame(left)
        top.pack(anchor="w")
        self.dot = ttk.Label(top, text="●", style="Ok.TLabel", font="App.Subtitle")
        self.dot.pack(side="left")
        self.status = ttk.Label(top, text="", style="Subtitle.TLabel")
        self.status.pack(side="left", padx=(8, 0))
        self.details = [ttk.Label(left, text="", style="Sub.TLabel") for _ in range(3)]
        for i, d in enumerate(self.details):
            d.pack(anchor="w", pady=(8 if i == 0 else 2, 0))

        self.run_btn = ttk.Button(right, text="今すぐ仕分け", style="Accent.TButton", command=self._run)
        self.run_btn.pack(side="left")
        self.app.register_run_button(self.run_btn)
        self.pause_btn = ttk.Button(right, text="一時停止", command=self._toggle_pause)
        self.pause_btn.pack(side="left", padx=(8, 0))
        self.resident_btn = ttk.Button(right, text="常駐を停止", command=self._toggle_resident)
        self.resident_btn.pack(side="left", padx=(8, 0))
        Tooltip(self.resident_btn, "通知領域に常駐して自動仕分けを行うプロセスを停止/開始します", t)

        self.banner_holder = ttk.Frame(b)
        self.banner_holder.pack(fill="x")

        # ---- クイック操作
        self.quick_card = card(b, padding=(18, 14))
        qh = ttk.Frame(self.quick_card)
        qh.pack(fill="x")
        ttk.Label(qh, text="クイック操作", style="Strong.TLabel").pack(side="left")
        ttk.Label(qh, text="　「手動のみ」に設定したルールを、ボタン1つで実行できます（トレイのメニューからも実行可）",
                  style="Sub.TLabel").pack(side="left")
        self.quick_box = ttk.Frame(self.quick_card)
        self.quick_box.pack(fill="x", pady=(10, 0))

        # ---- 統計
        self.stats = ttk.Frame(b)
        self.stats.pack(fill="x", pady=(14, 0))
        self.stat_vals = {}
        for i, (key, label) in enumerate((("today", "今日の処理"), ("week", "直近7日間"),
                                          ("total", "履歴の合計"), ("rules", "有効なルール"))):
            c = card(self.stats, padding=(18, 14))
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 12, 0))
            self.stats.columnconfigure(i, weight=1, uniform="stat")
            ttk.Label(c, text=label, style="Sub.TLabel").pack(anchor="w")
            v = ttk.Label(c, text="—", style="Stat.TLabel")
            v.pack(anchor="w", pady=(2, 0))
            self.stat_vals[key] = v

        # ---- 最近の処理
        head = ttk.Frame(b)
        head.pack(fill="x", pady=(20, 8))
        ttk.Label(head, text="最近の処理", style="Subtitle.TLabel").pack(side="left")
        link = ttk.Label(head, text="すべて表示 ›", style="Link.TLabel", cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda e: self.app.show("history"))
        frame, self.tree = make_tree(b, [
            ("time", "日時", 140, "w", False),
            ("action", "動作", 100, "center", False),
            ("name", "ファイル名", 260, "w", True),
            ("dest", "行き先", 260, "w", True),
            ("rule", "ルール", 140, "w", False),
        ], height=6)
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("undone", foreground=t.c("muted"))
        self.tree.tag_configure("error", foreground=t.c("err"))
        self.tree.bind("<Double-1>", self._open_selected)
        ttk.Label(b, text="ウィンドウを閉じても、通知領域（タスクバー右下）のアイコンで動作を続けます。"
                          "完全に終了するには左下の電源ボタンを押してください。",
                  style="Sub.TLabel").pack(anchor="w", pady=(10, 0))
        self._quick_sig = None
        self._banner_sig = None
        self._tree_sig = None

    # ---------------------------------------------------------------- 更新
    def on_show(self):
        self.refresh()

    def on_state_changed(self):
        self.refresh()

    def refresh(self):
        t = self.theme
        cfg = self.app.cfg
        st = self.app.state
        running = self.app.resident
        paused = not cfg.get("enabled", True)
        if not running:
            self.dot.configure(style="Err.TLabel")
            self.status.configure(text="常駐プロセスが停止しています")
        elif paused:
            self.dot.configure(style="Warn.TLabel")
            self.status.configure(text="自動仕分けを一時停止中")
        else:
            self.dot.configure(style="Ok.TLabel")
            self.status.configure(text="フォルダを監視中")
        folders = config.watch_folders(cfg)
        more = f"（ほか {len(folders) - 1} 件）" if len(folders) > 1 else ""
        self.details[0].configure(text=f"監視フォルダ: {elide_middle(folders[0], 64) if folders else '—'}{more}")
        self.details[1].configure(text=f"間隔: {_interval_text(cfg['interval_sec'])}　｜　待機時間: {cfg['settle_sec']} 秒"
                                       f"　｜　変更検知: {'オン' if cfg.get('watch_changes') else 'オフ'}")
        last = st.get("last_result") or {}
        last_txt = "—"
        if st.get("last_run"):
            n = int(last.get("done", last.get("moved", 0)) or 0)
            last_txt = f"{fmt_clock(st['last_run'])}（{n} 件を処理）" if n else f"{fmt_clock(st['last_run'])}（対象なし）"
            if last.get("errors"):
                last_txt += f"　⚠ {last['errors']} 件エラー"
        nxt = fmt_clock(st.get("next_run")) if running and not paused and st.get("next_run") else "—"
        self.details[2].configure(text=f"前回: {last_txt}　｜　次回: {nxt}")
        self.pause_btn.configure(text="再開" if paused else "一時停止", state="normal" if running else "disabled")
        self.resident_btn.configure(text="常駐を停止" if running else "常駐を開始",
                                    style="TButton" if running else "Accent.TButton")

        rules = cfg.get("rules", [])
        enabled_auto = [r for r in rules if r.get("enabled") and r.get("trigger", "auto") == "auto"]
        missing = [f for f in folders if not os.path.isdir(f)]
        banner_sig = (not enabled_auto, missing[0] if missing else None)
        if banner_sig != self._banner_sig:  # 内容が同じなら作り直さない（ちらつき防止）
            self._banner_sig = banner_sig
            for w in self.banner_holder.winfo_children():
                w.destroy()
            if not enabled_auto:
                Banner(self.banner_holder, t, "warn",
                       "自動で実行される有効なルールがありません。ルール画面で作成・有効化してください（サンプルは無効状態です）。",
                       "ルールを開く", lambda: self.app.show("rules")).pack(fill="x", pady=(14, 0))
            if missing:
                Banner(self.banner_holder, t, "warn", f"監視フォルダが見つかりません: {missing[0]}",
                       "設定を開く", lambda: self.app.show("settings")).pack(fill="x", pady=(14, 0))

        # クイック操作
        manual = [r for r in rules if r.get("trigger") == "manual"]
        sig = tuple((r["id"], r["name"]) for r in manual)
        if sig != self._quick_sig:
            self._quick_sig = sig
            for w in self.quick_box.winfo_children():
                w.destroy()
            if manual:
                for r in manual[:12]:
                    btn = ttk.Button(self.quick_box, text=f"▶  {r['name']}",
                                     command=lambda rid=r["id"]: self.app.run_now(rid))
                    btn.pack(side="left", padx=(0, 8), pady=2)
                    self.app.register_run_button(btn, keep_text=True)
                    Tooltip(btn, actions_tooltip(r, cfg), t)
                self.quick_card.pack(fill="x", pady=(14, 0), before=self.stats)
            else:
                self.quick_card.pack_forget()

        hist = store.load_history()
        now = time.time()
        today0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        active = [h for h in hist if not h.get("undone")]
        n = actions.entry_count  # ZIP にまとめた履歴は中のファイル数で数える
        self.stat_vals["today"].configure(text=f"{sum(n(h) for h in active if h.get('time', 0) >= today0)} 件")
        self.stat_vals["week"].configure(text=f"{sum(n(h) for h in active if h.get('time', 0) >= now - 7 * 86400)} 件")
        self.stat_vals["total"].configure(text=f"{sum(n(h) for h in active)} 件")
        self.stat_vals["rules"].configure(text=f"{sum(1 for r in rules if r.get('enabled'))} / {len(rules)}")

        recent = hist[-30:]
        tree_sig = (tuple((h.get("id"), h.get("undone")) for h in recent), tuple(folders))
        if tree_sig == self._tree_sig:
            return
        self._tree_sig = tree_sig
        self.tree.delete(*self.tree.get_children())
        for h in reversed(recent):
            dest = actions.entry_destination(h)
            label = "取消済" if h.get("undone") else actions.entry_label(h)
            tag = "undone" if h.get("undone") else ("error" if h.get("error") else "")
            self.tree.insert("", "end", iid=h.get("id"), tags=(tag,) if tag else (),
                             values=(fmt_datetime(h.get("time")), label, h.get("name"),
                                     display_path(dest, folders) if dest else "", h.get("rule")))

    # ---------------------------------------------------------------- 操作
    def _run(self):
        self.app.run_now(on_done=self.refresh)

    def _toggle_pause(self):
        self.app.set_paused(self.cfg.get("enabled", True))
        self.refresh()

    def _toggle_resident(self):
        if self.app.resident:
            self.app.stop_resident()
        else:
            self.app.start_resident()
        self.after(1300, self.refresh)

    def _open_selected(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        for h in store.load_history():
            if h.get("id") == sel[0]:
                path = h.get("restored_to") if h.get("undone") else actions.entry_destination(h)
                if path and os.path.exists(path):
                    subprocess.Popen(f'explorer /select,"{path}"')
                break


def actions_tooltip(rule: dict, cfg: dict) -> str:
    from ..rules import describe_actions, describe_conditions
    return f"条件: {describe_conditions(rule, cfg.get('categories', {}))}\n動作: {describe_actions(rule)}"
