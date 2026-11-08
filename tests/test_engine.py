"""ルール判定・動作・エンジンのテスト

実行:  python -m unittest discover -s tests -v
（設定・履歴は一時フォルダに隔離されるため、実際の設定やダウンロードフォルダには触れない）
"""
import os
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_DATA = tempfile.mkdtemp(prefix="fsk-data-")
os.environ["FILESORTKUN_DATA"] = _DATA

from unittest import mock  # noqa: E402

from filesorter import actions, config, engine, store, winapi  # noqa: E402


def _remove(path):
    import shutil
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
from filesorter.rules import (EvalContext, FileInfo, describe_conditions, describe_condition,  # noqa: E402
                              evaluate_condition, find_matching_rule, new_group, validate_rule)

CATS = config.DEFAULT_CATEGORIES
NOW = time.time()
DAY = 86400


def vfile(name, size=1000, mtime_days=0.0, ctime_days=None, urls=("", "")):
    return FileInfo(path="", name=name, size=size, mtime=NOW - mtime_days * DAY,
                    ctime=NOW - (mtime_days if ctime_days is None else ctime_days) * DAY, urls=urls)


def cond(field, op, value="", value2="", unit="", negate=False):
    return config.normalize_condition(dict(field=field, op=op, value=value, value2=value2, unit=unit, negate=negate))


def group(match, *items, negate=False):
    return {"type": "group", "match": match, "negate": negate, "items": list(items)}


def rule(name, conds, match="all", steps=None, enabled=True, trigger="auto"):
    root = conds if isinstance(conds, dict) else group(match, *conds)
    return config.normalize_rule(dict(name=name, conditions=root, enabled=enabled, trigger=trigger,
                                      actions=steps if steps is not None else [{"type": "move", "target": "dest"}]))


def move(target, conflict="rename"):
    return {"type": "move", "target": target, "conflict": conflict}


CTX = EvalContext(NOW, CATS)


def ok(c, f):
    return evaluate_condition(c, f, CTX).ok


class ConditionTests(unittest.TestCase):
    def test_text_ops(self):
        f = vfile("Invoice_2026-11.PDF")
        self.assertTrue(ok(cond("name", "contains", "invoice"), f))
        self.assertTrue(ok(cond("name", "contains", "請求|invoice"), f))
        self.assertTrue(ok(cond("name", "startswith", "inv"), f))
        self.assertTrue(ok(cond("name", "endswith", ".pdf"), f))
        self.assertTrue(ok(cond("stem", "equals", "invoice_2026-11"), f))
        self.assertTrue(ok(cond("name", "wildcard", "invoice_*.pdf"), f))
        self.assertTrue(ok(cond("name", "regex", r"\d{4}-\d{2}"), f))
        self.assertFalse(ok(cond("name", "contains", "draft"), f))
        self.assertTrue(ok(cond("name", "contains", "draft", negate=True), f))

    def test_regex_error_never_matches(self):
        r = evaluate_condition(cond("name", "regex", "([", negate=True), vfile("a.txt"), CTX)
        self.assertFalse(r.ok)
        self.assertTrue(r.error)

    def test_ext_category_size_age_url(self):
        self.assertTrue(ok(cond("ext", "in", ".PDF, docx"), vfile("a.pdf")))
        self.assertTrue(ok(cond("ext", "in", "tar.gz"), vfile("backup.tar.gz")))
        self.assertTrue(ok(cond("category", "is", "image"), vfile("photo.JPG")))
        f = vfile("a.bin", size=5 * 1024 * 1024)
        self.assertTrue(ok(cond("size", "gte", "5", unit="MB"), f))
        self.assertTrue(ok(cond("size", "between", "10", "1", unit="MB"), f))
        g = vfile("old.zip", mtime_days=40, ctime_days=1)
        self.assertTrue(ok(cond("mtime", "older", "30", unit="day"), g))
        self.assertTrue(ok(cond("ctime", "newer", "48", unit="hour"), g))
        u = vfile("x.zip", urls=("https://objects.githubusercontent.com/a", "https://github.com/foo/bar"))
        self.assertTrue(ok(cond("url", "contains", "github.com"), u))
        self.assertEqual(u.host(), "github.com")


class NestedGroupTests(unittest.TestCase):
    def test_nested_and_or_not(self):
        # pdf かつ（「請求」を含む または bank から）かつ NOT（draft を含む）
        r = rule("nested", group("all",
                                 cond("ext", "in", "pdf"),
                                 group("any", cond("name", "contains", "請求"), cond("url", "contains", "bank")),
                                 group("all", cond("name", "contains", "draft"), negate=True)))
        rules = [r]
        self.assertIsNotNone(find_matching_rule(rules, vfile("請求書.pdf"), CTX)[0])
        self.assertIsNotNone(find_matching_rule(rules, vfile("x.pdf", urls=("https://mybank.jp/x", "")), CTX)[0])
        self.assertIsNone(find_matching_rule(rules, vfile("請求書_draft.pdf"), CTX)[0])
        self.assertIsNone(find_matching_rule(rules, vfile("その他.pdf"), CTX)[0])
        self.assertIsNone(find_matching_rule(rules, vfile("請求書.docx"), CTX)[0])
        text = describe_conditions(r, CATS)
        self.assertIn("または", text)
        self.assertIn("NOT", text)

    def test_trace_contains_tree(self):
        r = rule("t", group("any", cond("ext", "in", "png"), group("all", cond("ext", "in", "pdf"))))
        _, traces = find_matching_rule([r], vfile("a.pdf"), CTX, trace=True)
        root = traces[0].root
        self.assertTrue(root.ok)
        self.assertEqual(len(root.children), 2)
        self.assertIsNotNone(root.children[1].children)

    def test_empty_rules_and_validation(self):
        self.assertIsNone(find_matching_rule([rule("empty", group("all"))], vfile("a"), CTX)[0])
        bad = dict(rule("x", group("all", cond("name", "regex", "("), group("any")), steps=[]), name=" ")
        errs = validate_rule(bad, CATS)
        self.assertTrue(any("ルール名" in e for e in errs))
        self.assertTrue(any("正規表現" in e for e in errs))
        self.assertTrue(any("空のグループ" in e for e in errs))
        self.assertTrue(any("動作" in e for e in errs))
        terminal = rule("t", [cond("all", "always")], steps=[{"type": "recycle"}, move("x")])
        self.assertTrue(any("後ろに動作" in e for e in validate_rule(terminal, CATS)))
        self.assertEqual(validate_rule(rule("ok", [cond("ext", "in", "pdf")]), CATS), [])


class MigrationTests(unittest.TestCase):
    def test_v1_rule_is_converted(self):
        v1 = {"name": "old", "match": "any", "conditions": [{"field": "ext", "op": "in", "value": "pdf"}],
              "action": "move", "destination": "文書", "conflict": "skip"}
        r = config.normalize_rule(v1)
        self.assertEqual(r["conditions"]["type"], "group")
        self.assertEqual(r["conditions"]["match"], "any")
        self.assertEqual(r["actions"], [{"type": "move", "target": "文書", "conflict": "skip"}])
        self.assertEqual(config.normalize_rule({"action": "recycle"})["actions"], [{"type": "recycle"}])

    def test_v1_history_entry_display(self):
        e = {"action": "move", "src": "a", "dst": r"C:\x\文書\a.pdf"}
        self.assertEqual(actions.entry_label(e), "移動")
        self.assertEqual(actions.entry_destination(e), r"C:\x\文書\a.pdf")

    def test_downloads_token(self):
        from filesorter.winapi import downloads_folder
        cfg = config.normalize_config({"version": 1, "watch_folders": [downloads_folder()]})
        self.assertEqual(cfg["watch_folders"], [config.DOWNLOADS_TOKEN])
        self.assertEqual(config.watch_folders(cfg), [os.path.normpath(downloads_folder())])

    def test_sample_rules_disabled(self):
        self.assertTrue(all(not r["enabled"] for r in config.default_config()["rules"]))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fsk-watch-")
        store.clear_history()
        store.clear_exclusions()
        # テストでは実際のごみ箱を汚さないよう、ごみ箱送りを通常の削除に差し替える
        patcher = mock.patch.object(winapi, "send_to_recycle_bin", side_effect=_remove)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, name, content=b"x", age_days=1.0):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(content)
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))
        return p

    def cfg(self, rules, settle_sec=0, **kw):
        return config.normalize_config(dict(version=2, watch_folders=[self.tmp], rules=rules,
                                            settle_sec=settle_sec, **kw))

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    def test_scan_skips_and_settle(self):
        self.make("a.pdf")
        self.make("b.crdownload")
        os.mkdir(self.p("subdir"))
        cfg = self.cfg([rule("docs", [cond("ext", "in", "pdf")], steps=[move("文書")])])
        items = {it.info.name: it for it in engine.plan_folder(self.tmp, cfg)}
        self.assertNotIn("subdir", items)
        self.assertEqual(items["a.pdf"].status, "run")
        self.assertEqual(items["b.crdownload"].status, "skip")
        cfg2 = self.cfg(cfg["rules"], settle_sec=30)
        self.assertIn("待機", engine.plan_folder(self.tmp, cfg2)[0].reason)  # 作成直後

    def test_move_with_placeholders_and_undo(self):
        self.make("report.pdf", b"new")
        os.makedirs(self.p("文書"))
        with open(self.p("文書", "report.pdf"), "wb") as fh:
            fh.write(b"old")
        cfg = self.cfg([rule("docs", [cond("ext", "in", "pdf")], steps=[move(r"{category}")])])
        s = engine.run_once(cfg)
        self.assertEqual(s.total, 1, s.errors)
        self.assertTrue(os.path.exists(self.p("文書", "report (1).pdf")))
        hist = store.load_history()
        n, msgs = engine.undo([hist[0]["id"]])
        self.assertEqual(n, 1, msgs)
        self.assertTrue(os.path.exists(self.p("report.pdf")))
        self.assertEqual(engine.run_once(cfg).total, 0)  # 元に戻したファイルは再仕分けしない

    def test_rename_then_move_chain(self):
        self.make("photo.png")
        cfg = self.cfg([rule("img", [cond("category", "is", "image")],
                             steps=[{"type": "rename", "target": "{yyyy}_{name}"}, move("画像")])])
        year = time.strftime("%Y", time.localtime(time.time() - DAY))
        s = engine.run_once(cfg)
        self.assertEqual(s.total, 1, s.errors)
        self.assertTrue(os.path.exists(self.p("画像", f"{year}_photo.png")))
        n, _ = engine.undo([store.load_history()[0]["id"]])
        self.assertEqual(n, 1)
        self.assertTrue(os.path.exists(self.p("photo.png")))

    def test_zip_by_modified_month_merges_history_and_undo(self):
        self.make("a.txt", b"aaa", age_days=1)
        self.make("b.txt", b"bbb", age_days=1)
        self.make("old.txt", b"old", age_days=400)
        cfg = self.cfg([rule("zip", [cond("ext", "in", "txt")],
                             steps=[{"type": "zip", "group": "month", "folder": "アーカイブ", "prefix": "log_",
                                     "remove_original": True}])])
        items = engine.plan_folder(self.tmp, cfg)
        self.assertEqual(len(engine.zip_summary(items)), 2)  # テスト表示: ZIP 2個（今月分と1年以上前の月）
        s = engine.run_once(cfg)
        self.assertEqual(s.total, 3, s.errors)
        recent = time.strftime("%Y-%m", time.localtime(time.time() - DAY))
        old = time.strftime("%Y-%m", time.localtime(time.time() - 400 * DAY))
        arc = self.p("アーカイブ", f"log_{recent}.zip")
        with zipfile.ZipFile(arc) as z:
            self.assertEqual(sorted(z.namelist()), ["a.txt", "b.txt"])
            self.assertEqual(z.read("a.txt"), b"aaa")
        with zipfile.ZipFile(self.p("アーカイブ", f"log_{old}.zip")) as z:
            self.assertEqual(z.namelist(), ["old.txt"])
        self.assertFalse(os.path.exists(self.p("a.txt")))  # 元はごみ箱へ
        # 履歴は ZIP 1つにつき1件（2件入った ZIP はまとめて1件）
        hist = store.load_history()
        self.assertEqual(len(hist), 2)
        merged = [e for e in hist if actions.entry_count(e) == 2][0]
        self.assertEqual(sorted(actions.entry_names(merged)), ["a.txt", "b.txt"])
        self.assertEqual(actions.entry_destination(merged), arc)
        # 同名ファイルをもう一度追加 → ZIP 内で連番
        self.make("a.txt", b"second", age_days=1)
        engine.run_once(cfg)
        with zipfile.ZipFile(arc) as z:
            self.assertIn("a (1).txt", z.namelist())
        # まとめた履歴を元に戻す → 2件とも ZIP から取り出し、更新日時も元に戻る
        n, msgs = engine.undo([merged["id"]])
        self.assertEqual(n, 2, msgs)
        with open(self.p("b.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"bbb")
        with open(self.p("a.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"aaa")
        self.assertLess(os.path.getmtime(self.p("b.txt")), time.time() - DAY / 2)
        self.assertTrue(next(e for e in store.load_history() if e["id"] == merged["id"])["undone"])

    def test_old_zip_template_is_migrated(self):
        def step(target):
            return config.normalize_step({"type": "zip", "target": target, "remove_original": True})
        s = step(r"アーカイブ\{cyyyy}-{cmm}.zip")
        self.assertEqual((s["group"], s["folder"], s["prefix"]), ("cmonth", "アーカイブ", ""))
        s = step(r"アーカイブ\スクリーンショット_{cyyyy}-{cmm}.zip")
        self.assertEqual((s["group"], s["prefix"]), ("cmonth", "スクリーンショット_"))
        self.assertEqual(step(r"a\{yyyy}-{mm}-{dd}.zip")["group"], "day")
        self.assertEqual(step(r"a\まとめ.zip")["group"], "all")
        s = step(r"a\{yyyy}\{mm}.zip")
        self.assertEqual((s["group"], s["target"]), ("custom", r"a\{yyyy}\{mm}.zip"))
        from filesorter.rules import zip_template
        for t in (r"アーカイブ\{cyyyy}-{cmm}.zip", r"a\まとめ.zip", r"a\{yyyy}\{mm}.zip"):
            self.assertEqual(zip_template(step(t)), t)  # 読み替えても保存先は変わらない
        self.assertEqual(zip_template({"group": "all", "folder": "z", "prefix": ""}), r"z\{rule}.zip")
        self.assertTrue(validate_rule(rule("z", [cond("ext", "in", "txt")],
                                           steps=[{"type": "zip", "group": "month", "folder": ""}]), CATS))

    def test_zip_into_watch_root_is_rejected(self):
        self.make("a.txt")
        cfg = self.cfg([rule("zip", [cond("ext", "in", "txt")],
                             steps=[{"type": "zip", "target": "all.zip", "remove_original": False}])])
        s = engine.run_once(cfg)
        self.assertEqual(len(s.errors), 1)
        self.assertTrue(os.path.exists(self.p("a.txt")))

    def test_copy_marks_processed(self):
        self.make("keep.pdf")
        cfg = self.cfg([rule("backup", [cond("ext", "in", "pdf")], steps=[{"type": "copy", "target": "backup"}])])
        self.assertEqual(engine.run_once(cfg).total, 1)
        self.assertTrue(os.path.exists(self.p("backup", "keep.pdf")))
        self.assertTrue(os.path.exists(self.p("keep.pdf")))
        self.assertEqual(engine.run_once(cfg).total, 0)  # 処理済みは繰り返さない
        self.assertFalse(os.path.exists(self.p("backup", "keep (1).pdf")))

    def test_extract(self):
        zp = self.p("pack.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("dir/日本語.txt", "hello")
            z.writestr("../evil.txt", "x")  # Zip Slip は無視される
        t = time.time() - DAY
        os.utime(zp, (t, t))
        cfg = self.cfg([rule("ex", [cond("ext", "in", "zip")],
                             steps=[{"type": "extract", "target": r"展開\{stem}", "remove_original": False}])])
        s = engine.run_once(cfg)
        self.assertEqual(s.total, 1, s.errors)
        self.assertTrue(os.path.exists(self.p("展開", "pack", "dir", "日本語.txt")))
        self.assertFalse(os.path.exists(self.p("展開", "evil.txt")))

    def test_command(self):
        self.make("c.log")
        out = self.p("out.txt")
        cfg = self.cfg([rule("cmd", [cond("ext", "in", "log")],
                             steps=[{"type": "command", "target": f'cmd /c echo {{name}}> "{out}"'}])])
        s = engine.run_once(cfg)
        self.assertEqual(s.total, 1, s.errors)
        with open(out, encoding="cp932") as fh:
            self.assertIn("c.log", fh.read())

    def test_conflict_skip_and_ignore(self):
        self.make("a.txt")
        self.make("keep.txt")
        os.makedirs(self.p("t"))
        open(self.p("t", "a.txt"), "wb").close()
        cfg = self.cfg([rule("keep", [cond("name", "startswith", "keep")], steps=[{"type": "ignore"}]),
                        rule("t", [cond("ext", "in", "txt")], steps=[move("t", "skip")])])
        st = {it.info.name: it.status for it in engine.plan_folder(self.tmp, cfg)}
        self.assertEqual(st, {"a.txt": "skip", "keep.txt": "ignore"})
        self.assertEqual(engine.run_once(cfg).total, 0)

    def test_manual_rule_only_runs_as_quick_action(self):
        self.make("m.pdf")
        r = rule("manual", [cond("ext", "in", "pdf")], steps=[move("手動")], trigger="manual", enabled=False)
        cfg = self.cfg([r])
        self.assertEqual(engine.run_once(cfg).total, 0)
        self.assertEqual(engine.run_once(cfg, rule_id=cfg["rules"][0]["id"]).total, 1)
        self.assertTrue(os.path.exists(self.p("手動", "m.pdf")))

    def test_rule_folder_scope(self):
        a, b = self.p("A"), self.p("B")
        os.makedirs(a)
        os.makedirs(b)
        for d in (a, b):
            for n in ("x.pdf", "y.txt"):
                p = os.path.join(d, n)
                open(p, "wb").close()
                t = time.time() - DAY
                os.utime(p, (t, t))
        only_b = dict(rule("Bのpdf", [cond("ext", "in", "pdf")], steps=[move("pdf")]), folders=[b.upper()])
        both = rule("txt", [cond("ext", "in", "txt")], steps=[move("txt")])
        gone = dict(rule("無い", [cond("all", "always")], steps=[move("x")]), folders=[self.p("存在しない")])
        cfg = config.normalize_config(dict(version=2, watch_folders=[a, b], settle_sec=0,
                                           rules=[gone, only_b, both]))
        self.assertEqual(cfg["rules"][1]["folders"], [b.upper()])
        s = engine.run_once(cfg)
        self.assertEqual(s.total, 3, s.errors)
        self.assertTrue(os.path.exists(os.path.join(a, "x.pdf")))          # A は対象外
        self.assertTrue(os.path.exists(os.path.join(b, "pdf", "x.pdf")))   # B だけ処理
        self.assertTrue(os.path.exists(os.path.join(a, "txt", "y.txt")))   # 指定なし = 両方
        self.assertTrue(os.path.exists(os.path.join(b, "txt", "y.txt")))
        self.assertEqual(config.missing_rule_folders(cfg, cfg["rules"][0]), [self.p("存在しない")])
        self.assertTrue(config.rule_applies_to(cfg["rules"][2], a))

    def test_progress_callback(self):
        for n in ("a.pdf", "b.pdf", "c.pdf"):
            self.make(n)
        calls = []
        cfg = self.cfg([rule("docs", [cond("ext", "in", "pdf")], steps=[move("文書")])])
        engine.run_once(cfg, progress=lambda d, t, name: calls.append((d, t)))
        self.assertEqual(calls[0], (0, 3))
        self.assertEqual(calls[-1], (3, 3))

    def test_locked_file_is_not_duplicated(self):
        p = self.make("locked.pdf")
        cfg = self.cfg([rule("docs", [cond("ext", "in", "pdf")], steps=[move("文書")])])
        h = open(p, "rb")  # Python の open は FILE_SHARE_DELETE を付けないため rename が失敗する
        try:
            s = engine.run_once(cfg)
        finally:
            h.close()
        self.assertEqual((s.total, len(s.errors)), (0, 1))
        self.assertFalse(os.path.exists(self.p("文書", "locked.pdf")))


class PortableTests(unittest.TestCase):
    def test_relative_watch_folder_resolves_to_app_dir(self):
        self.assertEqual(config.resolve_folder("data"), os.path.normpath(str(config.APP_DIR / "data")))
        self.assertTrue(os.path.isabs(config.resolve_folder("%USERPROFILE%")))


if __name__ == "__main__":
    unittest.main()
