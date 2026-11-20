import html
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from browser_controller import open_browser
from database import Database
from main import digest_plan, run_jobs, upload, verify_new
from tiktok_scheduler import matching_rows
from video_scanner import fingerprint

UPLOAD_FORM = """
<input type="file" accept="video/*" onchange="document.querySelector('#ready').hidden=false">
<div id="ready" hidden>
  <p>Uploaded</p>
  <textarea aria-label="Caption"></textarea>
  <select aria-label="Who can watch"><option value="public">Everyone</option><option value="private">Only you</option></select>
  <label><input type="radio" name="mode" onclick="document.querySelector('#schedule').hidden=false">Schedule</label>
</div>
<div id="schedule" hidden>
  <input type="date"><input type="time">
  <button onclick="fetch('/fixture/schedule', {method:'POST', body:JSON.stringify({
     caption:document.querySelector('textarea').value,
     privacy:document.querySelector('select').value,
     date:document.querySelector('[type=date]').value,
     time:document.querySelector('[type=time]').value,
     file:document.querySelector('[type=file]').files[0].name,
  })})">Schedule</button>
</div>
"""


def plan_for(folder):
    clip = folder / "Vídeo de ação.mp4"
    clip.write_bytes(b"browser-only upload fixture")
    local = (datetime.now(ZoneInfo("America/Sao_Paulo")) + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    job = dict(hash=fingerprint(clip), path=str(clip), name=clip.name, size=clip.stat().st_size, caption="Teste #ação",
               privacy="private", utc=local.astimezone(timezone.utc).isoformat(), local=local.isoformat(),
               zone="America/Sao_Paulo")
    return {"mode": "new", "jobs": [job], "excluded": [],
            "config": {"timezone": "America/Sao_Paulo", "browser": {"verify_timeout_seconds": 2}}}


class BrowserTest(unittest.TestCase):
    def test_approved_preview_hash_changes_with_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = plan_for(Path(directory))
            before = digest_plan(plan)
            plan["jobs"][0]["caption"] += " diferente"
            self.assertNotEqual(digest_plan(plan), before)

    def test_matching_rejects_wrong_ampm(self):
        job = {"utc": "2026-11-22T05:00:00+00:00", "zone": "America/Sao_Paulo", "caption": "teste"}
        self.assertEqual(matching_rows([("teste 2026-11-22 02:00 pm", None)], job, "en-US"), [])
        self.assertTrue(matching_rows([("teste 2026-11-22 02:00 am", None)], job, "en-US"))

    def test_browser_end_to_end_with_native_fields_and_visible_row(self):
        self.exercise(confirm=True)

    def test_missing_confirmation_blocks_retry_and_keeps_evidence(self):
        self.exercise(confirm=False)

    def test_closed_upload_tab_retries_without_reopening_browser(self):
        self.exercise(confirm=True, close_at="upload")

    def test_closed_verification_tab_does_not_submit_again(self):
        self.exercise(confirm=True, close_at="verify")

    def test_timeout_after_submit_checks_existing_post_in_same_browser(self):
        self.exercise(confirm=True, close_at="after_submit")

    def exercise(self, confirm, close_at=None):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            plan = plan_for(state)
            sent = []
            starts = []
            closed = []

            @contextmanager
            def fixture_browser(folder, config):
                starts.append(folder)
                with open_browser(folder, config, headless=True) as (context, page):
                    def route(request):
                        url = request.request.url
                        if url.endswith("/tiktokstudio/upload"):
                            request.fulfill(content_type="text/html; charset=utf-8", body=UPLOAD_FORM)
                        elif url.endswith("/fixture/schedule") and request.request.method == "POST":
                            sent.append(json.loads(request.request.post_data))
                            request.fulfill(status=200, body="ok")
                        elif url.endswith("/tiktokstudio/content"):
                            row = ""
                            if confirm and sent:
                                payload = sent[-1]
                                values = " ".join(payload[key] for key in ("caption", "date", "time"))
                                row = '<tr><td>' + html.escape(values) + '</td><td><a href="/@demo/video/123">View</a></td></tr>'
                            request.fulfill(content_type="text/html; charset=utf-8", body=(
                                '<button role="tab">Scheduled</button><table><tbody>' + row + '</tbody></table>'
                            ))
                        else:
                            request.abort()
                    context.route("**/*", route)
                    yield context, page

            def close_once(function):
                def run(page, *args):
                    if not closed:
                        page.close()
                        closed.append(page)
                    return function(page, *args)
                return run

            db = Database(state / "queue.sqlite3")
            try:
                with ExitStack() as stack:
                    stack.enter_context(patch("main.open_browser", fixture_browser))
                    if close_at == "upload":
                        stack.enter_context(patch("main.upload", close_once(upload)))
                    if close_at == "verify":
                        stack.enter_context(patch("main.verify_new", close_once(verify_new)))
                    if close_at == "after_submit":
                        def timeout_after_click(page, *_):
                            page.wait_for_timeout(400)
                            closed.append(page)
                            raise PlaywrightTimeout("simulated wait after submission")
                        stack.enter_context(patch("main.confirm_pending_check", timeout_after_click))
                    run_jobs(state, db, plan)
                    with self.assertRaises(ValueError):
                        run_jobs(state, db, plan)
                self.assertEqual(len(sent), 1)
                self.assertEqual(len(starts), 1)
                if close_at:
                    self.assertEqual(len(closed), 1)
                self.assertEqual(sent[0]["file"], "Vídeo de ação.mp4")
                self.assertEqual(sent[0]["privacy"], "private")
                self.assertEqual(sent[0]["time"], "12:00")
                row = db.rows()[0]
                self.assertEqual(row["attempts"], 2 if close_at == "upload" else 1)
                if confirm:
                    self.assertEqual(row["status"], "scheduled")
                    self.assertIn("/video/123", row["remote_url"])
                else:
                    self.assertEqual(row["status"], "uncertain")
                    self.assertTrue(list((state / "errors").glob("*.png")))
            finally:
                db.close()
