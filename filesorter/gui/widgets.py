"""共通ウィジェット"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class ScrollFrame(ttk.Frame):
    """縦スクロール可能なフレーム。中身は .body に配置する。"""

    def __init__(self, master, theme, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=theme.c("bg"))
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_body(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def can_scroll(self) -> bool:
        return self.body.winfo_reqheight() > self.canvas.winfo_height()

    def scroll(self, units: int):
        if self.can_scroll():
            self.canvas.yview_scroll(units, "units")

    def to_top(self):
        self.canvas.yview_moveto(0)


def install_mousewheel(root: tk.Tk) -> None:
    """ホイールをポインタ下の ScrollFrame に振り分ける（Treeview 等は自前で処理）。"""
    native = {"Treeview", "Text", "Listbox", "TCombobox", "TSpinbox"}

    def on_wheel(e):
        try:
            w = root.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        while w is not None:
            if w.winfo_class() in native:
                return
            if isinstance(w, ScrollFrame):
                w.scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1))
                return
            w = getattr(w, "master", None)

    root.bind_all("<MouseWheel>", on_wheel, add="+")


class NavItem(tk.Canvas):
    """サイドバーのナビゲーション項目（角丸ハイライト + アクセントバー）"""

    HEIGHT = 40

    def __init__(self, master, theme, icon: str, text: str, command):
        super().__init__(master, height=self.HEIGHT, bg=theme.c("side"), highlightthickness=0, bd=0,
                         cursor="hand2")
        self.theme = theme
        self.icon_text = icon
        self.text = text
        self.command = command
        self.selected = False
        self.hover = False
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.command())

    def _set_hover(self, v: bool):
        self.hover = v
        self.redraw()

    def set_selected(self, v: bool):
        self.selected = v
        self.redraw()

    def redraw(self):
        t = self.theme
        self.delete("all")
        w = self.winfo_width()
        h = self.HEIGHT
        if self.selected or self.hover:
            fill = t.c("active") if self.selected else t.c("hover")
            round_rect(self, 6, 3, w - 6, h - 3, 10, fill=fill, outline=fill)
        if self.selected:
            round_rect(self, 6, h / 2 - 9, 9, h / 2 + 9, 2, fill=t.c("accent"), outline=t.c("accent"))
        x = 22
        if self.icon_text:
            self.create_text(x, h / 2, text=self.icon_text, font=t.font("App.Icon"), fill=t.c("fg"), anchor="w")
            x += 32
        self.create_text(x, h / 2, text=self.text, anchor="w", fill=t.c("fg"),
                         font=t.font("App.Strong" if self.selected else "App.Body"))


class Tooltip:
    def __init__(self, widget, text: str, theme):
        self.widget = widget
        self.text = text
        self.theme = theme
        self.tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(450, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if self.tip or not self.widget.winfo_viewable():
            return
        t = self.theme
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.configure(bg=t.c("border"))
        tk.Label(self.tip, text=self.text, bg=t.c("toast_bg"), fg=t.c("fg"), font=t.font("App.Small"),
                 padx=8, pady=4, justify="left").pack(padx=1, pady=1)
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


def make_tree(parent, columns, height=10, selectmode="browse"):
    """columns: [(id, 見出し, 幅, anchor, stretch)]"""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height,
                        selectmode=selectmode)
    for cid, heading, width, anchor, stretch in columns:
        tree.heading(cid, text=heading, anchor=anchor)
        tree.column(cid, width=width, minwidth=40, anchor=anchor, stretch=stretch)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, tree


def card(parent, padding=16, **kw) -> ttk.Frame:
    return ttk.Frame(parent, style="Card.TFrame", padding=padding, **kw)


def section_label(parent, text: str) -> ttk.Label:
    return ttk.Label(parent, text=text, style="Subtitle.TLabel")


def make_text(parent, theme, height=8, wrap="word") -> tuple[ttk.Frame, tk.Text]:
    """読み取り専用の結果表示用 Text（タグ: h, ok, ng, muted, err, accent）"""
    t = theme
    frame = ttk.Frame(parent, style="Card.TFrame", padding=2)
    text = tk.Text(frame, height=height, wrap=wrap, bd=0, highlightthickness=0, padx=12, pady=10,
                   bg=t.c("text_bg"), fg=t.c("fg"), insertbackground=t.c("fg"), font=t.font("App.Body"),
                   selectbackground="#2f60d8", selectforeground="#ffffff", cursor="arrow")
    vsb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=vsb.set)
    text.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    text.tag_configure("h", font=t.font("App.Strong"), spacing1=4, spacing3=4)
    text.tag_configure("ok", foreground=t.c("ok"))
    text.tag_configure("ng", foreground=t.c("err"))
    text.tag_configure("muted", foreground=t.c("muted"))
    text.tag_configure("sub", foreground=t.c("sub"))
    text.tag_configure("err", foreground=t.c("err"))
    text.tag_configure("warn", foreground=t.c("warn"))
    text.tag_configure("accent", foreground=t.c("accent"))
    text.configure(state="disabled")
    return frame, text


def set_text(text: tk.Text, chunks) -> None:
    """chunks: [(文字列, タグ or None)]"""
    text.configure(state="normal")
    text.delete("1.0", "end")
    for s, tag in chunks:
        text.insert("end", s, tag or ())
    text.configure(state="disabled")


class Banner(tk.Frame):
    """情報/警告バー"""

    def __init__(self, master, theme, kind="info", text="", action_text=None, action=None):
        t = theme
        bg = t.c("warn_bg" if kind == "warn" else "info_bg")
        fg = t.c("warn" if kind == "warn" else "info")
        super().__init__(master, bg=bg, padx=14, pady=10)
        icon = t.icon("warn" if kind == "warn" else "info", "!")
        tk.Label(self, text=icon, bg=bg, fg=fg, font=t.font("App.Icon")).pack(side="left", padx=(0, 10))
        self.label = tk.Label(self, text=text, bg=bg, fg=t.c("fg"), font=t.font("App.Body"), justify="left",
                              anchor="w")
        self.label.pack(side="left", fill="x", expand=True)
        if action_text:
            ttk.Button(self, text=action_text, command=action).pack(side="right")
