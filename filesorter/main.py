"""エントリポイント（引数の解釈と起動モードの振り分け）

  (引数なし)        常駐プロセスが無ければ起動し、GUI を開く
  --background      常駐プロセスのみ（Windows 起動時の自動起動で使用）
  --gui [--page X]  GUI のみ（X = home / rules / test / history / settings）
  --run-once        1回だけ仕分けを実行して終了（コンソール用）
  --simulate        仕分けせずに判定結果を表示（コンソール用）
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys

from . import APP_NAME, __version__


def setup_logging() -> None:
    from .config import data_dir
    handler = logging.handlers.RotatingFileHandler(data_dir() / "app.log", maxBytes=512 * 1024,
                                                   backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None and sys.stderr.isatty():
        root.addHandler(logging.StreamHandler())


def _console_simulate() -> int:
    from . import config, engine
    cfg = config.load_config()
    for folder in config.watch_folders(cfg):
        print(f"[{folder}]")
        for it in engine.plan_folder(folder, cfg):
            rule = (it.rule or {}).get("name", "")
            detail = "　".join(p.text() for p in it.steps) or it.reason
            print(f"  {it.label:<8} {it.info.name}  {rule}  {detail}")
    return 0


def _console_run_once() -> int:
    from . import config, engine
    s = engine.run_once(config.load_config())
    print(f"処理 {s.total} 件 / スキップ {s.skipped} 件 / エラー {len(s.errors)} 件")
    for name, msg in s.errors:
        print(f"  ! {name}: {msg}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--page", default=None)
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    setup_logging()
    log = logging.getLogger("main")
    try:
        if args.simulate:
            return _console_simulate()
        if args.run_once:
            return _console_run_once()
        if args.background:
            from .resident import run_resident
            return run_resident()
        from . import ipc
        if not args.gui and not ipc.resident_running():
            ipc.spawn_resident()
        from .gui.app import run_gui
        return run_gui(args.page)
    except Exception:
        log.exception("予期しないエラー")
        raise
