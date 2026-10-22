"""Создаёт icon.ico для exe-файла (вызывается из build.bat)."""
from icons import save_ico

save_ico("icon.ico")
print("icon.ico created")
