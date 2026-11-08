"""通知領域（タスクトレイ）アイコン — Win32 API を ctypes で直接利用（依存ライブラリなし）"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes as wt

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
NIN_BALLOONUSERCLICK = 0x0405

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_USER, NIIF_NOSOUND, NIIF_LARGE_ICON = 0x4, 0x10, 0x20

MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED, MF_POPUP = 0x0, 0x800, 0x1, 0x8, 0x10
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CYSMICON, SM_CXICON, SM_CYICON = 49, 50, 11, 12
IDI_APPLICATION = 32512


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                ("time", wt.DWORD), ("pt", POINT)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

SEPARATOR = None


class TrayIcon:
    """メインスレッドで run() を呼ぶとメッセージループに入る。他スレッドからの
    set_tooltip / set_icon / notify / stop 呼び出しは安全。"""

    CLASS_NAME = "FileSortKunTrayWindow"

    def __init__(self, tooltip: str, icon_path: str, paused_icon_path: str,
                 on_click, on_command, menu_items, on_balloon_click=None):
        self.tooltip = tooltip
        self.on_click = on_click
        self.on_command = on_command
        self.menu_items = menu_items  # () -> [(id, label, flags) | None]
        self.on_balloon_click = on_balloon_click
        self.hwnd = None
        self._wndproc = WNDPROC(self._proc)
        self._hinst = kernel32.GetModuleHandleW(None)
        self._icons = {
            False: self._load_icon(icon_path, SM_CXSMICON, SM_CYSMICON),
            True: self._load_icon(paused_icon_path, SM_CXSMICON, SM_CYSMICON),
        }
        self._balloon_icon = self._load_icon(icon_path, SM_CXICON, SM_CYICON)
        self._paused = False
        self._msg_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

    # ---------------------------------------------------------------- 内部
    def _load_icon(self, path, mx, my):
        h = user32.LoadImageW(None, path, IMAGE_ICON, user32.GetSystemMetrics(mx),
                              user32.GetSystemMetrics(my), LR_LOADFROMFILE)
        return h or user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icons[self._paused]
        nid.szTip = self.tooltip[:127]
        return nid

    def _add_icon(self):
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            log.warning("トレイアイコンの追加に失敗しました")

    def _build_menu(self, items):
        menu = user32.CreatePopupMenu()
        default_id = None
        for item in items:
            if item is SEPARATOR:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            cmd, label, flags = item[:3]
            children = item[3] if len(item) > 3 else None
            if children is not None:  # サブメニュー
                sub, _ = self._build_menu(children)
                user32.AppendMenuW(menu, MF_POPUP | (MF_GRAYED if not children else 0), sub, label)
                continue
            mf = MF_STRING
            if "disabled" in flags:
                mf |= MF_GRAYED
            if "checked" in flags:
                mf |= MF_CHECKED
            user32.AppendMenuW(menu, mf, cmd, label)
            if "default" in flags:
                default_id = cmd
        return menu, default_id

    def _show_menu(self):
        menu, default_id = self._build_menu(self.menu_items())
        if default_id is not None:
            user32.SetMenuDefaultItem(menu, default_id, 0)
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd:
            self.on_command(cmd)

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    self.on_click()
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                elif event == NIN_BALLOONUSERCLICK and self.on_balloon_click:
                    self.on_balloon_click()
                return 0
            if msg == self._msg_taskbar_created and msg != 0:
                self._add_icon()  # エクスプローラ再起動時に再登録
                return 0
            if msg == WM_CLOSE:
                nid = self._nid(0)
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("トレイのイベント処理でエラー")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------------------------------------------------------------- 公開 API
    def run(self):
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinst
        wc.lpszClassName = self.CLASS_NAME
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.CLASS_NAME, self.CLASS_NAME, 0, 0, 0, 0, 0,
                                           None, None, self._hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._add_icon()
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def set_tooltip(self, text: str):
        self.tooltip = text
        if self.hwnd:
            nid = self._nid(NIF_TIP)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def set_paused(self, paused: bool):
        self._paused = bool(paused)
        if self.hwnd:
            nid = self._nid(NIF_ICON)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def notify(self, title: str, message: str):
        if not self.hwnd:
            return
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = message[:255]
        nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON | NIIF_NOSOUND
        nid.hBalloonIcon = self._balloon_icon
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
