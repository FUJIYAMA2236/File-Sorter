"""常駐プロセス：トレイアイコン + スケジューラ

スレッド構成
  メインスレッド      … TrayIcon のメッセージループ
  スケジューラスレッド … WaitForMultipleObjects で「終了/再読込/今すぐ実行/フォルダ変更」を
                        待ちつつ、指定間隔で仕分けを実行（待機中の CPU 使用率は 0）
"""
from __future__ import annotations

import logging
import os
import threading
import time

from . import APP_ID, APP_NAME, config, engine, ipc, store, winapi
from .config import ASSETS_DIR, watch_folders
from .tray import SEPARATOR, TrayIcon
from .util import fmt_clock

log = logging.getLogger(__name__)

CMD_OPEN, CMD_RUN, CMD_PAUSE, CMD_FOLDER, CMD_QUIT = 1, 2, 3, 4, 9
CMD_QUICK_BASE = 100
INITIAL_DELAY_SEC = 5
PROGRESS_INTERVAL_SEC = 0.25


class Resident:
    def __init__(self):
        self.cfg = config.load_config()
        self.h_quit = winapi.create_event(ipc.EV_QUIT)
        self.h_reload = winapi.create_event(ipc.EV_RELOAD)
        self.h_run = winapi.create_event(ipc.EV_RUN)
        self.watch_handles: list = []
        self.gui_proc = None
        self.next_run_wall: float | None = None
        self.last_text = "まだ実行していません"
        self.running = False
        self.run_seq = int(store.read_state().get("run_seq", 0) or 0)
        self._quick_ids: dict[int, str] = {}
        self.tray = TrayIcon(
            tooltip=APP_NAME,
            icon_path=str(ASSETS_DIR / "icon.ico"),
            paused_icon_path=str(ASSETS_DIR / "icon_paused.ico"),
            on_click=self.open_gui,
            on_command=self.on_command,
            menu_items=self.menu_items,
            on_balloon_click=lambda: self.open_gui("history"),
        )

    # ---------------------------------------------------------------- トレイ
    @property
    def paused(self) -> bool:
        return not self.cfg.get("enabled", True)

    def menu_items(self):
        manual = [r for r in self.cfg.get("rules", []) if r.get("trigger") == "manual"]
        self._quick_ids = {CMD_QUICK_BASE + i: r["id"] for i, r in enumerate(manual[:50])}
        quick = [(cmd, next(r["name"] for r in manual if r["id"] == rid), ("disabled",) if self.running else ())
                 for cmd, rid in self._quick_ids.items()]
        busy = ("disabled",) if self.running else ()
        return [
            (CMD_OPEN, f"{APP_NAME} を開く", ("default",)),
            SEPARATOR,
            (CMD_RUN, "仕分け中…" if self.running else "今すぐ仕分け", busy),
            (0, "クイック操作", (), quick),
            (CMD_PAUSE, "自動仕分けを再開" if self.paused else "自動仕分けを一時停止", ()),
            (CMD_FOLDER, "監視フォルダを開く", ()),
            SEPARATOR,
            (CMD_QUIT, "終了", ()),
        ]

    def on_command(self, cmd: int):
        if cmd == CMD_OPEN:
            self.open_gui()
        elif cmd == CMD_RUN:
            winapi.set_event(self.h_run)
        elif cmd in self._quick_ids:
            store.write_request(self._quick_ids[cmd])
            winapi.set_event(self.h_run)
        elif cmd == CMD_PAUSE:
            enabled = self.paused
            config.update_config(lambda c: c.__setitem__("enabled", enabled))
            winapi.set_event(self.h_reload)
        elif cmd == CMD_FOLDER:
            for f in watch_folders(self.cfg)[:1]:
                if os.path.isdir(f):
                    os.startfile(f)
        elif cmd == CMD_QUIT:
            winapi.set_event(self.h_quit)

    def open_gui(self, page: str | None = None):
        if self.gui_proc is not None and self.gui_proc.poll() is None:
            if winapi.focus_window(APP_NAME):
                return
        try:
            self.gui_proc = ipc.spawn_gui(page)
        except OSError:
            log.exception("GUI の起動に失敗しました")

    def update_tray(self):
        if self.running:
            status = "仕分け中…"
        elif self.paused:
            status = "一時停止中"
        else:
            status = f"次回 {fmt_clock(self.next_run_wall)}" if self.next_run_wall else "監視中"
        self.tray.set_paused(self.paused)
        self.tray.set_tooltip(f"{APP_NAME}（{status}）\n前回: {self.last_text}")

    # ---------------------------------------------------------------- 変更通知
    def _open_watches(self):
        self._close_watches()
        if not self.cfg.get("watch_changes") or self.paused:
            return
        for folder in watch_folders(self.cfg)[:60]:
            h = winapi.watch_directory(folder)
            if h:
                self.watch_handles.append(h)

    def _close_watches(self):
        for h in self.watch_handles:
            winapi.close_watch(h)
        self.watch_handles = []

    # ---------------------------------------------------------------- 実行
    def _do_run(self, manual: bool, rule_id: str | None = None):
        rule_name = ""
        if rule_id:
            rule_name = next((r["name"] for r in self.cfg.get("rules", []) if r["id"] == rule_id), "")
        self.running = True
        started = time.time()
        last_write = [0.0]

        def progress(done, total, name):
            now = time.time()
            if done in (0, total) or now - last_write[0] >= PROGRESS_INTERVAL_SEC:
                last_write[0] = now
                store.write_state(running=True, progress={"done": done, "total": total, "current": name,
                                                          "rule": rule_name, "started": started})

        store.write_state(running=True, progress={"done": 0, "total": 0, "current": "", "rule": rule_name,
                                                  "started": started})
        self.update_tray()
        summary = None
        error = ""
        try:
            summary = engine.run_once(self.cfg, rule_id=rule_id, progress=progress)
        except TimeoutError:
            error = "他の処理が実行中のため実行できませんでした"
            log.info(error)
        except Exception as e:
            log.exception("仕分け中にエラー")
            error = str(e)
        self.running = False
        now = time.time()
        self.run_seq += 1
        if summary is None:
            store.write_state(running=False, progress=None, run_seq=self.run_seq, last_run=now,
                              last_result={"done": 0, "errors": 1, "skipped": 0, "error_messages": [error],
                                           "rule": rule_name, "manual": manual},
                              last_error=error)
            self.update_tray()
            return
        prefix = f"「{rule_name}」" if rule_name else ""
        self.last_text = (f"{fmt_clock(now)} {prefix}{summary.total} 件を処理" if summary.total
                          else f"{fmt_clock(now)} {prefix}対象なし")
        result = summary.to_dict()
        result.update(rule=rule_name, manual=manual, seconds=round(now - started, 2))
        store.write_state(running=False, progress=None, run_seq=self.run_seq, last_run=now, last_result=result,
                          last_error=summary.errors[0][1] if summary.errors else "")
        if self.cfg.get("notify", True) and (summary.total or (manual and summary.errors)):
            self._notify(summary, rule_name)
        log.info("実行完了%s: 処理 %d / エラー %d", f"（{rule_name}）" if rule_name else "", summary.total,
                 len(summary.errors))
        self.update_tray()

    def _notify(self, s: engine.RunSummary, rule_name: str):
        from . import actions
        lines = []
        for e in s.entries[:3]:
            dest = actions.entry_destination(e)
            lines.append(f"{e['name']} → {os.path.basename(dest) if dest not in ('', 'ごみ箱') else dest or '処理済み'}")
        rest = s.total - len(lines)
        if rest > 0:
            lines.append(f"ほか {rest} 件")
        if s.errors:
            lines.append(f"⚠ {len(s.errors)} 件は処理できませんでした")
        head = f"「{rule_name}」で " if rule_name else ""
        title = f"{head}{s.total} 件のファイルを処理しました" if s.total else "処理できないファイルがあります"
        self.tray.notify(title, "\n".join(lines))

    def scheduler_loop(self):
        interval = self.cfg["interval_sec"]
        periodic_due = time.monotonic() + INITIAL_DELAY_SEC
        change_due = None
        self._open_watches()
        try:
            while True:
                now = time.monotonic()
                if self.paused:
                    timeout_ms = None
                    self.next_run_wall = None
                else:
                    due = periodic_due if change_due is None else min(periodic_due, change_due)
                    timeout_ms = max(0, int((due - now) * 1000))
                    self.next_run_wall = time.time() + (due - now)
                store.write_state(next_run=self.next_run_wall, paused=self.paused)
                self.update_tray()

                handles = [self.h_quit, self.h_reload, self.h_run] + self.watch_handles
                idx = winapi.wait_any(handles, timeout_ms)
                now = time.monotonic()

                if idx == 0:  # 終了
                    break
                if idx == 1:  # 設定の再読込
                    was_paused = self.paused
                    self.cfg = config.load_config()
                    if self.cfg["interval_sec"] != interval:
                        interval = self.cfg["interval_sec"]
                        periodic_due = min(periodic_due, now + interval)
                    if was_paused and not self.paused:
                        periodic_due = now + 2
                    change_due = None
                    self._open_watches()
                    log.info("設定を再読込しました")
                    continue
                if idx == 2:  # 今すぐ実行 / クイック操作（一時停止中でも実行）
                    self.cfg = config.load_config()
                    rule_id = store.take_request()
                    self._do_run(manual=True, rule_id=rule_id)
                    if not rule_id:
                        periodic_due = time.monotonic() + interval
                    change_due = None
                    self._open_watches()
                    continue
                if idx >= 3:  # 監視フォルダに変更あり → 待機時間経過後に実行
                    winapi.rearm_watch(handles[idx])
                    change_due = now + self.cfg.get("settle_sec", 30) + 2
                    continue
                if self.paused:
                    continue
                self._do_run(manual=False)
                now = time.monotonic()
                if change_due is not None and change_due <= now + 0.5:
                    change_due = None
                if periodic_due <= now + 0.5:
                    periodic_due = now + interval
                self._open_watches()  # 自分の移動で発生した通知を捨てる
        except Exception:
            log.exception("スケジューラが異常終了しました")
        finally:
            self._close_watches()
            store.write_state(pid=None, next_run=None, running=False, progress=None)
            self.tray.stop()

    def start(self):
        _heal_startup_entry()
        store.write_state(pid=os.getpid(), started=time.time(), paused=self.paused, next_run=None,
                          running=False, progress=None)
        threading.Thread(target=self.scheduler_loop, name="scheduler", daemon=True).start()
        self.tray.run()  # 終了まで戻らない
        log.info("常駐プロセスを終了しました")


def _heal_startup_entry():
    """アプリのフォルダを移動した後でも自動起動が働くよう、登録済みのコマンドを現在の場所に合わせる。"""
    try:
        cur = winapi.get_startup_command(APP_ID)
        want = ipc.startup_command_line()
        if cur and cur != want:
            winapi.set_startup_command(APP_ID, want)
            log.info("自動起動の登録を現在の場所に更新しました")
    except OSError:
        pass


def run_resident() -> int:
    instance = winapi.SingleInstance(ipc.MUTEX_RESIDENT)
    if instance.already_running:
        log.info("常駐プロセスは既に起動しています")
        return 0
    log.info("常駐プロセスを起動しました (pid=%d, データ: %s)", os.getpid(), config.data_dir())
    Resident().start()
    instance.release()
    return 0
