"""Background music: the asset pack's own audio, or a commercial-use track the agent downloads.

Downloads are registered with `fetch-bgm` (license evidence kept in config/bgm/); `package` accepts
only those two origins and spreads the batch over every available track, one track serving at most
eight videos.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from montage.audio import pcm
from montage.common import probe, read, sha, write

SCHEMA = "video-montage-bgm-licenses/v1"
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
MIN_SECONDS = 20.0  # the track loops, but a few seconds would repeat audibly
MAX_BYTES = 100 * 1024 * 1024
MIN_RMS_DBFS = -45.0
TIMEOUT_SECONDS = 60
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# Only licenses that allow commercial use with no attribution, share-alike or no-derivatives term:
# the finished video has nowhere to credit a creator and is itself sold or monetized.
LICENSES = {"cc0": "CC0 1.0 Universal", "public-domain": "Public Domain (PDM 1.0 / no known copyright)",
            "pixabay": "Pixabay Content License", "mixkit": "Mixkit License"}
ALIASES = {"cc0": "cc0", "cc01": "cc0", "cc010": "cc0", "cczero": "cc0",
           "publicdomain": "public-domain", "pd": "public-domain", "pdm": "public-domain", "pdm10": "public-domain",
           "pixabay": "pixabay", "pixabaylicense": "pixabay", "pixabaycontentlicense": "pixabay",
           "mixkit": "mixkit", "mixkitlicense": "mixkit"}
MAX_VIDEOS_PER_TRACK = 8  # one track serves at most eight videos of a batch
FORMAT_SUFFIX = {"mp3": ".mp3", "wav": ".wav", "flac": ".flac", "ogg": ".ogg", "aac": ".aac", "mov": ".m4a"}


def license_id(value: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(value).lower())
    if key not in ALIASES:
        raise ValueError(f"license {value!r} is not accepted for commercial delivery; accepted: "
                         + ", ".join(f"{name} ({label})" for name, label in LICENSES.items())
                         + ". CC BY, BY-SA, NC, ND, 'free for personal use' and unknown terms are rejected")
    return ALIASES[key]


def registry_path(job: Path) -> Path:
    return Path(job) / "config" / "bgm" / "bgm_licenses.json"


def load_registry(job: Path) -> list[dict]:
    path = registry_path(job)
    if not path.is_file():
        return []
    document = read(path)
    if document.get("schema") != SCHEMA:
        raise ValueError("unknown BGM license registry")
    return document["tracks"]


def asset_audio(asset_root: Path) -> list[dict]:
    rows = []
    for path in sorted(Path(asset_root).rglob("*")):
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
            rows.append({"path": str(path.resolve()), "sha256": sha(path), "duration_seconds": duration(path)})
    return rows


def duration(path: Path) -> float:
    info = probe(path)
    if not any(row["codec_type"] == "audio" for row in info["streams"]):
        raise ValueError(f"no audio stream: {path}")
    return round(float(info["format"]["duration"]), 3)


def max_uses(outputs: int) -> int:
    """Most videos one track may serve."""
    return max(1, min(outputs, MAX_VIDEOS_PER_TRACK))


def tracks_needed(outputs: int) -> int:
    return max(1, math.ceil(outputs / MAX_VIDEOS_PER_TRACK))


def require_distribution(shas: list[str], waiver) -> None:
    """Reject a batch where one BGM serves more than eight videos, unless the user accepted it."""
    if waiver or not shas:
        return
    worst = max(shas.count(item) for item in set(shas))
    if worst > MAX_VIDEOS_PER_TRACK:
        raise ValueError(f"BGM repetition: one track serves {worst} of {len(shas)} videos (limit {MAX_VIDEOS_PER_TRACK}); "
                         f"use at least {tracks_needed(len(shas))} different tracks: download the missing ones with `fetch-bgm` "
                         "(several different sources) instead of repeating a track. Only the user's own yes "
                         "(`decide --key bgm_repetition`) allows more than eight videos per track")


def available(job: Path, asset_root: Path) -> list[dict]:
    """Every usable track: pack audio first, then registered downloads."""
    rows, seen = [], set()
    for row in [*asset_audio(asset_root), *load_registry(job)]:
        if row["sha256"] not in seen:
            seen.add(row["sha256"])
            rows.append(row)
    return rows


def status(job: Path, asset_root: Path, outputs: int = 1) -> dict:
    pack = asset_audio(asset_root)
    downloads = load_registry(job)
    have = len({row["sha256"] for row in pack} | {row["sha256"] for row in downloads})
    need = tracks_needed(outputs)
    # Whatever the pack lacks for the eight-videos-per-track limit, the agent downloads itself.
    return {"asset_audio": pack, "licensed_downloads": downloads, "download_required": have < need,
            "tracks_available": have, "tracks_needed": need, "max_videos_per_track": max_uses(outputs),
            "accepted_licenses": LICENSES}


def download(url: str, target: Path, referer: str | None = None) -> None:
    """Browser-like request (some music CDNs refuse bare clients); the system curl retries when Python's TLS
    store rejects a certificate chain the Windows store accepts."""
    if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("download URL must be http(s)")
    headers = {"User-Agent": USER_AGENT, "Accept": "audio/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    try:
        request = urllib.request.Request(url, headers=headers)
        size = 0
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, target.open("wb") as stream:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_BYTES:
                    raise ValueError(f"download exceeds {MAX_BYTES // 1024 // 1024} MB")
                stream.write(block)
        return
    except (urllib.error.URLError, OSError) as error:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise ValueError(f"download failed: {error}") from error
    command = [curl, "-fsSL", "--max-time", str(TIMEOUT_SECONDS * 3), "--max-filesize", str(MAX_BYTES),
               "-A", USER_AGENT, "-o", str(target), url]
    if referer:
        command[1:1] = ["-e", referer]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise ValueError(f"download failed: {result.stderr.strip()[-300:] or f'curl exit {result.returncode}'}")


def acquire(job: Path, asset_root: Path, *, outputs: int = 1, url: str | None, file: Path | None, license: str, license_url: str,
            license_evidence: str, source_page: str, title: str, creator: str, instrumental: bool,
            asset_audio_unsuitable: str | None = None) -> dict:
    """Download (or take a local copy of) one BGM, check it and record its license evidence."""
    if bool(url) == bool(file):
        raise ValueError("give exactly one of --url (direct audio link) or --file (already downloaded)")
    pack = asset_audio(asset_root)
    pack_tracks = len({row["sha256"] for row in pack})
    if pack_tracks >= tracks_needed(outputs) and not (asset_audio_unsuitable or "").strip():
        raise ValueError(f"the asset pack already has {pack_tracks} audio track(s), enough for {outputs} video(s) at "
                         f"{MAX_VIDEOS_PER_TRACK} per track; use them as the BGM, or state in --asset-audio-unsuitable why none "
                         f"of it can be (found: {[row['path'] for row in pack]})")
    chosen = license_id(license)
    for label, value in (("--license-url", license_url), ("--source-page", source_page)):
        if urllib.parse.urlparse(value or "").scheme not in {"http", "https"}:
            raise ValueError(f"{label} must be the http(s) page that states the license")
    if len((license_evidence or "").strip()) < 8:
        raise ValueError("--license-evidence must quote the license statement seen on the source page")
    if not (title or "").strip() or not (creator or "").strip():
        raise ValueError("--title and --creator are required")
    if not instrumental:
        raise ValueError("BGM must be instrumental (no vocals under the speech); pass --instrumental after checking")

    folder = registry_path(job).parent
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / "download.partial"
    try:
        if url:
            download(url, partial, referer=source_page)
        else:
            partial.write_bytes(Path(file).read_bytes())
        info = probe(partial)
        if not any(row["codec_type"] == "audio" for row in info["streams"]):
            raise ValueError("downloaded file has no audio stream (a web page or an error response?)")
        seconds = round(float(info["format"]["duration"]), 3)
        if seconds < MIN_SECONDS:
            raise ValueError(f"BGM is {seconds:.1f} s; at least {MIN_SECONDS:.0f} s required")
        samples = pcm(partial)
        rms = 20 * math.log10(max(float(math.sqrt((samples * samples).mean())), 1e-8))
        if rms < MIN_RMS_DBFS:
            raise ValueError(f"BGM is nearly silent ({rms:.1f} dBFS RMS)")
        digest = sha(partial)
        suffix = FORMAT_SUFFIX.get(info["format"]["format_name"].split(",")[0], ".audio")
        target = folder / f"{digest[:12]}{suffix}"
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)

    entry = {"path": str(target.resolve()), "sha256": digest, "duration_seconds": seconds, "rms_dbfs": round(rms, 2),
             "title": title.strip(), "creator": creator.strip(), "license": chosen, "license_name": LICENSES[chosen],
             "license_url": license_url, "license_evidence": license_evidence.strip(), "source_page": source_page,
             "download_url": url, "instrumental": True, "asset_audio_unsuitable": (asset_audio_unsuitable or "").strip() or None,
             "downloaded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")}
    tracks = [row for row in load_registry(job) if row["sha256"] != digest] + [entry]
    write(registry_path(job), {"schema": SCHEMA, "tracks": tracks})
    return entry


def require_authorized(job: Path, asset_root: Path, music: dict) -> dict:
    """Return the origin of a packaging BGM, or raise when it is neither pack audio nor a licensed download."""
    path = Path(music["path"]).resolve()
    if path.suffix.lower() in AUDIO_SUFFIXES and path.is_relative_to(Path(asset_root).resolve()):
        return {"origin": "asset_pack", "path": str(path), "sha256": music["sha256"]}
    for row in load_registry(job):
        if row["sha256"] == music["sha256"] and Path(row["path"]).resolve() == path:
            license_id(row["license"])
            return {"origin": "licensed_download", "path": str(path), "sha256": music["sha256"],
                    "license": row["license"], "source_page": row["source_page"]}
    raise ValueError(f"BGM {path} is neither audio from the asset pack nor registered with fetch-bgm "
                     "(download a commercial-use track and register it with its license evidence)")
