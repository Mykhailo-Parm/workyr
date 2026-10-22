"""Интерфейс: иконка в трее (pystray) и окно с отчётами/проектами/настройками (tkinter)."""
from __future__ import annotations

import base64
import copy
import logging
import os
import queue
import sys
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

import pystray

import core
import winapi
from icons import make_icon, png_bytes

log = logging.getLogger("worktracker.ui")

STATE_LABELS = {
    "recording": "● Идёт запись",
    "idle": "◐ Нет движения",
    "other": "○ Не рабочее окно",
    "paused": "‖ Пауза",
}
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
PERIODS = [
    ("today", "Сегодня"),
    ("yesterday", "Вчера"),
    ("week", "Эта неделя"),
    ("last_week", "Прошлая неделя"),
    ("month", "Этот месяц"),
    ("last_month", "Прошлый месяц"),
    ("all", "Всё время"),
    ("custom", "Свой период"),
]
KIND_LABELS = {"process": "Программа (имя .exe)", "title": "Заголовок окна / вкладки браузера"}
NAG_EVERY_SECONDS = 20 * 60


def fmt_day(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m.%Y')}"


def fix_clipboard_shortcuts(root: tk.Misc) -> None:
    """Ctrl+C/V/X/A работают и при русской/украинской раскладке (в Tk это не так из коробки)."""
    def handler(event):
        if event.keysym.lower() in ("c", "v", "x", "a"):
            return None                       # латинская раскладка: Tk справится сам
        widget = event.widget
        if event.keycode == 86:
            widget.event_generate("<<Paste>>")
        elif event.keycode == 67:
            widget.event_generate("<<Copy>>")
        elif event.keycode == 88:
            widget.event_generate("<<Cut>>")
        elif event.keycode == 65:
            try:
                widget.select_range(0, "end")
                widget.icursor("end")
            except (tk.TclError, AttributeError):
                pass
        return None
    root.bind_all("<Control-KeyPress>", handler)


# ----------------------------------------------------------------------------
# Универсальный диалог с полями
# ----------------------------------------------------------------------------

class FormDialog(tk.Toplevel):
    def __init__(self, parent, title: str, fields: List[dict], hint: str = "", validate: Optional[Callable] = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: Optional[dict] = None
        self._validate = validate
        self.vars = {}
        self.widgets = {}
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        row = 0
        first = None
        for f in fields:
            ttk.Label(body, text=f["label"]).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            var = tk.StringVar(value=f.get("value", ""))
            self.vars[f["key"]] = var
            if f.get("choices"):
                w = ttk.Combobox(body, textvariable=var, values=f["choices"], state="readonly", width=f.get("width", 38))
            else:
                w = ttk.Entry(body, textvariable=var, width=f.get("width", 40))
            w.grid(row=row, column=1, sticky="ew", pady=4)
            self.widgets[f["key"]] = w
            first = first or w
            row += 1
        if hint:
            ttk.Label(body, text=hint, foreground="#666666", wraplength=440, justify="left").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
            row += 1
        self.extra = ttk.Frame(body)
        self.extra.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1
        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="right")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        if first is not None:
            first.focus_set()

    def _ok(self) -> None:
        values = {k: v.get() for k, v in self.vars.items()}
        if self._validate:
            error = self._validate(values)
            if error:
                messagebox.showwarning("Проверьте данные", error, parent=self)
                return
        self.result = values
        self.destroy()

    def run(self) -> Optional[dict]:
        try:
            self.update_idletasks()
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window(self)
        return self.result


# ----------------------------------------------------------------------------
# Вкладки главного окна
# ----------------------------------------------------------------------------

class ReportTab(ttk.Frame):
    def __init__(self, parent, mw: "MainWindow"):
        super().__init__(parent, padding=8)
        self.mw = mw
        self.app = mw.app
        self.report: Optional[core.Report] = None
        self.bold = tkfont.nametofont("TkDefaultFont").copy()
        self.bold.configure(weight="bold")

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="Период:").pack(side="left")
        self.period_var = tk.StringVar(value=dict(PERIODS)["week"])
        combo = ttk.Combobox(bar, textvariable=self.period_var, values=[p[1] for p in PERIODS],
                             state="readonly", width=16)
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_period())
        today = date.today().strftime("%d.%m.%Y")
        self.from_var = tk.StringVar(value=today)
        self.to_var = tk.StringVar(value=today)
        self.from_entry = ttk.Entry(bar, textvariable=self.from_var, width=11)
        self.to_entry = ttk.Entry(bar, textvariable=self.to_var, width=11)
        ttk.Label(bar, text="с").pack(side="left", padx=(8, 2))
        self.from_entry.pack(side="left")
        ttk.Label(bar, text="по").pack(side="left", padx=(6, 2))
        self.to_entry.pack(side="left")
        self.show_btn = ttk.Button(bar, text="Показать", command=self.refresh)
        self.show_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="Экспорт в CSV…", command=self.export).pack(side="right")
        self.from_entry.bind("<Return>", lambda e: self.refresh())
        self.to_entry.bind("<Return>", lambda e: self.refresh())

        self.total_var = tk.StringVar()
        ttk.Label(self, textvariable=self.total_var, font=self.bold).pack(anchor="w", pady=(10, 4))

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True)

        top = ttk.Frame(paned)
        self.tree_p = ttk.Treeview(top, columns=("time", "hours", "amount"), show="tree headings", height=6)
        self.tree_p.heading("#0", text="Проект")
        self.tree_p.heading("time", text="Время (ч:мм)")
        self.tree_p.heading("hours", text="Часы")
        self.tree_p.heading("amount", text="Сумма")
        self.tree_p.column("#0", width=330)
        for c in ("time", "hours", "amount"):
            self.tree_p.column(c, width=110, anchor="e")
        self.tree_p.tag_configure("total", font=self.bold)
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree_p.yview)
        self.tree_p.configure(yscrollcommand=sb.set)
        self.tree_p.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        paned.add(top, weight=1)

        bottom = ttk.Frame(paned)
        self.tree_d = ttk.Treeview(bottom, columns=("time", "hours"), show="tree headings", height=10)
        self.tree_d.heading("#0", text="По дням")
        self.tree_d.heading("time", text="Время (ч:мм)")
        self.tree_d.heading("hours", text="Часы")
        self.tree_d.column("#0", width=330)
        for c in ("time", "hours"):
            self.tree_d.column(c, width=110, anchor="e")
        self.tree_d.tag_configure("day", font=self.bold)
        sb2 = ttk.Scrollbar(bottom, orient="vertical", command=self.tree_d.yview)
        self.tree_d.configure(yscrollcommand=sb2.set)
        self.tree_d.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        paned.add(bottom, weight=2)

        self._on_period(refresh=False)

    def _key(self) -> str:
        label = self.period_var.get()
        for key, name in PERIODS:
            if name == label:
                return key
        return "week"

    def _on_period(self, refresh: bool = True) -> None:
        custom = self._key() == "custom"
        state = "normal" if custom else "disabled"
        self.from_entry.configure(state=state)
        self.to_entry.configure(state=state)
        self.show_btn.configure(state=state)
        if refresh:
            self.refresh()

    def _period(self) -> Optional[core.Period]:
        key = self._key()
        try:
            if key == "custom":
                a = core.parse_date(self.from_var.get())
                b = core.parse_date(self.to_var.get())
                if b < a:
                    raise ValueError("Дата «по» раньше даты «с»")
                return core.period_bounds(key, custom_from=a, custom_to=b)
            return core.period_bounds(key)
        except ValueError as exc:
            messagebox.showwarning("Период", f"{exc}\nФормат даты: 31.12.2026", parent=self)
            return None

    def refresh(self) -> None:
        period = self._period()
        if period is None:
            return
        self.app.tracker.flush()
        report = core.build_report(self.app.db, period.t0, period.t1)
        self.report = report
        currency = self.app.cfg["currency"]

        self.tree_p.delete(*self.tree_p.get_children())
        for r in report.rows:
            amount = f"{r.amount:.2f} {currency}".strip() if r.rate else ""
            self.tree_p.insert("", "end", text=r.name,
                               values=(core.fmt_hm(r.seconds), core.fmt_hours(r.seconds), amount))
        total_amount = f"{report.total_amount:.2f} {currency}".strip() if report.total_amount else ""
        self.tree_p.insert("", "end", text="ВСЕГО", tags=("total",),
                           values=(core.fmt_hm(report.total_seconds), core.fmt_hours(report.total_seconds), total_amount))

        self.tree_d.delete(*self.tree_d.get_children())
        for day in report.days:
            node = self.tree_d.insert("", "end", text=fmt_day(day.day), open=True, tags=("day",),
                                      values=(core.fmt_hm(day.seconds), core.fmt_hours(day.seconds)))
            for r in day.items:
                self.tree_d.insert(node, "end", text="    " + r.name,
                                   values=(core.fmt_hm(r.seconds), core.fmt_hours(r.seconds)))
        self.total_var.set(f"Всего за период: {core.fmt_hm(report.total_seconds)} ч "
                           f"({core.fmt_hours(report.total_seconds)} ч)")

    def export(self) -> None:
        period = self._period()
        if period is None:
            return
        self.refresh()
        if not self.report or not self.report.rows:
            messagebox.showinfo("Экспорт", "За выбранный период нет записей.", parent=self)
            return
        if period.first:
            name = f"time_report_{period.first:%Y-%m-%d}_{period.last:%Y-%m-%d}.csv"
        else:
            name = "time_report_all.csv"
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=name,
                                            filetypes=[("CSV (Excel)", "*.csv")])
        if not path:
            return
        try:
            core.export_csv(path, self.report, self.app.cfg["currency"])
        except OSError as exc:
            messagebox.showerror("Экспорт", f"Не удалось сохранить файл:\n{exc}", parent=self)
            return
        messagebox.showinfo("Экспорт", f"Готово:\n{path}", parent=self)


class RecordsTab(ttk.Frame):
    """Список отдельных отрезков за день: можно исправить проект, удалить или добавить время вручную."""

    def __init__(self, parent, mw: "MainWindow"):
        super().__init__(parent, padding=8)
        self.mw = mw
        self.app = mw.app
        self.day = date.today()
        self.bold = tkfont.nametofont("TkDefaultFont").copy()
        self.bold.configure(weight="bold")

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="◀", width=3, command=lambda: self.shift(-1)).pack(side="left")
        self.day_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.day_var, width=12)
        entry.pack(side="left", padx=4)
        entry.bind("<Return>", lambda e: self.goto_entry())
        ttk.Button(bar, text="▶", width=3, command=lambda: self.shift(1)).pack(side="left")
        ttk.Button(bar, text="Сегодня", command=self.goto_today).pack(side="left", padx=8)
        self.total_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.total_var, font=self.bold).pack(side="right")

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, pady=8)
        self.tree = ttk.Treeview(wrap, columns=("start", "end", "dur", "project", "src"), show="headings",
                                 selectmode="extended")
        for col, text, width, anchor in (("start", "Начало", 110, "w"), ("end", "Конец", 110, "w"),
                                         ("dur", "Длительность", 100, "e"), ("project", "Проект", 280, "w"),
                                         ("src", "", 80, "w")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btns = ttk.Frame(self)
        btns.pack(fill="x")
        ttk.Button(btns, text="Сменить проект…", command=self.change_project).pack(side="left")
        ttk.Button(btns, text="Удалить", command=self.delete).pack(side="left", padx=6)
        ttk.Button(btns, text="Добавить время вручную…", command=self.add_manual).pack(side="right")
        ttk.Label(self, foreground="#666666", wraplength=780, justify="left",
                  text="Если забыла выбрать проект, время записывается как «Без проекта». "
                       "Выделите такие строки и нажмите «Сменить проект».").pack(anchor="w", pady=(8, 0))

    def shift(self, days: int) -> None:
        self.day += timedelta(days=days)
        self.refresh()

    def goto_today(self) -> None:
        self.day = date.today()
        self.refresh()

    def goto_entry(self) -> None:
        try:
            self.day = core.parse_date(self.day_var.get())
        except ValueError:
            messagebox.showwarning("Дата", "Формат даты: 31.12.2026", parent=self)
            return
        self.refresh()

    def refresh(self) -> None:
        self.app.tracker.flush()
        self.day_var.set(self.day.strftime("%d.%m.%Y"))
        t0, t1 = core.day_bounds(self.day)
        segs = self.app.db.segments_between(t0, t1)
        self.tree.delete(*self.tree.get_children())
        total = 0.0
        for s in segs:
            total += max(0.0, min(s["end"], t1) - max(s["start"], t0))
            self.tree.insert("", "end", iid=str(s["id"]), values=(
                self._clock(s["start"], t0), self._clock(s["end"], t0), core.fmt_hms(s["end"] - s["start"]),
                s["project_name"] or core.NO_PROJECT_NAME, "вручную" if s["source"] == "manual" else ""))
        self.total_var.set(f"{fmt_day(self.day)}: {core.fmt_hm(total)} ч")

    @staticmethod
    def _clock(ts: float, day_start: float) -> str:
        dt = datetime.fromtimestamp(ts)
        if ts < day_start or ts >= day_start + 86400:
            return dt.strftime("%d.%m %H:%M:%S")
        return dt.strftime("%H:%M:%S")

    def _project_choices(self):
        projects = self.app.db.projects()
        return [core.NO_PROJECT_NAME] + [p["name"] for p in projects], [None] + [p["id"] for p in projects]

    def change_project(self) -> None:
        ids = [int(i) for i in self.tree.selection()]
        if not ids:
            messagebox.showinfo("Записи", "Сначала выделите строки.", parent=self)
            return
        names, pids = self._project_choices()
        dlg = FormDialog(self, "Сменить проект", [
            {"key": "project", "label": "Проект:", "choices": names, "value": names[0]}])
        res = dlg.run()
        if not res:
            return
        pid = pids[names.index(res["project"])]
        for sid in ids:
            self.app.db.set_segment_project(sid, pid)
        self.refresh()

    def delete(self) -> None:
        ids = [int(i) for i in self.tree.selection()]
        if not ids:
            return
        if not messagebox.askyesno("Удалить", f"Удалить выделенные записи ({len(ids)} шт.)?", parent=self):
            return
        if self.app.tracker.seg_id in ids:
            self.app.tracker.seg_id = None            # удалили активный отрезок: начнём новый
        for sid in ids:
            self.app.db.delete_segment(sid)
        self.refresh()

    def add_manual(self) -> None:
        names, pids = self._project_choices()
        current = self.app.tracker.project_id
        start_name = names[pids.index(current)] if current in pids else names[0]

        def validate(v):
            try:
                core.parse_date(v["date"])
                a = core.parse_clock(v["start"])
                b = core.parse_clock(v["end"])
            except ValueError as exc:
                return f"{exc}\nДата: 31.12.2026, время: 14:30"
            if b <= a:
                return "Конец должен быть позже начала (в пределах одного дня)."
            return None

        dlg = FormDialog(self, "Добавить время вручную", [
            {"key": "project", "label": "Проект:", "choices": names, "value": start_name},
            {"key": "date", "label": "Дата:", "value": self.day.strftime("%d.%m.%Y")},
            {"key": "start", "label": "Начало (ЧЧ:ММ):", "value": "10:00"},
            {"key": "end", "label": "Конец (ЧЧ:ММ):", "value": "11:00"},
        ], validate=validate)
        res = dlg.run()
        if not res:
            return
        d = core.parse_date(res["date"])
        start = datetime.combine(d, core.parse_clock(res["start"])).timestamp()
        end = datetime.combine(d, core.parse_clock(res["end"])).timestamp()
        if self.app.db.segments_between(start, end):
            if not messagebox.askyesno("Пересечение", "На это время уже есть записи, время посчитается дважды. "
                                       "Всё равно добавить?", parent=self):
                return
        self.app.db.add_manual(pids[names.index(res["project"])], start, end)
        self.day = d
        self.refresh()


class ProjectsTab(ttk.Frame):
    HINT = ("Ключевые слова необязательны: если слово есть в заголовке активного окна (например, имя файла "
            "chair_v3.blend), время пойдёт в этот проект автоматически, даже если в меню выбран другой. "
            "Выбирайте уникальные слова. Несколько слов разделяйте запятой.")

    def __init__(self, parent, mw: "MainWindow"):
        super().__init__(parent, padding=8)
        self.mw = mw
        self.app = mw.app
        self.show_archived = tk.BooleanVar(value=False)

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=("client", "rate", "keywords", "status"), show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="Проект")
        self.tree.heading("client", text="Заказчик")
        self.tree.heading("rate", text="Ставка/час")
        self.tree.heading("keywords", text="Ключевые слова")
        self.tree.heading("status", text="")
        self.tree.column("#0", width=230)
        self.tree.column("client", width=150)
        self.tree.column("rate", width=90, anchor="e")
        self.tree.column("keywords", width=220)
        self.tree.column("status", width=80)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit())

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Добавить проект…", command=self.add).pack(side="left")
        ttk.Button(btns, text="Изменить…", command=self.edit).pack(side="left", padx=6)
        ttk.Button(btns, text="В архив / из архива", command=self.toggle_archive).pack(side="left")
        ttk.Button(btns, text="Удалить", command=self.delete).pack(side="left", padx=6)
        ttk.Checkbutton(btns, text="Показывать архивные", variable=self.show_archived,
                        command=self.refresh).pack(side="right")
        ttk.Label(self, text=self.HINT, foreground="#666666", wraplength=780, justify="left").pack(anchor="w")

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for p in self.app.db.projects(include_archived=self.show_archived.get()):
            self.tree.insert("", "end", iid=str(p["id"]), text=p["name"], values=(
                p["client"], f"{p['rate']:.2f}" if p["rate"] else "", p["keywords"],
                "архив" if p["archived"] else ""))

    def _selected(self) -> Optional[dict]:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Проекты", "Сначала выберите проект в списке.", parent=self)
            return None
        return self.app.db.get_project(int(sel[0]))

    def _form(self, title: str, p: Optional[dict]) -> Optional[dict]:
        def validate(v):
            if not v["name"].strip():
                return "Введите название проекта."
            try:
                core.parse_rate(v["rate"])
            except ValueError:
                return "Ставка должна быть числом, например 15 или 12,5."
            return None

        return FormDialog(self, title, [
            {"key": "name", "label": "Название:", "value": p["name"] if p else ""},
            {"key": "client", "label": "Заказчик (необязательно):", "value": p["client"] if p else ""},
            {"key": "rate", "label": f"Ставка в час, {self.app.cfg['currency']} (необязательно):",
             "value": (f"{p['rate']:g}" if p and p["rate"] else "")},
            {"key": "keywords", "label": "Слова в заголовке окна:", "value": p["keywords"] if p else ""},
        ], hint="Ставка нужна только для подсчёта суммы в отчёте.", validate=validate).run()

    def add(self) -> None:
        res = self._form("Новый проект", None)
        if not res:
            return
        self.app.db.add_project(res["name"], res["client"], res["keywords"], core.parse_rate(res["rate"]))
        self.app.on_projects_changed()

    def edit(self) -> None:
        p = self._selected()
        if not p:
            return
        res = self._form("Изменить проект", p)
        if not res:
            return
        self.app.db.update_project(p["id"], name=res["name"].strip(), client=res["client"].strip(),
                                   keywords=res["keywords"].strip(), rate=core.parse_rate(res["rate"]))
        self.app.on_projects_changed()

    def toggle_archive(self) -> None:
        p = self._selected()
        if not p:
            return
        self.app.db.update_project(p["id"], archived=0 if p["archived"] else 1)
        self.app.on_projects_changed()

    def delete(self) -> None:
        p = self._selected()
        if not p:
            return
        if not messagebox.askyesno(
                "Удалить проект",
                f"Удалить проект «{p['name']}»?\nЕго записи не пропадут, а станут «Без проекта».\n"
                "Если проект просто закончился, лучше отправьте его в архив.", parent=self):
            return
        self.app.db.delete_project(p["id"])
        self.app.on_projects_changed()


class SettingsTab(ttk.Frame):
    def __init__(self, parent, mw: "MainWindow"):
        super().__init__(parent, padding=8)
        self.mw = mw
        self.app = mw.app
        cfg = self.app.cfg

        general = ttk.LabelFrame(self, text="Общие", padding=10)
        general.pack(fill="x")
        self.idle_var = tk.StringVar(value=f"{cfg['idle_timeout_min']:g}")
        self.poll_var = tk.StringVar(value=f"{cfg['poll_seconds']:g}")
        self.cur_var = tk.StringVar(value=cfg["currency"])
        self.auto_var = tk.BooleanVar(value=core.autostart_enabled())
        ttk.Label(general, text="Считать простоем, если нет движения мыши/пера (минут):").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Spinbox(general, from_=1, to=60, width=6, textvariable=self.idle_var).grid(row=0, column=1, padx=8)
        ttk.Label(general, text="Как часто проверять (секунд):").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Spinbox(general, from_=1, to=10, width=6, textvariable=self.poll_var).grid(row=1, column=1, padx=8)
        ttk.Label(general, text="Валюта (для отчётов):").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(general, textvariable=self.cur_var, width=8).grid(row=2, column=1, padx=8)
        ttk.Checkbutton(general, text="Запускать вместе с Windows", variable=self.auto_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Button(general, text="Сохранить", command=self.save_general).grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.saved_var = tk.StringVar()
        ttk.Label(general, textvariable=self.saved_var, foreground="#2e7d32").grid(row=4, column=1, sticky="w", pady=(8, 0))

        apps = ttk.LabelFrame(self, text="Рабочие программы и вкладки (время идёт, только если активно одно из них)", padding=10)
        apps.pack(fill="both", expand=True, pady=(10, 0))
        wrap = ttk.Frame(apps)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=("kind", "pattern"), show="tree headings", selectmode="browse", height=8)
        self.tree.heading("#0", text="Название")
        self.tree.heading("kind", text="Тип")
        self.tree.heading("pattern", text="Ищем (часть имени)")
        self.tree.column("#0", width=230)
        self.tree.column("kind", width=230)
        self.tree.column("pattern", width=200)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit_app())
        btns = ttk.Frame(apps)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Добавить…", command=self.add_app).pack(side="left")
        ttk.Button(btns, text="Изменить…", command=self.edit_app).pack(side="left", padx=6)
        ttk.Button(btns, text="Удалить", command=self.delete_app).pack(side="left")
        ttk.Button(btns, text="Вернуть список по умолчанию", command=self.reset_apps).pack(side="right")
        ttk.Label(apps, foreground="#666666", wraplength=780, justify="left",
                  text="Совет: нажмите «Добавить…», затем «Определить программу», за 5 секунд переключитесь "
                       "на нужное окно, и имя подставится само. Для сайтов в браузере выбирайте «заголовок "
                       "вкладки» и слово из названия вкладки (например, ArtStation, Pinterest, PureRef)."
                  ).pack(anchor="w", pady=(8, 0))
        ttk.Button(self, text="Открыть папку с данными", command=self.app.open_data_folder).pack(anchor="w", pady=(10, 0))

    def refresh(self) -> None:
        self.auto_var.set(core.autostart_enabled())
        self.tree.delete(*self.tree.get_children())
        for i, a in enumerate(self.app.cfg["apps"]):
            self.tree.insert("", "end", iid=str(i), text=a["name"], values=(KIND_LABELS[a["kind"]], a["pattern"]))

    def save_general(self) -> None:
        cfg = self.app.cfg
        try:
            idle = float(self.idle_var.get().replace(",", "."))
            poll = float(self.poll_var.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Настройки", "Минуты и секунды должны быть числами.", parent=self)
            return
        cfg["idle_timeout_min"] = max(0.5, min(120.0, idle))
        cfg["poll_seconds"] = max(1.0, min(10.0, poll))
        cfg["currency"] = self.cur_var.get().strip()
        cfg.save()
        try:
            core.set_autostart(self.auto_var.get())
        except OSError as exc:
            messagebox.showwarning("Автозапуск", f"Не удалось изменить автозапуск:\n{exc}", parent=self)
        self.app.refresh_caches()
        self.saved_var.set("Сохранено")
        self.after(2500, lambda: self.saved_var.set(""))

    # --- список программ ---------------------------------------------------

    def _index(self) -> Optional[int]:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Программы", "Сначала выберите строку в списке.", parent=self)
            return None
        return int(sel[0])

    def _app_form(self, title: str, a: Optional[dict]) -> Optional[dict]:
        kinds = list(KIND_LABELS.values())

        def validate(v):
            if not v["pattern"].strip():
                return "Заполните поле «Ищем»."
            return None

        dlg = FormDialog(self, title, [
            {"key": "name", "label": "Название:", "value": a["name"] if a else ""},
            {"key": "kind", "label": "Тип:", "choices": kinds, "value": KIND_LABELS[a["kind"]] if a else kinds[0]},
            {"key": "pattern", "label": "Ищем (часть имени):", "value": a["pattern"] if a else ""},
        ], hint="Регистр не важен. «Программа»: часть имени exe-файла, например blender. "
                "«Заголовок»: часть текста в заголовке окна или вкладки браузера.", validate=validate)

        status = tk.StringVar()
        ttk.Label(dlg.extra, textvariable=status, foreground="#1565c0", wraplength=440, justify="left").pack(
            anchor="w", pady=(6, 0))
        bar = ttk.Frame(dlg.extra)
        bar.pack(anchor="w", pady=(6, 0))

        def detect(mode: str) -> None:
            def fill(proc: str, title_text: str) -> None:
                if mode == "process":
                    base = proc[:-4] if proc.lower().endswith(".exe") else proc
                    dlg.vars["kind"].set(KIND_LABELS["process"])
                    dlg.vars["pattern"].set(base)
                    if not dlg.vars["name"].get().strip():
                        dlg.vars["name"].set(base.title())
                else:
                    dlg.vars["kind"].set(KIND_LABELS["title"])
                    dlg.vars["pattern"].set(title_text)
                    if not dlg.vars["name"].get().strip():
                        dlg.vars["name"].set(title_text[:40])
                status.set("Готово. Проверьте поле «Ищем» и нажмите OK.")
                dlg.deiconify()
                dlg.lift()
                dlg.bell()

            def countdown(n: int) -> None:
                if not dlg.winfo_exists():
                    return
                if n > 0:
                    status.set(f"Переключитесь на нужную программу или вкладку… {n}")
                    dlg.after(1000, lambda: countdown(n - 1))
                    return
                proc, title_text, pid = winapi.foreground_window()
                if pid == os.getpid() or not proc:
                    status.set("Не получилось: активным осталось окно WorkTracker. Попробуйте ещё раз.")
                    return
                fill(proc, title_text)

            countdown(5)

        ttk.Button(bar, text="Определить программу (5 сек)", command=lambda: detect("process")).pack(side="left")
        ttk.Button(bar, text="Взять заголовок окна (5 сек)", command=lambda: detect("title")).pack(side="left", padx=6)
        return dlg.run()

    def _store(self, res: dict, index: Optional[int]) -> None:
        kind = next(k for k, v in KIND_LABELS.items() if v == res["kind"])
        item = {"name": res["name"].strip() or res["pattern"].strip(), "kind": kind, "pattern": res["pattern"].strip()}
        apps = self.app.cfg["apps"]
        if index is None:
            apps.append(item)
        else:
            apps[index] = item
        self.app.cfg.save()
        self.refresh()

    def add_app(self) -> None:
        res = self._app_form("Новая программа", None)
        if res:
            self._store(res, None)

    def edit_app(self) -> None:
        i = self._index()
        if i is None:
            return
        res = self._app_form("Изменить программу", self.app.cfg["apps"][i])
        if res:
            self._store(res, i)

    def delete_app(self) -> None:
        i = self._index()
        if i is None:
            return
        del self.app.cfg["apps"][i]
        self.app.cfg.save()
        self.refresh()

    def reset_apps(self) -> None:
        if messagebox.askyesno("Список программ", "Вернуть стандартный список? Свои добавленные программы пропадут.",
                               parent=self):
            self.app.cfg["apps"] = copy.deepcopy(core.DEFAULT_APPS)
            self.app.cfg.save()
            self.refresh()


# ----------------------------------------------------------------------------
# Главное окно
# ----------------------------------------------------------------------------

class MainWindow:
    def __init__(self, app: "App"):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("WorkTracker: учёт времени")
        self.win.geometry("880x640")
        self.win.minsize(780, 560)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._live_id = None
        self._combo_ids: List[Optional[int]] = [None]

        top = ttk.Frame(self.win, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Записывать в проект:").pack(side="left")
        self.proj_var = tk.StringVar()
        self.proj_combo = ttk.Combobox(top, textvariable=self.proj_var, state="readonly", width=30)
        self.proj_combo.pack(side="left", padx=6)
        self.proj_combo.bind("<<ComboboxSelected>>", self._on_pick)
        self.pause_btn = ttk.Button(top, text="Пауза", width=12, command=app.toggle_pause)
        self.pause_btn.pack(side="left", padx=6)
        self.live_var = tk.StringVar()
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        ttk.Label(top, textvariable=self.live_var, font=bold).pack(side="right")

        self.nb = ttk.Notebook(self.win)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.report = ReportTab(self.nb, self)
        self.records = RecordsTab(self.nb, self)
        self.projects = ProjectsTab(self.nb, self)
        self.settings = SettingsTab(self.nb, self)
        for tab, text in ((self.report, "  Отчёт  "), (self.records, "  Записи  "),
                          (self.projects, "  Проекты  "), (self.settings, "  Настройки  ")):
            self.nb.add(tab, text=text)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_current())

        self.refresh_projects()
        self.refresh_current()
        self._live()

    def exists(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(250, lambda: self.win.attributes("-topmost", False))
        self.win.focus_force()

    def close(self) -> None:
        if self._live_id is not None:
            try:
                self.win.after_cancel(self._live_id)
            except tk.TclError:
                pass
        self.app.window = None
        self.win.destroy()

    def refresh_current(self) -> None:
        tab = self.nb.nametowidget(self.nb.select())
        tab.refresh()

    def refresh_projects(self) -> None:
        projects = self.app.db.projects()
        self._combo_ids = [None] + [p["id"] for p in projects]
        self.proj_combo["values"] = [core.NO_PROJECT_NAME] + [p["name"] for p in projects]
        current = self.app.tracker.project_id
        self.proj_combo.current(self._combo_ids.index(current) if current in self._combo_ids else 0)

    def _on_pick(self, _event=None) -> None:
        idx = self.proj_combo.current()
        if 0 <= idx < len(self._combo_ids):
            self.app.set_project(self._combo_ids[idx])

    def _live(self) -> None:
        if not self.exists():
            return
        t = self.app.tracker
        self.live_var.set(f"{STATE_LABELS[t.state]}   ·   сегодня {core.fmt_hms(t.today_seconds())}")
        self.pause_btn.configure(text="Продолжить" if t.paused else "Пауза")
        self._live_id = self.win.after(1000, self._live)


# ----------------------------------------------------------------------------
# Приложение: трей + опрос
# ----------------------------------------------------------------------------

class App:
    def __init__(self):
        self.cfg = core.Config()
        self.db = core.Database()
        self.tracker = core.Tracker(self.db, self.cfg)
        self.sampler = winapi.Sampler()
        self.window: Optional[MainWindow] = None
        self.icon: Optional[pystray.Icon] = None
        self._quitting = False
        self._queue: "queue.Queue" = queue.Queue()
        self._icons = {}
        self._last_state: Optional[str] = None
        self._last_cache = 0.0
        self._last_nag = 0.0
        self._names = {}
        self._cache_projects: List[tuple] = []
        self._cache_current: Optional[int] = None
        self._cache_status = ""
        self._cache_autostart = core.autostart_enabled()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("WorkTracker")
        self.root.report_callback_exception = lambda exc, val, tb: log.error("Ошибка в окне", exc_info=(exc, val, tb))
        self._tk_icon = tk.PhotoImage(data=base64.b64encode(png_bytes("recording", 64)))
        try:
            self.root.iconphoto(True, self._tk_icon)
        except tk.TclError:
            pass
        self._setup_style()
        fix_clipboard_shortcuts(self.root)

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        if sys.platform == "win32" and "vista" in style.theme_names():
            style.theme_use("vista")
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        style.configure("Treeview", rowheight=int(22 * scale))

    # --- работа с потоками -------------------------------------------------

    def post(self, fn: Callable, *args) -> None:
        """Передаёт действие из потока трея в основной поток Tk."""
        self._queue.put((fn, args))

    def _pump(self) -> None:
        if self._quitting:
            return
        try:
            while True:
                fn, args = self._queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log.exception("Ошибка в действии из меню")
        except queue.Empty:
            pass
        self.root.after(300, self._pump)

    # --- запуск ------------------------------------------------------------

    def run(self) -> None:
        self.refresh_caches()
        self.icon = pystray.Icon(core.APP_NAME, self._icon_image("other"), core.APP_NAME,
                                 menu=pystray.Menu(self._menu_items))
        self.icon.run_detached()
        self.root.after(300, self._pump)
        self.root.after(500, self._tick)
        self.root.mainloop()

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        try:
            self.tracker.finish()
            self.cfg.save()
        except Exception:
            log.exception("Ошибка при завершении")
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        if self.window and self.window.exists():
            self.window.win.destroy()
        self.root.quit()
        self.root.destroy()

    # --- опрос -------------------------------------------------------------

    def _tick(self) -> None:
        if self._quitting:
            return
        try:
            sample = self.sampler.read()
            self.tracker.step(sample)
            state = self.tracker.state
            changed = state != self._last_state
            if changed:
                self._last_state = state
                self._update_icon()
            self._maybe_nag(sample.now)
            if changed or sample.now - self._last_cache >= 30:
                self._last_cache = sample.now
                self.refresh_caches()
        except Exception:
            log.exception("Ошибка опроса")
        self.root.after(int(float(self.cfg["poll_seconds"]) * 1000), self._tick)

    def _maybe_nag(self, now: float) -> None:
        t = self.tracker
        if t.state == "recording" and t.target_id is None and now - self._last_nag > NAG_EVERY_SECONDS:
            self._last_nag = now
            has_projects = bool(self._cache_projects)
            text = ("Идёт запись, но проект не выбран. Выберите проект в меню иконки (правый клик)."
                    if has_projects else
                    "Идёт запись без проекта. Создайте первый проект: правый клик по иконке → «Открыть окно».")
            try:
                if self.icon:
                    self.icon.notify(text, "WorkTracker")
            except Exception:
                log.exception("Не удалось показать уведомление")

    # --- кэши для меню трея -----------------------------------------------

    def _project_name(self, pid: Optional[int]) -> str:
        return self._names.get(pid, core.NO_PROJECT_NAME) if pid is not None else core.NO_PROJECT_NAME

    def refresh_caches(self) -> None:
        projects = self.db.projects()
        self._names = {p["id"]: p["name"] for p in self.db.projects(include_archived=True)}
        self._cache_projects = [(p["id"], p["name"].replace("&", "&&")) for p in projects]
        self._cache_current = self.tracker.project_id
        t = self.tracker
        shown = t.target_id if t.state == "recording" else t.project_id
        self._cache_status = (f"{STATE_LABELS[t.state]} · {self._project_name(shown)} · "
                              f"сегодня {core.fmt_hm(t.today_seconds())}")
        if self.icon:
            try:
                self.icon.title = self._cache_status[:120]
                self.icon.update_menu()
            except Exception:
                log.exception("Не удалось обновить меню")

    def _icon_image(self, state: str):
        if state not in self._icons:
            self._icons[state] = make_icon(state)
        return self._icons[state]

    def _update_icon(self) -> None:
        if self.icon:
            try:
                self.icon.icon = self._icon_image(self.tracker.state)
            except Exception:
                log.exception("Не удалось сменить иконку")

    def _menu_items(self):
        item = pystray.MenuItem
        noop = lambda icon, it: None
        yield item(lambda it: self._cache_status, noop, enabled=False)
        yield pystray.Menu.SEPARATOR
        yield item("Записывать в проект:", noop, enabled=False)
        yield item(core.NO_PROJECT_NAME, lambda icon, it: self.post(self.set_project, None),
                   checked=lambda it: self._cache_current is None, radio=True)
        for pid, name in self._cache_projects:
            yield item(name, (lambda p: lambda icon, it: self.post(self.set_project, p))(pid),
                       checked=(lambda p: lambda it: self._cache_current == p)(pid), radio=True)
        yield pystray.Menu.SEPARATOR
        yield item("Пауза", lambda icon, it: self.post(self.toggle_pause),
                   checked=lambda it: self.tracker.paused)
        yield item("Открыть окно (отчёты, проекты, настройки)", lambda icon, it: self.post(self.open_window),
                   default=True)
        yield item("Запускать вместе с Windows", lambda icon, it: self.post(self.toggle_autostart),
                   checked=lambda it: self._cache_autostart)
        yield pystray.Menu.SEPARATOR
        yield item("Выход", lambda icon, it: self.post(self.quit))

    # --- действия ----------------------------------------------------------

    def set_project(self, project_id: Optional[int]) -> None:
        self.tracker.set_project(project_id)
        self.refresh_caches()
        if self.window and self.window.exists():
            self.window.refresh_projects()

    def toggle_pause(self) -> None:
        self.tracker.toggle_pause()
        self._last_state = None            # заставим иконку обновиться на ближайшем шаге
        self.tracker.state = "paused" if self.tracker.paused else self.tracker.state
        self._update_icon()
        self.refresh_caches()

    def toggle_autostart(self) -> None:
        try:
            core.set_autostart(not core.autostart_enabled())
        except OSError as exc:
            messagebox.showwarning("Автозапуск", f"Не удалось изменить автозапуск:\n{exc}")
        self._cache_autostart = core.autostart_enabled()
        self.refresh_caches()

    def on_projects_changed(self) -> None:
        self.tracker.reload_projects()
        self.refresh_caches()
        if self.window and self.window.exists():
            self.window.refresh_projects()
            self.window.projects.refresh()

    def open_window(self) -> None:
        if self.window and self.window.exists():
            self.window.show()
            return
        self.window = MainWindow(self)
        self.window.show()

    def open_data_folder(self) -> None:
        path = core.data_dir()
        try:
            if sys.platform == "win32":
                os.startfile(path)      # type: ignore[attr-defined]
            else:
                messagebox.showinfo("Папка с данными", path)
        except OSError:
            messagebox.showinfo("Папка с данными", path)
