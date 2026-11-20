from __future__ import annotations

import re
import time

from browser_controller import BrowserBlocked, UPLOAD_URL, normalize
from desktop_controller import choose_file

PRIVACY = {
    "public": r"^(everyone|public|público|todos)$",
    "friends": r"^(friends|amigos)$",
    "private": r"^(only you|only me|private|somente você|só você|privado)$",
}


def upload(page, dom, job, settings):
    dom.navigate(page, UPLOAD_URL)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        deadline += dom.guard(page)
        file = dom.locate(page, "file", hidden=True)
        if file is not None:
            file.set_input_files(job["path"], timeout=30000)
            break
        button = dom.locate(page, "select_file")
        if button is not None:
            choose_file(page, button, job["path"], allow_native=settings.get("native_dialog_fallback", False))
            break
        page.wait_for_timeout(250)
    else:
        raise BrowserBlocked("Controle de seleção de vídeo indisponível no Studio")
    wait_for_upload(page, dom, int(settings.get("upload_timeout_seconds", 900)))
    caption = dom.wait_for(page, "caption", 20)
    for _ in range(2):
        caption.fill(job["caption"])
        caption.press("Escape")
        caption.evaluate("(element) => element.blur()")
        read_back = caption.evaluate("(e) => e.isContentEditable ? e.innerText : e.value")
        if normalize(read_back) == normalize(job["caption"]):
            break
    else:
        raise BrowserBlocked(f"O Studio alterou a legenda durante o preenchimento: {read_back!r}")
    dom.dismiss_tour(page)
    set_privacy(page, dom, job["privacy"])


def wait_for_upload(page, dom, seconds):
    deadline = time.monotonic() + seconds
    quiet_since = None
    while time.monotonic() < deadline:
        deadline += dom.guard(page)
        if dom.any_visible(page, "upload_error"):
            raise BrowserBlocked("O Studio rejeitou o upload")
        explicit = dom.any_visible(page, "uploaded")
        preview = explicit or any(page.locator(selector).evaluate_all(
            "(elements) => elements.some(e => e.getClientRects().length && "
            "(e.tagName !== 'VIDEO' || e.readyState >= 2))"
        ) for selector in dom.selectors["preview"])
        if (explicit or preview) and dom.locate(page, "caption") is not None and not dom.any_visible(page, "busy"):
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= 3:
                return
        else:
            quiet_since = None
        page.wait_for_timeout(500)
    raise BrowserBlocked("O Studio não apresentou um estado verificável de upload concluído")


def set_privacy(page, dom, privacy):
    control = dom.wait_for(page, "privacy", 15)
    pattern = re.compile(PRIVACY[privacy], re.I)
    if pattern.fullmatch(control.inner_text().strip()):
        return
    if control.evaluate("(element) => element.tagName === 'SELECT'"):
        option = [item for item in control.locator("option").all() if pattern.fullmatch(item.inner_text().strip())]
        if len(option) == 1:
            control.select_option(value=option[0].get_attribute("value"))
            if pattern.fullmatch(control.locator("option:checked").inner_text().strip()):
                return
        raise BrowserBlocked("Privacidade não disponível")
    control.click()
    for role in ("option", "menuitem", "radio"):
        items = [item for item in page.get_by_role(role, name=pattern).all() if item.is_visible()]
        if len(items) == 1:
            items[0].click()
            break
    else:
        options = [item for item in page.get_by_text(pattern, exact=True).all() if item.is_visible()]
        if len(options) != 1:
            raise BrowserBlocked(f"Escolha de privacidade indisponível: {privacy}")
        options[0].click()
    if not pattern.fullmatch(dom.wait_for(page, "privacy", 8).inner_text().strip()):
        raise BrowserBlocked("Privacidade selecionada não pôde ser relida")
