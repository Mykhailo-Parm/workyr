"""Ядро WorkTracker: настройки, база данных, логика учёта времени и отчёты.

Здесь нет ни Windows API, ни интерфейса: модуль легко тестируется на любой системе.
"""
from __future__ import annotations

import copy
import csv
import json
import logging
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("worktracker.core")

APP_NAME = "WorkTracker"
MIN_SEGMENT_SECONDS = 5.0   # более короткие отрезки считаем случайными и не сохраняем
FLUSH_SECONDS = 15.0        # как часто дописываем текущий отрезок в базу
NO_PROJECT_NAME = "Без проекта"


# ----------------------------------------------------------------------------
# Пути и вспомогательные функции
# ----------------------------------------------------------------------------

def data_dir() -> str:
    override = os.environ.get("WORKTRACKER_DATA")
    if override:
        base = override
    else:
        root = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
        base = os.path.join(root, APP_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def fmt_hms(seconds: float) -> str:
    total = int(round(max(0.0, seconds)))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"


def fmt_hm(seconds: float) -> str:
    total = int(round(max(0.0, seconds) / 60.0))
    h, m = divmod(total, 60)
    return f"{h}:{m:02d}"


def fmt_hours(seconds: float) -> str:
    return f"{max(0.0, seconds) / 3600.0:.2f}"


def parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Не понимаю дату: {text!r}")


def parse_clock(text: str) -> dtime:
    text = text.strip().replace(".", ":")
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Не понимаю время: {text!r}")


def parse_rate(text: str) -> float:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return 0.0
    value = float(text)
    if value < 0:
        raise ValueError("Ставка не может быть отрицательной")
    return value


# ----------------------------------------------------------------------------
# Настройки
# ----------------------------------------------------------------------------

DEFAULT_APPS = [
    {"name": "Blender", "kind": "process", "pattern": "blender"},
    {"name": "ZBrush", "kind": "process", "pattern": "zbrush"},
    {"name": "Marmoset Toolbag", "kind": "process", "pattern": "toolbag"},
    {"name": "Substance 3D", "kind": "process", "pattern": "substance"},
    {"name": "Photoshop", "kind": "process", "pattern": "photoshop"},
    {"name": "Unreal Engine 5", "kind": "process", "pattern": "unrealeditor"},
    {"name": "Unreal Engine 4", "kind": "process", "pattern": "ue4editor"},
    {"name": "Meshy (вкладка в браузере)", "kind": "title", "pattern": "meshy"},
]

DEFAULT_CONFIG = {
    "idle_timeout_min": 3,
    "poll_seconds": 2,
    "currency": "$",
    "current_project_id": None,
    "apps": DEFAULT_APPS,
}


def _clamp(value, lo, hi, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


class Config:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(data_dir(), "config.json")
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.exception("Не удалось прочитать config.json, использую настройки по умолчанию")
        self._sanitize()

    def _sanitize(self) -> None:
        d = self.data
        d["idle_timeout_min"] = _clamp(d.get("idle_timeout_min"), 0.5, 120, 3)
        d["poll_seconds"] = _clamp(d.get("poll_seconds"), 1, 10, 2)
        if not isinstance(d.get("currency"), str):
            d["currency"] = "$"
        apps = d.get("apps")
        if not isinstance(apps, list):
            apps = copy.deepcopy(DEFAULT_APPS)
        clean = []
        for a in apps:
            if not isinstance(a, dict):
                continue
            pattern = str(a.get("pattern", "")).strip()
            if a.get("kind") in ("process", "title") and pattern:
                clean.append({"name": str(a.get("name") or pattern), "kind": a["kind"], "pattern": pattern})
        d["apps"] = clean

    def save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Не удалось сохранить config.json")

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


# ----------------------------------------------------------------------------
# База данных
# ----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    client   TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',
    rate     REAL NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    start      REAL NOT NULL,
    end        REAL NOT NULL,
    source     TEXT NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_segments_start ON segments(start);
"""


class Database:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(data_dir(), "tracker.db")
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # --- проекты -----------------------------------------------------------

    def projects(self, include_archived: bool = False) -> List[dict]:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY archived, LOWER(name)"
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql)]

    def get_project(self, project_id: int) -> Optional[dict]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return dict(row) if row else None

    def add_project(self, name: str, client: str = "", keywords: str = "", rate: float = 0.0, now: float = 0.0) -> int:
        import time as _time
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO projects(name, client, keywords, rate, created) VALUES (?,?,?,?,?)",
                (name.strip(), client.strip(), keywords.strip(), float(rate), now or _time.time()),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def update_project(self, project_id: int, **fields) -> None:
        allowed = {"name", "client", "keywords", "rate", "archived"}
        items = [(k, v) for k, v in fields.items() if k in allowed]
        if not items:
            return
        sets = ", ".join(f"{k} = ?" for k, _ in items)
        with self.lock:
            self.conn.execute(f"UPDATE projects SET {sets} WHERE id = ?", [v for _, v in items] + [project_id])
            self.conn.commit()

    def delete_project(self, project_id: int) -> None:
        """Удаляет проект; его записи остаются, но становятся «Без проекта»."""
        with self.lock:
            self.conn.execute("UPDATE segments SET project_id = NULL WHERE project_id = ?", (project_id,))
            self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            self.conn.commit()

    # --- отрезки времени ---------------------------------------------------

    def open_segment(self, project_id: Optional[int], start: float, source: str = "auto") -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO segments(project_id, start, end, source) VALUES (?,?,?,?)",
                (project_id, start, start, source),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def extend_segment(self, segment_id: int, end: float) -> None:
        with self.lock:
            self.conn.execute("UPDATE segments SET end = ? WHERE id = ?", (end, segment_id))
            self.conn.commit()

    def close_segment(self, segment_id: int, start: float, end: float) -> None:
        with self.lock:
            if end - start < MIN_SEGMENT_SECONDS:
                self.conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
            else:
                self.conn.execute("UPDATE segments SET end = ? WHERE id = ?", (end, segment_id))
            self.conn.commit()

    def add_manual(self, project_id: Optional[int], start: float, end: float) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO segments(project_id, start, end, source) VALUES (?,?,?, 'manual')",
                (project_id, start, end),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def delete_segment(self, segment_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
            self.conn.commit()

    def set_segment_project(self, segment_id: int, project_id: Optional[int]) -> None:
        with self.lock:
            self.conn.execute("UPDATE segments SET project_id = ? WHERE id = ?", (project_id, segment_id))
            self.conn.commit()

    def segments_between(self, t0: float, t1: float) -> List[dict]:
        """Все отрезки, пересекающиеся с интервалом [t0, t1)."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT s.*, p.name AS project_name FROM segments s "
                "LEFT JOIN projects p ON p.id = s.project_id "
                "WHERE s.end > ? AND s.start < ? ORDER BY s.start",
                (t0, t1),
            ).fetchall()
            return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# Выбор «рабочих» окон
# ----------------------------------------------------------------------------

@dataclass
class Sample:
    now: float       # time.time()
    idle: float      # секунд с последнего движения мыши/пера или нажатия клавиши
    process: str     # имя exe активного окна, например "blender.exe"
    title: str       # заголовок активного окна


def match_app(apps: List[dict], process: str, title: str) -> Optional[dict]:
    p = (process or "").lower()
    t = (title or "").lower()
    for app in apps:
        pattern = str(app.get("pattern", "")).strip().lower()
        if not pattern:
            continue
        if app.get("kind") == "process":
            if pattern in p:
                return app
        elif pattern in t:
            return app
    return None


# ----------------------------------------------------------------------------
# Трекер
# ----------------------------------------------------------------------------

class Tracker:
    """Превращает поток замеров (окно + простой) в отрезки времени по проектам.

    Состояния: recording (идёт запись), idle (рабочее окно, но нет движения),
    other (активно нерабочее окно), paused (поставлено на паузу).
    """

    def __init__(self, db: Database, cfg: Config):
        self.db = db
        self.cfg = cfg
        self.paused = False
        self.state = "other"
        self.target_id: Optional[int] = None     # проект, в который идёт запись прямо сейчас
        self.window_app = ""                     # название сработавшей программы
        self._projects: List[Tuple[int, List[str]]] = []
        self.project_id: Optional[int] = None
        self.seg_id: Optional[int] = None
        self.seg_start = 0.0
        self.seg_end = 0.0
        self.seg_project: Optional[int] = None
        self._flushed_end = 0.0
        self._last_flush = 0.0
        self.last_tick: Optional[float] = None
        self.reload_projects()
        saved = cfg.get("current_project_id")
        self.project_id = saved if self._is_selectable(saved) else None

    # --- проекты -----------------------------------------------------------

    def reload_projects(self) -> None:
        self._projects = []
        selectable = set()
        for p in self.db.projects():
            words = [w.strip().lower() for w in (p.get("keywords") or "").split(",") if w.strip()]
            self._projects.append((p["id"], words))
            selectable.add(p["id"])
        if self.project_id is not None and self.project_id not in selectable:
            self.project_id = None
            self.cfg["current_project_id"] = None
            self.cfg.save()

    def _is_selectable(self, project_id) -> bool:
        return project_id is not None and any(pid == project_id for pid, _ in self._projects)

    def set_project(self, project_id: Optional[int]) -> None:
        self.project_id = project_id if self._is_selectable(project_id) else None
        self.cfg["current_project_id"] = self.project_id
        self.cfg.save()

    def _project_by_title(self, title: str) -> Optional[int]:
        t = (title or "").lower()
        for pid, words in self._projects:
            for w in words:
                if w in t:
                    return pid
        return None

    # --- основной шаг ------------------------------------------------------

    def step(self, s: Sample) -> str:
        now = s.now
        poll = float(self.cfg["poll_seconds"])
        timeout = float(self.cfg["idle_timeout_min"]) * 60.0

        # Сон, гибернация, перевод часов: время «дыры» не считаем.
        if self.last_tick is not None and (now < self.last_tick or now - self.last_tick > max(30.0, poll * 5)):
            self._close(min(self.seg_end, now))
        self.last_tick = now

        app = match_app(self.cfg["apps"], s.process, s.title)
        kw_project = self._project_by_title(s.title)
        is_work = app is not None or kw_project is not None
        idle_too_long = s.idle >= timeout
        target = kw_project if kw_project is not None else self.project_id
        self.window_app = app["name"] if app else ""
        active = (not self.paused) and is_work and not idle_too_long

        if active:
            if self.seg_id is not None and self.seg_project != target:
                self._close(now)
            if self.seg_id is None:
                self._open(target, now)
            else:
                self.seg_end = now
                if now - self._last_flush >= FLUSH_SECONDS:
                    self.flush()
            self.state = "recording"
            self.target_id = target
        else:
            if self.seg_id is not None:
                if (not self.paused) and is_work and idle_too_long:
                    end = min(now, max(self.seg_start, now - s.idle))   # отрезаем «хвост» простоя
                else:
                    end = now
                self._close(end)
            self.state = "paused" if self.paused else ("idle" if is_work else "other")
            self.target_id = target if is_work else None
        return self.state

    def _open(self, project_id: Optional[int], now: float) -> None:
        self.seg_id = self.db.open_segment(project_id, now)
        self.seg_project = project_id
        self.seg_start = self.seg_end = self._flushed_end = self._last_flush = now

    def _close(self, end: float) -> None:
        if self.seg_id is None:
            return
        end = max(end, self.seg_start)
        self.db.close_segment(self.seg_id, self.seg_start, end)
        self.seg_id = None

    def flush(self) -> None:
        if self.seg_id is not None and self.seg_end > self._flushed_end:
            self.db.extend_segment(self.seg_id, self.seg_end)
            self._flushed_end = self.seg_end
            self._last_flush = self.seg_end

    def finish(self) -> None:
        """Корректно завершает текущий отрезок (при выходе из программы)."""
        self._close(self.seg_end)

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    # --- «живые» цифры -----------------------------------------------------

    def seconds_between(self, t0: float, t1: float) -> float:
        """Сумма записанного времени в [t0, t1) с учётом ещё не сохранённого хвоста."""
        total = sum(summarize(self.db.segments_between(t0, t1), t0, t1).values())
        if self.seg_id is not None and self.seg_end > self._flushed_end:
            lo = max(self._flushed_end, t0)
            hi = min(self.seg_end, t1)
            if hi > lo:
                total += hi - lo
        return total

    def today_seconds(self, now: Optional[float] = None) -> float:
        import time as _time
        now = _time.time() if now is None else now
        t0, t1 = day_bounds(datetime.fromtimestamp(now).date())
        return self.seconds_between(t0, t1)


# ----------------------------------------------------------------------------
# Отчёты
# ----------------------------------------------------------------------------

def day_bounds(d: date) -> Tuple[float, float]:
    start = datetime.combine(d, dtime.min).timestamp()
    end = datetime.combine(d + timedelta(days=1), dtime.min).timestamp()
    return start, end


@dataclass
class Period:
    t0: float
    t1: float
    first: Optional[date]
    last: Optional[date]


def period_bounds(kind: str, today: Optional[date] = None,
                  custom_from: Optional[date] = None, custom_to: Optional[date] = None) -> Period:
    today = today or date.today()
    if kind == "all":
        return Period(0.0, 4102444800.0, None, None)       # до 2100 года
    if kind == "today":
        a = b = today
    elif kind == "yesterday":
        a = b = today - timedelta(days=1)
    elif kind == "week":
        a = today - timedelta(days=today.weekday())
        b = a + timedelta(days=6)
    elif kind == "last_week":
        a = today - timedelta(days=today.weekday() + 7)
        b = a + timedelta(days=6)
    elif kind == "month":
        a = today.replace(day=1)
        b = (a.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    elif kind == "last_month":
        b = today.replace(day=1) - timedelta(days=1)
        a = b.replace(day=1)
    elif kind == "custom":
        if custom_from is None or custom_to is None:
            raise ValueError("Не задан период")
        a, b = custom_from, custom_to
    else:
        raise ValueError(f"Неизвестный период: {kind}")
    return Period(day_bounds(a)[0], day_bounds(b)[1], a, b)


def iter_day_pieces(start: float, end: float):
    """Режет отрезок по локальным полуночам: выдаёт (дата, секунды)."""
    cur = start
    while cur < end:
        d = datetime.fromtimestamp(cur).date()
        boundary = datetime.combine(d + timedelta(days=1), dtime.min).timestamp()
        piece_end = min(end, boundary)
        if piece_end <= cur:
            break
        yield d, piece_end - cur
        cur = piece_end


def summarize(segments: List[dict], t0: float, t1: float) -> Dict[Optional[int], float]:
    result: Dict[Optional[int], float] = {}
    for seg in segments:
        lo = max(seg["start"], t0)
        hi = min(seg["end"], t1)
        if hi > lo:
            pid = seg["project_id"]
            result[pid] = result.get(pid, 0.0) + (hi - lo)
    return result


@dataclass
class ReportRow:
    project_id: Optional[int]
    name: str
    seconds: float
    rate: float = 0.0

    @property
    def amount(self) -> float:
        return self.rate * self.seconds / 3600.0


@dataclass
class DayRow:
    day: date
    items: List[ReportRow] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(i.seconds for i in self.items)


@dataclass
class Report:
    rows: List[ReportRow]
    days: List[DayRow]

    @property
    def total_seconds(self) -> float:
        return sum(r.seconds for r in self.rows)

    @property
    def total_amount(self) -> float:
        return sum(r.amount for r in self.rows)


def build_report(db: Database, t0: float, t1: float) -> Report:
    projects = {p["id"]: p for p in db.projects(include_archived=True)}

    def row(pid: Optional[int], seconds: float) -> ReportRow:
        p = projects.get(pid)
        return ReportRow(pid, p["name"] if p else NO_PROJECT_NAME, seconds, float(p["rate"]) if p else 0.0)

    by_project: Dict[Optional[int], float] = {}
    by_day: Dict[date, Dict[Optional[int], float]] = {}
    for seg in db.segments_between(t0, t1):
        lo = max(seg["start"], t0)
        hi = min(seg["end"], t1)
        if hi <= lo:
            continue
        pid = seg["project_id"]
        for d, secs in iter_day_pieces(lo, hi):
            by_project[pid] = by_project.get(pid, 0.0) + secs
            day = by_day.setdefault(d, {})
            day[pid] = day.get(pid, 0.0) + secs

    rows = sorted((row(pid, s) for pid, s in by_project.items()), key=lambda r: -r.seconds)
    days = []
    for d in sorted(by_day):
        items = sorted((row(pid, s) for pid, s in by_day[d].items()), key=lambda r: -r.seconds)
        days.append(DayRow(d, items))
    return Report(rows, days)


def export_csv(path: str, report: Report, currency: str = "") -> None:
    def num(x: float) -> str:
        return f"{x:.2f}".replace(".", ",")      # запятая — чтобы Excel в RU/UA/DE понял число

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Итого по проектам"])
        w.writerow(["Проект", "Время (ч:мм)", "Часы", "Ставка в час", f"Сумма {currency}".strip()])
        for r in report.rows:
            w.writerow([r.name, fmt_hm(r.seconds), num(r.seconds / 3600.0),
                        num(r.rate) if r.rate else "", num(r.amount) if r.rate else ""])
        w.writerow(["ВСЕГО", fmt_hm(report.total_seconds), num(report.total_seconds / 3600.0), "",
                    num(report.total_amount) if report.total_amount else ""])
        w.writerow([])
        w.writerow(["По дням"])
        w.writerow(["Дата", "Проект", "Время (ч:мм)", "Часы"])
        for day in report.days:
            for r in day.items:
                w.writerow([day.day.strftime("%d.%m.%Y"), r.name, fmt_hm(r.seconds), num(r.seconds / 3600.0)])


# ----------------------------------------------------------------------------
# Автозапуск с Windows
# ----------------------------------------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}"'


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
