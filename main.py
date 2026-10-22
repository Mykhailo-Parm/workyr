"""Точка входа WorkTracker: один экземпляр, лог в файл, трей + окно."""
import logging
import logging.handlers
import os
import sys


def _single_instance():
    """Не даёт запустить программу дважды. Возвращает «ручку», которую нужно держать до выхода."""
    if sys.platform != "win32":
        return object()
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\WorkTrackerSingleInstance")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            0, "WorkTracker уже запущен: ищите иконку с часами возле часов Windows (может быть за стрелкой ^).",
            "WorkTracker", 0x40)
        return None
    return handle or True      # даже если мьютекс не создался, запускаемся, а не молча выходим


def _set_dpi_awareness():
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _setup_logging():
    import core
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(core.data_dir(), "worktracker.log"), maxBytes=200_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    sys.excepthook = lambda t, v, tb: logging.getLogger("worktracker").error("Необработанная ошибка", exc_info=(t, v, tb))


def main():
    handle = _single_instance()
    if handle is None:
        return
    _set_dpi_awareness()
    _setup_logging()
    from ui import App
    App().run()


if __name__ == "__main__":
    main()
