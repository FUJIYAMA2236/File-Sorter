"""Windows API ヘルパー（ctypes / 標準ライブラリのみ）

常駐プロセスを軽量に保つため、サードパーティ製ライブラリは使わずに
名前付きイベント・ミューテックス・変更通知・ごみ箱・ウィンドウ操作などを提供する。
"""
from __future__ import annotations

import ctypes
import os
import winreg
from ctypes import wintypes as wt

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

HANDLE = wt.HANDLE
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x0
WAIT_ABANDONED_0 = 0x80
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4

# ---------------------------------------------------------------- kernel32
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.CreateEventW.restype = HANDLE
kernel32.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.OpenEventW.restype = HANDLE
kernel32.SetEvent.argtypes = [HANDLE]
kernel32.SetEvent.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.WaitForSingleObject.argtypes = [HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.WaitForMultipleObjects.argtypes = [wt.DWORD, ctypes.POINTER(HANDLE), wt.BOOL, wt.DWORD]
kernel32.WaitForMultipleObjects.restype = wt.DWORD
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = HANDLE
kernel32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.OpenMutexW.restype = HANDLE
kernel32.ReleaseMutex.argtypes = [HANDLE]
kernel32.ReleaseMutex.restype = wt.BOOL
kernel32.FindFirstChangeNotificationW.argtypes = [wt.LPCWSTR, wt.BOOL, wt.DWORD]
kernel32.FindFirstChangeNotificationW.restype = HANDLE
kernel32.FindNextChangeNotification.argtypes = [HANDLE]
kernel32.FindNextChangeNotification.restype = wt.BOOL
kernel32.FindCloseChangeNotification.argtypes = [HANDLE]
kernel32.FindCloseChangeNotification.restype = wt.BOOL


def close_handle(h) -> None:
    if h:
        kernel32.CloseHandle(h)


# ---------------------------------------------------------------- 名前付きイベント
def create_event(name: str) -> int:
    """自動リセットの名前付きイベントを作成（既存なら開く）。"""
    h = kernel32.CreateEventW(None, False, False, name)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def set_event(h) -> None:
    kernel32.SetEvent(h)


def signal_named_event(name: str) -> bool:
    """他プロセスが作成したイベントをシグナル状態にする。存在しなければ False。"""
    h = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not h:
        return False
    try:
        return bool(kernel32.SetEvent(h))
    finally:
        kernel32.CloseHandle(h)


def wait_any(handles: list, timeout_ms: int) -> int:
    """いずれかのハンドルを待つ。戻り値: シグナルされたインデックス、タイムアウト時 -1。"""
    timeout_ms = INFINITE if timeout_ms is None else max(0, min(int(timeout_ms), 0x7FFFFFFF))
    arr = (HANDLE * len(handles))(*handles)
    r = kernel32.WaitForMultipleObjects(len(handles), arr, False, timeout_ms)
    if r == WAIT_TIMEOUT:
        return -1
    if r == WAIT_FAILED:
        raise ctypes.WinError(ctypes.get_last_error())
    if WAIT_ABANDONED_0 <= r < WAIT_ABANDONED_0 + len(handles):
        return r - WAIT_ABANDONED_0
    return r - WAIT_OBJECT_0


# ---------------------------------------------------------------- ミューテックス
class SingleInstance:
    """名前付きミューテックスによる多重起動防止。プロセス終了まで保持する。"""

    def __init__(self, name: str):
        self.handle = kernel32.CreateMutexW(None, False, name)
        self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def release(self):
        close_handle(self.handle)
        self.handle = None


def mutex_exists(name: str) -> bool:
    h = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


class NamedLock:
    """プロセス間ロック（with 文で使用）。仕分け実行・履歴書き込みの排他に使う。"""

    def __init__(self, name: str, timeout_ms: int = 120_000):
        self.name = name
        self.timeout_ms = timeout_ms
        self.handle = None

    def __enter__(self):
        self.handle = kernel32.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        r = kernel32.WaitForSingleObject(self.handle, self.timeout_ms)
        if r not in (WAIT_OBJECT_0, WAIT_ABANDONED_0):
            kernel32.CloseHandle(self.handle)
            self.handle = None
            raise TimeoutError("他の処理が実行中です")
        return self

    def __exit__(self, *exc):
        if self.handle:
            kernel32.ReleaseMutex(self.handle)
            kernel32.CloseHandle(self.handle)
            self.handle = None
        return False


# ---------------------------------------------------------------- フォルダ変更通知
def watch_directory(path: str):
    """フォルダ直下のファイル名変更（作成・削除・リネーム）通知ハンドル。失敗時 None。"""
    h = kernel32.FindFirstChangeNotificationW(path, False, FILE_NOTIFY_CHANGE_FILE_NAME)
    if not h or h == INVALID_HANDLE_VALUE:
        return None
    return h


def rearm_watch(h) -> bool:
    return bool(kernel32.FindNextChangeNotification(h))


def close_watch(h) -> None:
    if h:
        kernel32.FindCloseChangeNotification(h)


# ---------------------------------------------------------------- ごみ箱
class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("wFunc", wt.UINT),
        ("pFrom", wt.LPCWSTR),
        ("pTo", wt.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wt.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wt.LPCWSTR),
    ]


shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
shell32.SHFileOperationW.restype = ctypes.c_int

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


def send_to_recycle_bin(path: str) -> None:
    """ファイルをごみ箱へ送る（完全削除はしない）。失敗時 OSError。"""
    path = os.path.abspath(path)
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = path + "\0"  # 二重 NUL 終端
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    r = shell32.SHFileOperationW(ctypes.byref(op))
    if r != 0 or op.fAnyOperationsAborted:
        raise OSError(f"ごみ箱への移動に失敗しました（コード {r}）: {path}")


# ---------------------------------------------------------------- ウィンドウ / 外観
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.AllowSetForegroundWindow.argtypes = [wt.DWORD]
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND

ASFW_ANY = 0xFFFFFFFF
SW_RESTORE = 9
SW_SHOW = 5


def focus_window(title: str, class_name: str | None = "TkTopLevel") -> bool:
    hwnd = user32.FindWindowW(class_name, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
    user32.SetForegroundWindow(hwnd)
    return True


def allow_foreground_any() -> None:
    try:
        user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        pass


def toplevel_hwnd(tk_widget) -> int:
    return user32.GetParent(tk_widget.winfo_id()) or tk_widget.winfo_id()


def set_titlebar_theme(hwnd: int, dark: bool, caption_rgb: tuple[int, int, int] | None = None) -> None:
    """Windows 10/11 のタイトルバーをダーク/ライトに合わせる（失敗は無視）。"""
    try:
        dwm = ctypes.WinDLL("dwmapi")
        value = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(wt.HWND(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value))
        if caption_rgb:
            r, g, b = caption_rgb
            color = ctypes.c_uint(r | (g << 8) | (b << 16))
            dwm.DwmSetWindowAttribute(wt.HWND(hwnd), 35, ctypes.byref(color), ctypes.sizeof(color))
    except Exception:
        pass


def set_app_user_model_id(app_id: str) -> None:
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


def set_dpi_aware() -> None:
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------- レジストリ
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_startup_command(value_name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            v, _ = winreg.QueryValueEx(k, value_name)
            return v
    except OSError:
        return None


def set_startup_command(value_name: str, command: str | None) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if command:
            winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(k, value_name)
            except FileNotFoundError:
                pass


def system_uses_dark_theme() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return int(v) == 0
    except OSError:
        return False


def downloads_folder() -> str:
    guid = "{374DE290-123F-4565-9164-39C4925E467B}"
    for sub in (r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:
                v, _ = winreg.QueryValueEx(k, guid)
                p = os.path.expandvars(v)
                if os.path.isdir(p):
                    return p
        except OSError:
            continue
    return os.path.join(os.path.expanduser("~"), "Downloads")
