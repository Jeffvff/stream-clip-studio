from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, sync_playwright

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
CONTENT_URL = "https://www.tiktok.com/tiktokstudio/content"

DEFAULT_SELECTORS = {
    "file": ['input[type="file"][accept*="video"]', 'input[type="file"]'],
    "select_file": ['role=button[name=/^(select video|select file|selecionar vídeo|selecionar arquivo|selecione um arquivo)$/i]'],
    "caption": ['[contenteditable="true"][role="textbox"]', 'div[contenteditable="true"]', 'textarea'],
    "uploaded": ['text=/^(uploaded|upload complete|upload concluído|carregamento concluído)(?:\\s*[(（].*[)）])?$/i'],
    "preview": ["video"],
    "busy": ['[role="progressbar"]', 'text=/^(uploading|processing|carregando|processando)([ .…]|$)/i'],
    "upload_error": ['text=/^(upload failed|couldn.t upload|falha no upload|falha ao carregar)/i'],
    "schedule_mode": ['role=radio[name=/^(schedule|agendar)$/i]', 'text=/^(schedule|agendar)$/i'],
    "date": ['input[type="date"]', 'input[placeholder*="YYYY" i]', 'input[placeholder*="AAAA" i]',
             r'text=/^[A-Za-zÀ-ÿ]{3,12}\s+\d{1,2},\s+\d{4}$/'],
    "time": ['input[type="time"]', 'input[placeholder*="HH" i]',
             r'text=/^\d{1,2}:\d{2}(?:\s*[AP]M)?$/i'],
    "submit": ['role=button[name=/^(schedule|agendar)$/i]'],
    "privacy": ['role=combobox[name=/who can watch|who can view|who can see|quem pode assistir|quem pode ver/i]',
                '[role="combobox"]:has-text("Everyone")',
                'role=button[name=/^(everyone|public|friends|only you|público|todos|amigos|somente você)$/i]'],
    "scheduled_tab": [r'role=tab[name=/^(scheduled|agendados?)(?:\s*\(\d+\))?$/i]',
                      r'role=button[name=/^(scheduled|agendados?)(?:\s*\(\d+\))?$/i]'],
    "scheduled_row": ['[role="row"]', 'table tbody tr'],
    "challenge": ['iframe[title*="captcha" i]', '[id*="captcha" i]', 'input[autocomplete="one-time-code"]',
                  'text=/^(verify to continue|security verification|verificação de segurança|verifique para continuar)$/i'],
}


class BrowserBlocked(RuntimeError):
    pass


def safe_url(url: str):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {"www.tiktok.com", "tiktok.com", "studio.tiktok.com"}


def is_transient_browser_error(error):
    if isinstance(error, PlaywrightTimeout):
        return True
    return isinstance(error, PlaywrightError) and any(message in str(error).lower() for message in (
        "target page, context or browser has been closed", "execution context was destroyed",
        "frame was detached",
    ))


def reopen_tab(context, page):
    replacement = context.new_page()
    if not page.is_closed():
        page.close()
    return replacement


@contextmanager
def open_browser(state: Path, config: dict, *, headless=False):
    settings = config.get("browser", {})
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(state / "profile"), headless=headless, timezone_id=config["timezone"],
            locale=settings.get("locale", "en-US"), viewport={"width": 1440, "height": 950},
        )
        try:
            context.set_default_timeout(int(settings.get("action_timeout_seconds", 12)) * 1000)
            yield context, context.new_page()
        finally:
            try:
                context.close()
            except PlaywrightError as exc:
                if not is_transient_browser_error(exc):
                    raise


class StudioDOM:
    def __init__(self, settings: dict):
        custom = settings.get("selectors", {})
        unknown = set(custom) - set(DEFAULT_SELECTORS)
        if unknown:
            raise ValueError(f"Seletores desconhecidos: {sorted(unknown)}")
        if any(not isinstance(items, list) or not all(isinstance(item, str) and item for item in items)
               for items in custom.values()):
            raise ValueError("Cada seletor Studio deve ser uma lista de strings")
        self.selectors = {**DEFAULT_SELECTORS, **custom}
        self.intervention = int(settings.get("intervention_timeout_seconds", 600))

    def locate(self, page, key, *, hidden=False):
        for selector in self.selectors[key]:
            matches = page.locator(selector)
            visible = [item for item in matches.all() if hidden or item.is_visible()]
            if len(visible) == 1:
                return visible[0]
        return None

    def any_visible(self, page, key):
        return any(item.is_visible() for selector in self.selectors[key] for item in page.locator(selector).all())

    def wait_for(self, page, key, seconds, *, hidden=False):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            waited = self.guard(page)
            deadline += waited
            result = self.locate(page, key, hidden=hidden)
            if result is not None:
                return result
            page.wait_for_timeout(250)
        details = ""
        if key in {"date", "time"}:
            details = str(page.locator("input").evaluate_all(r"""inputs => inputs
                .filter(e => e.getBoundingClientRect().width > 0 && /[:\/-]/.test(e.value))
                .map(e => [e.type, e.value, e.readOnly, e.className]).slice(0,8)"""))
        raise BrowserBlocked(f"Controle Studio ausente ou ambíguo: {key} {details}")

    def guard(self, page):
        if not safe_url(page.url):
            raise BrowserBlocked(f"Página fora do TikTok autorizado: {urlparse(page.url).hostname}")
        if "/login" not in urlparse(page.url).path and not self.any_visible(page, "challenge"):
            return 0
        began = time.monotonic()
        page.bring_to_front()
        print("Conclua o login ou a verificação no navegador. O fluxo aguarda sua intervenção.", flush=True)
        while time.monotonic() - began < self.intervention:
            page.wait_for_timeout(1000)
            if not safe_url(page.url):
                raise BrowserBlocked("Verificação abriu outro domínio; conclua-a manualmente e retome")
            if "/login" not in urlparse(page.url).path and not self.any_visible(page, "challenge"):
                return time.monotonic() - began
        raise BrowserBlocked("Tempo limite para login ou verificação manual")

    def navigate(self, page, url):
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.guard(page)

    def rows(self, page):
        for selector in self.selectors["scheduled_row"]:
            rows = [row for row in page.locator(selector).all() if row.is_visible()]
            if rows:
                return rows
        return []

    def dismiss_tour(self, page):
        portal = page.locator("#react-joyride-portal")
        for _ in range(6):
            overlay = portal.locator('[data-test-id="overlay"]')
            if not any(item.is_visible() for item in overlay.all()):
                return
            candidates = portal.get_by_role("button", name=re.compile(r"^(skip|close|got it|done|pular|fechar|entendi|next|próximo)$", re.I))
            buttons = [item for item in candidates.all() if item.is_visible()]
            if buttons:
                buttons[0].click()
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        if any(item.is_visible() for item in portal.locator('[data-test-id="overlay"]').all()):
            labels = portal.get_by_role("button").all_text_contents()
            raise BrowserBlocked(f"Tutorial do editor continua ativo. Controles visíveis: {labels}")


def normalize(text):
    return re.sub(r"\s+", " ", text).strip().casefold()
