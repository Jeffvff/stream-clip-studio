from __future__ import annotations

import re
import time
from collections import Counter
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from browser_controller import BrowserBlocked, CONTENT_URL, normalize, safe_url

MONTHS = (
    ("january", "janeiro"), ("february", "fevereiro"), ("march", "março"),
    ("april", "abril"), ("may", "maio"), ("june", "junho"),
    ("july", "julho"), ("august", "agosto"), ("september", "setembro"),
    ("october", "outubro"), ("november", "novembro"), ("december", "dezembro"),
)
VISIBLE_TIMEZONE = re.compile(r"\(gmt\s*([+-]\d{1,2})(?::(\d{2}))?\)")
VISIBLE_SCHEDULE_DATE = re.compile(r"\b[a-z]{3,9} \d{1,2},\s+\d{1,2}:\d{2}\s+[ap]m\b")
STUDIO_SCHEDULE_MARKER = "studio verified date field"


def local_target(job):
    return datetime.fromisoformat(job["utc"]).astimezone(ZoneInfo(job["zone"]))


def date_labels(target, locale):
    y, m, d = target.year, target.month, target.day
    names = MONTHS[m - 1]
    results = {target.date().isoformat(), f"{y}/{m:02}/{d:02}", f"{y}.{m:02}.{d:02}"}
    layouts = ((m, d),) if locale == "en-US" else ((d, m),)
    for a, b in layouts:
        results.update({f"{a:02}/{b:02}/{y}", f"{a}/{b}/{y}", f"{a:02}/{b:02}/{str(y)[-2:]}"})
    results.update(f"{name} {d}, {y}" for name in (names[0], names[0][:3]))
    results.update(f"{d} de {name} de {y}" for name in (names[1], names[1][:3]))
    return results


def time_labels(target):
    hour = target.hour
    half = 12 if hour % 12 == 0 else hour % 12
    period = "am" if hour < 12 else "pm"
    minute = target.minute
    return {target.strftime("%H:%M"), f"{hour}:{minute:02}",
            f"{half}:{minute:02} {period}", f"{half}:{minute:02}{period}",
            f"{half:02}:{minute:02} {period}", f"{half:02}:{minute:02}{period}"}


def contains_label(text, choices):
    return any(re.search(r"(?<![\w/:-])" + re.escape(choice) + r"(?![\w/:-])", text) for choice in choices)


def label_date_value(control):
    return normalize(control.evaluate("(e) => 'value' in e ? e.value : e.innerText"))


def prepare_schedule(page, dom, job, settings):
    target = local_target(job)
    locale = settings.get("locale", "en-US")
    if page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") != job["zone"]:
        raise BrowserBlocked("O fuso do navegador não coincide com o plano aprovado")
    dom.dismiss_tour(page)
    mode = dom.wait_for(page, "schedule_mode", 20)
    if mode.get_attribute("type") == "radio":
        mode.press("Space")
    else:
        mode.click()
    dom.dismiss_tour(page)
    date_values = date_labels(target, locale)
    time_values = time_labels(target)
    fill_control(page, dom, "date", target.date().isoformat(), date_values, locale)
    fill_control(page, dom, "time", target.strftime("%H:%M"), time_values, locale)
    verify_controls(page, dom, job, settings)
    dom.guard(page)
    submit = dom.wait_for(page, "submit", 15)
    if not re.fullmatch(r"schedule|agendar", normalize(submit.inner_text())) or not submit.is_enabled():
        raise BrowserBlocked("Botão inequívoco de agendamento indisponível")
    return submit


def verify_controls(page, dom, job, settings):
    target = local_target(job)
    read_date = label_date_value(dom.wait_for(page, "date", 5))
    read_time = label_date_value(dom.wait_for(page, "time", 5))
    if read_date not in date_labels(target, settings.get("locale", "en-US")) or read_time not in time_labels(target):
        raise BrowserBlocked("A data ou hora lida do Studio difere da prévia")


def confirm_pending_check(page, dom, job, settings):
    title = page.get_by_text("Continue to post?", exact=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if title.count() == 1 and title.is_visible():
            if not settings.get("confirm_pending_check", False):
                raise BrowserBlocked("O Studio pediu confirmação para continuar antes do fim da checagem")
            message = page.get_by_text(re.compile(r"still checking your video for potential issues", re.I))
            action = page.get_by_role("button", name="Post now", exact=True)
            mode = page.locator('input[type="radio"][name="postSchedule"][value="schedule"]')
            if (message.count() != 1 or not message.is_visible() or action.count() != 1
                    or not action.is_visible() or mode.count() != 1 or not mode.is_checked()):
                raise BrowserBlocked("A confirmação exibida não corresponde ao agendamento aprovado")
            verify_controls(page, dom, job, settings)
            dom.guard(page)
            action.click(timeout=10000)
            return
        if "/tiktokstudio/upload" not in page.url:
            return
        page.wait_for_timeout(250)


def fill_control(page, dom, key, iso, accepted, locale):
    control = dom.wait_for(page, key, 12)
    if label_date_value(control) in accepted:
        return
    if control.evaluate("(e) => e.tagName === 'INPUT' && !e.readOnly"):
        kind = control.get_attribute("type")
        placeholder = (control.get_attribute("placeholder") or "").upper()
        value = iso
        if key == "date" and kind != "date" and ("MM/DD" in placeholder or "DD/MM" in placeholder):
            day = datetime.fromisoformat(iso)
            value = day.strftime("%m/%d/%Y" if "MM/DD" in placeholder else "%d/%m/%Y")
        control.fill(value)
        control.press("Tab")
        return
    control.click()
    if key == "date":
        for _ in range(3):  # TikTok's supported window covers at most two calendar months.
            cell = page.locator(f'[data-date="{iso}"], [datetime="{iso}"], [aria-label="{iso}"]')
            visible = [item for item in cell.all() if item.is_visible() and item.is_enabled()]
            if len(visible) == 1:
                visible[0].click()
                return
            for label in accepted:
                matches = [item for item in page.get_by_role("button", name=re.compile(re.escape(label), re.I)).all()
                           if item.is_visible() and item.is_enabled()]
                if len(matches) == 1:
                    matches[0].click()
                    return
            forward = [item for item in page.get_by_role(
                "button", name=re.compile(r"^(next month|próximo mês)$", re.I)
            ).all() if item.is_visible() and item.is_enabled()]
            if len(forward) != 1:
                break
            forward[0].click()
    else:
        pattern = re.compile(r"^(?:" + "|".join(map(re.escape, accepted)) + r")$", re.I)
        for role in ("option", "button"):
            options = [item for item in page.get_by_role(role, name=pattern).all()
                       if item.is_visible() and item.is_enabled()]
            if len(options) == 1:
                options[0].click()
                return
        if select_time_wheels(page, control, iso):
            return
    raise BrowserBlocked(f"O seletor de {key} precisa ser calibrado nesta versão do Studio")


def select_time_wheels(page, control, iso):
    # The Studio renders two independently scrolling numeric columns in a popover.
    anchor = control.bounding_box()
    if not anchor:
        return False
    candidates = page.locator("div").evaluate_all(r"""(nodes, a) => nodes.flatMap((e, index) => {
        const r = e.getBoundingClientRect();
        if (r.width < 30 || r.width > 180 || r.height < 50 || r.height > 320 ||
            Math.abs(r.x - a.x) > 220 || Math.abs(r.y + r.height/2 - a.y) > 230 ||
            e.scrollHeight - e.clientHeight < 32) return [];
        const numbers = e.innerText.split(/\s+/).filter(text => /^\d{1,2}$/.test(text));
        if (numbers.length < 4 || new Set(numbers).size !== numbers.length) return [];
        const css = window.getComputedStyle(e);
        if (!/^(auto|scroll|overlay)$/.test(css.overflowY)) return [];
        return [{index, x:r.x, y:r.y, width:r.width, height:r.height, numbers,
                 capacity:e.scrollHeight-e.clientHeight}];
    }).sort((a,b)=>a.x-b.x)""", anchor)
    if len(candidates) != 2:
        raise BrowserBlocked(f"Colunas de hora/minuto do Studio não reconhecidas: {candidates}")
    for wheel, wanted in zip(candidates, iso.split(":")):
        current = page.locator("div").nth(wheel["index"])
        choice = current.get_by_text(re.compile(r"^0?" + str(int(wanted)) + r"$"), exact=True)
        box = current.bounding_box()
        if not box:
            raise BrowserBlocked("Coluna do horário saiu da tela")
        for _ in range(15):
            if choice.count() == 1:
                choice.click(timeout=4500)
                break
            numbers = current.inner_text().split()
            digits = [int(value) for value in numbers if value.isdecimal()]
            if not digits:
                raise BrowserBlocked("Sem valores na coluna do horário")
            direction = 1 if int(wanted) > max(digits) else -1
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.wheel(0, direction * round(box["height"] * 0.8))
            page.wait_for_timeout(200)
        else:
            raise BrowserBlocked(f"Valor do horário indisponível: {wanted}")
        page.wait_for_timeout(250)
    # Click a harmless visible field label to close the picker and commit its selection.
    label = page.get_by_text(re.compile(r"^(when to post|quando publicar)$", re.I), exact=True)
    if label.count() == 1:
        label.click()
    else:
        page.keyboard.press("Escape")
    return True


def scheduled_snapshot(page, dom, *, allow_unfiltered=False):
    dom.navigate(page, CONTENT_URL)
    if dom.selectors["scheduled_tab"]:
        tab = dom.wait_for(page, "scheduled_tab", 20)
        tab.click()
    elif not allow_unfiltered:
        raise BrowserBlocked("A lista de publicações agendadas precisa de um seletor")
    else:
        page.get_by_role("button", name=re.compile(r"^posts \(created on\)$", re.I)).wait_for(timeout=15000)
    # Let the content list settle before reading its first page.
    page.wait_for_timeout(1600)
    dom.guard(page)
    records = []
    for row in dom.rows(page):
        text = normalize(row.inner_text())
        links = row.locator("a[href]")
        urls = [urljoin(page.url, link.get_attribute("href") or "") for link in links.all()]
        url = next((link for link in urls if safe_url(link) and "/video/" in link), None)
        if not dom.selectors["scheduled_tab"] and not re.search(r"\b(scheduled|agendad[oa])\b", text):
            privacy = row.get_by_role("button", name=re.compile(
                r"^(everyone|friends|only you|todos|amigos|somente você)$", re.I
            ))
            if (not url or not (VISIBLE_TIMEZONE.search(text) or VISIBLE_SCHEDULE_DATE.search(text))
                    or not any(btn.is_disabled() for btn in privacy.all())):
                continue
            text += " " + STUDIO_SCHEDULE_MARKER
        if url:
            # The visible description is elided on this list, but its link exposes the full name.
            text += " " + normalize(links.aria_snapshot() if links.count() == 1 else row.aria_snapshot())
        records.append((text, url))
    return records


def matching_rows(records, job, locale):
    target = local_target(job)
    caption = normalize(job["caption"])
    if not caption:
        return []
    dates, hours = date_labels(target, locale), time_labels(target)
    am_pm = re.compile(r"(?<!\d)\d{1,2}:\d{2}\s*[ap]m\b")
    explicit_hours = {re.sub(r"\s+", "", value) for value in hours if value.endswith(("am", "pm"))}
    def has_time(text):
        explicit = am_pm.findall(text)
        if explicit:
            return any(re.sub(r"\s+", "", value) in explicit_hours for value in explicit)
        return contains_label(text, hours)
    def has_date(text):
        if contains_label(text, dates):
            return True
        offset = VISIBLE_TIMEZONE.search(text)
        if not offset and STUDIO_SCHEDULE_MARKER not in text:
            return False
        if offset:
            signed_hours, minutes = offset.groups()
            sign = -1 if signed_hours.startswith("-") else 1
            platform_offset = sign * (abs(int(signed_hours)) * 60 + int(minutes or 0))
            if platform_offset != int(target.utcoffset().total_seconds() / 60):
                return False
        name, portuguese = MONTHS[target.month - 1]
        day = target.day
        return contains_label(text, {f"{name[:3]} {day},", f"{name} {day},",
                                     f"{day} de {portuguese} ", f"{day} de {portuguese[:3]} "})
    return [(text, url) for text, url in records if caption in text
            and has_date(text) and has_time(text)]


def verify_new(page, dom, job, previous, settings):
    deadline = time.monotonic() + int(settings.get("verify_timeout_seconds", 75))
    locale = settings.get("locale", "en-US")
    before = Counter(matching_rows(previous, job, locale))
    while time.monotonic() < deadline:
        rows = scheduled_snapshot(page, dom, allow_unfiltered=settings.get("allow_unfiltered_content", False))
        matches = list((Counter(matching_rows(rows, job, locale)) - before).elements())
        if len(matches) == 1:
            record, url = matches[0]
            return "Linha nova na lista de agendados: " + record, url
        if len(matches) > 1:
            break
        page.wait_for_timeout(2500)
    raise BrowserBlocked("Sem confirmação inequívoca na lista de agendados")
