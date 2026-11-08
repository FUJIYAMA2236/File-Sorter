"""プロセス間連携（常駐プロセス ⇔ GUI）

- 常駐プロセスは名前付きイベントを待ち受け、GUI はそれをシグナルして指示を出す。
- 状態は state.json 経由で GUI に伝える。
"""
from __future__ import annotations

import os
import subprocess
import sys

from . import APP_ID
from . import winapi
from .config import APP_DIR as PROJECT_ROOT, FROZEN

EV_RELOAD = rf"Local\{APP_ID}.Reload"
EV_RUN = rf"Local\{APP_ID}.RunNow"
EV_QUIT = rf"Local\{APP_ID}.Quit"
MUTEX_RESIDENT = rf"Local\{APP_ID}.Resident"
MUTEX_GUI = rf"Local\{APP_ID}.GUI"
LOCK_RUN = rf"Local\{APP_ID}.RunLock"

ENTRY_SCRIPT = PROJECT_ROOT / "run.pyw"


def pythonw_exe() -> str:
    exe = sys.executable
    d, name = os.path.split(exe)
    if name.lower() == "python.exe":
        cand = os.path.join(d, "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe


def launch_command(*args: str) -> list[str]:
    if FROZEN:  # exe 版は自分自身を引数付きで起動する
        return [sys.executable, *args]
    return [pythonw_exe(), str(ENTRY_SCRIPT), *args]


def startup_command_line() -> str:
    return subprocess.list2cmdline(launch_command("--background"))


def resident_running() -> bool:
    return winapi.mutex_exists(MUTEX_RESIDENT)


def request_reload() -> bool:
    return winapi.signal_named_event(EV_RELOAD)


def request_run() -> bool:
    return winapi.signal_named_event(EV_RUN)


def request_quit() -> bool:
    return winapi.signal_named_event(EV_QUIT)


def request_quick_action(rule_id: str) -> bool:
    """指定ルールだけを今すぐ実行するよう常駐プロセスに依頼する。"""
    from . import store
    store.write_request(rule_id)
    return winapi.signal_named_event(EV_RUN)


def wait_resident_stopped(timeout: float = 10.0) -> bool:
    import time
    end = time.time() + timeout
    while time.time() < end:
        if not resident_running():
            return True
        time.sleep(0.2)
    return False


def _spawn(args: list[str]) -> subprocess.Popen:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(args, cwd=str(PROJECT_ROOT), creationflags=flags, close_fds=True)


def spawn_resident() -> subprocess.Popen:
    return _spawn(launch_command("--background"))


def spawn_gui(page: str | None = None) -> subprocess.Popen:
    winapi.allow_foreground_any()
    args = launch_command("--gui")
    if page:
        args += ["--page", page]
    return _spawn(args)
