"""メインウィンドウ（サイドバー・状態ポーリング・実行と進捗表示・トースト）"""
from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .. import APP_NAME, __version__, config, engine, ipc, store, winapi
from ..config import ASSETS_DIR
from ..util import elide_middle
from .theme import Theme
from .widgets import NavItem, Tooltip, install_mousewheel

log = logging.getLogger(__name__)

PAGES = [
    ("home", "home", "ホーム"),
    ("rules", "rules", "ルール"),
    ("test", "test", "テスト"),
    ("history", "history", "履歴"),
    ("settings", "settings", "設定"),
]
POLL_IDLE_MS = 2000
POLL_BUSY_MS = 300
RUN_TIMEOUT_SEC = 300


class App:
    def __init__(self, page: str | None = None):
        winapi.set_dpi_aware()
        winapi.set_app_user_model_id("FileSortKun.App")
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        try:
            self.root.iconbitmap(default=str(ASSETS_DIR / "icon.ico"))
        except tk.TclError:
            pass
        self.cfg = config.load_config()
        self.theme = Theme(self.root)
        self.theme.apply(self.cfg.get("theme", "system"))
        self.pages: dict = {}
        self.nav: dict = {}
        self.current = None
        self.container = None
        self._toast = None
        self._last_state_stamp = None
        self.state: dict = store.read_state()
        self.resident = ipc.resident_running()
        # 実行状態
        self.run_buttons: list = []
        self._await_seq = None      # 常駐に依頼した実行の完了待ち（依頼時の run_seq）
        self._await_since = 0.0
        self._await_label = ""
        self._local = None          # 常駐なしで GUI 内実行中の進捗 {"done","total","current","started","rule"}
        self._run_done_callbacks: list = []
        self._poll_after = None
        install_mousewheel(self.root)
        self._build()
        self._center(1180, 780)
        self.root.minsize(1000, 660)
        self.show(page if page in {p[0] for p in PAGES} else "home")
        self.root.deiconify()
        self.theme.titlebar(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-w>", lambda e: self.close())
        self._schedule_poll(200)

    # ---------------------------------------------------------------- 構築
    def _center(self, w, h):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(w, sw - 80), min(h, sh - 120)
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 30)}")

    def _build(self):
        t = self.theme
        self.root.configure(bg=t.c("bg"))
        self.run_buttons = []
        self._buttons_busy = None   # ボタンに最後に反映した実行中状態
        self._status_shown = None   # サイドバーの状態表示に最後に反映した (色, 文字)
        self.container = tk.Frame(self.root, bg=t.c("bg"))
        self.container.pack(fill="both", expand=True)
        side = tk.Frame(self.container, bg=t.c("side"), width=236)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(self.container, bg=t.c("border"), width=1).pack(side="left", fill="y")
        self.content = ttk.Frame(self.container)
        self.content.pack(side="left", fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        brand = tk.Frame(side, bg=t.c("side"))
        brand.pack(fill="x", padx=18, pady=(20, 18))
        try:
            self._logo = tk.PhotoImage(file=str(ASSETS_DIR / "icon_32.png"))
            tk.Label(brand, image=self._logo, bg=t.c("side")).pack(side="left", padx=(0, 10))
        except tk.TclError:
            pass
        tk.Label(brand, text=APP_NAME, bg=t.c("side"), fg=t.c("fg"), font=t.font("App.Brand")).pack(side="left")

        for key, icon, label in PAGES:
            item = NavItem(side, t, t.icon(icon), label, lambda k=key: self.show(k))
            item.pack(fill="x", padx=6, pady=1)
            self.nav[key] = item

        foot = tk.Frame(side, bg=t.c("side"))
        foot.pack(side="bottom", fill="x", padx=14, pady=14)
        row = tk.Frame(foot, bg=t.c("side"))
        row.pack(fill="x", padx=6)
        self.status_dot = tk.Label(row, text="●", bg=t.c("side"), fg=t.c("muted"), font=t.font("App.Small"))
        self.status_dot.pack(side="left")
        self.status_text = tk.Label(row, text="確認中…", bg=t.c("side"), fg=t.c("sub"), font=t.font("App.Small"))
        self.status_text.pack(side="left", padx=(6, 0))
        row2 = tk.Frame(foot, bg=t.c("side"))
        row2.pack(fill="x", padx=6, pady=(8, 0))
        tk.Label(row2, text=f"v{__version__}" + ("　ポータブル" if config.is_portable() else ""), bg=t.c("side"),
                 fg=t.c("muted"), font=t.font("App.Small")).pack(side="left")
        quit_btn = ttk.Button(row2, text=t.icon("power", "⏻"), style="Icon.TButton", command=self.quit_all)
        quit_btn.pack(side="right")
        Tooltip(quit_btn, "アプリを完全に終了（常駐も停止）", t)

        # 実行中パネル（右下）
        self.busy_panel = tk.Frame(self.root, bg=t.c("border"))
        inner = tk.Frame(self.busy_panel, bg=t.c("toast_bg"), padx=16, pady=12)
        inner.pack(padx=1, pady=1)
        self.busy_title = tk.Label(inner, text="", bg=t.c("toast_bg"), fg=t.c("fg"), font=t.font("App.Strong"),
                                   anchor="w")
        self.busy_title.pack(fill="x")
        self.busy_bar = ttk.Progressbar(inner, length=300, mode="determinate", maximum=100)
        self.busy_bar.pack(fill="x", pady=(8, 6))
        self.busy_detail = tk.Label(inner, text="", bg=t.c("toast_bg"), fg=t.c("sub"), font=t.font("App.Small"),
                                    anchor="w", width=44)
        self.busy_detail.pack(fill="x")
        self._busy_visible = False

    def _page_class(self, key):
        if key == "home":
            from .page_home import HomePage as cls
        elif key == "rules":
            from .page_rules import RulesPage as cls
        elif key == "test":
            from .page_test import TestPage as cls
        elif key == "history":
            from .page_history import HistoryPage as cls
        else:
            from .page_settings import SettingsPage as cls
        return cls

    def show(self, key: str):
        if self.current == key:
            return
        if self.current and self.current in self.pages:
            self.pages[self.current].on_hide()
        if key not in self.pages:
            page = self._page_class(key)(self.content, self)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page
        for k, item in self.nav.items():
            item.set_selected(k == key)
        self.current = key
        page = self.pages[key]
        page.tkraise()
        self.reload_config()
        page.on_show()
        self._update_run_buttons()

    def rebuild(self):
        """テーマ変更時に UI を作り直す"""
        current = self.current or "home"
        self.container.destroy()
        self.busy_panel.destroy()
        self.pages.clear()
        self.nav.clear()
        self.current = None
        self.theme.apply(self.cfg.get("theme", "system"))
        self._build()
        self.show(current)
        self.theme.titlebar(self.root)
        self._update_status_widgets()

    # ---------------------------------------------------------------- 設定
    def reload_config(self) -> dict:
        self.cfg = config.load_config()
        return self.cfg

    def update_config(self, mutator) -> dict:
        self.cfg = config.update_config(mutator)
        ipc.request_reload()
        return self.cfg

    # ---------------------------------------------------------------- 状態ポーリング
    @property
    def busy(self) -> bool:
        return self._await_seq is not None or self._local is not None or bool(self.state.get("running"))

    def _schedule_poll(self, ms=None):
        if self._poll_after:
            try:
                self.root.after_cancel(self._poll_after)
            except tk.TclError:
                pass
        self._poll_after = self.root.after(ms if ms is not None else (POLL_BUSY_MS if self.busy else POLL_IDLE_MS),
                                           self._poll)

    def _poll(self):
        self._poll_after = None
        try:
            self.resident = ipc.resident_running()
            self.state = store.read_state() if self.resident or self._await_seq is not None else {
                **store.read_state(), "running": False}
            self._check_run_finished()
            self._update_busy_panel()
            self._update_status_widgets()
            self._update_run_buttons()
            stamp = (self.resident, self.state.get("run_seq"), self.state.get("paused"), bool(self.state.get("running")))
            if stamp != self._last_state_stamp:
                self._last_state_stamp = stamp
                page = self.pages.get(self.current)
                if page is not None:
                    page.on_state_changed()
        except Exception:
            log.exception("状態の取得に失敗")
        self._schedule_poll()

    def _update_status_widgets(self):
        t = self.theme
        if self.busy:
            color, text = t.c("accent"), "実行中…"
        elif not self.resident:
            color, text = t.c("err"), "常駐プロセス停止中"
        elif not self.cfg.get("enabled", True):
            color, text = t.c("warn"), "一時停止中"
        else:
            color, text = t.c("ok"), "監視中"
        if (color, text) == self._status_shown:
            return
        try:
            self.status_dot.configure(fg=color)
            self.status_text.configure(text=text)
            self._status_shown = (color, text)
        except tk.TclError:
            pass

    # ---------------------------------------------------------------- 実行
    def register_run_button(self, button: ttk.Button, keep_text: bool = False):
        """「今すぐ仕分け」系のボタンを登録すると、実行中は自動で無効化される（連打防止）。"""
        self.run_buttons.append((button, button.cget("text"), keep_text))
        self._update_run_buttons(force=True)

    def _update_run_buttons(self, force: bool = False):
        # 定期ポーリングのたびに configure するとボタンが再描画されてちらつくため、状態が変わったときだけ反映する
        if not force and self.busy == self._buttons_busy:
            return
        self._buttons_busy = self.busy
        alive = []
        for btn, text, keep in self.run_buttons:
            try:
                if self.busy:
                    btn.configure(state="disabled", text=text if keep else "実行中…")
                else:
                    btn.configure(state="normal", text=text)
                alive.append((btn, text, keep))
            except tk.TclError:
                pass
        self.run_buttons = alive

    def run_now(self, rule_id: str | None = None, on_done=None):
        """今すぐ仕分け（rule_id 指定でそのルールだけを実行するクイック操作）"""
        if self.busy:
            self.toast("実行中です。終わるまでお待ちください", "warn")
            return
        cfg = self.reload_config()
        label = ""
        if rule_id:
            label = next((r["name"] for r in cfg.get("rules", []) if r["id"] == rule_id), "")
        if on_done:
            self._run_done_callbacks.append(on_done)
        if ipc.resident_running():
            self._await_seq = int(self.state.get("run_seq", 0) or 0)
            self._await_since = time.time()
            self._await_label = label
            if rule_id:
                ipc.request_quick_action(rule_id)
            else:
                ipc.request_run()
        else:
            self._local = {"done": 0, "total": 0, "current": "", "started": time.time(), "rule": label}

            def progress(done, total, name):
                self._local.update(done=done, total=total, current=name)

            def work():
                try:
                    s = engine.run_once(cfg, rule_id=rule_id, progress=progress)
                    result = {**s.to_dict(), "rule": label, "seconds": time.time() - self._local["started"]}
                except Exception as e:
                    result = {"done": 0, "errors": 1, "error_messages": [str(e)], "rule": label}
                self.root.after(0, lambda: self._finish_run(result))

            threading.Thread(target=work, daemon=True).start()
        self._update_busy_panel()
        self._update_run_buttons()
        self._update_status_widgets()
        self._schedule_poll(POLL_BUSY_MS)

    def _check_run_finished(self):
        if self._await_seq is None:
            return
        seq = int(self.state.get("run_seq", 0) or 0)
        if seq > self._await_seq and not self.state.get("running"):
            self._finish_run(self.state.get("last_result") or {})
        elif not self.resident:
            self._finish_run({"done": 0, "errors": 1, "error_messages": ["常駐プロセスが終了しました"]})
        elif time.time() - self._await_since > RUN_TIMEOUT_SEC:
            self._finish_run({"done": 0, "errors": 1, "error_messages": ["応答がありません（ログを確認してください）"]})

    def _finish_run(self, result: dict):
        self._await_seq = None
        self._local = None
        done = int(result.get("done", 0) or 0)
        errors = int(result.get("errors", 0) or 0)
        name = f"「{result['rule']}」: " if result.get("rule") else ""
        sec = result.get("seconds")
        took = f"（{sec:.1f} 秒）" if isinstance(sec, (int, float)) else ""
        if done:
            msg, kind = f"{name}{done} 件を処理しました{took}", "ok"
        elif errors:
            msg, kind = f"{name}処理できませんでした", "err"
        else:
            msg, kind = f"{name}対象のファイルはありませんでした{took}", "ok"
        if errors and done:
            msg += f"　⚠ {errors} 件エラー"
            kind = "warn"
        if errors and result.get("error_messages"):
            msg += f"\n{elide_middle(result['error_messages'][0], 70)}"
        self._update_busy_panel()
        self._update_run_buttons()
        self._update_status_widgets()
        self.toast(msg, kind, 5000)
        callbacks, self._run_done_callbacks = self._run_done_callbacks, []
        for cb in callbacks:
            try:
                cb()
            except Exception:
                log.exception("完了時の処理でエラー")
        page = self.pages.get(self.current)
        if page is not None:
            page.on_state_changed()

    def _update_busy_panel(self):
        prog = None
        if self._local is not None:
            prog = self._local
        elif self.state.get("running"):
            prog = self.state.get("progress") or {}
        elif self._await_seq is not None:
            prog = {"done": 0, "total": 0, "current": "", "started": self._await_since, "rule": self._await_label,
                    "waiting": True}
        if prog is None:
            if self._busy_visible:
                self.busy_panel.place_forget()
                self._busy_visible = False
            return
        done, total = int(prog.get("done") or 0), int(prog.get("total") or 0)
        rule = prog.get("rule") or ""
        title = f"「{rule}」を実行中…" if rule else "仕分け中…"
        if prog.get("waiting"):
            title = "開始を待っています…"
        if total:
            title += f"　{done} / {total}"
            if str(self.busy_bar.cget("mode")) != "determinate":
                self.busy_bar.stop()  # stop() は値を 0 に戻すため、値の設定より前に呼ぶ
                self.busy_bar.configure(mode="determinate")
            self.busy_bar.configure(value=done * 100 / total)
        else:
            if str(self.busy_bar.cget("mode")) != "indeterminate":
                self.busy_bar.configure(mode="indeterminate")
                self.busy_bar.start(12)
        elapsed = time.time() - float(prog.get("started") or time.time())
        cur = prog.get("current") or ("対象のファイルを確認しています" if not total else "")
        self.busy_title.configure(text=title)
        self.busy_detail.configure(text=f"{elide_middle(cur, 40)}　（{elapsed:.0f} 秒経過）" if cur else
                                   f"{elapsed:.0f} 秒経過")
        if not self._busy_visible:
            self.busy_panel.place(relx=1.0, rely=1.0, x=-24, y=-24, anchor="se")
            self.busy_panel.lift()
            self._busy_visible = True

    # ---------------------------------------------------------------- 常駐の操作
    def set_paused(self, paused: bool):
        self.update_config(lambda c: c.__setitem__("enabled", not paused))
        self._update_status_widgets()
        self.toast("自動仕分けを一時停止しました" if paused else "自動仕分けを再開しました")

    def start_resident(self):
        if not ipc.resident_running():
            ipc.spawn_resident()
            self.toast("常駐プロセスを起動しました")
        self._schedule_poll(1200)

    def stop_resident(self, confirm: bool = True) -> bool:
        if not ipc.resident_running():
            return True
        if confirm and not messagebox.askyesno(
                "常駐プロセスの停止", "常駐プロセスを停止しますか？\n停止中は自動仕分けとトレイアイコンが無くなります。"
                                     "\n（ホーム画面からいつでも再開できます）", parent=self.root):
            return False
        ipc.request_quit()
        ipc.wait_resident_stopped(5)
        self.toast("常駐プロセスを停止しました")
        self._schedule_poll(100)
        return True

    def restart_resident(self):
        if ipc.resident_running():
            ipc.request_quit()
            ipc.wait_resident_stopped(8)
        ipc.spawn_resident()
        self._schedule_poll(1200)

    def quit_all(self):
        if messagebox.askyesno("終了", f"{APP_NAME} を完全に終了しますか？\n常駐プロセスも停止し、自動仕分けは行われなくなります。",
                               parent=self.root):
            ipc.request_quit()
            self.close()

    # ---------------------------------------------------------------- トースト
    def toast(self, text: str, kind: str = "ok", ms: int = 3200):
        t = self.theme
        if self._toast is not None:
            try:
                self._toast.destroy()
            except tk.TclError:
                pass
        color = {"ok": t.c("ok"), "warn": t.c("warn"), "err": t.c("err")}.get(kind, t.c("accent"))
        outer = tk.Frame(self.root, bg=t.c("border"))
        inner = tk.Frame(outer, bg=t.c("toast_bg"), padx=16, pady=10)
        inner.pack(padx=1, pady=1)
        tk.Label(inner, text="●", fg=color, bg=t.c("toast_bg"), font=t.font("App.Small")).pack(side="left", anchor="n")
        tk.Label(inner, text=text, fg=t.c("fg"), bg=t.c("toast_bg"), font=t.font("App.Body"), justify="left").pack(
            side="left", padx=(8, 0))
        y = -24
        if self._busy_visible:
            y -= self.busy_panel.winfo_height() + 12
        outer.place(relx=1.0, rely=1.0, x=-24, y=y, anchor="se")
        outer.lift()
        self._toast = outer
        self.root.after(ms, lambda w=outer: w.destroy() if w.winfo_exists() else None)

    def close(self):
        self.root.destroy()

    def mainloop(self):
        self.root.mainloop()


def run_gui(page: str | None = None) -> int:
    instance = winapi.SingleInstance(ipc.MUTEX_GUI)
    if instance.already_running:
        winapi.focus_window(APP_NAME)
        return 0
    app = App(page)
    app.mainloop()
    instance.release()
    return 0
