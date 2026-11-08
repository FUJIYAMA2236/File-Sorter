"""テーマ（色・フォント・ttk スタイル）"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .. import winapi

try:
    import sv_ttk
except ImportError:  # sv-ttk が無い場合は標準テーマで動作（見た目のみ劣化）
    sv_ttk = None

PALETTES = {
    "dark": dict(
        bg="#1c1c1c", side="#202020", fg="#fafafa", sub="#a8a8a8", muted="#707070",
        hover="#2a2a2a", active="#2f2f2f", accent="#57c8ff", border="#2f2f2f",
        ok="#6ccb5f", warn="#fce100", err="#ff99a4", info="#57c8ff",
        warn_bg="#433519", info_bg="#1d2f3f", toast_bg="#2b2b2b", text_bg="#1c1c1c",
    ),
    "light": dict(
        bg="#fafafa", side="#f0f0f0", fg="#1c1c1c", sub="#5c5c5c", muted="#9a9a9a",
        hover="#e5e5e5", active="#dcdcdc", accent="#005fb8", border="#e1e1e1",
        ok="#0f7b0f", warn="#9d5d00", err="#c42b1c", info="#005fb8",
        warn_bg="#fff4ce", info_bg="#e6f0fa", toast_bg="#ffffff", text_bg="#fafafa",
    ),
}

ICON_GLYPHS = {
    "home": "", "rules": "", "test": "", "history": "", "settings": "",
    "add": "", "delete": "", "edit": "", "up": "", "down": "",
    "folder": "", "close": "", "play": "", "pause": "", "undo": "",
    "copy": "", "refresh": "", "check": "", "info": "", "warn": "",
    "power": "", "lightning": "",
}


def _pick(families: set, *candidates: str) -> str:
    for c in candidates:
        if c in families:
            return c
    return candidates[-1]


class Theme:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.mode = "dark"
        self.p = PALETTES["dark"]
        self.fonts: dict[str, tkfont.Font] = {}
        fams = set(tkfont.families(root))
        self.family = _pick(fams, "Yu Gothic UI", "Meiryo UI", "Segoe UI")
        self.family_strong = _pick(fams, "Yu Gothic UI Semibold", self.family)
        self.icon_family = _pick(fams, "Segoe Fluent Icons", "Segoe MDL2 Assets", self.family)
        self.has_icons = self.icon_family != self.family

    def icon(self, name: str, fallback: str = "") -> str:
        return ICON_GLYPHS.get(name, fallback) if self.has_icons else fallback

    @property
    def dark(self) -> bool:
        return self.mode == "dark"

    def c(self, key: str) -> str:
        return self.p[key]

    def font(self, key: str) -> tkfont.Font:
        return self.fonts[key]

    def _named_font(self, name: str, **kw) -> tkfont.Font:
        if name in self.fonts:
            self.fonts[name].configure(**kw)
            return self.fonts[name]
        try:
            f = tkfont.Font(self.root, name=name, exists=True)
            f.configure(**kw)
        except tk.TclError:
            f = tkfont.Font(self.root, name=name, **kw)
        self.fonts[name] = f
        return f

    def apply(self, mode_setting: str) -> None:
        if mode_setting == "system":
            mode = "dark" if winapi.system_uses_dark_theme() else "light"
        else:
            mode = mode_setting if mode_setting in PALETTES else "dark"
        self.mode = mode
        self.p = PALETTES[mode]
        if sv_ttk is not None:
            sv_ttk.set_theme(mode, self.root)
        self._setup_fonts()
        self._setup_styles()

    def _setup_fonts(self) -> None:
        fam, strong = self.family, self.family_strong
        # sv-ttk 既定フォント（Segoe UI Variable / ピクセル指定）を日本語フォント・ポイント指定に置き換える
        for name, family, size in (
            ("SunValleyCaptionFont", fam, 9), ("SunValleyBodyFont", fam, 10),
            ("SunValleyBodyStrongFont", strong, 10), ("SunValleyBodyLargeFont", fam, 12),
            ("SunValleySubtitleFont", strong, 14), ("SunValleyTitleFont", strong, 20),
            ("SunValleyTitleLargeFont", strong, 26), ("SunValleyDisplayFont", strong, 40),
            ("TkDefaultFont", fam, 10), ("TkTextFont", fam, 10), ("TkMenuFont", fam, 10),
            ("TkHeadingFont", fam, 9),
        ):
            self._named_font(name, family=family, size=size)
        self._named_font("App.Body", family=fam, size=10)
        self._named_font("App.Small", family=fam, size=9)
        self._named_font("App.Strong", family=strong, size=10)
        self._named_font("App.Subtitle", family=strong, size=13)
        self._named_font("App.Title", family=strong, size=20)
        self._named_font("App.Stat", family=strong, size=24)
        self._named_font("App.Brand", family=strong, size=12)
        self._named_font("App.Icon", family=self.icon_family, size=12)
        self._named_font("App.IconSmall", family=self.icon_family, size=10)
        self._named_font("App.IconLarge", family=self.icon_family, size=20)
        self._named_font("App.Mono", family=_pick(set(tkfont.families(self.root)), "BIZ UDGothic", "MS Gothic", "Consolas"), size=10)

    def _setup_styles(self) -> None:
        s = ttk.Style(self.root)
        p = self.p
        s.configure("TButton", padding=(14, 5))
        s.configure("Accent.TButton", padding=(14, 5))
        s.configure("Small.TButton", padding=(8, 2), font="App.Small")
        s.configure("Icon.TButton", padding=(7, 5), font="App.IconSmall")
        s.configure("Title.TLabel", font="App.Title")
        s.configure("Subtitle.TLabel", font="App.Subtitle")
        s.configure("Strong.TLabel", font="App.Strong")
        s.configure("Sub.TLabel", foreground=p["sub"], font="App.Small")
        s.configure("Muted.TLabel", foreground=p["muted"])
        s.configure("Stat.TLabel", font="App.Stat")
        s.configure("Ok.TLabel", foreground=p["ok"], font="App.Strong")
        s.configure("Warn.TLabel", foreground=p["warn"], font="App.Strong")
        s.configure("Err.TLabel", foreground=p["err"], font="App.Strong")
        s.configure("Link.TLabel", foreground=p["accent"])
        s.configure("Icon.TLabel", font="App.Icon")
        s.configure("BigIcon.TLabel", font="App.IconLarge")
        line = tkfont.Font(self.root, font="App.Body").metrics("linespace")
        s.configure("Treeview", rowheight=int(line * 1.75), font="App.Body")
        s.configure("Heading", font="App.Small")
        s.configure("TCheckbutton", font="App.Body")
        s.configure("TRadiobutton", font="App.Body")
        s.configure("TNotebook.Tab", font="App.Body")

    def titlebar(self, window: tk.Misc) -> None:
        try:
            window.update_idletasks()
            hwnd = winapi.toplevel_hwnd(window)
            rgb = tuple(int(self.p["side"][i:i + 2], 16) for i in (1, 3, 5))
            winapi.set_titlebar_theme(hwnd, self.dark, rgb)
        except Exception:
            pass
