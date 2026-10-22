"""Минимальные обращения к Windows API через ctypes (без внешних зависимостей).

Всё, что здесь вызывается, это дешёвые системные вызовы: активное окно, время
последнего ввода и позиция курсора. На не-Windows системах работают заглушки.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional, Tuple

from core import Sample

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _user32.GetLastInputInfo.restype = wintypes.BOOL
    _kernel32.GetTickCount.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def system_idle_seconds() -> float:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = (_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return millis / 1000.0

    def cursor_pos() -> Tuple[int, int]:
        pt = wintypes.POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
        return 0, 0

    def _process_name(pid: int) -> str:
        if not pid:
            return ""
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            _kernel32.CloseHandle(handle)
        return ""

    def foreground_window() -> Tuple[str, str, int]:
        """Возвращает (имя exe в нижнем регистре, заголовок окна, pid) активного окна."""
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return "", "", 0
        length = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _process_name(pid.value), buf.value, int(pid.value)

else:
    def system_idle_seconds() -> float:
        return 0.0

    def cursor_pos() -> Tuple[int, int]:
        return 0, 0

    def foreground_window() -> Tuple[str, str, int]:
        return "", "", 0


class Sampler:
    """Собирает один замер: активное окно + сколько секунд нет ввода.

    Простой считается по двум источникам: GetLastInputInfo (мышь, перо, клавиатура)
    и независимо по смещению курсора. Второе страхует случаи, когда драйвер планшета
    (например, Huion) не попадает в счётчик системного ввода, но двигает курсор.
    """

    def __init__(self) -> None:
        self._last_pos: Optional[Tuple[int, int]] = None
        self._last_move: Optional[float] = None

    def read(self) -> Sample:
        now = time.time()
        idle = system_idle_seconds()
        pos = cursor_pos()
        if self._last_pos is None:
            self._last_pos = pos
        elif pos != self._last_pos:
            self._last_pos = pos
            self._last_move = now
        if self._last_move is not None:
            idle = min(idle, max(0.0, now - self._last_move))
        process, title, _pid = foreground_window()
        return Sample(now=now, idle=idle, process=process, title=title)
