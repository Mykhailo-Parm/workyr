"""Тесты логики учёта. Запуск: python -m unittest -v test_core"""
import csv
import os
import tempfile
import unittest
from datetime import date, datetime

import core
from core import Sample

BASE = datetime(2026, 10, 22, 10, 0, 0).timestamp()


class Sim:
    """Прогоняет трекер по сценарию с шагом poll секунд."""

    def __init__(self, tmp):
        os.environ["WORKTRACKER_DATA"] = tmp
        self.cfg = core.Config(os.path.join(tmp, "config.json"))
        self.db = core.Database(os.path.join(tmp, "t.db"))
        self.tr = core.Tracker(self.db, self.cfg)
        self.t = BASE

    def run(self, seconds, process="blender.exe", title="chair.blend - Blender", idle=0.0, idle_grows=False):
        poll = self.cfg["poll_seconds"]
        end = self.t + seconds
        start_idle = idle
        t0 = self.t
        while self.t < end:
            cur_idle = start_idle + (self.t - t0) if idle_grows else idle
            self.tr.step(Sample(self.t, cur_idle, process, title))
            self.t += poll

    def segs(self):
        self.tr.flush()      # открытый отрезок в базе отстаёт до 15 с; как и в программе перед показом отчёта
        return self.db.segments_between(0, 4102444800)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sim = Sim(self.tmp.name)

    def tearDown(self):
        self.sim.db.close()
        self.tmp.cleanup()

    def test_basic_recording_is_one_segment(self):
        self.sim.run(600)
        self.sim.run(10, process="explorer.exe", title="Desktop")
        segs = self.sim.segs()
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["end"] - segs[0]["start"], 600, delta=4)

    def test_idle_tail_is_trimmed(self):
        # 10 минут работы, потом человек ушёл: простой растёт до 5 минут
        self.sim.run(600)
        self.sim.run(300, idle=2.0, idle_grows=True)
        segs = self.sim.segs()
        self.assertEqual(len(segs), 1)
        length = segs[0]["end"] - segs[0]["start"]
        # работа ~600 с плюс то, что было до последнего ввода (первые ~2 с простоя)
        self.assertAlmostEqual(length, 600, delta=6)

    def test_idle_below_timeout_still_counts(self):
        self.sim.run(120)
        self.sim.run(120, idle=100.0)      # 100 с без движения при пороге 180 с: это ещё «думает»
        self.sim.run(60)
        segs = self.sim.segs()
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["end"] - segs[0]["start"], 300, delta=4)

    def test_non_work_window_not_counted(self):
        self.sim.run(120, process="chrome.exe", title="YouTube - Google Chrome")
        self.assertEqual(self.sim.segs(), [])
        self.assertEqual(self.sim.tr.state, "other")

    def test_browser_tab_title_rule(self):
        self.sim.run(120, process="chrome.exe", title="Meshy - AI 3D Model Generator - Google Chrome")
        self.assertEqual(len(self.sim.segs()), 1)

    def test_default_apps_match(self):
        apps = core.DEFAULT_APPS
        for exe in ("blender.exe", "zbrush.exe", "toolbag.exe", "adobe substance 3d painter.exe",
                    "substance painter.exe", "photoshop.exe", "unrealeditor.exe", "ue4editor.exe"):
            self.assertIsNotNone(core.match_app(apps, exe, ""), exe)
        self.assertIsNone(core.match_app(apps, "notepad.exe", "readme.txt"))

    def test_sleep_gap_not_counted(self):
        self.sim.run(300)
        self.sim.t += 3 * 3600                # ноутбук проспал 3 часа
        self.sim.run(120)
        segs = self.sim.segs()
        self.assertEqual(len(segs), 2)
        total = sum(s["end"] - s["start"] for s in segs)
        self.assertAlmostEqual(total, 420, delta=8)

    def test_project_switch_splits_segments(self):
        a = self.sim.db.add_project("Кресло")
        b = self.sim.db.add_project("Меч")
        self.sim.tr.reload_projects()
        self.sim.tr.set_project(a)
        self.sim.run(200)
        self.sim.tr.set_project(b)
        self.sim.run(100)
        segs = self.sim.segs()
        self.assertEqual([s["project_id"] for s in segs], [a, b])
        self.assertAlmostEqual(segs[0]["end"] - segs[0]["start"], 200, delta=4)
        self.assertAlmostEqual(segs[1]["end"] - segs[1]["start"], 100, delta=4)

    def test_keyword_project_overrides_selection(self):
        a = self.sim.db.add_project("Кресло", keywords="chair_v3")
        b = self.sim.db.add_project("Меч", keywords="sword")
        self.sim.tr.reload_projects()
        self.sim.tr.set_project(b)
        self.sim.run(100, title="chair_v3.blend - Blender")
        self.assertEqual(self.sim.segs()[0]["project_id"], a)

    def test_no_project_is_recorded_as_null(self):
        self.sim.run(60)
        self.assertIsNone(self.sim.segs()[0]["project_id"])

    def test_pause(self):
        self.sim.run(60)
        self.sim.tr.toggle_pause()
        self.sim.run(120)
        self.assertEqual(self.sim.tr.state, "paused")
        self.sim.tr.toggle_pause()
        self.sim.run(60)
        total = sum(s["end"] - s["start"] for s in self.sim.segs())
        self.assertAlmostEqual(total, 120, delta=8)

    def test_tiny_segment_dropped(self):
        self.sim.run(2)                         # 1 тик
        self.sim.run(10, process="explorer.exe", title="x")
        self.assertEqual(self.sim.segs(), [])

    def test_live_seconds_include_unflushed_tail(self):
        self.sim.run(10)
        live = self.sim.tr.seconds_between(BASE - 10, BASE + 3600)
        self.assertGreaterEqual(live, 6)

    def test_finish_closes_segment(self):
        self.sim.run(60)
        self.sim.tr.finish()
        self.assertIsNone(self.sim.tr.seg_id)
        self.assertEqual(len(self.sim.segs()), 1)

    def test_crash_recovery_keeps_flushed_time(self):
        self.sim.run(120)                       # «краш»: close не вызывали
        segs = self.sim.segs()
        self.assertEqual(len(segs), 1)
        self.assertGreater(segs[0]["end"] - segs[0]["start"], 90)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WORKTRACKER_DATA"] = self.tmp.name
        self.db = core.Database(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_segment_split_at_midnight(self):
        pid = self.db.add_project("Кресло", rate=10)
        start = datetime(2026, 10, 22, 23, 0).timestamp()
        end = datetime(2026, 10, 23, 1, 30).timestamp()
        self.db.add_manual(pid, start, end)
        p = core.period_bounds("all")
        rep = core.build_report(self.db, p.t0, p.t1)
        self.assertEqual(len(rep.days), 2)
        self.assertAlmostEqual(rep.days[0].seconds, 3600)
        self.assertAlmostEqual(rep.days[1].seconds, 5400)
        self.assertAlmostEqual(rep.total_seconds, 9000)
        self.assertAlmostEqual(rep.total_amount, 25.0)

    def test_period_clipping(self):
        pid = self.db.add_project("X")
        self.db.add_manual(pid, datetime(2026, 10, 21, 23, 0).timestamp(), datetime(2026, 10, 22, 1, 0).timestamp())
        p = core.period_bounds("today", today=date(2026, 10, 22))
        rep = core.build_report(self.db, p.t0, p.t1)
        self.assertAlmostEqual(rep.total_seconds, 3600)

    def test_period_bounds(self):
        d = date(2026, 10, 22)                  # четверг
        self.assertEqual(core.period_bounds("week", d).first, date(2026, 10, 19))
        self.assertEqual(core.period_bounds("week", d).last, date(2026, 10, 25))
        self.assertEqual(core.period_bounds("last_week", d).first, date(2026, 10, 12))
        self.assertEqual(core.period_bounds("month", d).last, date(2026, 10, 31))
        self.assertEqual(core.period_bounds("last_month", d).first, date(2026, 9, 1))
        self.assertEqual(core.period_bounds("last_month", d).last, date(2026, 9, 30))
        self.assertEqual(core.period_bounds("month", date(2026, 12, 5)).last, date(2026, 12, 31))

    def test_delete_project_keeps_time(self):
        pid = self.db.add_project("Старый")
        self.db.add_manual(pid, BASE, BASE + 3600)
        self.db.delete_project(pid)
        rep = core.build_report(self.db, 0, 4102444800.0)
        self.assertEqual(rep.rows[0].name, core.NO_PROJECT_NAME)
        self.assertAlmostEqual(rep.total_seconds, 3600)

    def test_csv_export(self):
        pid = self.db.add_project("Кресло", rate=12.5)
        self.db.add_manual(pid, BASE, BASE + 5400)
        rep = core.build_report(self.db, 0, 4102444800.0)
        path = os.path.join(self.tmp.name, "r.csv")
        core.export_csv(path, rep, "$")
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f, delimiter=";"))
        flat = [";".join(r) for r in rows]
        self.assertIn("Кресло;1:30;1,50;12,50;18,75", flat)
        self.assertIn("ВСЕГО;1:30;1,50;;18,75", flat)
        self.assertIn("22.10.2026;Кресло;1:30;1,50", flat)

    def test_parsers(self):
        self.assertEqual(core.parse_date("22.10.2026"), date(2026, 10, 22))
        self.assertEqual(core.parse_date("2026-10-22"), date(2026, 10, 22))
        self.assertEqual(core.parse_clock("9:05").hour, 9)
        self.assertEqual(core.parse_rate("12,5"), 12.5)
        self.assertEqual(core.parse_rate(""), 0.0)
        with self.assertRaises(ValueError):
            core.parse_date("вчера")


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_sanitize(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["WORKTRACKER_DATA"] = tmp
            path = os.path.join(tmp, "config.json")
            cfg = core.Config(path)
            cfg["apps"].append({"name": "Maya", "kind": "process", "pattern": "maya"})
            cfg["idle_timeout_min"] = 5
            cfg.save()
            again = core.Config(path)
            self.assertEqual(again["idle_timeout_min"], 5)
            self.assertEqual(again["apps"][-1]["pattern"], "maya")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{ сломанный json")
            broken = core.Config(path)
            self.assertEqual(len(broken["apps"]), len(core.DEFAULT_APPS))

    def test_defaults_not_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["WORKTRACKER_DATA"] = tmp
            cfg = core.Config(os.path.join(tmp, "c.json"))
            cfg["apps"].append({"name": "x", "kind": "process", "pattern": "x"})
            self.assertEqual(len(core.DEFAULT_APPS), 8)


if __name__ == "__main__":
    unittest.main()
