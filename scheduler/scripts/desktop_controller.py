from __future__ import annotations

import os
import re
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout


def choose_file(page, button, path, *, allow_native: bool):
    try:
        with page.expect_file_chooser(timeout=5000) as chooser:
            button.click()
        chooser.value.set_files(str(path))
        return
    except PlaywrightTimeout:
        if not allow_native or os.name != "nt":
            raise RuntimeError("Seletor de arquivo não detectado pelo Playwright") from None
    windows_file_dialog(str(path))


def windows_file_dialog(path: str):
    from pywinauto import Desktop
    import win32gui
    import win32con

    # Only act on the foreground native dialog opened by this upload step.
    deadline = time.monotonic() + 8
    title = re.compile(r"^(open|abrir|select file|selecionar arquivo)$", re.I)
    while time.monotonic() < deadline:
        handle = win32gui.GetForegroundWindow()
        owner = win32gui.GetWindow(handle, win32con.GW_OWNER) if handle else None
        if (handle and win32gui.GetClassName(handle) == "#32770"
                and title.fullmatch(win32gui.GetWindowText(handle))
                and owner and win32gui.GetClassName(owner) == "Chrome_WidgetWin_1"):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("Janela nativa de seleção não identificada")
    window = Desktop(backend="uia").window(handle=handle)
    try:
        edits = window.descendants(control_type="Edit")
        filenames = [item for item in edits if re.search(r"file name|nome do arquivo", item.window_text(), re.I)]
        target = filenames[0] if len(filenames) == 1 else edits[0] if len(edits) == 1 else None
        buttons = [item for item in window.descendants(control_type="Button") if title.fullmatch(item.window_text())]
        if target is None or len(buttons) != 1:
            raise RuntimeError("Controles nativos ambíguos")
        target.set_edit_text(path)
        if win32gui.GetForegroundWindow() != handle:
            raise RuntimeError("A janela perdeu o foco")
        buttons[0].invoke()
    except Exception as exc:
        if win32gui.GetForegroundWindow() != handle or not path.isascii():
            raise RuntimeError("Seleção nativa precisa de intervenção manual") from exc
        import pyautogui
        pyautogui.hotkey("alt", "n")
        pyautogui.hotkey("ctrl", "a")
        pyautogui.write(path, interval=0.015)
        pyautogui.press("enter")
