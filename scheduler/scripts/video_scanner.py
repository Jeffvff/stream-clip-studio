from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

EXTENSIONS = {".mp4", ".mov", ".webm"}


@dataclass(frozen=True)
class Video:
    path: Path
    digest: str
    size: int
    duration: float | None


def fingerprint(path: Path) -> str:
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("arquivo mudou durante a leitura")
    return digest


def scan(folder: Path, *, recursive: bool, order: str, custom: list[str],
         ffprobe: str | None) -> tuple[list[Video], list[dict]]:
    if not folder.is_dir():
        raise ValueError(f"Pasta inexistente: {folder}")
    items = folder.rglob("*") if recursive else folder.iterdir()
    paths = [path for path in items if path.is_file() and path.suffix.lower() in EXTENSIONS]
    names = {path: path.relative_to(folder).as_posix().casefold() for path in paths}
    if order == "creation":
        def creation_key(path):
            status = path.stat()
            return getattr(status, "st_birthtime_ns", status.st_ctime_ns), names[path]

        paths.sort(key=creation_key)
    elif order == "custom":
        ranks = {name.replace("\\", "/").casefold(): index for index, name in enumerate(custom)}
        if len(ranks) != len(custom) or set(ranks) != set(names.values()):
            raise ValueError("custom_order deve listar uma vez cada arquivo encontrado, com caminho relativo")
        paths.sort(key=lambda path: ranks[names[path]])
    elif order == "name":
        paths.sort(key=names.__getitem__)
    else:
        raise ValueError(f"Ordem desconhecida: {order}")
    probe = shutil.which(ffprobe or "ffprobe")
    if ffprobe and not probe:
        raise ValueError(f"FFprobe não encontrado: {ffprobe}")
    found, rejected = [], []
    seen = set()
    for path in paths:
        try:
            size = path.stat().st_size
            if size <= 0:
                raise ValueError("arquivo vazio")
            duration = inspect(path, probe) if probe else None
            digest = fingerprint(path)
            if digest in seen:
                raise ValueError("conteúdo idêntico a outro arquivo da pasta")
            seen.add(digest)
            found.append(Video(path.resolve(), digest, size, duration))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            rejected.append({"file": str(path), "reason": str(exc)})
    return found, rejected


def inspect(path: Path, probe: str) -> float:
    result = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    payload = json.loads(result.stdout)
    if not payload.get("streams"):
        raise ValueError("sem faixa de vídeo legível")
    try:
        duration = float(payload["format"]["duration"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("duração inválida") from exc
    if not 0 < duration < float("inf"):
        raise ValueError("duração inválida")
    return duration
