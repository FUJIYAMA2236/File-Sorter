"""テスト（フォルダでシミュレーション / 仮想ファイルで判定）"""
from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, ttk

from .. import engine, store
from ..config import watch_folders
from ..engine import STATUS_LABELS
from ..rules import FileInfo, SIZE_UNITS
from ..util import display_path, fmt_datetime, fmt_size
from . import trace
from .page_base import Page
from .widgets import card, make_text, make_tree, set_text

STATUS_TAGS = {"run": "run", "ignore": "ignore", "nomatch": "muted", "skip": "muted", "error": "error"}
AGE_CHOICES = [("分", 60), ("時間", 3600), ("日", 86400)]


class TestPage(Page):
    title = "テスト"
    subtitle = "保存済みのルールで、ファイルがどう仕分けられるかを確認します。ファイルは一切移動しません。"

    def build(self):
        nb = ttk.Notebook(self.body)
        nb.pack(fill="both", expand=True)
        self.tab_sim = ttk.Frame(nb, padding=(4, 14, 4, 4))
        self.tab_virtual = ttk.Frame(nb, padding=(4, 14, 4, 4))
        nb.add(self.tab_sim, text="  フォルダでシミュレーション  ")
        nb.add(self.tab_virtual, text="  仮想ファイルで判定  ")
        self._build_sim()
        self._build_virtual()
        self._items = {}

    # ================================================================ シミュレーション
    def _build_sim(self):
        t = self.theme
        f = self.tab_sim
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        run = ttk.Button(bar, text="今すぐ仕分け", command=lambda: self.app.run_now(on_done=self.simulate))
        run.pack(side="right")
        self.app.register_run_button(run)
        ttk.Label(bar, text="対象フォルダ").pack(side="left")
        self.v_folder = tk.StringVar()
        self.cb_folder = ttk.Combobox(bar, textvariable=self.v_folder, width=40)
        self.cb_folder.pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="参照…", command=self._browse).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="シミュレーション", style="Accent.TButton", command=self.simulate).pack(side="left", padx=(12, 0))
        self.v_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="仕分け対象のみ表示", variable=self.v_only, command=self._render).pack(side="left", padx=(14, 0))

        self.sim_summary = ttk.Label(f, text="「シミュレーション」を押すと、フォルダ内の各ファイルの判定結果を一覧表示します。",
                                     style="Sub.TLabel")
        self.sim_summary.pack(anchor="w", pady=(10, 8))

        pw = ttk.Panedwindow(f, orient="vertical")
        pw.pack(fill="both", expand=True)
        top = ttk.Frame(pw)
        tf, self.tree = make_tree(top, [
            ("name", "ファイル名", 260, "w", True),
            ("status", "判定", 100, "center", False),
            ("rule", "ルール", 150, "w", False),
            ("dest", "移動先 / 理由", 330, "w", True),
            ("size", "サイズ", 80, "e", False),
            ("mtime", "更新日時", 130, "w", False),
        ], height=10)
        tf.pack(fill="both", expand=True)
        for tag, color in (("run", "ok"), ("ignore", "accent"), ("error", "err"),
                           ("muted", "muted")):
            self.tree.tag_configure(tag, foreground=t.c(color))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        pw.add(top, weight=3)
        bottom = ttk.Frame(pw, padding=(0, 8, 0, 0))
        xf, self.sim_text = make_text(bottom, t, height=9)
        xf.pack(fill="both", expand=True)
        pw.add(bottom, weight=2)
        set_text(self.sim_text, [("行を選択すると、どのルールのどの条件に一致したかを表示します。", "muted")])

    def on_show(self):
        folders = watch_folders(self.cfg)
        self.cb_folder["values"] = folders
        if not self.v_folder.get() and folders:
            self.v_folder.set(folders[0])
        self.cb_vfolder["values"] = folders
        if self.v_vfolder.get() not in folders and folders:
            self.v_vfolder.set(folders[0])
        self._live_eval()

    def _browse(self):
        p = filedialog.askdirectory(parent=self.app.root, title="フォルダを選択", initialdir=self.v_folder.get() or None)
        if p:
            self.v_folder.set(os.path.normpath(p))
            self.simulate()

    def simulate(self):
        folder = self.v_folder.get().strip()
        if not os.path.isdir(folder):
            self.sim_summary.configure(text="フォルダが見つかりません。")
            return
        cfg = self.app.reload_config()
        self.app.root.configure(cursor="watch")
        self.app.root.update_idletasks()
        try:
            started = time.perf_counter()
            items = engine.plan_folder(folder, cfg, trace=True, exclusions=store.load_exclusions())
            elapsed = time.perf_counter() - started
        except OSError as e:
            self.sim_summary.configure(text=f"読み込めませんでした: {e}")
            return
        finally:
            self.app.root.configure(cursor="")
        self._rules = cfg.get("rules", [])
        self._cats = cfg.get("categories", {})
        self._now = time.time()
        self._items = {f"i{n}": it for n, it in enumerate(items)}
        counts = {}
        for it in items:
            counts[it.status] = counts.get(it.status, 0) + 1
        parts = [f"{STATUS_LABELS[k]} {counts[k]}" for k in ("run", "ignore", "nomatch", "skip", "error")
                 if counts.get(k)]
        zips = engine.zip_summary_text(items, [folder])
        self.sim_summary.configure(text=f"全 {len(items)} 件：" + "　/　".join(parts) + f"　（判定 {elapsed * 1000:.0f} ms）"
                                        + (f"\n{zips}" if zips else ""))
        self._render()

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for iid, it in self._items.items():
            if self.v_only.get() and it.status != "run":
                continue
            if it.status == "run":
                dest = "　＞　".join(display_path(p.target, [it.folder]) for p in it.steps if p.target) or it.reason
            else:
                dest = it.reason
            self.tree.insert("", "end", iid=iid, tags=(STATUS_TAGS.get(it.status, ""),),
                             values=(it.info.name, it.label, (it.rule or {}).get("name", ""), dest,
                                     fmt_size(it.info.size), fmt_datetime(it.info.mtime)))

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._items:
            return
        it = self._items[sel[0]]
        it.info.get_urls()
        chunks = trace.file_header(it, self._now) + trace.result_line(it)
        if it.traces:
            chunks += trace.rules_trace(it.traces, self._rules, self._cats, it.rule, folder=it.folder)
        elif it.status == "skip" and not it.rule:
            chunks.append(("\nこのファイルはルール判定の前にスキップされました（設定の除外パターン・待機時間など）。\n", "muted"))
        set_text(self.sim_text, chunks)

    # ================================================================ 仮想ファイル
    def _build_virtual(self):
        t = self.theme
        f = self.tab_virtual
        ttk.Label(f, text="実在しないファイルを想定して、条件の組み立てを確認できます。入力するとすぐに判定結果が更新されます。",
                  style="Sub.TLabel").pack(anchor="w")
        form = card(f)
        form.pack(fill="x", pady=(10, 0))
        self.v_vname = tk.StringVar(value="請求書_2026-11.pdf")
        self.v_vsize = tk.StringVar(value="1.5")
        self.v_vsize_unit = tk.StringVar(value="MB")
        self.v_vmtime = tk.StringVar(value="0")
        self.v_vmtime_unit = tk.StringVar(value="日")
        self.v_vctime = tk.StringVar(value="0")
        self.v_vctime_unit = tk.StringVar(value="日")
        self.v_vurl = tk.StringVar(value="")
        self.v_vref = tk.StringVar(value="")

        def row(r, label, *widgets, hint=""):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=4)
            box = ttk.Frame(form)
            box.grid(row=r, column=1, sticky="ew", padx=(14, 0), pady=4)
            for w in widgets:
                w(box)
            if hint:
                ttk.Label(form, text=hint, style="Sub.TLabel").grid(row=r, column=2, sticky="w", padx=(12, 0))

        self.v_vfolder = tk.StringVar()

        def folder_box(b):
            self.cb_vfolder = ttk.Combobox(b, textvariable=self.v_vfolder, state="readonly", width=58)
            self.cb_vfolder.pack(side="left")

        row(7, "置かれる監視フォルダ", folder_box, hint="ルールの「対象フォルダ」の確認用")
        row(0, "ファイル名", lambda b: ttk.Entry(b, textvariable=self.v_vname, width=46).pack(side="left"))
        row(1, "サイズ",
            lambda b: ttk.Entry(b, textvariable=self.v_vsize, width=10).pack(side="left"),
            lambda b: ttk.Combobox(b, textvariable=self.v_vsize_unit, values=list(SIZE_UNITS), state="readonly",
                                   width=5).pack(side="left", padx=(6, 0)))
        row(2, "更新日時",
            lambda b: ttk.Entry(b, textvariable=self.v_vmtime, width=10).pack(side="left"),
            lambda b: ttk.Combobox(b, textvariable=self.v_vmtime_unit, values=[a for a, _ in AGE_CHOICES],
                                   state="readonly", width=6).pack(side="left", padx=(6, 0)),
            lambda b: ttk.Label(b, text="前").pack(side="left", padx=(6, 0)))
        row(3, "作成日時（ダウンロード日時）",
            lambda b: ttk.Entry(b, textvariable=self.v_vctime, width=10).pack(side="left"),
            lambda b: ttk.Combobox(b, textvariable=self.v_vctime_unit, values=[a for a, _ in AGE_CHOICES],
                                   state="readonly", width=6).pack(side="left", padx=(6, 0)),
            lambda b: ttk.Label(b, text="前").pack(side="left", padx=(6, 0)))
        row(4, "ダウンロードURL", lambda b: ttk.Entry(b, textvariable=self.v_vurl, width=60).pack(side="left"),
            hint="HostUrl（任意）")
        row(5, "参照元ページURL", lambda b: ttk.Entry(b, textvariable=self.v_vref, width=60).pack(side="left"),
            hint="ReferrerUrl（任意）")
        btns = ttk.Frame(form)
        btns.grid(row=8, column=1, sticky="w", padx=(14, 0), pady=(8, 0))
        ttk.Button(btns, text="実在ファイルから読み込む…", command=self._load_real).pack(side="left")
        form.columnconfigure(2, weight=1)

        xf, self.v_text = make_text(f, t, height=12)
        xf.pack(fill="both", expand=True, pady=(12, 0))
        for v in (self.v_vname, self.v_vsize, self.v_vsize_unit, self.v_vmtime, self.v_vmtime_unit,
                  self.v_vctime, self.v_vctime_unit, self.v_vurl, self.v_vref, self.v_vfolder):
            v.trace_add("write", lambda *a: self._schedule_live())
        self._live_after = None

    def _schedule_live(self):
        if self._live_after:
            self.after_cancel(self._live_after)
        self._live_after = self.after(250, self._live_eval)

    def _num(self, s, default=0.0):
        try:
            return max(0.0, float(str(s).replace(",", "")))
        except ValueError:
            return default

    def _live_eval(self):
        self._live_after = None
        cfg = self.cfg
        now = time.time()
        name = self.v_vname.get().strip() or "（無題）"
        size = int(self._num(self.v_vsize.get()) * SIZE_UNITS.get(self.v_vsize_unit.get(), 1))
        mul = dict(AGE_CHOICES)
        mtime = now - self._num(self.v_vmtime.get()) * mul.get(self.v_vmtime_unit.get(), 86400)
        ctime = now - self._num(self.v_vctime.get()) * mul.get(self.v_vctime_unit.get(), 86400)
        info = FileInfo(path="", name=name, size=size, mtime=mtime, ctime=ctime,
                        urls=(self.v_vurl.get().strip(), self.v_vref.get().strip()))
        folders = watch_folders(cfg) or [""]
        folder = self.v_vfolder.get() if self.v_vfolder.get() in folders else folders[0]
        ctx = engine.build_context(cfg, now)
        rules = cfg.get("rules", [])
        it = engine.plan_file(info, folder, engine.rules_for_folder(engine.auto_rules(cfg), folder), ctx, trace=True)
        chunks = trace.file_header(it, now) + trace.result_line(it)
        chunks += trace.rules_trace(it.traces, rules, cfg.get("categories", {}), it.rule, folder=folder)
        if not rules:
            chunks.append(("\nルールがありません。ルール画面で作成してください。\n", "muted"))
        set_text(self.v_text, chunks)

    def _load_real(self):
        p = filedialog.askopenfilename(parent=self.app.root, title="ファイルを選択",
                                       initialdir=(watch_folders(self.cfg) or [None])[0])
        if not p:
            return
        try:
            info = FileInfo.from_path(os.path.normpath(p))
        except OSError:
            return
        now = time.time()
        self.v_vname.set(info.name)
        size, unit = info.size, "B"
        for u in ("GB", "MB", "KB"):
            if info.size >= SIZE_UNITS[u]:
                size, unit = info.size / SIZE_UNITS[u], u
                break
        self.v_vsize.set(f"{size:.2f}".rstrip("0").rstrip("."))
        self.v_vsize_unit.set(unit)
        for var, uvar, ts in ((self.v_vmtime, self.v_vmtime_unit, info.mtime), (self.v_vctime, self.v_vctime_unit, info.ctime)):
            age = max(0.0, now - ts)
            label, sec = ("日", 86400) if age >= 86400 else (("時間", 3600) if age >= 3600 else ("分", 60))
            var.set(f"{age / sec:.1f}".rstrip("0").rstrip("."))
            uvar.set(label)
        host, ref = info.get_urls()
        self.v_vurl.set(host)
        self.v_vref.set(ref)
