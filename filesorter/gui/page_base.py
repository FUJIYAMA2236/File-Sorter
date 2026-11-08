"""ページの基底クラス"""
from __future__ import annotations

from tkinter import ttk


class Page(ttk.Frame):
    title = ""
    subtitle = ""

    def __init__(self, master, app):
        super().__init__(master, padding=(32, 26, 32, 22))
        self.app = app
        self.theme = app.theme
        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text=self.title, style="Title.TLabel").pack(side="left")
        self.actions = ttk.Frame(header)
        self.actions.pack(side="right")
        if self.subtitle:
            ttk.Label(self, text=self.subtitle, style="Sub.TLabel").pack(anchor="w", pady=(2, 0))
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, pady=(18, 0))
        self.build()

    @property
    def cfg(self) -> dict:
        return self.app.cfg

    def build(self):
        pass

    def on_show(self):
        pass

    def on_hide(self):
        pass

    def on_state_changed(self):
        pass
