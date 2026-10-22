"""Иконки для трея и для самого exe (рисуются через Pillow, файлов с картинками нет)."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

COLORS = {
    "recording": (46, 204, 113),   # зелёный: идёт запись
    "idle": (241, 196, 15),        # жёлтый: рабочее окно, но нет движения
    "other": (138, 143, 152),      # серый: сейчас не рабочее окно
    "paused": (231, 76, 60),       # красный: пауза
}


def make_icon(state: str = "other", size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = COLORS.get(state, COLORS["other"])
    d.ellipse((2, 2, size - 3, size - 3), fill=color + (255,))
    white = (255, 255, 255, 255)
    c = size / 2
    if state == "paused":
        bar_w = size * 0.13
        d.rectangle((c - bar_w * 2.0, size * 0.28, c - bar_w * 0.6, size * 0.72), fill=white)
        d.rectangle((c + bar_w * 0.6, size * 0.28, c + bar_w * 2.0, size * 0.72), fill=white)
    else:
        width = max(2, size // 10)
        d.line((c, c, c, size * 0.2), fill=white, width=width)
        d.line((c, c, size * 0.72, c), fill=white, width=width)
        r = width / 2
        d.ellipse((c - r, c - r, c + r, c + r), fill=white)
    return img


def png_bytes(state: str = "recording", size: int = 64) -> bytes:
    buf = io.BytesIO()
    make_icon(state, size).save(buf, format="PNG")
    return buf.getvalue()


def save_ico(path: str) -> None:
    make_icon("recording", 256).save(
        path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
