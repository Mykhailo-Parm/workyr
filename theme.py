"""Тёмная тема workyr: палитра, стили ttk и тёмная рамка окна Windows."""
from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

BG = "#0e1016"          # фон окна
SURFACE = "#161a24"     # карточки, шапка, таблицы
SURFACE2 = "#1e2331"    # поля ввода, кнопки
HOVER = "#2a3146"
BORDER = "#2b3144"
TEXT = "#e8eaf2"
MUTED = "#8a93a8"
ACCENT = "#7c6cf0"
ACCENT_HOVER = "#9588ff"
ACCENT_PRESSED = "#6556d6"
SELECTION = "#34306b"
GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#f87171"

FAMILY = "Segoe UI"


def apply(root: tk.Tk) -> float:
    """Применяет тему ко всему приложению. Возвращает масштаб экрана (1.0 = 96 dpi)."""
    global FAMILY
    style = ttk.Style(root)
    style.theme_use("clam")

    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    if FAMILY not in available:
        FAMILY = tkfont.nametofont("TkDefaultFont").actual("family")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=FAMILY, size=10)
        except tk.TclError:
            pass

    scale = max(1.0, root.winfo_fpixels("1i") / 96.0)

    root.configure(bg=BG)
    root.option_add("*Toplevel.background", BG)
    root.option_add("*TCombobox*Listbox.background", SURFACE2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)

    style.configure(".", background=BG, foreground=TEXT, fieldbackground=SURFACE2, bordercolor=BORDER,
                    darkcolor=BORDER, lightcolor=BORDER, troughcolor=BG, focuscolor=BG,
                    selectbackground=SELECTION, selectforeground=TEXT, insertcolor=TEXT)

    # --- рамки и подписи
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Info.TLabel", foreground=ACCENT_HOVER)
    style.configure("Success.TLabel", foreground=GREEN)
    style.configure("Header.TFrame", background=SURFACE)
    style.configure("Brand.TLabel", background=SURFACE, foreground=ACCENT_HOVER, font=(FAMILY, 17, "bold"))
    style.configure("HeaderText.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Clock.TLabel", background=SURFACE, foreground=TEXT, font=(FAMILY, 13, "bold"))
    for state, color in (("Recording", GREEN), ("Idle", AMBER), ("Other", MUTED), ("Paused", RED)):
        style.configure(f"State{state}.TLabel", background=SURFACE, foreground=color, font=(FAMILY, 11, "bold"))
    style.configure("Card.TFrame", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=(FAMILY, 9))
    style.configure("CardBig.TLabel", background=SURFACE, foreground=TEXT, font=(FAMILY, 20, "bold"))
    style.configure("Section.TLabel", foreground=MUTED, font=(FAMILY, 9, "bold"))
    style.configure("TSeparator", background=BORDER)

    style.configure("TLabelframe", background=BG, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=(FAMILY, 9, "bold"))

    # --- кнопки
    style.configure("TButton", background=SURFACE2, foreground=TEXT, bordercolor=BORDER, lightcolor=SURFACE2,
                    darkcolor=SURFACE2, focusthickness=0, focuscolor=SURFACE2, padding=(14, 6), relief="flat")
    style.map("TButton",
              background=[("disabled", SURFACE), ("pressed", BORDER), ("active", HOVER)],
              foreground=[("disabled", MUTED)],
              bordercolor=[("active", ACCENT)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT)
    style.map("Accent.TButton",
              background=[("disabled", SURFACE2), ("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
              bordercolor=[("active", ACCENT_HOVER)])
    style.configure("Danger.TButton", foreground=RED)
    style.map("Danger.TButton", bordercolor=[("active", RED)])

    # --- поля ввода
    for widget in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(widget, fieldbackground=SURFACE2, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT, padding=5,
                        arrowcolor=MUTED, background=SURFACE2)
        style.map(widget,
                  bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)],
                  fieldbackground=[("disabled", SURFACE), ("readonly", SURFACE2)],
                  foreground=[("disabled", MUTED)],
                  arrowcolor=[("active", TEXT)])
    style.map("TCombobox", selectbackground=[("readonly", SURFACE2)], selectforeground=[("readonly", TEXT)],
              background=[("active", HOVER)])

    # --- флажки
    style.configure("TCheckbutton", background=BG, foreground=TEXT, focuscolor=BG, indicatorbackground=SURFACE2,
                    indicatorforeground="#ffffff")
    style.map("TCheckbutton", background=[("active", BG)],
              indicatorbackground=[("selected", ACCENT), ("pressed", HOVER), ("!selected", SURFACE2)])

    # --- вкладки
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(18, 9), borderwidth=0,
                    font=(FAMILY, 10, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", SURFACE), ("active", SURFACE2)],
              foreground=[("selected", TEXT), ("active", TEXT)])
    try:
        style.layout("TNotebook.Tab", [("Notebook.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""})]})]})])
    except tk.TclError:
        pass

    # --- таблицы
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, borderwidth=0,
                    rowheight=int(26 * scale), relief="flat")
    style.map("Treeview", background=[("selected", SELECTION)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=SURFACE2, foreground=MUTED, relief="flat", borderwidth=0,
                    padding=(8, 7), font=(FAMILY, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", HOVER)], foreground=[("active", TEXT)])
    try:
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    except tk.TclError:
        pass

    # --- полосы прокрутки и разделитель
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=SURFACE2, troughcolor=BG, bordercolor=BG,
                        lightcolor=SURFACE2, darkcolor=SURFACE2, arrowcolor=MUTED, gripcount=0, relief="flat")
        style.map(f"{orient}.TScrollbar", background=[("active", HOVER), ("pressed", ACCENT)])
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=8, gripcount=0, background=BG)
    return scale


def dark_titlebar(window: tk.Misc) -> None:
    """Тёмная рамка окна в Windows 10/11 (в остальных системах ничего не делает)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        window.update_idletasks()
        user32 = ctypes.WinDLL("user32")
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1)
        dwm = ctypes.WinDLL("dwmapi")
        for attribute in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 1903+ / Win11)
            if dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute, ctypes.byref(value),
                                         ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass
