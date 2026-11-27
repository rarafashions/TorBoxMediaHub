"""Local Roxxy downloader worker (yt-dlp / aria2c).

Runs only user-selected Roxxy streams. Magnet streams remain handled by TorBox.
Queue is process-local in this first Phase C increment; V1.2 database is untouched.
"""
from __future__ import annotations

import os
import re
import json
import time
import shutil
import threading
import subprocess
import uuid
from datetime import datetime
from urllib.parse import urlparse, unquote, quote
from email.message import Message
from typing import Any, Dict, List, Optional

import requests
from flask import Blueprint, jsonify, request
import database_manager as db
import classification_engine
import subtitle_engine

localdl_bp = Blueprint("roxxy_localdl", __name__)
_lock = threading.RLock()
_jobs: Dict[str, Dict[str, Any]] = {}
_workers = threading.Semaphore(3)
_library_worker_started = False
_library_worker_lock = threading.Lock()
_registration_worker_started = False
DEFAULT_PATH = r"E:\01 Imaulana\01 Isa Maulana\03 Ionos\Movies"
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m4v", ".mpg", ".mpeg"}
CONTENT_TYPE_EXTS = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}
YTDLP_FRAGMENT_CONCURRENCY = 4
ARIA2C_MAX_CONNECTIONS = 4
ARIA2C_MIN_SPLIT_SIZE = "1M"
ARIA2C_MAX_TRIES = 3
ARIA2C_RETRY_WAIT = 2
RETRY_LIMIT = 3
# The download drawer keeps a history of finished jobs, but the in-memory map
# must not grow for the whole lifetime of the server process.
LOCAL_JOB_HISTORY_MAX = 50
_FINISHED_JOB_STATUSES = {"completed", "failed"}
_LIBRARY_PENDING_STATUSES = {"queued", "validating", "identified", "registering"}
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "TorBoxMediaHub-Roxxy/1.3", "Accept": "*/*"})

def _safe_filename(value: str) -> str:
    value = unquote(str(value or "")).replace("\x00", "")
    value = re.sub(r'[<>:"/\\\\|?*]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:180] or "VideoFeeds_Download"

def _fallback_name(payload: Dict[str, Any], ext: str = "") -> str:
    meta = payload.get("meta") or {}
    code = str(meta.get("jav_code") or payload.get("jav_code") or "").strip().upper()
    title = str(meta.get("name") or payload.get("title") or "Video Feeds").strip()
    resolution = str(payload.get("resolution") or "")
    if not resolution:
        text = " ".join(str(payload.get(k) or "") for k in ("name", "title", "description"))
        m = re.search(r"\b(2160p|1080p|720p|480p)\b", text, re.I)
        resolution = m.group(1) if m else ""
    base = " - ".join(x for x in (code, title, resolution) if x)
    return _safe_filename(base or "VideoFeeds_Download") + ext

def _content_disposition_name(value: str) -> str:
    if not value:
        return ""
    msg = Message()
    msg["content-disposition"] = value
    try:
        return msg.get_filename() or ""
    except Exception:
        return ""

def _validate_http_url(url: str) -> str:
    p = urlparse(str(url or "").strip())
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise ValueError("URL harus berupa HTTP/HTTPS.")
    if p.username or p.password:
        raise ValueError("URL dengan kredensial tertanam tidak didukung.")
    return p.geturl()

def _download_denied_roots() -> List[str]:
    """Directories a download must never write into: OS trees, shell autostart
    folders, and the application's own source directory."""
    candidates: List[str] = [
        os.environ.get("SystemRoot") or "",
        os.environ.get("windir") or "",
        os.environ.get("ProgramFiles") or "",
        os.environ.get("ProgramFiles(x86)") or "",
        os.environ.get("ProgramData") or "",
    ]
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        candidates.append(os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"))
    if os.name == "nt":
        candidates.append(r"C:\Windows")
    else:
        candidates.extend(["/etc", "/bin", "/sbin", "/usr", "/boot", "/var/lib", "/var/spool"])
    candidates.append(os.path.dirname(os.path.abspath(__file__)))

    denied = set()
    for raw in candidates:
        if not raw:
            continue
        normalized = os.path.normcase(os.path.realpath(raw)).rstrip("\\/")
        if normalized:
            denied.add(normalized)
    return sorted(denied)


def _safe_download_dir(raw: Any) -> str:
    """Validate a client-supplied download directory and return its real path.

    The GUI lets the user browse to any folder, so downloads cannot be limited to
    one allowlisted root. Instead this rejects the locations that would turn an
    arbitrary write into code execution: the OS trees, autostart folders, and the
    app's own source directory. Device-namespace prefixes are also refused.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Folder tujuan belum diisi.")
    if text.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        raise ValueError("Folder tujuan tidak valid.")
    if not os.path.isabs(text):
        raise ValueError("Folder tujuan harus berupa path absolut.")

    real = os.path.realpath(text)
    normalized = os.path.normcase(real).rstrip("\\/")
    for denied in _download_denied_roots():
        if normalized == denied or normalized.startswith(denied + os.sep):
            raise ValueError("Folder tujuan berada di lokasi sistem yang tidak diizinkan.")

    parent = os.path.dirname(real) or real
    if not os.path.isdir(real) and not os.path.isdir(parent):
        raise ValueError("Folder tujuan tidak ditemukan.")
    return real


def _inspect(url: str) -> Dict[str, Any]:
    url = _validate_http_url(url)
    try:
        r = HTTP.head(url, allow_redirects=False, timeout=(8, 15))
        if r.status_code in (405, 501):
            r.close()
            r = HTTP.get(url, headers={"Range": "bytes=0-0"}, allow_redirects=False, stream=True, timeout=(8, 15))
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].lower()
        cd_name = _content_disposition_name(r.headers.get("Content-Disposition", ""))
        length = r.headers.get("Content-Length")
        location = r.headers.get("Location")
        status = r.status_code
        r.close()
        if 300 <= status < 400 and location:
            from urllib.parse import urljoin
            target = _validate_http_url(urljoin(url, location))
            # Inspect one redirect hop only; never recursively follow unknown redirects.
            r2 = HTTP.head(target, allow_redirects=False, timeout=(8, 15))
            ctype = (r2.headers.get("Content-Type") or "").split(";")[0].lower()
            cd_name = _content_disposition_name(r2.headers.get("Content-Disposition", "")) or cd_name
            length = r2.headers.get("Content-Length") or length
            status = r2.status_code
            r2.close()
            url = target
        path = urlparse(url).path.lower()
        ext = os.path.splitext(path)[1]
        name_ext = os.path.splitext(cd_name)[1].lower()
        chosen_ext = name_ext or ext
        if chosen_ext in {".m3u8", ".mpd"} or ctype in {"application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml"}:
            engine, kind = "yt-dlp", "manifest"
        elif chosen_ext == ".mkv" or "matroska" in ctype or "hevc" in cd_name.lower() or "h265" in cd_name.lower():
            engine, kind = "aria2c", "file"
        elif chosen_ext == ".mp4" or ctype in {"video/mp4", "video/webm", "video/quicktime"}:
            # Direct media files are downloaded with aria2c multi-connection;
            # yt-dlp remains the fallback and the only HLS/DASH handler.
            engine, kind = "aria2c", "file"
        elif ctype.startswith("video/") or ctype in {"application/octet-stream", "binary/octet-stream"} or chosen_ext in VIDEO_EXTS:
            engine, kind = "aria2c", "file"
        else:
            engine, kind = "manual", "unknown"
        return {"engine": engine, "kind": kind, "content_type": ctype, "filename": cd_name,
                "extension": chosen_ext, "content_length": int(length) if str(length or "").isdigit() else None,
                "http_status": status, "url": url}
    except requests.RequestException as exc:
        raise RuntimeError("Pemeriksaan URL gagal. Periksa akses provider atau URL yang kedaluwarsa.") from exc

def _set(job_id: str, **changes: Any) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(changes)
            _jobs[job_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _prune_job_history_locked(keep: int = LOCAL_JOB_HISTORY_MAX) -> int:
    """Drop the oldest finished jobs so the drawer history stays bounded.

    The caller must hold ``_lock``. Active downloads and jobs whose Library task
    is still queued/validating/registering are never removed: the library worker
    and the UI still update those rows.
    """
    finished = [
        (job_id, job)
        for job_id, job in _jobs.items()
        if job.get("status") in _FINISHED_JOB_STATUSES
        and str(job.get("library_status") or "") not in _LIBRARY_PENDING_STATUSES
    ]
    excess = len(finished) - max(0, int(keep))
    if excess <= 0:
        return 0
    finished.sort(key=lambda pair: str(pair[1].get("created_at") or ""))
    for job_id, _job in finished[:excess]:
        _jobs.pop(job_id, None)
    return excess


def _set_job_library(job_id: Any, status: str, message: str) -> None:
    """Mirror the DB Library stage onto the in-memory download card."""
    if not job_id:
        return
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["library_status"] = status
            _jobs[job_id]["library_message"] = message
            _jobs[job_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _find_completed_file(output_dir: str, requested_filename: str, engine: str, min_mtime: float = 0.0) -> str:
    """Resolve the actual completed output, including yt-dlp's selected extension.

    ``min_mtime`` keeps a job from claiming an older file that merely shares the
    same stem (for example ``Title.mp4`` from a previous download).
    """
    expected = os.path.join(output_dir, requested_filename)
    if os.path.isfile(expected):
        return expected
    stem = os.path.splitext(requested_filename)[0]
    candidates = []
    try:
        for name in os.listdir(output_dir):
            path = os.path.join(output_dir, name)
            if os.path.isfile(path) and (name == requested_filename or name.startswith(stem + ".")):
                if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                    if min_mtime and os.path.getmtime(path) < min_mtime - 2.0:
                        continue
                    candidates.append(path)
    except OSError:
        return ""
    if not candidates:
        return ""
    return max(candidates, key=lambda p: os.path.getmtime(p))


def _ytdlp_command(exe: str, target: str, url: str) -> List[str]:
    template = os.path.splitext(target)[0] + ".%(ext)s"
    return [
        exe, "--newline", "--no-playlist", "--continue", "--part",
        "--concurrent-fragments", str(YTDLP_FRAGMENT_CONCURRENCY),
        "--fragment-retries", "10", "--retries", "10", "--retry-sleep", "2",
        "--socket-timeout", "20", "--throttled-rate", "100K",
        "--progress-template", "download:%(progress._percent_str)s",
        "-o", template, url,
    ]


def _aria2c_command(exe: str, output_dir: str, filename: str, url: str) -> List[str]:
    return [
        exe, "--continue=true", "--allow-overwrite=false", "--auto-file-renaming=true",
        f"--max-connection-per-server={ARIA2C_MAX_CONNECTIONS}",
        f"--split={ARIA2C_MAX_CONNECTIONS}",
        f"--min-split-size={ARIA2C_MIN_SPLIT_SIZE}",
        "--file-allocation=none",
        f"--max-tries={ARIA2C_MAX_TRIES}",
        f"--retry-wait={ARIA2C_RETRY_WAIT}",
        "--summary-interval=1", "--console-log-level=notice",
        "--dir", output_dir, "--out", filename, url,
    ]


def _engine_command(engine: str, exe: str, target: str, output_dir: str, url: str) -> List[str]:
    if engine == "yt-dlp":
        return _ytdlp_command(exe, target, url)
    if engine == "aria2c":
        # Use the collision-resolved basename, not the requested filename.
        return _aria2c_command(exe, output_dir, os.path.basename(target), url)
    raise RuntimeError("Engine lokal tidak dapat ditentukan secara aman.")


def _engine_sequence(engine: str) -> List[str]:
    # aria2c accelerates direct files; yt-dlp is kept as the fallback so a
    # provider that rejects Range requests still completes the download.
    return ["aria2c", "yt-dlp"] if engine == "aria2c" else ["yt-dlp"]


def _remove_incomplete_output(target: str) -> None:
    """Drop partial output before switching engines.

    aria2c writes directly to the target file, while yt-dlp would skip an
    existing target it did not create. Removing the partial file and its
    control files prevents a corrupt "completed" download.
    """
    for path in (target, target + ".aria2", target + ".part"):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    # yt-dlp leaves per-fragment temp files behind when an HLS/DASH download dies.
    directory = os.path.dirname(os.path.abspath(target))
    prefix = os.path.basename(target) + ".part-Frag"
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix):
                os.remove(os.path.join(directory, name))
    except OSError:
        pass


def _sanitize_process_line(line: str) -> str:
    """Return a short, token-free tail line safe to store and show in the GUI."""
    text = re.sub(r"https?://\S+", "<url>", str(line or ""))
    text = re.sub(
        r"([?&](?:token|sig|signature|expires|key|auth|hdnts)=)[^\s&]+",
        r"\1<redacted>", text, flags=re.I,
    )
    return text.strip()[:200]


def _run_download_process(job_id: str, engine: str, cmd: List[str]) -> tuple:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=creationflags)
    with _lock:
        _jobs[job_id]["pid"] = proc.pid
    tail: List[str] = []
    for line in proc.stdout or []:
        line = line.strip()
        pct = None
        if engine == "yt-dlp":
            m = re.search(r"(\d+(?:\.\d+)?)%", line)
            if m:
                pct = float(m.group(1))
        else:
            m = re.search(r"\((\d+(?:\.\d+)?)%\)", line)
            if m:
                pct = float(m.group(1))
        if pct is not None:
            _set(job_id, progress=max(0.0, min(100.0, pct)))
            continue
        # Keep only a short sanitized tail; never store URL/command.
        safe = _sanitize_process_line(line) if line and not line.startswith(("http://", "https://")) else ""
        if safe:
            tail = (tail + [safe])[-6:]
            _set(job_id, message=safe[:180])
    return proc.wait(), (tail[-1] if tail else "")


def _stream_path(url: str) -> str:
    try:
        return urlparse(str(url or "")).path
    except Exception:
        return ""


def _refresh_stream_url(meta: Dict[str, Any], stream_snapshot: Dict[str, Any]) -> str:
    """Ask Video Feeds for a freshly signed URL for the same stream.

    Signed CDN links expire while a long download is still running, which is
    the main cause of downloads that die half-way. The refresh keeps the same
    stream by comparing the URL path first and the expected filename second.
    """
    meta_type = str(meta.get("_roxxyType") or meta.get("type") or "").strip()
    meta_id = str(meta.get("id") or "").strip()
    if not meta_type or not meta_id:
        return ""
    source = str(meta.get("_roxxySource") or "main").strip().lower()
    if source not in ("main", "extra"):
        source = "main"
    import roxxy_module  # local import: keeps module startup free of a cycle
    data = roxxy_module._roxxy_get(
        f"stream/{quote(meta_type, safe='')}/{quote(meta_id, safe=':')}.json",
        source=source,
    )
    streams = data.get("streams") or []
    old_url = str(stream_snapshot.get("url") or "")
    old_path = _stream_path(old_url)
    old_name = str((stream_snapshot.get("behaviorHints") or {}).get("filename") or "").strip().lower()
    candidates = []
    for stream in streams:
        candidate = str(stream.get("url") or "").strip()
        if candidate.startswith(("http://", "https://")):
            candidates.append((stream, candidate))
    for _, candidate in candidates:
        if old_path and _stream_path(candidate) == old_path:
            try:
                return _validate_http_url(candidate)
            except Exception:
                continue
    if old_name:
        for stream, candidate in candidates:
            hints = stream.get("behaviorHints") or {}
            name = str(hints.get("filename") or stream.get("filename") or "").strip().lower()
            if name == old_name:
                try:
                    return _validate_http_url(candidate)
                except Exception:
                    continue
    return ""


def _looks_like_expiry_error(text: str) -> bool:
    """Transient CDN failures worth one fresh-link retry: expiry, 403/410, drops."""
    return bool(re.search(
        r"http error (401|403|410)|expired|forbidden|gone|token|"
        r"unable to download fragment|timeout|timed out|connection (reset|aborted)",
        text or "", re.I,
    ))


def _validate_completed_file(path: str) -> Dict[str, Any]:
    """Lightweight D1 validation: existence, regular file, allowed video extension, stable size."""
    if not path or not os.path.isfile(path):
        raise ValueError("File hasil download tidak ditemukan.")
    ext = os.path.splitext(path)[1].lower()
    if ext not in VIDEO_EXTS:
        raise ValueError(f"Ekstensi file tidak didukung untuk Library: {ext or '(tanpa ekstensi)'}")
    first = os.path.getsize(path)
    if first <= 0:
        raise ValueError("File hasil download kosong.")
    time.sleep(1.0)
    second = os.path.getsize(path)
    if second <= 0 or second != first:
        raise ValueError("Ukuran file belum stabil; validasi akan dicoba kembali.")
    return {"valid": True, "extension": ext, "size": second, "mtime": os.path.getmtime(path)}


def _identify_roxxy_media(task: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
    """D2: classify the downloaded title and detect a verified JAV duplicate."""
    metadata = task.get("metadata") or {}
    filename = os.path.basename(task.get("file_path") or "")
    title = str(metadata.get("name") or metadata.get("title") or "").strip()
    description = str(metadata.get("description") or "").strip()
    # Roxxy metadata is preferred over the generated local filename; the file
    # name remains available to the classifier as a second signal.
    classification = classification_engine.classify_media(
        filename=filename,
        parent_name=description,
        normalized_title=title or os.path.splitext(filename)[0],
        metadata=metadata,
    )
    category = classification.get("detected_category")
    code = str(metadata.get("jav_code") or classification.get("detected_code") or "").strip().upper()
    if category != "jav":
        code = ""

    duplicate = None
    if category == "jav" and code:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT id, title, file_name, source_type, content_category "
                "FROM media_items WHERE UPPER(REPLACE(REPLACE(COALESCE(code_id,''),' ',''),'_','-'))=? "
                "ORDER BY id DESC LIMIT 1",
                (code.replace(" ", "").replace("_", "-"),),
            ).fetchone()
        duplicate = dict(row) if row else None

    if duplicate:
        status = "duplicate"
    elif classification.get("classification_status") == "classified" and not (category == "jav" and not code):
        status = "identified"
    else:
        status = "needs_review"

    result = dict(validation or {})
    result["d2_identification"] = {
        "classifier_version": classification_engine.CLASSIFIER_VERSION,
        "category": category,
        "classification_status": classification.get("classification_status"),
        "classification_method": classification.get("classification_method"),
        "confidence": classification.get("category_confidence"),
        "signals": classification.get("signals") or [],
        "warnings": (classification.get("warnings") or []) + (["JAV category detected but no verified production code was extracted; manual review required."] if category == "jav" and not code else []),
        "engine_results": classification.get("engine_results") or [],
        "attributes": classification.get("attributes") or [],
        "code_id": code or None,
        "duplicate": duplicate,
        "subtitle_policy": "skip" if category == "indo" else "eligible_if_needed",
    }
    return {"status": status, "file_size": int(validation.get("size") or task.get("file_size") or 0),
            "validation": result, "code_id": code, "category": category}



def _roxxy_runtime_minutes(value: Any) -> int:
    """Parse Stremio-style runtime strings into the V1.2 minute unit."""
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = re.search(r"(\d{1,4})", str(value or ""))
    return int(match.group(1)) if match else 0


def _episode_int(value: Any) -> Optional[int]:
    """Positive integer or None; season/episode numbers are optional."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _register_roxxy_media(task: Dict[str, Any]) -> Dict[str, Any]:
    """D3: create/update one local Library record using the Roxxy metadata snapshot."""
    metadata = task.get("metadata") or {}
    validation = task.get("validation") or {}
    identification = validation.get("d2_identification") or {}
    category = identification.get("category") or "uncategorized"
    if identification.get("classification_status") != "classified":
        raise ValueError("Task belum memiliki hasil klasifikasi D2 yang valid.")
    if category == "jav" and not identification.get("code_id"):
        raise ValueError("Kategori JAV tidak memiliki production code terverifikasi.")
    path = os.path.abspath(task.get("file_path") or "")
    if not os.path.isfile(path):
        raise ValueError("File lokal tidak ditemukan saat registrasi Library.")

    filename = os.path.basename(path)
    title = str(metadata.get("name") or metadata.get("title") or os.path.splitext(filename)[0]).strip()
    if not title:
        title = os.path.splitext(filename)[0]
    release = str(metadata.get("releaseInfo") or "").strip()
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", release)
    try:
        year_value = int(metadata.get("year") or 0) or (int(year_match.group(1)) if year_match else None)
    except (TypeError, ValueError):
        year_value = int(year_match.group(1)) if year_match else None
    genres = metadata.get("genres") or []
    if not isinstance(genres, list):
        genres = [str(genres)]
    genres = [str(g).strip() for g in genres if str(g).strip()]
    runtime = _roxxy_runtime_minutes(metadata.get("runtime"))
    try:
        rating = float(metadata.get("imdbRating") or 0) or None
    except (TypeError, ValueError):
        rating = None
    unique_path = os.path.normcase(os.path.abspath(path))
    # A Donghua episode must stay attached to its series so the Library can
    # group it under the right season/episode instead of a loose movie row.
    season_value = _episode_int(metadata.get("season_num"))
    episode_value = _episode_int(metadata.get("episode_num"))
    series_title = str(metadata.get("series_title") or "").strip() or None
    media_data = {
        "unique_key": "local:" + unique_path,
        "source_type": "local",
        "file_name": filename,
        "file_path": path,
        "file_size": int(validation.get("size") or task.get("file_size") or os.path.getsize(path)),
        "code_id": identification.get("code_id") or None,
        "title": title,
        "original_title": str(metadata.get("original_name") or title),
        "year": year_value,
        "runtime": runtime,
        "studio": str(metadata.get("studio") or "").strip() or None,
        "plot": str(metadata.get("description") or "").strip() or None,
        "content_category": category,
        "is_adult": 1 if category in ("jav", "english_adult", "indo") else 0,
        "has_metadata": 1,
        "metadata_source": "roxxy",
        "season_num": season_value,
        "episode_num": episode_value,
        "series_title": series_title,
        "poster_url": metadata.get("poster") or metadata.get("background") or None,
        "genres": genres,
        "rating": rating,
        "has_subtitle": 0,
        "subtitle_source": "none",
    }
    media_id = db.upsert_media_item(media_data)
    cache_library_poster(media_id, media_data)
    return {"media_id": media_id, "unique_key": media_data["unique_key"],
            "title": title, "category": category}


def cache_library_poster(media_id: int, media_data: Dict[str, Any]) -> Optional[str]:
    """Materialize the Roxxy cover so the Library does not depend on a hotlink."""
    poster_url = str(media_data.get("poster_url") or "").strip()
    if not poster_url:
        return None
    try:
        import metadata_engine as meta_engine
        result = meta_engine.download_poster_image_result(
            poster_url, str(media_data.get("unique_key") or media_id)
        )
        local_path = result.get("local_poster_path")
        if not local_path:
            return None
        db.update_media_poster_state(
            media_id,
            poster_url=result.get("poster_url") or poster_url,
            local_poster_path=local_path,
            status=result.get("poster_status") or "found",
            error=result.get("poster_error"),
            source="roxxy",
            replace_url=False,
        )
        return local_path
    except Exception:
        return None


def _roxxy_registration_worker() -> None:
    """Serialized D3 worker: register identified downloads and run subtitle fallback."""
    while True:
        try:
            task = db.claim_roxxy_registration_task()
            if not task:
                time.sleep(2.0)
                continue
            validation = task.get("validation") or {}
            job_id = task.get("download_job_id")
            _set_job_library(job_id, "registering", "Mendaftarkan ke Library & menyiapkan cover…")
            try:
                registered = _register_roxxy_media(task)
                d3 = {
                    "status": "registered",
                    "media_id": registered["media_id"],
                    "unique_key": registered["unique_key"],
                    "title": registered["title"],
                    "category": registered["category"],
                }
                validation["d3_registration"] = d3
                category = registered["category"]
                item = db.get_media_item(registered["media_id"]) or {}
                attached_langs: List[str] = []
                provided_error = None
                stream_snapshot = task.get("stream") or {}
                provided_subtitles = stream_snapshot.get("_subtitles")
                if not provided_subtitles:
                    provided_subtitles = (task.get("metadata") or {}).get("subtitles") or []
                if category != "indo":
                    try:
                        attached_langs = attach_provided_subtitles(
                            registered["media_id"], item, provided_subtitles
                        )
                    except Exception as sub_exc:
                        provided_error = str(sub_exc)[:180]
                        validation.setdefault("d3_warnings", []).append(
                            "Roxxy subtitle processing failed: " + provided_error
                        )
                subtitle_result = None
                if attached_langs:
                    subtitle_note = "roxxy_" + "+".join(sorted(set(attached_langs)))
                else:
                    subtitle_note = "skipped_indonesian" if category == "indo" else "not_found"
                    if category != "indo":
                        try:
                            settings = db.get_settings()
                            subtitle_result = subtitle_engine.fetch_subtitle_for_item(
                                item, torbox_api_key=settings.get("torbox_api_key") or ""
                            )
                            if subtitle_result and subtitle_result.get("subtitle_path"):
                                db.upsert_media_subtitle(
                                    registered["media_id"],
                                    subtitle_result.get("subtitle_lang") or "en",
                                    subtitle_result["subtitle_path"],
                                    subtitle_result.get("subtitle_source") or "roxxy_fallback",
                                    is_embedded=bool(subtitle_result.get("is_embedded", False)),
                                    stream_index=subtitle_result.get("stream_index"),
                                    verified=True,
                                )
                                subtitle_note = "found"
                        except Exception as sub_exc:
                            # Subtitle failure must not roll back a valid Library registration.
                            validation.setdefault("d3_warnings", []).append(
                                "Subtitle processing failed: " + str(sub_exc)[:180]
                            )
                            subtitle_note = "error"
                validation["d3_registration"]["subtitle_status"] = subtitle_note
                db.update_roxxy_library_task(
                    task["id"], "completed", int(validation.get("size") or task.get("file_size") or 0),
                    validation, None
                )
                _set_job_library(
                    job_id, "completed",
                    f"Library terdaftar (ID {registered['media_id']}); subtitle: {subtitle_note}."
                )
            except Exception as exc:
                db.update_roxxy_library_task(
                    task["id"], "failed", int(validation.get("size") or task.get("file_size") or 0),
                    validation, "D3 registration failed: " + str(exc)
                )
                _set_job_library(job_id, "failed", str(exc)[:250])
        except Exception:
            time.sleep(3.0)


def attach_provided_subtitles(
    media_id: int, item: Dict[str, Any], provided: Any
) -> List[str]:
    """Persist subtitle tracks already supplied by Roxxy; returns attached languages."""
    langs: List[str] = []
    for track in subtitle_engine.fetch_provided_subtitles(provided, item):
        db.upsert_media_subtitle(
            media_id,
            track["subtitle_lang"],
            track["subtitle_path"],
            track.get("subtitle_source") or "roxxy",
            is_embedded=False,
            verified=True,
        )
        langs.append(track["subtitle_lang"])
    return langs


def _library_task_worker() -> None:
    """One serialized worker: validate (D1), then identify/classify (D2)."""
    while True:
        try:
            task = db.claim_roxxy_library_task()
            if not task:
                time.sleep(2.0)
                continue
            try:
                job_id = task.get("download_job_id")
                _set_job_library(job_id, "validating", "Memvalidasi file dan mengidentifikasi kategori…")
                result = _validate_completed_file(task["file_path"])
                identified = _identify_roxxy_media(task, result)
                db.update_roxxy_library_task(
                    task["id"], identified["status"], identified["file_size"],
                    identified["validation"],
                    "Duplicate code already exists in Library" if identified["status"] == "duplicate" else None,
                )
                messages = {
                    "identified": "File valid dan kategori berhasil diidentifikasi; siap untuk registrasi Library.",
                    "duplicate": "Kode JAV sudah ada di Library; tidak boleh dibuat sebagai entri duplikat.",
                    "needs_review": "File valid, tetapi kategori belum cukup meyakinkan; perlu pemeriksaan manual.",
                }
                _set_job_library(job_id, identified["status"], messages[identified["status"]])
            except Exception as exc:
                db.update_roxxy_library_task(task["id"], "failed", 0, {}, str(exc))
                _set_job_library(task.get("download_job_id"), "failed", str(exc)[:250])
        except Exception:
            time.sleep(3.0)


def start_library_task_worker() -> None:
    """Start exactly one persistent post-download worker per application process."""
    global _library_worker_started
    with _library_worker_lock:
        if _library_worker_started:
            return
        db.recover_roxxy_library_tasks()
        db.promote_roxxy_validated_tasks_for_identification()
        thread = threading.Thread(target=_library_task_worker, name="roxxy-library-queue", daemon=True)
        thread.start()
        _library_worker_started = True
        global _registration_worker_started
        if not _registration_worker_started:
            registration_thread = threading.Thread(
                target=_roxxy_registration_worker, name="roxxy-library-registration", daemon=True
            )
            registration_thread.start()
            _registration_worker_started = True


def _run_job(job_id: str, engine: str, url: str, output_dir: str, filename: str,
             resume: bool = False, target_path: str | None = None) -> None:
    with _workers:
        _set(job_id, status="downloading", message="Downloader dimulai.")
        try:
            os.makedirs(output_dir, exist_ok=True)
            target = target_path or os.path.join(output_dir, filename)
            if os.path.dirname(os.path.abspath(target)) != os.path.abspath(output_dir):
                target = os.path.join(output_dir, filename)
            if not resume:
                stem, ext = os.path.splitext(target)
                n = 1
                while os.path.exists(target):
                    target = f"{stem} ({n}){ext}"
                    n += 1
            _set(job_id, target_path=target)
            if resume:
                # A manual retry may reuse a signed URL that has already expired;
                # resolve a fresh link for the same stream when possible.
                try:
                    with _lock:
                        meta_snapshot = dict(_jobs.get(job_id, {}).get("meta_snapshot") or {})
                        stream_snapshot = dict(_jobs.get(job_id, {}).get("stream_snapshot") or {})
                    refreshed = _refresh_stream_url(meta_snapshot, stream_snapshot)
                except Exception:
                    refreshed = ""
                if refreshed:
                    url = refreshed
                    _set(job_id, url=url, message="Tautan CDN diperbarui; melanjutkan download.")
            attempted: List[str] = []
            final_engine = engine
            succeeded = False
            last_tail = ""
            last_engine = ""
            plan = _engine_sequence(engine)
            for index, current in enumerate(plan):
                exe = shutil.which(current)
                if not exe:
                    attempted.append(f"{current}: tidak ditemukan")
                    _set(job_id, message=f"{current} tidak ditemukan; mencoba engine berikutnya.")
                    continue
                if index > 0:
                    # Do not let the next engine resume or skip a partial file
                    # produced by the previous engine.
                    _remove_incomplete_output(target)
                    _set(job_id, progress=0.0, message=f"{plan[index - 1]} gagal, mencoba {current}.")
                _set(job_id, engine_active=current)
                cmd = _engine_command(current, exe, target, output_dir, url)
                exit_code, tail = _run_download_process(job_id, current, cmd)
                last_engine = current
                if exit_code == 0:
                    final_engine = current
                    succeeded = True
                    break
                last_tail = tail or last_tail
                detail = f" — {tail[:90]}" if tail else ""
                attempted.append(f"{current}: exit {exit_code}{detail}")
                _set(job_id, message=(f"{current} gagal (exit {exit_code}){detail}")[:250])

            if not succeeded and _looks_like_expiry_error(last_tail):
                # Signed CDN links expire while a long download is still running.
                # Ask Video Feeds for a fresh link and resume once before giving up.
                try:
                    with _lock:
                        meta_snapshot = dict(_jobs.get(job_id, {}).get("meta_snapshot") or {})
                        stream_snapshot = dict(_jobs.get(job_id, {}).get("stream_snapshot") or {})
                    refreshed = _refresh_stream_url(meta_snapshot, stream_snapshot)
                except Exception as refresh_exc:
                    refreshed = ""
                    attempted.append(f"refresh tautan: {str(refresh_exc)[:80]}")
                exe = shutil.which(last_engine) if refreshed else None
                if refreshed and exe:
                    _set(job_id, progress=0.0, engine_active=last_engine,
                         message="Tautan CDN kedaluwarsa; mencoba ulang dengan tautan baru.")
                    cmd = _engine_command(last_engine, exe, target, output_dir, refreshed)
                    exit_code, tail = _run_download_process(job_id, last_engine, cmd)
                    if exit_code == 0:
                        final_engine = last_engine
                        succeeded = True
                    else:
                        detail = f" — {tail[:90]}" if tail else ""
                        attempted.append(f"{last_engine} (tautan baru): exit {exit_code}{detail}")
                        last_tail = tail or last_tail

            if not succeeded:
                last_error = attempted[-1] if attempted else "Tidak ada downloader lokal yang tersedia."
                raise RuntimeError(("; ".join(attempted) or last_error)[:240])

            _set(job_id, status="completed", progress=100.0, message="Download selesai.",
                 completed_at=datetime.now().isoformat(timespec="seconds"), engine_active=final_engine)
            with _lock:
                completed_job = dict(_jobs.get(job_id) or {})
            final_path = _find_completed_file(
                output_dir, os.path.basename(target), final_engine,
                float(completed_job.get("started_ts") or 0)
            )
            if final_path:
                task_id = db.enqueue_roxxy_library_task(
                    job_id, final_path,
                    completed_job.get("meta_snapshot") or {},
                    completed_job.get("stream_snapshot") or {},
                )
                _set(job_id, library_task_id=task_id, library_status="queued",
                     message="Download selesai. Menunggu validasi & registrasi Library.")
            else:
                _set(job_id, library_status="failed",
                     message="Download selesai, tetapi file hasil tidak ditemukan untuk validasi Library.")
        except Exception as exc:
            _set(job_id, status="failed", message=str(exc)[:250])
        finally:
            _set(job_id, pid=None)
            with _lock:
                _prune_job_history_locked()

@localdl_bp.post("/api/roxxy/local/inspect")
def api_local_inspect():
    data = request.get_json(silent=True) or {}
    stream = data.get("stream") or {}
    try:
        info = _inspect(stream.get("url") or "")
        return jsonify({"ok": True, "inspection": {k:v for k,v in info.items() if k != "url"}})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:250]}), 400

@localdl_bp.post("/api/roxxy/local/start")
def api_local_start():
    data = request.get_json(silent=True) or {}
    stream = data.get("stream") or {}
    try:
        url = _validate_http_url(stream.get("url") or "")
        info = _inspect(url)
        if info["engine"] not in {"yt-dlp", "aria2c"}:
            return jsonify({"ok": False, "error": "URL belum teridentifikasi sebagai media/file yang dapat diunduh."}), 400
        outdir = _safe_download_dir(data.get("download_path") or DEFAULT_PATH)
        supplied_name = info.get("filename")
        if supplied_name:
            filename = _safe_filename(supplied_name)
        else:
            ext = info.get("extension") or CONTENT_TYPE_EXTS.get(info.get("content_type") or "")
            if not ext:
                ext = ".mp4" if info["engine"] == "yt-dlp" else ".bin"
            filename = _fallback_name({**data, "name": stream.get("name"), "meta": data.get("meta")}, ext)
        job_id = uuid.uuid4().hex[:12]
        stream_snapshot = dict(stream)
        provided_subs = data.get("subtitles")
        if isinstance(provided_subs, list) and provided_subs:
            stream_snapshot["_subtitles"] = provided_subs[:20]
        job = {"id": job_id, "title": str((data.get("meta") or {}).get("name") or stream.get("name") or filename),
               "filename": filename, "engine": info["engine"], "engine_active": info["engine"],
               "status": "queued", "progress": 0.0,
               "message": "Menunggu slot downloader.", "output_dir": outdir,
               "created_at": datetime.now().isoformat(timespec="seconds"), "updated_at": datetime.now().isoformat(timespec="seconds"),
               "completed_at": None, "pid": None, "attempts": 0,
               # Kept in memory only for retry; never serialised to the UI.
               "url": info["url"], "started_ts": time.time(),
               "meta_snapshot": data.get("meta") or {},
               "stream_snapshot": stream_snapshot}
        with _lock:
            _jobs[job_id] = job
            _prune_job_history_locked()
        thread = threading.Thread(target=_run_job, args=(job_id, info["engine"], info["url"], outdir, filename), daemon=True)
        thread.start()
        return jsonify({"ok": True, "job": {k:v for k,v in job.items() if k not in {"output_dir","pid","url","started_ts"}}, "inspection": {k:v for k,v in info.items() if k not in {"url"}}})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:250]}), 400

@localdl_bp.get("/api/roxxy/library-queue")
def api_library_queue():
    try:
        tasks = db.list_roxxy_library_tasks(100)
        for task in tasks:
            file_path = task.get("file_path") or ""
            task.pop("file_path", None)
            task.pop("stream", None)
            # Keep only the display metadata needed by the local UI.
            meta = task.get("metadata") or {}
            task["title"] = meta.get("name") or meta.get("title") or os.path.basename(file_path)
            task["file_name"] = os.path.basename(file_path)
            task["jav_code"] = meta.get("jav_code") or meta.get("code") or ""
            validation = task.get("validation") or {}
            task["identification"] = validation.get("d2_identification") or {}
            task["registration"] = validation.get("d3_registration") or {}
            task["d3_warnings"] = validation.get("d3_warnings") or []
            task.pop("metadata", None)
            task.pop("validation", None)
        return jsonify({"ok": True, "tasks": tasks})
    except Exception:
        return jsonify({"ok": False, "error": "Library queue tidak dapat dibaca."}), 500


@localdl_bp.get("/api/roxxy/local/queue")
def api_local_queue():
    with _lock:
        jobs = [dict(j) for j in _jobs.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    for job in jobs:
        job.pop("output_dir", None)
        job.pop("pid", None)
        job.pop("url", None)
        job.pop("started_ts", None)
        job.pop("meta_snapshot", None)
        job.pop("stream_snapshot", None)
        job.pop("target_path", None)
    return jsonify({"ok": True, "jobs": jobs})

@localdl_bp.post("/api/roxxy/local/clear")
def api_local_clear():
    """Hapus riwayat download lokal yang sudah selesai atau gagal.

    Download yang masih queued/downloading dan job yang Library-nya masih
    diproses tidak pernah dihapus.
    """
    with _lock:
        removed = _prune_job_history_locked(keep=0)
        remaining = len(_jobs)
    return jsonify({"ok": True, "removed": removed, "jobs": remaining})

@localdl_bp.post("/api/roxxy/local/retry/<job_id>")
def api_local_retry(job_id: str):
    with _lock:
        old = dict(_jobs.get(job_id) or {})
    if not old:
        return jsonify({"ok": False, "error": "Job download tidak ditemukan; mulai ulang dari katalog."}), 404
    if old.get("status") != "failed":
        return jsonify({"ok": False, "error": "Hanya download gagal yang dapat dicoba ulang."}), 400
    url = str(old.get("url") or "")
    if not url:
        return jsonify({"ok": False, "error": "URL sesi sebelumnya sudah tidak tersedia (aplikasi pernah dimulai ulang). Pilih ulang dari katalog."}), 409
    attempts = int(old.get("attempts") or 0)
    if attempts >= RETRY_LIMIT:
        return jsonify({"ok": False, "error": f"Batas {RETRY_LIMIT}x percobaan ulang tercapai untuk file ini."}), 409
    try:
        # Re-validate: the stored directory may have been tampered with or removed.
        outdir = _safe_download_dir(old.get("output_dir"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _lock:
        job = _jobs[job_id]
        job.update(
            status="queued", progress=0.0, pid=None, completed_at=None,
            message="Menunggu slot downloader (percobaan ulang).",
            engine_active=old.get("engine"),
            library_status=None, library_task_id=None, library_message=None,
            attempts=attempts + 1, started_ts=time.time(),
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )
        snapshot = {k: v for k, v in job.items() if k not in {"output_dir", "pid", "url", "started_ts", "target_path"}}
    threading.Thread(
        target=_run_job,
        args=(job_id, old.get("engine"), url, outdir, old.get("filename"), True, old.get("target_path")),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "job": snapshot})
