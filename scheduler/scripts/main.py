from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from browser_controller import BrowserBlocked, StudioDOM, UPLOAD_URL, is_transient_browser_error, open_browser, reopen_tab
from database import Database
from reporter import show, to_csv
from schedule_builder import build_slots
from tiktok_scheduler import confirm_pending_check, matching_rows, prepare_schedule, scheduled_snapshot, verify_new
from tiktok_uploader import PRIVACY, upload
from video_scanner import fingerprint, scan

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
MAX_CAPTION = 2200


def serialize(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_plan(plan):
    return hashlib.sha256(serialize(plan).encode()).hexdigest()


def config_from(path):
    config = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("A configuração precisa ser um mapa YAML")
    if config.get("start_date") == "tomorrow":
        from zoneinfo import ZoneInfo
        config["start_date"] = (datetime.now(ZoneInfo(config["timezone"])).date() + timedelta(days=1)).isoformat()
    elif isinstance(config.get("start_date"), date):
        config["start_date"] = config["start_date"].isoformat()
    if config.get("privacy") not in PRIVACY:
        raise ValueError("privacy deve ser public, friends ou private")
    if not 15 <= int(config.get("lead_minutes", 20)) <= 1440:
        raise ValueError("lead_minutes deve estar entre 15 e 1440")
    if not 1 <= int(config.get("horizon_days", 10)) <= 10:
        raise ValueError("horizon_days deve estar entre 1 e 10")
    if not isinstance(config.get("browser", {}), dict):
        raise ValueError("browser deve ser um mapa")
    return config


def limit(config, requested):
    configured = int(config.get("max_per_run", 10))
    if configured < 1 or requested is not None and requested < 1:
        raise ValueError("O limite por execução precisa ser positivo")
    return min(configured, requested) if requested is not None else configured


def caption_for(config, path):
    overrides = config.get("captions", {})
    folder = Path(config["folder"]).expanduser().resolve()
    text = overrides.get(path.relative_to(folder).as_posix(), config.get("caption", "{stem}"))
    if not isinstance(text, str):
        raise ValueError(f"Legenda inválida: {path.name}")
    text = text.replace("{stem}", path.stem).strip()
    tags = " ".join("#" + str(tag).strip().lstrip("#") for tag in config.get("hashtags", []) if str(tag).strip())
    combined = " ".join(part for part in (text, tags) if part)
    if not combined or len(combined) > MAX_CAPTION:
        raise ValueError(f"Legenda vazia ou acima de {MAX_CAPTION} caracteres: {path.name}")
    return combined


def create_plan(config, db, requested, now):
    folder = Path(config["folder"]).expanduser().resolve()
    videos, rejected = scan(folder, recursive=bool(config.get("recursive", False)),
                            order=config.get("order", "name"), custom=config.get("custom_order", []),
                            ffprobe=config.get("ffprobe"))
    rows = db.rows()
    existing = {job["hash"] for job in rows}
    for video in videos:
        if video.digest in existing:
            rejected.append({"file": str(video.path), "reason": "já registrado na fila"})
    videos = [video for video in videos if video.digest not in existing]
    reserved = {job["utc"] for job in rows if job["status"] not in {"cancelled", "expired"}}
    slots = build_slots(config, now, min(len(videos), limit(config, requested)), reserved)
    jobs = []
    for video, slot in zip(videos, slots):
        jobs.append(dict(name=video.path.name, path=str(video.path), hash=video.digest, size=video.size,
                         duration=video.duration, zone=config["timezone"],
                         local=slot.isoformat(), utc=slot.astimezone(UTC).isoformat(),
                         caption=caption_for(config, video.path), privacy=config["privacy"]))
    rejected.extend({"file": str(video.path), "reason": "sem vaga nesta execução"}
                    for video in videos[len(jobs):])
    return {"mode": "new", "jobs": jobs, "excluded": rejected, "config": config}


@contextmanager
def worker_lock(state):
    with (state / "worker.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Já existe uma execução usando este perfil") from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def current_window(job, config):
    delta = datetime.fromisoformat(job["utc"]) - datetime.now(UTC)
    return timedelta(minutes=15) <= delta <= timedelta(days=int(config.get("horizon_days", 10)))


def persist_preview(state, plan):
    token = digest_plan(plan)
    path = state / f"plan-{token}.json"
    path.write_text(serialize(plan), encoding="utf-8")
    print(f"Prévia · {len(plan['jobs'])} vídeo(s)")
    for index, job in enumerate(plan["jobs"], 1):
        size = f"{job['duration']:.1f}s" if job.get("duration") else "duração não verificada"
        print(f"{index:2}  {job['local']} [{job['zone']}] {job['name']} ({size})")
        print(f"    {job['privacy']} | {job['caption']}")
    for row in plan.get("excluded", []):
        print(f"Fora do lote: {row['file']} | {row['reason']}")
    print(f"Hash da prévia, para aprovar depois de revisar: {token}")
    return token


def screenshots(state, page, job, stage):
    try:
        folder = state / "errors"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        page.screenshot(path=str(folder / f"{stamp}-{job['hash'][:12]}-{stage}.png"), timeout=10000)
    except Exception as exc:
        logging.warning("Captura indisponível: %s", exc)


def run_jobs(state, db, plan):
    config = plan["config"]
    settings = config.get("browser", {})
    jobs = plan["jobs"]
    if not jobs:
        raise ValueError("O plano não contém vídeos")
    now_jobs = {job["hash"]: job for job in db.rows()}
    if plan["mode"] == "new":
        if any(job["hash"] in now_jobs for job in jobs):
            raise ValueError("Plano já utilizado ou houve alteração na fila; gere nova prévia")
        db.add(jobs)
    elif plan["mode"] == "reschedule":
        db.reschedule(jobs)
    else:
        for job in jobs:
            old = now_jobs.get(job["hash"])
            if old is None or any(old[key] != job[key] for key in ("utc", "caption", "status", "attempts")):
                raise ValueError("Fila mudou desde a prévia de retomada")
    dom = StudioDOM(settings)
    with open_browser(state, config) as (context, page):
        verify = context.new_page()
        for job in jobs:
            status = job["status"] if plan["mode"] == "resume" else "pending"
            if not current_window(job, config):
                db.change(job["hash"], status, "expired", error="Horário fora da janela do Studio; gere um novo horário")
                continue
            for attempt in range(2):
                try:
                    path = Path(job["path"])
                    if path.stat().st_size != job["size"] or fingerprint(path) != job["hash"]:
                        raise ValueError("Arquivo diferente do que foi aprovado")
                    if verify.is_closed():
                        verify = context.new_page()
                    baseline = scheduled_snapshot(verify, dom, allow_unfiltered=settings.get("allow_unfiltered_content", False))
                    if matching_rows(baseline, job, settings.get("locale", "en-US")):
                        db.change(job["hash"], status, "uncertain", error="Já existe uma linha semelhante no Studio; revise antes de repetir")
                        return
                    db.change(job["hash"], status, "uploading")
                    status = "uploading"
                    if page.is_closed():
                        page = context.new_page()
                    page.bring_to_front()
                    upload(page, dom, job, settings)
                    button = prepare_schedule(page, dom, job, settings)
                    if not current_window(job, config):
                        raise BrowserBlocked("Horário deixou de ser agendável durante o upload")
                    # Recovery after submission can only read the Studio list.
                    db.change(job["hash"], status, "submitting")
                    status = "submitting"
                    try:
                        button.click(timeout=12000)
                        confirm_pending_check(page, dom, job, settings)
                        if not page.is_closed():
                            dom.guard(page)
                    except Exception as exc:
                        if not is_transient_browser_error(exc):
                            raise
                        logging.warning("O envio pode ter sido aceito; conferindo sem clicar novamente: %s", exc)
                    try:
                        if verify.is_closed():
                            verify = context.new_page()
                        evidence, url = verify_new(verify, dom, job, baseline, settings)
                    except Exception as exc:
                        if not is_transient_browser_error(exc):
                            raise
                        verify = reopen_tab(context, verify)
                        evidence, url = verify_new(verify, dom, job, baseline, settings)
                    db.change(job["hash"], status, "scheduled", evidence=evidence, url=url)
                    print(f"Agendado e verificado: {job['local']} | {job['name']}", flush=True)
                    logging.info("Agendamento verificado: %s -> %s", job["hash"], job["utc"])
                    break
                except Exception as exc:
                    error = str(exc)[:2000]
                    if attempt == 0 and status != "submitting" and is_transient_browser_error(exc):
                        try:
                            page = reopen_tab(context, page)
                            if verify.is_closed():
                                verify = context.new_page()
                        except Exception as recovery:
                            logging.warning("A janela do Chromium ficou indisponível: %s", recovery)
                        else:
                            db.change(job["hash"], status, "pending", error=error)
                            status = "pending"
                            logging.info("Retomando no mesmo navegador após falha transitória: %s", error)
                            print(f"Recuperando a aba do Studio uma vez: {job['name']}", flush=True)
                            continue
                    target = "uncertain" if status == "submitting" else "error"
                    db.change(job["hash"], status, target, error=error)
                    screenshots(state, page, job, "upload")
                    screenshots(state, verify, job, "lista")
                    logging.exception("Falha %s em %s", target, job["name"])
                    print(f"{target}: {job['name']} | {error}", flush=True)
                    return


def reconcile(state, db, config, digest=None):
    settings = config.get("browser", {})
    dom = StudioDOM(settings)
    pending = [row for row in db.rows(("uncertain",)) if digest is None or row["hash"] == digest]
    if not pending:
        return
    with open_browser(state, config) as (_, page):
        rows = scheduled_snapshot(page, dom, allow_unfiltered=settings.get("allow_unfiltered_content", False))
        for job in pending:
            matches = matching_rows(rows, job, settings.get("locale", "en-US"))
            if len(matches) == 1:
                # If an identical row was already present before upload, leave it for human review.
                if (job.get("error") or "").startswith("Já existe uma linha semelhante"):
                    continue
                text, url = matches[0]
                db.change(job["hash"], "uncertain", "scheduled", evidence="Reconciliado na lista: " + text, url=url)


def main():
    parser = argparse.ArgumentParser(description="Fila local e agendamento verificado no TikTok Studio")
    parser.add_argument("--state", type=Path, default=ROOT / "state")
    cmd = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "resume-plan", "login", "reconcile"):
        sub = cmd.add_parser(name)
        sub.add_argument("--config", type=Path, required=True)
        if name in {"plan", "resume-plan"}:
            sub.add_argument("--limit", type=int)
            if name == "resume-plan":
                sub.add_argument("--new-times", action="store_true", help="Planeja novos horários para itens vencidos")
        elif name == "reconcile":
            sub.add_argument("--hash")
    sub = cmd.add_parser("run")
    sub.add_argument("--approve", required=True)
    sub = cmd.add_parser("report")
    sub.add_argument("--csv", type=Path)
    cmd.add_parser("pending")
    args = parser.parse_args()
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=state / "scheduler.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    db = Database(state / "queue.sqlite3")
    try:
        name = args.command
        if name in {"report", "pending"}:
            rows = db.rows(None if name == "report" else ("pending", "error", "expired", "uncertain", "uploading", "submitting"))
            show(rows)
            if name == "report" and args.csv:
                to_csv(args.csv, rows)
            return 0
        if name == "plan":
            persist_preview(state, create_plan(config_from(args.config), db, args.limit, datetime.now(UTC)))
            return 0
        with worker_lock(state):
            db.recover()
            if name == "run":
                token = args.approve
                if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
                    raise ValueError("Hash de aprovação inválido")
                plan = json.loads((state / f"plan-{token}.json").read_text(encoding="utf-8"))
                if token != digest_plan(plan):
                    raise ValueError("A prévia mudou; gere outra e obtenha aprovação novamente")
                run_jobs(state, db, plan)
                results = [row for row in db.rows() if row["hash"] in {job["hash"] for job in plan["jobs"]}]
                show(results)
                return int(any(row["status"] != "scheduled" for row in results))
            config = config_from(args.config)
            if name == "resume-plan":
                candidates = db.rows(("pending", "error", "expired"))
                compatible = [row for row in candidates if row["zone"] == config["timezone"]]
                if args.new_times:
                    eligible = [row for row in compatible if row["status"] == "expired"
                                or not current_window(row, config)]
                    reschedule = eligible[:limit(config, args.limit)]
                    old = {row["hash"] for row in reschedule}
                    reserved = {row["utc"] for row in db.rows()
                                if row["hash"] not in old and row["status"] not in {"cancelled", "expired"}}
                    slots = build_slots(config, datetime.now(UTC), len(reschedule), reserved)
                    chosen = [dict(row, previous_utc=row["utc"], local=slot.isoformat(),
                                   utc=slot.astimezone(UTC).isoformat()) for row, slot in zip(reschedule, slots)]
                else:
                    eligible = [row for row in compatible if row["status"] != "expired" and current_window(row, config)]
                    chosen = eligible[:limit(config, args.limit)]
                chosen_hashes = {row["hash"] for row in chosen}
                excluded = [{"file": row["path"], "reason": "fora da janela, fuso diferente ou limite atingido"}
                            for row in candidates if row["hash"] not in chosen_hashes]
                mode = "reschedule" if args.new_times else "resume"
                persist_preview(state, {"mode": mode, "config": config, "jobs": chosen, "excluded": excluded})
            elif name == "login":
                with open_browser(state, config) as (_, page):
                    dom = StudioDOM(config.get("browser", {}))
                    print("Entre na conta no Chromium. O navegador fecha quando o Studio estiver pronto.", flush=True)
                    dom.navigate(page, UPLOAD_URL)
                    deadline = time.monotonic() + dom.intervention
                    while time.monotonic() < deadline:
                        dom.guard(page)
                        if "/tiktokstudio/upload" in page.url and dom.locate(page, "select_file"):
                            print("Sessão do Studio detectada no perfil local.", flush=True)
                            break
                        page.wait_for_timeout(500)
                    else:
                        raise BrowserBlocked("Tela de upload indisponível depois do login")
            elif name == "reconcile":
                reconcile(state, db, config, args.hash)
                show(db.rows())
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("Execução interrompida. A fila foi preservada.", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        logging.exception("Execução bloqueada")
        print(f"Erro: {error}", file=sys.stderr)
        sys.exit(1)
