from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ENGINE_REPO = "https://github.com/iyansr/gaming-clip.git"
ENGINE_COMMIT = "87d5ea4e7e13fdfb0f57c764e6db46d16a31641a"
BUN_VERSION = "1.4.5"
FFMPEG_RELEASE = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download"
FFMPEG_ARCHIVE = "ffmpeg-master-latest-win64-gpl.zip"


def locate_bun(engine: Path, state: Path) -> Path:
    path_bun = shutil.which("bun")
    candidates = [engine / ".local-runtime/node_modules/bun/bin/bun.exe", state / "bun/bun.exe"]
    if path_bun:
        candidates.append(Path(path_bun))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Bun indisponível. Rode setup.ps1 install-engine.")


def engine_tool(engine: Path, name: str) -> Path:
    if name in {"ffmpeg", "ffprobe"}:
        candidates = [engine / directory / f"{name}.exe" for directory in
                      ("tools/ffmpeg-full/bin", "tools/ffmpeg/bin")]
    else:
        candidates = [engine / "tools" / f"{name}.exe"]
    system = shutil.which(name)
    if system:
        candidates.append(Path(system))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{name} indisponível. Rode setup.ps1 install-engine.")


def download_file(url: str, output: Path):
    print(f"Baixando {output.name}...", flush=True)
    with urllib.request.urlopen(url, timeout=60) as response, output.open("wb") as destination:
        shutil.copyfileobj(response, destination)


def zip_files(archive: Path, destination: Path, names: set[str]):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for name in names:
            paths = [entry for entry in package.namelist() if entry.rsplit("/", 1)[-1] == name]
            if len(paths) != 1:
                raise ValueError(f"Arquivo de distribuição não contém {name} de forma inequívoca")
            with package.open(paths[0]) as src, (destination / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)


def install_engine(state: Path, existing: Path | None = None) -> Path:
    if existing is not None:
        engine = existing.resolve(strict=True)
        if not (engine / "src/cli.ts").is_file():
            raise ValueError("A pasta fornecida não é uma instalação do Gaming Clip")
        locate_bun(engine, state)
        for name in ("ffmpeg", "ffprobe", "yt-dlp"):
            engine_tool(engine, name)
        return engine
    git = shutil.which("git")
    if not git:
        raise FileNotFoundError("Instale o Git para Windows antes de baixar o Gaming Clip")
    engine = state / "engine"
    if not (engine / ".git").is_dir():
        subprocess.run([git, "clone", "--no-checkout", ENGINE_REPO, str(engine)], check=True)
    subprocess.run([git, "checkout", "--detach", ENGINE_COMMIT], cwd=engine, check=True)
    with tempfile.TemporaryDirectory(dir=state) as scratch:
        work = Path(scratch)
        try:
            bun = locate_bun(engine, state)
        except FileNotFoundError:
            archive = work / "bun-windows-x64.zip"
            download_file(f"https://github.com/oven-sh/bun/releases/download/bun-v{BUN_VERSION}/{archive.name}", archive)
            zip_files(archive, state / "bun", {"bun.exe"})
            bun = locate_bun(engine, state)
        ffmpeg_dir = engine / "tools/ffmpeg/bin"
        if not (ffmpeg_dir / "ffmpeg.exe").is_file() or not (ffmpeg_dir / "ffprobe.exe").is_file():
            archive = work / FFMPEG_ARCHIVE
            sums = work / "checksums.sha256"
            download_file(f"{FFMPEG_RELEASE}/{sums.name}", sums)
            checksum = next(line.split()[0] for line in sums.read_text().splitlines()
                            if line.split() and line.split()[-1].lstrip("*") == archive.name)
            download_file(f"{FFMPEG_RELEASE}/{archive.name}", archive)
            with archive.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual.lower() != checksum.lower():
                raise ValueError("Checksum do FFmpeg não confere")
            zip_files(archive, ffmpeg_dir, {"ffmpeg.exe", "ffprobe.exe"})
    subprocess.run([str(bun), "install", "--frozen-lockfile"], cwd=engine, check=True)
    subprocess.run([str(bun), "run", "setup"], cwd=engine, check=True)
    subprocess.run([str(bun), "src/cli.ts", "doctor"], cwd=engine, check=True)
    return engine
