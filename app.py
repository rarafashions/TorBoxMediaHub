import hashlib
import ipaddress
import json
import logging.handlers
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from requests.exceptions import RequestException, Timeout
from werkzeug.exceptions import HTTPException

import database_manager as db
import classification_engine as class_eng
import metadata_engine as meta_eng
import subtitle_engine as sub_eng
import dual_subtitle_engine as dual_subs
import subtitle_quality_audit as subtitle_audit
import platform_support as platform_env
import webdl_resolver as webdl
from roxxy_module import (
    ROXXY_ALL_SOURCES,
    ROXXY_SOURCE_CATEGORIES,
    invalidate_library_index,
    roxxy_bp,
    source_category,
)

APP_VERSION = "v1.3DB"  # single source of truth; templates render this

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.register_blueprint(roxxy_bp)
from roxxy_download_engine import attach_provided_subtitles, cache_library_poster, localdl_bp
app.register_blueprint(localdl_bp)


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@app.before_request
def _guard_cross_site_writes():
    """Reject browser-initiated writes whose Origin/Referer is not this app.

    The server has no login flow, so a page on any site could otherwise POST to
    127.0.0.1:5050 with the user's browser. Requests without Origin/Referer
    (curl, scripts, the desktop launcher) are left alone on purpose.
    """
    if request.method not in _MUTATING_METHODS:
        return None
    site = request.headers.get("Sec-Fetch-Site")
    if site and site not in {"same-origin", "none"}:
        return jsonify({"ok": False, "error": "Cross-site request blocked."}), 403
    source = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if not source:
        return None
    expected = urlparse(request.url_root).netloc.lower()
    if urlparse(source).netloc.lower() != expected:
        return jsonify({"ok": False, "error": "Cross-site request blocked."}), 403
    return None

LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 4


class _ConsoleTee:
    """Mirror stdout/stderr into the rotating log file, keep console output.

    The server normally runs in a console window, and that window is the only
    log sink: running it hidden or from another shell loses every traceback.
    Each write still goes to the original stream, and the same text is also
    recorded through the ``console`` logger.
    """

    _local = threading.local()
    _ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

    def __init__(self, stream: Any, logger_name: str = "console") -> None:
        self._stream = stream
        self._logger = logging.getLogger(logger_name)

    def write(self, data: Any) -> int:
        raw = data
        if isinstance(data, (bytes, bytearray)):
            text = bytes(data).decode("utf-8", "replace")
        else:
            text = str(data)
        if not getattr(self._local, "writing", False) and text.strip():
            self._local.writing = True
            try:
                for line in self._ansi.sub("", text).splitlines():
                    if line.strip():
                        self._logger.info(line.rstrip("\r\n"))
            except Exception:
                pass
            finally:
                self._local.writing = False
        try:
            # Kirim objek aslinya: click/colorama menulis bytes ke stream.
            self._stream.write(raw)
        except Exception:
            pass
        return len(raw) if isinstance(raw, (bytes, bytearray)) else len(text)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        return "utf-8"


def _setup_server_logging() -> str:
    """Attach a rotating file log so restarts stop losing server output."""
    if getattr(app, "_tbh_file_logging", False):
        return LOG_FILE
    os.makedirs(LOG_DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Tee pertama, agar stream handler yang dibuat setelah ini menulis ke tee
    # dan setiap baris tercatat tepat sekali di file.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not isinstance(stream, _ConsoleTee):
            setattr(sys, stream_name, _ConsoleTee(stream))
    # Werkzeug/Flask menulis ke konsol sendiri; tee yang menyalinnya ke file.
    # Propagate dimatikan supaya baris yang sama tidak masuk dua kali.
    for logger in (logging.getLogger("werkzeug"), app.logger):
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(logging.StreamHandler())
    app._tbh_file_logging = True
    return LOG_FILE


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".m2ts")
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
TORBOX_API_BASE = "https://api.torbox.app/v1/api"
# TorBox disurvei hanya selama ada item yang dipantau. Sepuluh detik cukup untuk
# menampilkan progres tanpa membanjiri API dengan permintaan tiap 5 detik.
TORBOX_WATCH_POLL_SECONDS = 10
M3U_FRESH_TTL_SECONDS = 1800  # 30 minutes cache for freshly generated playlist M3U

DISCOVER_CACHE_TTL = 600  # 10 minutes cache for Discovery feeds
_DISCOVER_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_DISCOVER_CACHE_LOCK = threading.Lock()

class BackgroundTaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running: bool = False
        self.task_type: str = "idle"
        self.message: str = "Ready"
        self.current: int = 0
        self.total: int = 0
        self.logs: List[str] = []

    def start(self, task_type: str, message: str, total: int = 0) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.task_type = task_type
            self.message = message
            self.current = 0
            self.total = total
            self.logs = [f"[{datetime.now().strftime('%H:%M:%S')}] {message}"]
            return True

    def update(
        self,
        current: Optional[int] = None,
        total: Optional[int] = None,
        message: Optional[str] = None,
        log_line: Optional[str] = None,
    ) -> None:
        with self._lock:
            if current is not None:
                self.current = current
            if total is not None:
                self.total = total
            if message is not None:
                self.message = message
            if log_line:
                self.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {log_line}")
                if len(self.logs) > 80:
                    self.logs = self.logs[-80:]

    def finish(self, message: str) -> None:
        with self._lock:
            self.running = False
            self.message = message
            self.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
            if len(self.logs) > 80:
                self.logs = self.logs[-80:]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "task_type": self.task_type,
                "message": self.message,
                "current": self.current,
                "total": self.total,
                "logs": list(self.logs),
            }


TASK_MGR = BackgroundTaskManager()

# Phase 7.2.1.1: in-memory results for the latest visible-library audit.
_SUBTITLE_AUDIT_BATCH_LOCK = threading.Lock()
_SUBTITLE_AUDIT_BATCH_RESULTS: List[Dict[str, Any]] = []
_SUBTITLE_AUDIT_BATCH_SUMMARY: Dict[str, Any] = {}



def delete_media_physical(
    media_id: int,
    delete_local: bool = True,
    delete_remote: bool = True,
) -> Dict[str, Any]:
    """
    Safely deletes a media item from SQLite, removes local video + sidecar subtitles from disk,
    and deletes the corresponding torrent/usenet item from TorBox Cloud (with multi-file pack protection).
    """
    item = db.get_media_item(media_id)
    if not item:
        return {"ok": False, "error": "Media not found."}

    report: Dict[str, Any] = {
        "ok": True,
        "media_id": media_id,
        "title": item.get("title") or item.get("file_name"),
        "local_deleted": False,
        "remote_deleted": False,
        "remote_skipped_reason": "",
        "warnings": [],
    }

    # 1. Remove local file & sidecar/cached subtitles
    if delete_local and item.get("source_type") == "local":
        fp = item.get("file_path") or ""
        if fp and os.path.isfile(fp):
            try:
                os.remove(fp)
                report["local_deleted"] = True
            except Exception as exc:
                report["warnings"].append(f"Failed to delete local file: {exc}")

            base_no_ext = os.path.splitext(fp)[0]
            parent_dir = os.path.dirname(fp)
            base_name = os.path.basename(base_no_ext).lower()
            if os.path.isdir(parent_dir):
                try:
                    for fn in os.listdir(parent_dir):
                        fn_lower = fn.lower()
                        if fn_lower.startswith(base_name) and fn_lower.endswith(SUBTITLE_EXTENSIONS):
                            try:
                                os.remove(os.path.join(parent_dir, fn))
                            except OSError:
                                pass
                except OSError:
                    pass

    sp = item.get("subtitle_path") or ""
    if sp and not sp.startswith("embedded://") and os.path.isfile(sp):
        try:
            if os.path.abspath(sp).startswith(os.path.abspath(db.DATA_DIR)):
                os.remove(sp)
        except OSError:
            pass

    lp = item.get("local_poster_path") or ""
    if lp and os.path.isfile(lp):
        try:
            os.remove(lp)
        except OSError:
            pass

    # 2. Remove from TorBox Cloud if requested
    if delete_remote and item.get("source_type") == "cloud" and item.get("torbox_item_id"):
        torbox_item_id = str(item["torbox_item_id"]).strip()
        torbox_kind = (item.get("torbox_kind") or "torrent").strip().lower()

        sibling_count = 0
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM media_items WHERE torbox_item_id = ? AND torbox_kind = ? AND id != ?",
                (torbox_item_id, torbox_kind, media_id),
            ).fetchone()
            sibling_count = int(row["cnt"]) if row else 0

        if sibling_count > 0:
            report["remote_skipped_reason"] = (
                f"TorBox pack #{torbox_item_id} is shared by {sibling_count} other episode(s); "
                "removed from library without deleting the multi-file pack on TorBox."
            )
        else:
            settings = db.get_settings()
            api_key = (settings.get("torbox_api_key") or "").strip()
            if not api_key:
                report["warnings"].append("TorBox API Key not configured; skipped remote deletion.")
            else:
                headers = {"Authorization": f"Bearer {api_key}"}
                try:
                    if torbox_kind == "usenet":
                        resp = meta_eng.HTTP_SESSION.post(
                            f"{TORBOX_API_BASE}/usenet/controlusenetdownload",
                            headers=headers,
                            json={"usenet_id": int(torbox_item_id) if torbox_item_id.isdigit() else torbox_item_id, "operation": "delete"},
                            timeout=15,
                        )
                    elif torbox_kind == "webdl":
                        resp = meta_eng.HTTP_SESSION.post(
                            f"{TORBOX_API_BASE}/webdl/controlwebdownload",
                            headers=headers,
                            json={"webdl_id": int(torbox_item_id) if torbox_item_id.isdigit() else torbox_item_id, "operation": "delete"},
                            timeout=15,
                        )
                    else:
                        resp = meta_eng.HTTP_SESSION.post(
                            f"{TORBOX_API_BASE}/torrents/controltorrent",
                            headers=headers,
                            json={"torrent_id": int(torbox_item_id) if torbox_item_id.isdigit() else torbox_item_id, "operation": "delete"},
                            timeout=15,
                        )
                    if resp.status_code in (200, 201, 204):
                        report["remote_deleted"] = True
                    else:
                        report["warnings"].append(f"TorBox API delete HTTP {resp.status_code}: {resp.text[:160]}")
                except Exception as exc:
                    report["warnings"].append(f"TorBox delete request error: {exc}")

    # 3. Remove from SQLite database (and playlist links)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM playlist_items WHERE media_id = ?", (media_id,))
        conn.execute("DELETE FROM media_items WHERE id = ?", (media_id,))
        conn.commit()

    return report


class TorboxDownloadMonitor:
    """Monitors active TorBox cloud downloads and auto-imports + enriches them once finished."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.watched_items: Dict[str, Dict[str, Any]] = {}
        self.syncing_items: List[Dict[str, Any]] = []
        self.recently_imported: List[Dict[str, Any]] = []
        self._thread_started: bool = False

    def track(
        self,
        kind: str,
        item_id: str,
        title: str,
        series_title: str = "",
        category: str = "",
        season: int = 1,
        replace_media_id: Optional[int] = None,
        delete_remote_on_replace: bool = True,
        poster_url: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        subtitles: Optional[List[Dict[str, Any]]] = None,
        expected_filename: str = "",
    ) -> None:
        if not item_id:
            return
        key = f"{kind}:{item_id}"
        hint_payload: Dict[str, Any] = {}
        if isinstance(metadata, dict) and metadata:
            hint_payload["meta"] = metadata
        if isinstance(subtitles, list) and subtitles:
            hint_payload["subtitles"] = subtitles
        if expected_filename:
            hint_payload["expected_filename"] = expected_filename
        with self._lock:
            self.watched_items[key] = {
                "key": key,
                "kind": kind,
                "item_id": str(item_id),
                "title": title or f"{kind} #{item_id}",
                "series_title": series_title,
                "category": category,
                "poster_url": poster_url or "",
                "season": season,
                "replace_media_id": int(replace_media_id) if replace_media_id else None,
                "delete_remote_on_replace": bool(delete_remote_on_replace),
                "progress": 0.0,
                "download_state": "queued",
                "finished": False,
                "library_stage": "",
                "library_message": "",
                "added_at": time.time(),
            }
            with _TORBOX_IMPORT_HINTS_LOCK:
                _TORBOX_IMPORT_HINTS[key] = {
                    "content_category": category if category in db.VALID_CATEGORIES else "uncategorized",
                    "poster_url": poster_url or "",
                    "metadata": hint_payload,
                }
            try:
                db.save_torbox_import_hint(kind, str(item_id), category, poster_url, hint_payload)
            except Exception as hint_exc:
                print(f"[TorBoxImportHint] Persistence warning: {hint_exc}")
            if not self._thread_started:
                self._thread_started = True
                threading.Thread(target=self._loop, daemon=True).start()

    def set_library_progress(self, keys: Any, stage: str, message: str) -> None:
        """Update the import stage shown under a completed cloud download."""
        wanted = {keys} if isinstance(keys, str) else set(keys or [])
        if not wanted:
            return
        with self._lock:
            for entry in self.syncing_items:
                if entry.get("key") in wanted:
                    entry["library_stage"] = stage
                    entry["library_message"] = message

    def status(self) -> Dict[str, Any]:
        with self._lock:
            active = list(self.watched_items.values())
            syncing = list(self.syncing_items)
            imported = list(self.recently_imported)
            self.recently_imported.clear()
        return {
            "active_count": len(active) + len(syncing),
            "active": active,
            "syncing": syncing,
            "newly_imported": imported,
        }

    def _loop(self) -> None:
        while True:
            time.sleep(TORBOX_WATCH_POLL_SECONDS)
            with self._lock:
                if not self.watched_items:
                    self._thread_started = False
                    return
                snapshot = dict(self.watched_items)

            settings = db.get_settings()
            api_key = (settings.get("torbox_api_key") or "").strip()
            if not api_key:
                continue

            headers = {"Authorization": f"Bearer {api_key}"}
            torbox_state: Dict[str, Dict[str, Any]] = {}

            for kind, ep in (("torrent", "torrents/mylist"), ("usenet", "usenet/mylist"), ("webdl", "webdl/mylist")):
                if not any(v["kind"] == kind for v in snapshot.values()):
                    continue
                try:
                    r = meta_eng.HTTP_SESSION.get(
                        f"{TORBOX_API_BASE}/{ep}",
                        headers=headers,
                        params={"bypass_cache": "true"},
                        timeout=15,
                    )
                    if r.status_code == 200:
                        rows = r.json().get("data") or []
                        if isinstance(rows, dict):
                            rows = [rows]
                        for row in rows:
                            rid = str(row.get("id") or "")
                            if rid:
                                torbox_state[f"{kind}:{rid}"] = row
                except Exception:
                    continue

            completed_entries: List[Dict[str, Any]] = []
            with self._lock:
                for key, entry in list(self.watched_items.items()):
                    if time.time() - entry["added_at"] > 21600:
                        del self.watched_items[key]
                        _forget_import_hints(key)
                        continue

                    remote = torbox_state.get(key)
                    if not remote:
                        continue

                    prog = float(remote.get("progress") or 0.0)
                    state_txt = str(remote.get("download_state") or "downloading")
                    is_done = bool(remote.get("download_finished")) and (
                        remote.get("download_present") is not False
                    )

                    entry["progress"] = round(prog * 100, 1) if prog <= 1.0 else round(prog, 1)
                    entry["download_state"] = state_txt
                    entry["finished"] = is_done

                    if is_done:
                        entry["library_stage"] = "queued"
                        entry["library_message"] = "Menunggu impor ke Library…"
                        completed_entries.append(dict(entry))
                        del self.watched_items[key]

                if completed_entries:
                    self.syncing_items.extend(completed_entries)

            if completed_entries:
                imported_successfully = False
                try:
                    for _ in range(60):
                        if not TASK_MGR.running:
                            break
                        time.sleep(2)

                    started = TASK_MGR.start(
                        "auto_import_torbox",
                        f"Auto-importing {len(completed_entries)} completed TorBox download(s)...",
                    )
                    if not started:
                        # Never bypass the shared task lock. Re-queue completed items
                        # so they can be imported after the foreground task finishes.
                        with self._lock:
                            for entry in completed_entries:
                                self.watched_items[entry["key"]] = entry
                        continue

                    try:
                        _sync_torbox_cloud_worker(auto_enrich=True)
                        imported_successfully = True
                    except Exception as exc:
                        TASK_MGR.finish(f"Auto-import error: {exc}")

                    if imported_successfully:
                        for entry in completed_entries:
                            rep_id = entry.get("replace_media_id")
                            if rep_id:
                                try:
                                    delete_media_physical(
                                        int(rep_id),
                                        delete_local=True,
                                        delete_remote=bool(entry.get("delete_remote_on_replace", True)),
                                    )
                                except Exception as del_exc:
                                    print(f"[TorboxDownloadMonitor] Replace delete error for #{rep_id}: {del_exc}")
                except Exception as exc:
                    print(f"[TorboxDownloadMonitor] Sync error: {exc}")
                finally:
                    done_keys = {e["key"] for e in completed_entries}
                    if imported_successfully:
                        with self._lock:
                            self.syncing_items = [
                                s for s in self.syncing_items if s["key"] not in done_keys
                            ]
                            self.recently_imported.extend(completed_entries)
                        # Hint sudah dipakai impor ini; salinan persisten di DB
                        # (get_torbox_import_hint) tetap tersedia untuk impor ulang.
                        _forget_import_hints(done_keys)
                        # Judul yang baru masuk Library harus langsung tampil
                        # sebagai IN LIBRARY pada pengecekan badge berikutnya.
                        invalidate_library_index()
                    else:
                        with self._lock:
                            self.syncing_items = [
                                s for s in self.syncing_items if s["key"] not in done_keys
                            ]



TORBOX_MONITOR = TorboxDownloadMonitor()
# Phase 8.4.0: preserve Discovery category and poster across WebDL completion/import.
_TORBOX_IMPORT_HINTS: Dict[str, Dict[str, Any]] = {}
_TORBOX_IMPORT_HINTS_LOCK = threading.Lock()


def _forget_import_hints(keys: Any) -> None:
    """Buang hint in-memory setelah item selesai diimpor atau kedaluwarsa.

    Salinan persisten di DB (``get_torbox_import_hint``) tetap dipakai saat
    impor ulang, jadi menghapus entri ini tidak menghilangkan metadata.
    """
    wanted = {keys} if isinstance(keys, str) else {k for k in (keys or []) if k}
    if not wanted:
        return
    with _TORBOX_IMPORT_HINTS_LOCK:
        for key in wanted:
            _TORBOX_IMPORT_HINTS.pop(key, None)


def _is_video_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS


_IMPORT_META_TEXT_FIELDS = (
    "name", "description", "poster", "background", "releaseInfo", "runtime",
    "studio", "jav_code", "original_name", "director",
)
_IMPORT_META_LIST_FIELDS = ("genres", "cast")


def _sanitize_import_metadata(raw: Any) -> Dict[str, Any]:
    """Keep only bounded, known Roxxy meta fields for the import hint."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in _IMPORT_META_TEXT_FIELDS:
        value = raw.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                out[key] = text[:5000]
    try:
        year = int(raw.get("year") or 0)
        if 1800 <= year <= 2200:
            out["year"] = year
    except (TypeError, ValueError):
        pass
    try:
        rating = float(raw.get("imdbRating") or 0)
        if 0 < rating <= 10:
            out["imdbRating"] = rating
    except (TypeError, ValueError):
        pass
    for key in _IMPORT_META_LIST_FIELDS:
        value = raw.get(key)
        if isinstance(value, list):
            items = [str(v).strip()[:200] for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
            if items:
                out[key] = items[:20]
    # The addon a download came from decides its category, so the sanitized
    # hint must keep the selector; only known values are accepted.
    source = str(raw.get("_roxxySource") or "").strip().lower()
    if source in ROXXY_ALL_SOURCES:
        out["_roxxySource"] = source
    return out


def _sanitize_import_subtitles(raw: Any) -> List[Dict[str, str]]:
    """Bound subtitle-track hints; URLs are validated again at download time."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for entry in raw[:20]:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or len(url) > 2048:
            continue
        out.append(
            {
                "url": url,
                "lang": str(
                    entry.get("lang") or entry.get("language") or entry.get("langName") or ""
                ).strip()[:32],
                "name": str(entry.get("name") or "").strip()[:120],
            }
        )
        if len(out) >= 8:
            break
    return out


def _import_hint_media_payload(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a sanitized Roxxy metadata snapshot into media_items fields."""
    if not metadata:
        return {}
    payload: Dict[str, Any] = {}
    name = str(metadata.get("name") or metadata.get("title") or "").strip()
    if name:
        payload["title"] = name[:300]
    plot = str(metadata.get("description") or "").strip()
    if plot:
        payload["plot"] = plot[:5000]
    original = str(metadata.get("original_name") or "").strip()
    if original:
        payload["original_title"] = original[:300]
    studio = str(metadata.get("studio") or "").strip()
    if studio:
        payload["studio"] = studio[:200]
    try:
        year = int(metadata.get("year") or 0)
        if 1800 <= year <= 2200:
            payload["year"] = year
    except (TypeError, ValueError):
        pass
    runtime_match = re.search(r"(\d{1,4})", str(metadata.get("runtime") or ""))
    if runtime_match:
        payload["runtime"] = int(runtime_match.group(1))
    genres = metadata.get("genres")
    if isinstance(genres, list) and genres:
        payload["genres"] = [str(g).strip()[:80] for g in genres if str(g).strip()][:20]
    try:
        rating = float(metadata.get("imdbRating") or 0)
        if 0 < rating <= 10:
            payload["rating"] = rating
    except (TypeError, ValueError):
        pass
    if payload:
        payload["has_metadata"] = 1
        payload["metadata_source"] = "roxxy"
    return payload


def _classify_import_hint(
    metadata: Dict[str, Any],
    expected_filename: str,
    parent_name: str = "",
) -> Dict[str, Any]:
    """Return the full classifier verdict for a TorBox import.

    The file name and parent folder travel with the import so regional evidence
    is not lost when the provider title is generic or English.
    """
    try:
        # The JAV code is valid evidence, so include it when Roxxy did not
        # provide a usable filename for the torrent.
        code = str(metadata.get("jav_code") or "").strip()
        filename_hint = expected_filename or code
        normalized_title = str(metadata.get("name") or "")
        if code and code.lower() not in normalized_title.lower():
            normalized_title = f"{normalized_title} {code}".strip()
        if not normalized_title and not filename_hint:
            return {}
        return class_eng.classify_media(
            filename=filename_hint,
            parent_name=parent_name or "",
            normalized_title=normalized_title or None,
            metadata=metadata,
        )
    except Exception:
        return {}


def _resolve_import_category(
    category: str,
    hint_metadata: Dict[str, Any],
    expected_filename: str,
    parent_name: str = "",
) -> str:
    """Prefer a classified hint verdict over the caller-supplied category."""
    if not hint_metadata:
        return category
    verdict = _classify_import_hint(hint_metadata, expected_filename, parent_name)
    detected = verdict.get("detected_category")
    if verdict.get("classification_status") == "classified" and detected in db.VALID_CATEGORIES:
        return detected
    return category


_ADULT_CATEGORIES = {"jav", "english_adult", "indo"}
_NON_ADULT_CATEGORIES = {"general_movie", "tv_show", "anime", "donghua", "indo_movie"}
# Categories fixed by the Roxxy addon a download came from. The hint classifier
# must not override them, otherwise an IndonesiaMovies title could land in Indo
# and a Donghua episode could become a plain TV show. JAV is not in this set:
# its Library surface needs a verified production code, so the source alone
# cannot decide the category (see roxxy_module.ROXXY_SOURCE_CATEGORIES).
_MANIFEST_LOCKED_CATEGORIES = set(ROXXY_SOURCE_CATEGORIES.values())
# Non-adult addon categories are sticky in every automatic pass. The adult
# addons stay refinable: an Indonesian file name may correctly promote an
# English Adult import to Indo, while a demotion to a non-adult category is
# already blocked above.
_STICKY_MANIFEST_CATEGORIES = {"indo_movie", "donghua"}
# Addons that only publish adult titles. A non-adult verdict for these sources
# is treated as a classifier miss instead of filing porn as a family title.
_ADULT_MANIFEST_SOURCES = {"jav"}


def _apply_manifest_category_lock(
    category: str,
    hint_metadata: Dict[str, Any],
    expected_filename: str,
    parent_name: str = "",
) -> str:
    """Keep an addon-defined category, otherwise let the hint re-classify.

    English Adult/OnlyFans, IndonesiaMovies and Donghua downloads keep the
    category their manifest declares, and the manifest wins over a stale client
    value. JAV is deliberately not provenance-locked: its Library surface needs
    a verified production code, so a JAV title becomes "jav" only when the
    classifier extracts one; a non-adult verdict for that adult-only addon is
    left uncategorized for review. Unknown categories fall back to
    ``uncategorized``.
    """
    category = str(category or "").strip()
    source = str((hint_metadata or {}).get("_roxxySource") or "").strip().lower()
    declared = source_category(source)
    if declared:
        category = declared
    elif hint_metadata and category not in _MANIFEST_LOCKED_CATEGORIES:
        category = _resolve_import_category(
            category,
            hint_metadata,
            expected_filename,
            parent_name,
        )
        if source in _ADULT_MANIFEST_SOURCES and category in _NON_ADULT_CATEGORIES:
            category = "uncategorized"
    if category not in db.VALID_CATEGORIES:
        category = "uncategorized"
    return category


def _guard_adult_reclassification(
    item: Dict[str, Any],
    result: Dict[str, Any],
    source: str = "",
) -> Dict[str, Any]:
    """Never strip an adult addon category because weaker evidence appeared later.

    Reclassification runs from stored media fields, which can be thinner than the
    original provider metadata. An Indo item is never demoted, an adult item is
    never moved into a non-adult category, and IndonesiaMovies/Donghua titles
    keep their addon category in every automatic pass. Each of those cases
    becomes Needs Review while the previous category stays visible; only a
    manual classification may move them elsewhere. ``source`` is the Roxxy addon
    the download came from, when it is still known.
    """
    old_category = str(item.get("content_category") or "uncategorized")
    new_category = result.get("detected_category")
    if result.get("classification_status") != "classified":
        return result
    demoted_indo = old_category == "indo" and new_category != "indo"
    demoted_adult = old_category in _ADULT_CATEGORIES and new_category in _NON_ADULT_CATEGORIES
    # Download categories defined by a Roxxy manifest are authoritative: a
    # Donghua episode must not turn into a plain TV show just because the file
    # name looks episodic.
    demoted_manifest = (
        old_category in _STICKY_MANIFEST_CATEGORIES and new_category != old_category
    )
    # An adult-only addon never publishes a family title, so a non-adult verdict
    # for one of its downloads is a classifier miss, not a correction.
    misclassified_adult_source = (
        str(source or "").strip().lower() in _ADULT_MANIFEST_SOURCES
        and new_category in _NON_ADULT_CATEGORIES
    )
    if demoted_indo or demoted_adult or demoted_manifest or misclassified_adult_source:
        guarded = dict(result)
        guarded["classification_status"] = "needs_review"
        guarded["detected_category"] = None
        guarded["warnings"] = list(result.get("warnings") or []) + [
            "Reclassification suggested a weaker category; the previous addon category was kept for review."
        ]
        return guarded
    return result


_EPISODIC_CATEGORIES = {"tv_show", "anime", "donghua"}


def _reclassification_category_change_allowed(
    old_category: str,
    result: Dict[str, Any],
) -> bool:
    """Decide whether a batch verdict may move an existing row's category.

    Every candidate gets its verdict recorded, but the category only moves when
    the previous bucket was provisional (uncategorized), when an adult surface
    is recovered, or inside the episodic family. Provider/manifest categories
    (IndonesiaMovies, Donghua) are never rewritten here; a weaker verdict for
    those rows becomes Needs Review through ``_guard_adult_reclassification``.
    """
    if result.get("classification_status") != "classified":
        return False
    new_category = result.get("detected_category")
    if not new_category:
        return False
    old_category = str(old_category or "uncategorized")
    if old_category in _STICKY_MANIFEST_CATEGORIES:
        return False
    if old_category == "uncategorized":
        return True
    if old_category in _ADULT_CATEGORIES:
        # Any adult demotion was already turned into Needs Review by the guard.
        return True
    if old_category == "general_movie":
        return new_category in _EPISODIC_CATEGORIES or new_category in _ADULT_CATEGORIES
    if old_category in _EPISODIC_CATEGORIES:
        return new_category in _EPISODIC_CATEGORIES
    return False


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_environment_info() -> Dict[str, Any]:
    os_name = db.detect_runtime_os()
    is_docker = os.path.exists("/.dockerenv") or os.environ.get("TBH_CONTAINER") == "1"
    has_display = (
        os_name == "windows"
        or os_name == "darwin"
        or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    )
    host_bind = os.environ.get("TBH_HOST", "127.0.0.1")
    port_bind = int(os.environ.get("TBH_PORT", "5050"))
    lan_ip = _get_lan_ip()
    return {
        "os_platform": os_name,
        "arch": platform.machine(),
        "python_version": platform.python_version(),
        "is_docker": is_docker,
        "is_headless": not has_display and os_name != "android",
        "data_dir": db.DATA_DIR,
        "host": host_bind,
        "port": port_bind,
        "lan_url": f"http://{lan_ip}:{port_bind}",
        "ffprobe_available": bool(platform_env.resolve_executable("ffprobe")),
        "ffmpeg_available": bool(platform_env.resolve_executable("ffmpeg")),
        "mpv_available": bool(platform_env.resolve_executable("mpv")),
    }


def detect_installed_players() -> List[Dict[str, str]]:
    """Scans the current OS for installed media players (VLC, MPV, PotPlayer, MPC-HC, IINA, etc.)."""
    os_name = db.detect_runtime_os()
    found: List[Dict[str, str]] = []
    seen_paths = set()

    def _add(player_type: str, label: str, path: str) -> None:
        if not path:
            return
        norm = path if path.startswith("android://") else os.path.normpath(path)
        if norm.lower() in seen_paths:
            return
        if path.startswith("android://") or os.path.isfile(norm):
            seen_paths.add(norm.lower())
            found.append({"type": player_type, "label": label, "path": norm})

    if os_name == "android":
        _add("vlc", "VLC for Android (Intent)", "android://org.videolan.vlc")
        _add("mpv", "mpv-android (Intent)", "android://is.xyz.mpv")
        _add("custom", "Android System Chooser (Any Player)", "android://auto")
        return found

    if os_name == "windows":
        prog_dirs = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
            os.path.join(os.environ.get("USERPROFILE", ""), "scoop", "apps"),
        ]
        candidates = [
            ("vlc", "VLC Media Player", [os.path.join("VideoLAN", "VLC", "vlc.exe"), os.path.join("vlc", "current", "vlc.exe")]),
            ("mpv", "MPV Media Player", [os.path.join("mpv", "mpv.exe"), os.path.join("mpv.net", "mpvnet.exe"), os.path.join("mpv", "current", "mpv.exe")]),
            ("custom", "PotPlayer (64-bit)", [os.path.join("DAUM", "PotPlayer", "PotPlayerMini64.exe"), os.path.join("PotPlayer", "PotPlayerMini64.exe")]),
            ("custom", "MPC-HC (64-bit)", [os.path.join("MPC-HC", "mpc-hc64.exe"), os.path.join("K-Lite Codec Pack", "MPC-HC64", "mpc-hc64.exe")]),
            ("custom", "MPC-BE (64-bit)", [os.path.join("MPC-BE x64", "mpc-be64.exe")]),
            ("custom", "KMPlayer", [os.path.join("KMPlayer 64X", "KMPlayer64.exe"), os.path.join("KMPlayer", "KMPlayer.exe")]),
        ]
        for base in prog_dirs:
            if not base or not os.path.isdir(base):
                continue
            for ptype, label, rels in candidates:
                for rel in rels:
                    _add(ptype, label, os.path.join(base, rel))

        for bin_name, ptype, label in [
            ("vlc.exe", "vlc", "VLC (System PATH)"),
            ("mpv.exe", "mpv", "MPV (System PATH)"),
            ("mpvnet.exe", "custom", "mpv.net (System PATH)"),
            ("PotPlayerMini64.exe", "custom", "PotPlayer (System PATH)"),
            ("mpc-hc64.exe", "custom", "MPC-HC (System PATH)"),
        ]:
            w = shutil.which(bin_name)
            if w:
                _add(ptype, label, w)

    elif os_name == "darwin":
        mac_candidates = [
            ("vlc", "VLC Media Player", "/Applications/VLC.app/Contents/MacOS/VLC"),
            ("vlc", "VLC (User Apps)", os.path.expanduser("~/Applications/VLC.app/Contents/MacOS/VLC")),
            ("mpv", "MPV (Homebrew Apple Silicon)", "/opt/homebrew/bin/mpv"),
            ("mpv", "MPV (Homebrew Intel)", "/usr/local/bin/mpv"),
            ("mpv", "MPV App Bundle", "/Applications/mpv.app/Contents/MacOS/mpv"),
            ("custom", "IINA Media Player", "/Applications/IINA.app/Contents/MacOS/iina-cli"),
            ("custom", "IINA App", "/Applications/IINA.app/Contents/MacOS/IINA"),
            ("custom", "Elmedia Player", "/Applications/Elmedia Player.app/Contents/MacOS/Elmedia Player"),
        ]
        for ptype, label, p in mac_candidates:
            _add(ptype, label, p)
        for bin_name, ptype, label in [("vlc", "vlc", "VLC (PATH)"), ("mpv", "mpv", "MPV (PATH)"), ("iina", "custom", "IINA (PATH)")]:
            w = shutil.which(bin_name)
            if w:
                _add(ptype, label, w)

    else:
        linux_candidates = [
            ("vlc", "VLC Media Player", "/usr/bin/vlc"),
            ("vlc", "VLC (/usr/local)", "/usr/local/bin/vlc"),
            ("vlc", "VLC (Snap)", "/snap/bin/vlc"),
            ("vlc", "VLC (Flatpak)", "/var/lib/flatpak/exports/bin/org.videolan.VLC"),
            ("mpv", "MPV Media Player", "/usr/bin/mpv"),
            ("mpv", "MPV (/usr/local)", "/usr/local/bin/mpv"),
            ("mpv", "MPV (Flatpak)", "/var/lib/flatpak/exports/bin/io.mpv.Mpv"),
            ("custom", "Celluloid (GNOME MPV)", "/usr/bin/celluloid"),
            ("custom", "SMPlayer", "/usr/bin/smplayer"),
            ("custom", "Haruna Media Player", "/usr/bin/haruna"),
        ]
        for ptype, label, p in linux_candidates:
            _add(ptype, label, p)
        for bin_name, ptype, label in [
            ("vlc", "vlc", "VLC (PATH)"),
            ("mpv", "mpv", "MPV (PATH)"),
            ("celluloid", "custom", "Celluloid (PATH)"),
            ("smplayer", "custom", "SMPlayer (PATH)"),
        ]:
            w = shutil.which(bin_name)
            if w:
                _add(ptype, label, w)

    # Generic resolver catches PATH, Homebrew, Scoop, Snap and Flatpak locations.
    for ptype in ("mpv", "vlc"):
        resolved = platform_env.resolve_executable(ptype)
        if resolved:
            _add(ptype, f"{ptype.upper()} (Auto-detected)", resolved)

    return found


def _request_torbox_stream_url(api_key: str, kind: str, item_id: str, file_id: str) -> Optional[str]:
    endpoint_map = {"torrent": ("torrents", "torrent_id"), "usenet": ("usenet", "usenet_id"), "webdl": ("webdl", "web_id")}
    endpoint, id_param = endpoint_map.get(str(kind or "torrent").lower(), ("torrents", "torrent_id"))
    resolved_file_id = str(file_id).strip() if file_id is not None and str(file_id).strip() != "" else "0"
    try:
        resp = meta_eng.HTTP_SESSION.get(
            f"{TORBOX_API_BASE}/{endpoint}/requestdl",
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            params={
                "token": api_key.strip(),
                id_param: item_id,
                "file_id": resolved_file_id,
                "zip_link": "false",
            },
            timeout=12,
        )
        if resp.status_code == 200:
            url = resp.json().get("data")
            if isinstance(url, str) and url.startswith("http"):
                return url
    except Exception:
        pass
    return None


def _attach_cloud_stream_for_probe(item: Dict[str, Any], settings: Dict[str, str]) -> Dict[str, Any]:
    if (
        not int(item.get("runtime") or 0)
        and item.get("source_type") == "cloud"
        and not item.get("stream_url")
        and settings.get("torbox_api_key")
        and item.get("torbox_item_id")
    ):
        fid_val = str(item.get("torbox_file_id")) if item.get("torbox_file_id") not in (None, "") else "0"
        temp_url = _request_torbox_stream_url(
            settings["torbox_api_key"],
            item.get("torbox_kind") or "torrent",
            str(item["torbox_item_id"]),
            fid_val,
        )
        if temp_url:
            cloned = dict(item)
            cloned["stream_url"] = temp_url
            return cloned
    return item


def _sync_torbox_cloud_worker(auto_enrich: bool = False) -> None:
    """Import completed video files from TorBox Torrents, Usenet and Web Downloads."""
    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        TASK_MGR.finish("Failed: TorBox API Key is not configured in Settings.")
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    discovered: List[Dict[str, Any]] = []
    source_counts = {"torrent": 0, "usenet": 0, "webdl": 0}
    errors: List[str] = []

    # TorBox mylist endpoints support offset/limit. Fetch every page instead of
    # silently relying on the default first page (1000 records).
    sources = (
        ("torrent", "torrents/mylist"),
        ("usenet", "usenet/mylist"),
        ("webdl", "webdl/mylist"),
    )
    page_size = 1000
    max_pages = 100

    for kind, ep in sources:
        offset = 0
        page_num = 0
        while page_num < max_pages:
            page_num += 1
            TASK_MGR.update(
                message=f"Fetching {kind} list from TorBox Cloud (page {page_num})..."
            )
            try:
                resp = meta_eng.HTTP_SESSION.get(
                    f"{TORBOX_API_BASE}/{ep}",
                    headers=headers,
                    params={"bypass_cache": "true", "offset": offset, "limit": page_size},
                    timeout=(8, 30),
                )
                if resp.status_code != 200:
                    detail = ""
                    try:
                        payload_err = resp.json()
                        detail = str(payload_err.get("detail") or payload_err.get("error") or "")
                    except Exception:
                        detail = (resp.text or "")[:180]
                    errors.append(f"{kind}: HTTP {resp.status_code} {detail}".strip())
                    TASK_MGR.update(log_line=f"Warning {kind}: HTTP {resp.status_code} {detail}".strip())
                    break

                payload = resp.json()
                data_list = payload.get("data") if isinstance(payload, dict) else []
                if isinstance(data_list, dict):
                    data_list = [data_list]
                if not isinstance(data_list, list):
                    errors.append(f"{kind}: unexpected response data format")
                    TASK_MGR.update(log_line=f"Warning {kind}: unexpected response data format")
                    break

                for parent in data_list:
                    if not isinstance(parent, dict):
                        continue
                    # Only import files TorBox reports as present and finished.
                    # Missing flags are tolerated for older/alternate response shapes.
                    if parent.get("download_present") is False:
                        continue
                    if parent.get("download_finished") is False:
                        continue

                    raw_pid = parent.get("id") or parent.get("web_id") or parent.get("webdl_id")
                    if raw_pid is None:
                        continue
                    pid = str(raw_pid)
                    parent_title = (parent.get("name") or parent.get("title") or "").strip()

                    files_list = parent.get("files") or []
                    # Some WebDL responses expose a single file at the top level.
                    if not files_list and (parent.get("short_name") or parent.get("name")):
                        files_list = [parent]

                    for fobj in files_list:
                        if not isinstance(fobj, dict):
                            continue
                        raw_name = (
                            fobj.get("short_name")
                            or os.path.basename(fobj.get("name") or fobj.get("filename") or "")
                        )
                        if not raw_name or not _is_video_file(raw_name):
                            continue
                        raw_fid = fobj.get("id")
                        fid = str(raw_fid) if raw_fid is not None else "0"
                        try:
                            fsize = int(fobj.get("size") or fobj.get("size_bytes") or 0)
                        except (TypeError, ValueError):
                            fsize = 0

                        with _TORBOX_IMPORT_HINTS_LOCK:
                            import_hint = dict(_TORBOX_IMPORT_HINTS.get(f"{kind}:{pid}") or {})
                        if not import_hint:
                            try:
                                import_hint = db.get_torbox_import_hint(kind, pid)
                            except Exception:
                                import_hint = {}
                        hint_meta = import_hint.get("metadata") or {}
                        if not isinstance(hint_meta, dict):
                            hint_meta = {}
                        discovered.append({
                            "kind": kind,
                            "item_id": pid,
                            "file_id": fid,
                            "content_category": import_hint.get("content_category", "uncategorized"),
                            "poster_url": import_hint.get("poster_url", ""),
                            "hint_metadata": hint_meta.get("meta") or {},
                            "hint_subtitles": hint_meta.get("subtitles") or [],
                            "parent_name": parent_title,
                            "file_name": raw_name,
                            "file_size": fsize,
                        })
                        source_counts[kind] += 1

                if len(data_list) < page_size:
                    break
                offset += page_size
            except Exception as exc:
                errors.append(f"{kind}: {exc}")
                TASK_MGR.update(log_line=f"Connection error ({kind}): {exc}")
                break

    TASK_MGR.update(
        total=len(discovered),
        current=0,
        message=f"Saving {len(discovered)} completed video files to database..."
    )
    tracked_keys = {
        f"{entry['kind']}:{entry['item_id']}"
        for entry in discovered
        if entry.get("hint_metadata") or entry.get("hint_subtitles")
    }
    if tracked_keys:
        TORBOX_MONITOR.set_library_progress(
            tracked_keys, "importing", "Mengimpor ke Library TorBox Media Hub…"
        )
    new_or_updated_ids: List[int] = []
    with db.get_connection() as conn:
        for idx, entry in enumerate(discovered, start=1):
            ukey = f"torbox:{entry['kind']}:{entry['item_id']}:{entry['file_id']}"
            media_payload: Dict[str, Any] = {
                "unique_key": ukey,
                "source_type": "cloud",
                "torbox_kind": entry["kind"],
                "torbox_item_id": entry["item_id"],
                "torbox_file_id": entry["file_id"],
                "parent_name": entry["parent_name"],
                "file_name": entry["file_name"],
                "file_size": entry["file_size"],
                "title": entry["file_name"],
                "content_category": entry.get("content_category") or "uncategorized",
                "poster_url": entry.get("poster_url") or None,
            }
            hint_fields = _import_hint_media_payload(entry.get("hint_metadata") or {})
            if hint_fields:
                media_payload.update(hint_fields)
            mid = db.upsert_media_item(media_payload, conn=conn)
            new_or_updated_ids.append(mid)
            if idx % 25 == 0 or idx == len(discovered):
                TASK_MGR.update(current=idx, message=f"Syncing Cloud ({idx}/{len(discovered)})...")
        conn.commit()

    # Re-classify imported items with the file name and parent folder available.
    # The add-time hint only sees the provider title, so this second pass can
    # recover regional evidence that is stored on the TorBox entry itself.
    for mid, entry in zip(new_or_updated_ids, discovered):
        hint_metadata = entry.get("hint_metadata") or {}
        if not hint_metadata:
            continue
        verdict = _classify_import_hint(
            hint_metadata,
            entry.get("file_name") or "",
            entry.get("parent_name") or "",
        )
        if verdict.get("classification_status") not in ("classified", "needs_review"):
            continue
        verdict = _guard_adult_reclassification(
            {"content_category": entry.get("content_category") or "uncategorized"},
            verdict,
            source=hint_metadata.get("_roxxySource") or "",
        )
        try:
            saved_item = db.save_classification_result(
                mid,
                verdict,
                changed_by="import_auto",
                note="TorBox import classification",
            )
        except PermissionError:
            continue
        if saved_item and saved_item.get("content_category"):
            entry["content_category"] = saved_item["content_category"]

    if auto_enrich and new_or_updated_ids:
        non_indo_ids = [mid for mid, entry in zip(new_or_updated_ids, discovered)
                        if (entry.get("content_category") or "") != "indo"]
        if non_indo_ids:
            _enrich_ids_internal(non_indo_ids, force=False)

    # Apply Roxxy cover + subtitle tracks for downloads started from Video Feeds.
    provided_targets = [
        (mid, entry) for mid, entry in zip(new_or_updated_ids, discovered)
        if entry.get("hint_metadata") or entry.get("hint_subtitles")
    ]
    if provided_targets:
        finalized: Dict[str, str] = {}
        for mid, entry in provided_targets:
            key = f"{entry['kind']}:{entry['item_id']}"
            item = None
            try:
                item = db.get_media_item(mid) or {}
            except Exception:
                item = {}
            if entry.get("hint_metadata") and item.get("poster_url"):
                TORBOX_MONITOR.set_library_progress(key, "cover", "Menyimpan cover Roxxy…")
                try:
                    cache_library_poster(mid, item)
                except Exception as poster_exc:
                    print(f"[TorBoxImport] Poster warning {key}: {poster_exc}")
            category = str(item.get("content_category") or entry.get("content_category") or "")
            subtitle_note = ""
            if entry.get("hint_subtitles"):
                if category == "indo":
                    subtitle_note = "subtitle Indonesia dilewati"
                else:
                    TORBOX_MONITOR.set_library_progress(key, "subtitle", "Mengunduh subtitle Roxxy…")
                    try:
                        langs = attach_provided_subtitles(mid, item or {}, entry["hint_subtitles"])
                        subtitle_note = (
                            "subtitle Roxxy: " + "+".join(sorted(set(langs)))
                            if langs else "subtitle Roxxy tidak tersedia"
                        )
                    except Exception as sub_exc:
                        print(f"[TorBoxImport] Subtitle warning {key}: {sub_exc}")
                        subtitle_note = "subtitle gagal diproses"
            finalized[key] = subtitle_note
            TORBOX_MONITOR.set_library_progress(
                key,
                "completed",
                "Library terdaftar" + (f"; {subtitle_note}" if subtitle_note else "") + ".",
            )
        leftover = tracked_keys - set(finalized)
        if leftover:
            TORBOX_MONITOR.set_library_progress(
                leftover, "completed", "Library terdaftar (tanpa subtitle tambahan)."
            )

    summary = (
        f"TorBox sync completed: {len(discovered)} video files processed "
        f"(Torrent {source_counts['torrent']}, Usenet {source_counts['usenet']}, "
        f"WebDL {source_counts['webdl']})."
    )
    if errors:
        summary += " Warnings: " + " | ".join(errors[:3])
    TASK_MGR.finish(summary)


def _scan_local_folders_worker(auto_enrich: bool = False) -> None:
    settings = db.get_settings()
    try:
        paths = json.loads(settings.get("local_scan_paths") or "[]")
    except Exception:
        paths = []

    valid_dirs = [p for p in paths if p and os.path.isdir(p)]
    if not valid_dirs:
        TASK_MGR.finish("No valid local folders configured in Settings.")
        return

    found_files: List[str] = []
    for root_dir in valid_dirs:
        TASK_MGR.update(message=f"Scanning directory: {root_dir}")
        for dirpath, _, filenames in os.walk(root_dir):
            for fn in filenames:
                if _is_video_file(fn):
                    found_files.append(os.path.join(dirpath, fn))

    TASK_MGR.update(total=len(found_files), current=0, message=f"Found {len(found_files)} local videos...")
    ids: List[int] = []

    with db.get_connection() as conn:
        for idx, fp in enumerate(found_files, start=1):
            norm_p = os.path.normpath(fp)
            fn = os.path.basename(norm_p)
            parent_dir = os.path.basename(os.path.dirname(norm_p))
            try:
                fsize = os.path.getsize(norm_p)
            except OSError:
                fsize = 0

            probed_runtime = meta_eng.probe_video_duration_minutes(norm_p)
            sidecar = sub_eng.find_local_sidecar_subtitle(norm_p)
            payload: Dict[str, Any] = {
                "unique_key": f"local:{norm_p.lower()}",
                "source_type": "local",
                "parent_name": parent_dir,
                "file_name": fn,
                "file_path": norm_p,
                "file_size": fsize,
                "runtime": probed_runtime,
                "title": fn,
            }
            if sidecar:
                payload["has_subtitle"] = 1
                payload["subtitle_path"] = sidecar[0]
                payload["subtitle_lang"] = sidecar[1]
                payload["subtitle_source"] = "local_sidecar"

            mid = db.upsert_media_item(payload, conn=conn)
            ids.append(mid)
            if idx % 25 == 0 or idx == len(found_files):
                TASK_MGR.update(current=idx, message=f"Local Scan ({idx}/{len(found_files)}): {fn}")
        conn.commit()

    if auto_enrich and ids:
        _enrich_ids_internal(ids, force=False)

    TASK_MGR.finish(f"Local scan completed: {len(found_files)} files processed.")


def _classify_pending_worker() -> None:
    """Record a classifier verdict for rows whose category has no stored one.

    Import/enrich paths write ``content_category`` without touching the
    classification status, which left hundreds of rows permanently "pending".
    This pass persists the verdict for every candidate and only moves a category
    when the evidence is provable (see ``_reclassification_category_change_allowed``).
    """
    try:
        candidates = db.get_reclassify_candidates(limit=5000)
        total = len(candidates)
        TASK_MGR.update(total=total, current=0, message=f"Reclassifying {total} items...")
        classified_count = 0
        review_count = 0
        skipped_count = 0
        for idx, item in enumerate(candidates, start=1):
            try:
                # media_items stores provider evidence separately; feed it back so
                # a reclassification does not lose studio/genre signals that the
                # original import used.
                metadata = {
                    "name": item.get("title") or "",
                    "studio": item.get("studio") or "",
                    "genres": item.get("genres") or [],
                    "description": item.get("plot") or "",
                    "year": item.get("year") or None,
                }
                result = class_eng.classify_media(
                    item.get("file_name") or "",
                    item.get("parent_name") or "",
                    normalized_title=item.get("normalized_title") or item.get("title") or None,
                    metadata=metadata,
                )
                old_category = str(item.get("content_category") or "uncategorized")
                result = _guard_adult_reclassification(item, result)
                # Record a verdict for every candidate. The category only moves
                # when the bucket was provisional or an adult/episodic verdict is
                # provable; provider-canonical titles are never replaced.
                saved = db.save_classification_result(
                    int(item["id"]), result,
                    changed_by="classifier",
                    note="Batch reclassification",
                    apply_category=_reclassification_category_change_allowed(old_category, result),
                    apply_title=not bool(
                        item.get("has_metadata") or item.get("title_is_manual")
                    ),
                )
                if saved:
                    if saved.get("classification_status") == "classified":
                        classified_count += 1
                    else:
                        review_count += 1
                else:
                    skipped_count += 1
            except PermissionError:
                skipped_count += 1
            except Exception as exc:
                skipped_count += 1
                TASK_MGR.update(log_line=f"Classification error for media #{item.get('id')}: {exc}")
            TASK_MGR.update(
                current=idx,
                message=f"Classifying ({idx}/{total}) — classified {classified_count}, review {review_count}...",
            )
        TASK_MGR.finish(
            f"Classification completed: {classified_count} classified, {review_count} needs review, {skipped_count} skipped."
        )
    except Exception as exc:
        TASK_MGR.finish(f"Classification failed: {exc}")


def _enrich_ids_internal(media_ids: List[int], force: bool = False) -> int:
    settings = db.get_settings()
    items_to_process: List[Dict[str, Any]] = []

    for mid in media_ids:
        item = db.get_media_item(mid)
        if not item:
            continue
        # IndonesiaMovies metadata comes from Roxxy; missing covers/subtitles are
        # filled manually on the media card, never by an automatic provider.
        if not force and (item.get("content_category") or "").lower() == "indo_movie":
            continue
        if (
            not force
            and item.get("has_metadata") == 1
            and item.get("content_category") != "uncategorized"
            and (item.get("has_poster") or int(item.get("poster_is_manual") or 0))
            and int(item.get("runtime") or 0) > 0
        ):
            continue
        items_to_process.append(item)

    total = len(items_to_process)
    TASK_MGR.update(total=total, current=0)
    if total == 0:
        return 0

    enriched_count = 0
    completed = 0

    def _worker(it: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[str]]:
        try:
            probe_ready_item = _attach_cloud_stream_for_probe(it, settings)
            res = meta_eng.enrich_media_item(probe_ready_item, settings)
            return it, res, None
        except Exception as e:
            return it, None, str(e)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_worker, it) for it in items_to_process]
        for fut in as_completed(futures):
            completed += 1
            it, enriched, err = fut.result()
            if enriched:
                enriched["unique_key"] = it["unique_key"]
                enriched["file_name"] = it["file_name"]
                enriched["_force_meta"] = True
                media_id = db.upsert_media_item(enriched)
                if enriched.get("poster_status"):
                    db.update_media_poster_state(
                        media_id,
                        poster_url=enriched.get("poster_url"),
                        local_poster_path=enriched.get("local_poster_path"),
                        status=enriched.get("poster_status"),
                        error=enriched.get("poster_error"),
                        source=enriched.get("poster_source") or enriched.get("metadata_source"),
                        replace_url=True,
                    )
                enriched_count += 1
                lookup = enriched.get("_lookup") or {}
                TASK_MGR.update(
                    current=completed,
                    message=f"Enrich ({completed}/{total}): [{enriched.get('content_category')}] {it['file_name']} — {lookup.get('status', 'processed')}",
                    log_line=(
                        f"Metadata lookup {lookup.get('status')} via {lookup.get('provider')}; "
                        f"query={lookup.get('query') or '-'}; poster={lookup.get('poster_status') or enriched.get('poster_status') or '-'}"
                    ) if lookup.get("status") not in ("metadata_found",) or enriched.get("poster_status") != "found" else None,
                )
            else:
                TASK_MGR.update(
                    current=completed,
                    log_line=f"Failed to enrich {it['file_name']}: {err}",
                )

    return enriched_count


def _batch_enrich_worker(force: bool = False) -> None:
    targets = db.query_media_items(needs_enrichment=not force)
    if not targets:
        TASK_MGR.finish("All media already have complete metadata, posters & duration.")
        return
    ids = [t["id"] for t in targets]
    count = _enrich_ids_internal(ids, force=force)
    TASK_MGR.finish(f"Enrich Meta completed: {count}/{len(ids)} media items updated.")


def _batch_subtitle_worker() -> None:
    """Language-aware batch retrieval: scan first, fetch only missing targets."""
    settings = db.get_settings()
    prefs = db.get_subtitle_preferences()
    indonesian_enabled = prefs["indonesian_enabled"]
    all_targets = db.query_media_items(missing_subtitle_only=False)
    if not all_targets:
        TASK_MGR.finish("No media items available for subtitle processing.")
        return

    # Indo videos intentionally do not enter any subtitle workflow: no subtitle
    # inventory scan, FFprobe, provider lookup, or download is performed.
    # IndonesiaMovies subtitles come from Roxxy; when missing, the user fetches
    # them manually from the media card instead of an automatic provider.
    skip_categories = {"indo", "indo_movie"}
    indo_skipped = sum(
        1 for item in all_targets
        if (item.get("content_category") or "").lower() in skip_categories
    )
    targets = [
        item for item in all_targets
        if (item.get("content_category") or "").lower() not in skip_categories
    ]
    if not targets:
        TASK_MGR.finish(
            f"Subtitle processing skipped: all {indo_skipped} media items are category Indo/IndonesiaMovies; "
            "subtitle search is disabled for these categories."
        )
        return

    db.verify_subtitle_files()
    total = len(targets)
    TASK_MGR.update(
        total=total, current=0,
        message=f"Checking subtitles for {total} eligible items; skipped {indo_skipped} Indo/IndonesiaMovies items..."
    )
    torbox_key = (settings.get("torbox_api_key") or "").strip()
    processed = found_count = downloaded_count = 0
    cloud_targets = any(item.get("source_type") == "cloud" for item in targets)
    torbox_sub_index = sub_eng.build_torbox_subtitle_index(torbox_key) if torbox_key and cloud_targets else None

    for item in targets:
        media_id = int(item["id"])
        try:
            known = {r["language"]: r for r in db.get_media_subtitles(media_id)}

            # Phase 7.1 reliability guard: skip already-complete media before
            # running FFprobe or contacting any subtitle provider.
            usable_known = {
                row["language"]: row
                for row in dual_subs.filter_usable_subtitle_tracks(list(known.values()), db.CACHE_DIR)
            }
            missing = dual_subs.plan_missing_languages(
                list(usable_known.values()), indonesian_enabled=indonesian_enabled
            )
            if not missing:
                status = dual_subs.subtitle_status(
                    list(usable_known.values()), indonesian_enabled=indonesian_enabled
                )
                TASK_MGR.update(
                    current=processed + 1, total=total,
                    message=f"Dual subtitles ({processed + 1}/{total}): {item['file_name']} [{status}]",
                    log_line=f"SKIPPED: {item['file_name']} | required subtitles already available; no scan or fetch needed",
                )
                processed += 1
                continue

            # Scan embedded/sidecar inventory only when at least one required
            # language is missing. This preserves the Phase 6B.8 discovery path
            # without repeatedly probing already-complete items.
            for local_track in dual_subs.scan_item_subtitles(item, torbox_api_key=torbox_key):
                if local_track["language"] not in known:
                    db.upsert_media_subtitle(media_id, local_track["language"], local_track["subtitle_path"],
                        local_track.get("subtitle_source", "local"),
                        is_embedded=bool(local_track.get("is_embedded")),
                        stream_index=local_track.get("stream_index"), verified=True)
                    known[local_track["language"]] = local_track

            missing = dual_subs.plan_missing_languages(list(known.values()), indonesian_enabled=indonesian_enabled)
            for language in missing:
                result = sub_eng.fetch_subtitle_for_language(
                    item, language, torbox_api_key=torbox_key, torbox_sub_index=torbox_sub_index
                )
                if result and result.get("subtitle_path") and result.get("subtitle_lang") == language:
                    db.upsert_media_subtitle(media_id, language, result["subtitle_path"],
                        result.get("subtitle_source") or "auto",
                        is_embedded=bool(result.get("is_embedded", False)),
                        stream_index=result.get("stream_index"), verified=True)
                    known[language] = result
                    downloaded_count += 1
            status = dual_subs.subtitle_status(list(known.values()), indonesian_enabled=indonesian_enabled)
            if status in ("Ready", "Complete"):
                found_count += 1
            TASK_MGR.update(current=processed + 1, total=total,
                message=f"Dual subtitles ({processed + 1}/{total}): {item['file_name']} [{status}]",
                log_line=f"{status}: {item['file_name']} | missing searched: {','.join(missing) or 'none'}")
        except Exception as exc:
            TASK_MGR.update(current=processed + 1, total=total,
                message=f"Subtitle error: {item['file_name']}",
                log_line=f"ERROR: {item['file_name']}: {exc}")
        processed += 1

    stats = db.get_subtitle_language_stats(indonesian_enabled=indonesian_enabled)
    TASK_MGR.finish(
        f"Dual-language subtitle scan completed. English-ready: {stats['ready'] + stats['complete']}; "
        f"Complete: {stats['complete']}; downloaded this run: {downloaded_count}; "
        f"Indo skipped: {indo_skipped}."
    )


def _get_item_subtitle_tracks(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read verified dual-language rows, retaining compatibility with legacy media rows."""
    media_id = item.get("id")
    rows: List[Dict[str, Any]] = []
    if media_id is not None:
        try:
            rows = db.get_media_subtitles(int(media_id))
        except Exception:
            rows = []
    if not rows:
        legacy_path = item.get("subtitle_path") or ""
        legacy_lang = (item.get("subtitle_lang") or "en").lower()
        if legacy_path and legacy_lang in ("en", "id"):
            rows = [{"language": legacy_lang, "subtitle_path": legacy_path,
                     "is_embedded": str(legacy_path).startswith("embedded://"), "verified": True}]
    for row in rows:
        if row.get("subtitle_path") and not str(row.get("subtitle_path")).startswith("embedded://"):
            row["subtitle_path"] = dual_subs.resolve_external_path(row["subtitle_path"], db.CACHE_DIR)
    return rows


def _attach_subtitle_track_summaries(items: List[Dict[str, Any]]) -> None:
    """Attach compact per-language source labels to media-list API records in one DB query."""
    if not items:
        return
    ids = [int(item["id"]) for item in items if item.get("id") is not None]
    grouped: Dict[int, List[Dict[str, Any]]] = {media_id: [] for media_id in ids}
    try:
        placeholders = ",".join("?" for _ in ids)
        with db.get_connection() as conn:
            rows = conn.execute(
                f"SELECT media_id, language, is_embedded, subtitle_path, verified FROM media_subtitles "
                f"WHERE media_id IN ({placeholders}) AND verified=1 ORDER BY CASE language WHEN 'en' THEN 0 ELSE 1 END",
                ids,
            ).fetchall()
        for row in rows:
            grouped.setdefault(int(row["media_id"]), []).append(dict(row))
    except Exception:
        # Older databases/legacy records remain renderable without dual-source badges.
        pass

    for item in items:
        tracks = grouped.get(int(item["id"]), [])
        if not tracks and item.get("has_subtitle") and item.get("subtitle_path"):
            path = item.get("subtitle_path") or ""
            lang = (item.get("subtitle_lang") or "en").lower()
            if lang in ("en", "id"):
                tracks = [{"language": lang, "is_embedded": path.startswith("embedded://"),
                           "subtitle_path": path, "verified": True}]
        item["subtitle_tracks"] = [
            {"language": row.get("language"),
             "source": "embedded" if row.get("is_embedded") or str(row.get("subtitle_path") or "").startswith("embedded://") else "external"}
            for row in tracks if row.get("language") in ("en", "id")
        ]


def _resolve_runtime_playlist_path(playlist: Dict[str, Any]) -> str:
    """Relocate a generated playlist file into the current OS playlist directory if needed."""
    stored = str(playlist.get("file_path") or "").strip()
    if stored and os.path.isfile(stored):
        return stored
    file_name = str(playlist.get("file_name") or "").strip()
    if not file_name and stored:
        file_name = stored.replace("\\", "/").rsplit("/", 1)[-1]
    if not file_name:
        file_name = f"playlist_{playlist.get('id', 'unknown')}.m3u"
    os.makedirs(db.PLAYLIST_DIR, exist_ok=True)
    runtime_path = os.path.join(db.PLAYLIST_DIR, os.path.basename(file_name))
    if stored != runtime_path:
        with db.get_connection() as conn:
            conn.execute("UPDATE playlists SET file_path=? WHERE id=?", (runtime_path, playlist.get("id")))
            conn.commit()
        playlist["file_path"] = runtime_path
    return runtime_path


def _read_m3u_media_entries(file_path: str) -> List[Dict[str, Any]]:
    """Read media URI/path lines and their stable media IDs from a generated M3U."""
    entries: List[Dict[str, Any]] = []
    current_media_id: Optional[int] = None
    with open(file_path, "r", encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("#TBH-MEDIA-ID:"):
                try:
                    current_media_id = int(line.split(":", 1)[1])
                except (TypeError, ValueError):
                    current_media_id = None
            elif line and not line.startswith("#"):
                entries.append({"media_id": current_media_id, "target": line})
                current_media_id = None
    return entries


def _generate_m3u_content(
    name: str,
    items: List[Dict[str, Any]],
    remote_base_url: Optional[str] = None,
    progress_cb: Optional[Any] = None,
) -> Tuple[str, int]:
    """Generates M3U text concurrently so multi-item cloud playlists resolve rapidly."""
    settings = db.get_settings()
    torbox_key = (settings.get("torbox_api_key") or "").strip()

    total_items = len(items)
    resolved_uris: List[Optional[str]] = [None] * total_items
    fresh_updates: List[Dict[str, Any]] = []
    cloud_jobs: List[Tuple[int, Dict[str, Any]]] = []

    for idx, it in enumerate(items):
        if it.get("source_type") == "local":
            if remote_base_url:
                resolved_uris[idx] = f"{remote_base_url.rstrip('/')}/api/media/{it['id']}/stream"
            else:
                resolved_uris[idx] = it.get("file_path") or ""
        else:
            if torbox_key and it.get("torbox_item_id"):
                cloud_jobs.append((idx, it))
            else:
                resolved_uris[idx] = it.get("stream_url") or ""

    if cloud_jobs:
        completed_cloud = 0
        total_cloud = len(cloud_jobs)

        def _fetch_one(job: Tuple[int, Dict[str, Any]]) -> Tuple[int, Dict[str, Any], Optional[str]]:
            i, item_obj = job
            fid_val = str(item_obj.get("torbox_file_id")) if item_obj.get("torbox_file_id") not in (None, "") else "0"
            url = _request_torbox_stream_url(
                torbox_key,
                item_obj.get("torbox_kind") or "torrent",
                str(item_obj["torbox_item_id"]),
                fid_val,
            )
            return i, item_obj, url

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_fetch_one, job) for job in cloud_jobs]
            for fut in as_completed(futures):
                i, item_obj, fresh_url = fut.result()
                completed_cloud += 1
                if fresh_url:
                    resolved_uris[i] = fresh_url
                    fresh_updates.append(
                        {
                            "unique_key": item_obj["unique_key"],
                            "file_name": item_obj["file_name"],
                            "stream_url": fresh_url,
                        }
                    )
                else:
                    resolved_uris[i] = item_obj.get("stream_url") or ""

                if progress_cb:
                    disp = (item_obj.get("title") or item_obj.get("file_name") or "Media")[:55]
                    progress_cb(
                        completed_cloud,
                        total_cloud,
                        f"Preparing stream ({completed_cloud}/{total_cloud}): {disp}...",
                    )

        if fresh_updates:
            with db.get_connection() as conn:
                for upd in fresh_updates:
                    db.upsert_media_item(upd, conn=conn)
                conn.commit()
    elif progress_cb and total_items > 0:
        progress_cb(total_items, total_items, f"Prepared {total_items} local stream(s).")

    lines = ["#EXTM3U", "#TBH-PLAYLIST-VERSION:6B5", f"#PLAYLIST:{name}"]
    skipped_count = 0

    for idx, it in enumerate(items):
        target_uri = resolved_uris[idx] or ""
        if not target_uri:
            skipped_count += 1
            continue

        dur = int((it.get("runtime") or 0) * 60) if it.get("runtime") else -1
        disp_title = it.get("title") or it.get("file_name") or "Media"
        if it.get("code_id"):
            disp_title = f"[{it['code_id']}] {disp_title}"
        elif it.get("season_num") is not None and it.get("episode_num") is not None:
            disp_title = f"{disp_title} - S{int(it['season_num']):02d}E{int(it['episode_num']):02d}"

        lines.append(f"#TBH-MEDIA-ID:{it.get('id', '')}")
        lines.append(f"#EXTINF:{dur},{disp_title}")
        subtitle_rows = _get_item_subtitle_tracks(it)
        vlc_track = dual_subs.vlc_playback_track(subtitle_rows)
        if vlc_track:
            language = vlc_track.get("language") or "en"
            sp = vlc_track.get("subtitle_path") or ""
            if remote_base_url:
                sub_url = f"{remote_base_url.rstrip('/')}/api/media/{it['id']}/subtitle-file/{language}"
                lines.append(f"#EXTVLCOPT:sub-file={sub_url}")
            elif sp and os.path.isfile(sp):
                lines.append(f"#EXTVLCOPT:sub-file={sp}")
        lines.append(target_uri)

    return "\n".join(lines), skipped_count


def _generate_m3u_file(
    name: str,
    items: List[Dict[str, Any]],
    existing_file_path: Optional[str] = None,
    progress_cb: Optional[Any] = None,
) -> str:
    os.makedirs(db.PLAYLIST_DIR, exist_ok=True)
    if existing_file_path:
        out_path = existing_file_path
    else:
        safe_slug = re.sub(r"[^\w\-]+", "_", name.strip()).strip("_") or "playlist"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(db.PLAYLIST_DIR, f"{safe_slug}_{ts}.m3u")

    content, skipped_count = _generate_m3u_content(name, items, remote_base_url=None, progress_cb=progress_cb)
    if skipped_count > 0:
        TASK_MGR.update(log_line=f"Playlist '{name}': {skipped_count} item(s) skipped due to unavailable stream URL.")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


def _is_m3u_file_fresh(file_path: Optional[str], ttl_seconds: int = M3U_FRESH_TTL_SECONDS) -> bool:
    if not file_path or not os.path.isfile(file_path):
        return False
    try:
        if os.path.getsize(file_path) < 20:
            return False
        with open(file_path, "r", encoding="utf-8-sig") as handle:
            header = handle.read(512)
        if "#TBH-PLAYLIST-VERSION:6B5" not in header:
            return False
        age = time.time() - os.path.getmtime(file_path)
        return 0 <= age <= ttl_seconds
    except OSError:
        return False


def _resolve_player_binary(settings: Dict[str, str]) -> Tuple[str, str, Optional[str], str]:
    """Returns (player_type, display_name, executable_path_or_intent, extra_args)."""
    player_type = (settings.get("media_player") or "vlc").strip().lower()
    if player_type not in ("vlc", "mpv", "custom"):
        player_type = "vlc"

    defaults = db.get_default_player_paths()
    extra_args = (settings.get("custom_player_args") or "").strip() if player_type == "custom" else ""

    if player_type == "mpv":
        raw_path = (settings.get("mpv_path") or defaults.get("mpv") or "").strip()
        display_name = "MPV"
    elif player_type == "custom":
        raw_path = (settings.get("custom_player_path") or "").strip()
        base_exe = os.path.splitext(os.path.basename(raw_path))[0] if raw_path else ""
        display_name = base_exe or "Custom Player"
    else:
        raw_path = (settings.get("vlc_path") or defaults.get("vlc") or "").strip()
        display_name = "VLC"

    if raw_path.startswith("android://"):
        return player_type, display_name, raw_path, extra_args

    if raw_path and os.path.isfile(raw_path):
        return player_type, display_name, raw_path, extra_args

    which_bin = platform_env.resolve_executable(
        player_type if player_type in ("vlc", "mpv") else raw_path,
        configured_path=raw_path,
    ) if raw_path or player_type in ("vlc", "mpv") else None
    if which_bin:
        return player_type, display_name, which_bin, extra_args

    return player_type, display_name, None, extra_args


def _launch_player_process(cmd: List[str]) -> None:
    kwargs: Dict[str, Any] = {"close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(cmd, **kwargs)


def _launch_android_intent(target_uri: str, intent_uri: str, sub_url: Optional[str] = None) -> Tuple[bool, str]:
    pkg = intent_uri.replace("android://", "").strip()
    cmd = ["am", "start", "-a", "android.intent.action.VIEW", "-d", target_uri, "-t", "video/*"]
    if pkg and pkg != "auto":
        cmd.extend(["-p", pkg])
    if sub_url:
        cmd.extend(["--es", "subtitles_location", sub_url])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if proc.returncode == 0:
            return True, f"Sent media intent to Android ({pkg or 'system chooser'})."
        return False, f"Android intent failed: {proc.stderr.strip() or proc.stdout.strip()}"
    except Exception as exc:
        return False, f"Android intent error: {exc}"


# ==================== DISCOVERY, SERIES & TORBOX CACHE HELPERS ====================

def _get_torbox_user_info(api_key: str) -> Dict[str, Any]:
    """Checks TorBox user plan (0 = Free, >0 = Essential/Standard/Pro) and Usenet support."""
    if not api_key:
        return {"ok": False, "is_pro": False, "plan": 0}
    try:
        resp = meta_eng.HTTP_SESSION.get(
            f"{TORBOX_API_BASE}/user/me",
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            params={"settings": "false"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data") or {}
            plan = int(data.get("plan") or 0)
            is_pro = plan == 2 or bool(data.get("is_subscribed") and plan >= 2)
            return {"ok": True, "is_pro": is_pro, "plan": plan, "email": data.get("email") or ""}
    except Exception:
        pass
    return {"ok": False, "is_pro": False, "plan": 0}


def _extract_infohash_from_magnet(magnet: str) -> Optional[str]:
    m = re.search(r"urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet or "", re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return None


def _resolve_imdb_id_from_tmdb(tmdb_id: Optional[int], api_key: str, media_type: str = "tv") -> Optional[str]:
    """Resolves IMDb ID (tt...) from TMDb TV or Movie ID so Stremio addons (Torrentio) return full results."""
    if not tmdb_id or not api_key:
        return None
    endpoint_type = "movie" if media_type == "movie" else "tv"
    try:
        r = meta_eng.HTTP_SESSION.get(
            f"https://api.themoviedb.org/3/{endpoint_type}/{tmdb_id}/external_ids",
            params={"api_key": api_key.strip()},
            timeout=6,
        )
        if r.status_code == 200:
            imdb_id = (r.json().get("imdb_id") or "").strip()
            if imdb_id.startswith("tt"):
                return imdb_id
    except Exception:
        pass
    return None


def _extract_quality_tags(title: str) -> List[str]:
    """Extracts clean release quality badges (2160p, 1080p, BluRay, HDR, Uncensored, Sub) from release title."""
    t = (title or "").lower()
    tags: List[str] = []
    if any(k in t for k in ("2160p", "4k", "uhd")):
        tags.append("2160p 4K")
    elif "1080p" in t or "fhd" in t:
        tags.append("1080p")
    elif "720p" in t:
        tags.append("720p")

    if "remux" in t:
        tags.append("REMUX")
    elif any(k in t for k in ("bluray", "blu-ray", "bdrip")):
        tags.append("BluRay")
    elif any(k in t for k in ("web-dl", "webdl", "webrip")):
        tags.append("WEB-DL")

    if any(k in t for k in ("hdr", "dv", "dolby vision")):
        tags.append("HDR")
    if any(k in t for k in ("hevc", "x265", "h265", "av1")):
        tags.append("HEVC")
    if any(k in t for k in ("uncensored", "decensored", "leaked")):
        tags.append("Uncensored")
    if any(k in t for k in ("sub", "english", "eng", "indo", "multi")):
        tags.append("Sub")
    return tags[:4]


def _search_public_torrent_candidates(
    series_title: str,
    season_num: int,
    missing_episodes: List[int],
    category: str,
    tmdb_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Searches multiple public indexers (Torrentio via IMDb/TMDb, EZTV API, APIBay/TPB, and Nyaa RSS)
    to gather magnet links / infohashes for TorBox cache verification.
    """
    candidates: List[Dict[str, Any]] = []
    seen_hashes = set()
    settings = db.get_settings()
    tmdb_key = (settings.get("tmdb_api_key") or "").strip()
    imdb_id = _resolve_imdb_id_from_tmdb(tmdb_id, tmdb_key, media_type="tv") if tmdb_id else None

    def _add_candidate(ih: str, title_txt: str, ep: Optional[int], source: str) -> None:
        clean_ih = (ih or "").lower().strip()
        if not clean_ih or len(clean_ih) < 32 or clean_ih in seen_hashes:
            return
        seen_hashes.add(clean_ih)
        clean_title = (title_txt or f"{series_title} S{season_num:02d}").split("\n")[0].strip()
        magnet = f"magnet:?xt=urn:btih:{clean_ih}&dn={quote_plus(clean_title)}"
        candidates.append(
            {
                "kind": "torrent",
                "hash": clean_ih,
                "title": clean_title,
                "episode": ep,
                "magnet": magnet,
                "source": source,
                "tags": _extract_quality_tags(clean_title),
            }
        )

    target_eps = missing_episodes[:4] if missing_episodes else [1]

    # 1. Torrentio Stremio Addon
    stremio_ids = [x for x in (imdb_id, f"tmdb:{tmdb_id}" if tmdb_id else None) if x]
    for sid in stremio_ids:
        found_for_sid = False
        for ep in target_eps:
            try:
                t_url = f"https://torrentio.strem.fun/stream/series/{sid}:{season_num}:{ep}.json"
                r = meta_eng.HTTP_SESSION.get(t_url, timeout=8)
                if r.status_code == 200:
                    streams = r.json().get("streams") or []
                    if streams:
                        found_for_sid = True
                    for st in streams[:8]:
                        _add_candidate(
                            st.get("infoHash") or "",
                            st.get("title") or st.get("name") or f"{series_title} S{season_num:02d}E{ep:02d}",
                            ep,
                            "Torrentio",
                        )
            except Exception:
                continue
        if found_for_sid:
            break

    # 2. EZTV API
    if imdb_id and imdb_id.startswith("tt"):
        numeric_imdb = imdb_id[2:]
        try:
            eztv_url = f"https://eztvx.to/api/get-torrents?imdb_id={numeric_imdb}&limit=50"
            r = meta_eng.HTTP_SESSION.get(eztv_url, timeout=8)
            if r.status_code == 200:
                for tor in (r.json().get("torrents") or []):
                    t_season = int(tor.get("season") or 0)
                    t_ep = int(tor.get("episode") or 0)
                    if t_season == season_num and (not missing_episodes or t_ep in missing_episodes or t_ep == 0):
                        _add_candidate(
                            tor.get("hash") or "",
                            tor.get("title") or tor.get("filename") or "",
                            t_ep if t_ep > 0 else None,
                            "EZTV",
                        )
        except Exception:
            pass

    # 3. APIBay (ThePirateBay JSON API)
    ep0 = target_eps[0]
    tpb_queries = [f"{series_title} S{season_num:02d}E{ep0:02d}", f"{series_title} S{season_num:02d}"]
    for q in tpb_queries:
        try:
            r = meta_eng.HTTP_SESSION.get(
                "https://apibay.org/q.php",
                params={"q": q, "cat": "205"},
                timeout=8,
            )
            if r.status_code == 200:
                rows = r.json()
                if isinstance(rows, list):
                    for row in rows[:10]:
                        ih = row.get("info_hash") or ""
                        if ih == "0000000000000000000000000000000000000000":
                            continue
                        t_name = row.get("name") or ""
                        _, ep_det = meta_eng._extract_season_episode(t_name)
                        _add_candidate(ih, t_name, ep_det, "TPB")
        except Exception:
            continue

    # 4. Nyaa.si RSS Search
    if category in ("anime", "donghua") or not candidates:
        nyaa_queries = [f"{series_title} S{season_num:02d}E{ep0:02d}", f"{series_title} {ep0:02d}"]
        for q in nyaa_queries[:2]:
            try:
                nyaa_url = f"https://nyaa.si/?page=rss&q={quote_plus(q)}&c=1_0&f=0"
                r = meta_eng.HTTP_SESSION.get(nyaa_url, timeout=8)
                if r.status_code == 200 and "<item>" in r.text:
                    soup = BeautifulSoup(r.text, "xml")
                    for item in soup.find_all("item")[:8]:
                        title_txt = item.title.get_text(strip=True) if item.title else ""
                        ih_tag = item.find("nyaa:infoHash")
                        ih = ih_tag.get_text(strip=True) if ih_tag else ""
                        _, ep_detected = meta_eng._extract_season_episode(title_txt)
                        _add_candidate(ih, title_txt, ep_detected, "Nyaa")
            except Exception:
                continue

    return candidates


def _search_universal_torrent_candidates(
    query: str,
    category: str = "general_movie",
    tmdb_id: Optional[int] = None,
    imdb_id: Optional[str] = None,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Multi-indexer cache search for the four Discovery categories plus library labels.
    """
    candidates: List[Dict[str, Any]] = []
    seen_hashes = set()
    settings = db.get_settings()
    tmdb_key = (settings.get("tmdb_api_key") or "").strip()

    clean_q = (query or "").strip()
    if not clean_q and not tmdb_id and not imdb_id:
        return []

    if tmdb_id and not imdb_id and category in ("general_movie", "tv_show", "anime", "donghua"):
        mtype = "movie" if category == "general_movie" else "tv"
        imdb_id = _resolve_imdb_id_from_tmdb(int(tmdb_id), tmdb_key, media_type=mtype)

    def _add(ih: str, title_txt: str, source: str, ep_num: Optional[int] = None) -> None:
        clean_ih = (ih or "").lower().strip()
        if not clean_ih or len(clean_ih) < 32 or clean_ih in seen_hashes:
            return
        seen_hashes.add(clean_ih)
        clean_title = (title_txt or clean_q or "Release").split("\n")[0].strip()
        magnet = f"magnet:?xt=urn:btih:{clean_ih}&dn={quote_plus(clean_title)}"
        candidates.append(
            {
                "kind": "torrent",
                "hash": clean_ih,
                "title": clean_title,
                "episode": ep_num,
                "magnet": magnet,
                "source": source,
                "tags": _extract_quality_tags(clean_title),
            }
        )

    # A. GENERAL MOVIE
    if category == "general_movie":
        stremio_ids = [x for x in (imdb_id, f"tmdb:{tmdb_id}" if tmdb_id else None) if x]
        for sid in stremio_ids:
            try:
                r = meta_eng.HTTP_SESSION.get(f"https://torrentio.strem.fun/stream/movie/{sid}.json", timeout=8)
                if r.status_code == 200:
                    for st in (r.json().get("streams") or [])[:12]:
                        _add(st.get("infoHash") or "", st.get("title") or st.get("name") or clean_q, "Torrentio")
                if candidates:
                    break
            except Exception:
                continue

        if clean_q:
            try:
                yts_q = imdb_id if (imdb_id and imdb_id.startswith("tt")) else clean_q
                r = meta_eng.HTTP_SESSION.get(
                    "https://yts.mx/api/v2/list_movies.json",
                    params={"query_term": yts_q, "limit": 8},
                    timeout=8,
                )
                if r.status_code == 200:
                    movies = (r.json().get("data") or {}).get("movies") or []
                    for mv in movies:
                        mv_title = mv.get("title_long") or mv.get("title") or clean_q
                        for tor in (mv.get("torrents") or []):
                            q_label = f"{mv_title} [{tor.get('quality', '')} {tor.get('type', '')} {tor.get('size', '')}]".strip()
                            _add(tor.get("hash") or "", q_label, "YTS")
            except Exception:
                pass

        if clean_q:
            q_search = f"{clean_q} {year}" if year and str(year) not in clean_q else clean_q
            try:
                r = meta_eng.HTTP_SESSION.get("https://apibay.org/q.php", params={"q": q_search, "cat": "201"}, timeout=8)
                if r.status_code == 200 and isinstance(r.json(), list):
                    for row in r.json()[:12]:
                        ih = row.get("info_hash") or ""
                        if ih != "0000000000000000000000000000000000000000":
                            _add(ih, row.get("name") or "", "TPB")
            except Exception:
                pass

    # B. TV SHOW / ANIME / DONGHUA
    elif category in ("tv_show", "anime", "donghua"):
        s_num = int(season or 1)
        ep_list = [int(episode)] if episode else [1]
        series_cands = _search_public_torrent_candidates(
            clean_q, s_num, ep_list, category, tmdb_id=int(tmdb_id) if tmdb_id else None
        )
        for sc in series_cands:
            _add(sc["hash"], sc["title"], sc["source"], ep_num=sc.get("episode"))

        if len(candidates) < 4 and clean_q:
            if category in ("anime", "donghua"):
                try:
                    r = meta_eng.HTTP_SESSION.get(
                        f"https://nyaa.si/?page=rss&q={quote_plus(clean_q)}&c=1_0&f=0",
                        timeout=8,
                    )
                    if r.status_code == 200 and "<item>" in r.text:
                        soup = BeautifulSoup(r.text, "xml")
                        for item in soup.find_all("item")[:10]:
                            t_txt = item.title.get_text(strip=True) if item.title else ""
                            ih_tag = item.find("nyaa:infoHash")
                            ih = ih_tag.get_text(strip=True) if ih_tag else ""
                            _, ep_det = meta_eng._extract_season_episode(t_txt)
                            _add(ih, t_txt, "Nyaa", ep_num=ep_det)
                except Exception:
                    pass
            try:
                r = meta_eng.HTTP_SESSION.get("https://apibay.org/q.php", params={"q": clean_q, "cat": "205"}, timeout=8)
                if r.status_code == 200 and isinstance(r.json(), list):
                    for row in r.json()[:8]:
                        ih = row.get("info_hash") or ""
                        if ih != "0000000000000000000000000000000000000000":
                            t_txt = row.get("name") or ""
                            _, ep_det = meta_eng._extract_season_episode(t_txt)
                            _add(ih, t_txt, "TPB", ep_num=ep_det)
            except Exception:
                pass

    return candidates


def _search_public_nzb_candidates(
    series_title: str,
    season_num: Optional[int] = None,
    missing_episodes: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Searches public NZB indexers (NZBIndex RSS) for TorBox Pro Usenet cache checks."""
    candidates: List[Dict[str, Any]] = []
    seen_hashes = set()
    q_list: List[str] = []
    if season_num is not None:
        q_list.append(f"{series_title} S{season_num:02d}")
        if missing_episodes:
            q_list.insert(0, f"{series_title} S{season_num:02d}E{missing_episodes[0]:02d}")
    else:
        q_list.append(series_title)

    for q in q_list[:2]:
        try:
            url = f"https://nzbindex.com/rss/?q={quote_plus(q)}&sort=agedesc&max=10"
            r = meta_eng.HTTP_SESSION.get(url, timeout=8)
            if r.status_code == 200 and "<item>" in r.text:
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item")[:8]:
                    title_txt = item.title.get_text(strip=True) if item.title else ""
                    enc = item.find("enclosure")
                    nzb_url = enc.get("url") if enc else (item.link.get_text(strip=True) if item.link else "")
                    if not nzb_url or not title_txt:
                        continue
                    md5_hash = hashlib.md5(title_txt.encode("utf-8")).hexdigest().lower()
                    if md5_hash in seen_hashes:
                        continue
                    seen_hashes.add(md5_hash)
                    _, ep_detected = meta_eng._extract_season_episode(title_txt)
                    candidates.append(
                        {
                            "kind": "usenet",
                            "hash": md5_hash,
                            "title": title_txt,
                            "episode": ep_detected,
                            "nzb_url": nzb_url,
                            "source": "NZBIndex",
                            "tags": _extract_quality_tags(title_txt),
                        }
                    )
        except Exception:
            continue
    return candidates


def _check_torbox_cached_hashes(api_key: str, kind: str, hashes: List[str]) -> set:
    """Queries TorBox /torrents/checkcached or /usenet/checkcached and returns set of cached hashes."""
    cached = set()
    if not api_key or not hashes:
        return cached
    endpoint = "torrents/checkcached" if kind == "torrent" else "usenet/checkcached"
    try:
        resp = meta_eng.HTTP_SESSION.get(
            f"{TORBOX_API_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            params={"hash": ",".join(hashes[:25]), "format": "list"},
            timeout=12,
        )
        if resp.status_code == 200:
            data = resp.json().get("data") or []
            if isinstance(data, list):
                for entry in data:
                    h = (entry.get("hash") or "").lower().strip()
                    if h:
                        cached.add(h)
            elif isinstance(data, dict):
                for k, v in data.items():
                    if v:
                        cached.add(k.lower().strip())
    except Exception:
        pass
    return cached


@app.route("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)


@app.route("/v2")
def index_v2():
    """Netflix/Prime-style UI preview. Tidak menggantikan index.html."""
    return render_template("home_v2.html", app_version=APP_VERSION)


@app.route("/api/bootstrap", methods=["GET"])
def api_bootstrap():
    db.verify_subtitle_files()
    pl_scope = request.args.get("playlist_scope", "unassigned")
    items = db.query_media_items(
        search=request.args.get("search", ""),
        source=request.args.get("source", "all"),
        meta_status=request.args.get("meta_status", "all"),
        sub_status=request.args.get("sub_status", "all"),
        category=request.args.get("category", "all"),
        watched_status=request.args.get("watched_status", "all"),
        genre=request.args.get("genre", ""),
        sort_by=request.args.get("sort_by", "newest"),
        playlist_scope=pl_scope,
        classification_status=request.args.get("classification_status", "all"),
    )
    _attach_subtitle_track_summaries(items)
    return jsonify(
        {
            "ok": True,
            "version": APP_VERSION,
            "env": _get_environment_info(),
            "stats": db.get_telemetry_stats(),
            "items": items,
            "playlists": db.get_all_playlists(),
            "task": TASK_MGR.status(),
        }
    )


# ==================== UNIVERSAL DISCOVERY & SEARCH ROUTES ====================

DISCOVERY_CATEGORIES = ("general_movie", "tv_show", "anime", "donghua")


@app.route("/api/discover", methods=["GET"])
def api_discover():
    """Catalog feeds for the four categories Roxxy has no good feed for.

    JAV, English Adult and Indo discovery/search deliberately live in Roxxy;
    this endpoint rejects them instead of keeping a second implementation.
    """
    category = (request.args.get("category") or "general_movie").strip().lower()
    if category not in DISCOVERY_CATEGORIES:
        return jsonify({
            "ok": False,
            "error": (
                "Discovery hanya mendukung General Movie, TV Show, Anime, dan Donghua. "
                "Gunakan Video Feeds untuk JAV, English Adult, dan Indo."
            ),
        }), 400
    feed = (request.args.get("feed") or "trending").strip().lower()
    query = (request.args.get("query") or "").strip()
    try:
        limit = min(max(int(request.args.get("limit") or 50), 1), 300)
    except ValueError:
        limit = 50

    cache_key = f"{category}:{feed}:{query.lower()}:{limit}"
    now_ts = time.time()
    with _DISCOVER_CACHE_LOCK:
        cached_entry = _DISCOVER_CACHE.get(cache_key)
        if cached_entry and (now_ts - cached_entry[0]) <= DISCOVER_CACHE_TTL:
            cached_results = cached_entry[1]
            return jsonify({
                "ok": True,
                "category": category,
                "feed": feed,
                "count": len(cached_results),
                "results": cached_results,
                "cached": True,
            })

    settings = db.get_settings()
    tmdb_key = (settings.get("tmdb_api_key") or "").strip()
    results: List[Dict[str, Any]] = []

    try:
        # 1. GENERAL MOVIE & TV SHOW (TMDb paginated, capped at 300 items)
        if category in ("general_movie", "tv_show"):
            if not tmdb_key:
                return jsonify({"ok": False, "error": "TMDb API Key is not configured in Settings."}), 400

            mtype = "tv" if category == "tv_show" else "movie"
            if query:
                url = f"https://api.themoviedb.org/3/search/{mtype}"
                base_params: Dict[str, Any] = {"api_key": tmdb_key, "query": query, "include_adult": "false"}
            elif feed == "popular":
                url = f"https://api.themoviedb.org/3/{mtype}/popular"
                base_params = {"api_key": tmdb_key}
            elif feed == "top_rated":
                url = f"https://api.themoviedb.org/3/{mtype}/top_rated"
                base_params = {"api_key": tmdb_key}
            else:
                url = f"https://api.themoviedb.org/3/trending/{mtype}/week"
                base_params = {"api_key": tmdb_key}

            seen_tmdb_ids = set()
            max_pages = (limit // 20) + 2
            for page in range(1, max_pages + 1):
                if len(results) >= limit:
                    break
                page_params = {**base_params, "page": page}
                r = meta_eng.HTTP_SESSION.get(url, params=page_params, timeout=12)
                if r.status_code != 200:
                    break
                payload = r.json()
                rows = payload.get("results") or []
                if not rows:
                    break

                for row in rows:
                    row_id = row.get("id")
                    if row_id in seen_tmdb_ids:
                        continue
                    seen_tmdb_ids.add(row_id)

                    title_val = row.get("title") or row.get("name") or ""
                    date_val = row.get("release_date") or row.get("first_air_date") or ""
                    year_val = int(date_val[:4]) if len(date_val) >= 4 and date_val[:4].isdigit() else None
                    poster_path = row.get("poster_path")
                    results.append(
                        {
                            "id": f"tmdb:{row_id}",
                            "tmdb_id": row_id,
                            "anilist_id": None,
                            "title": title_val,
                            "year": year_val,
                            "rating": round(float(row.get("vote_average") or 0), 1),
                            "poster_url": f"https://image.tmdb.org/t/p/w342{poster_path}" if poster_path else "",
                            "overview": (row.get("overview") or "")[:240],
                            "category": category,
                            "search_query": title_val,
                        }
                    )
                    if len(results) >= limit:
                        break

                total_pages = payload.get("total_pages") or 1
                if page >= total_pages:
                    break

        # 2. ANIME & DONGHUA (AniList GraphQL paginated, capped at 300 items)
        elif category in ("anime", "donghua"):
            country_code = "CN" if category == "donghua" else "JP"
            sort_enum = "SEARCH_MATCH" if query else (
                "SCORE_DESC" if feed == "top_rated" else (
                    "POPULARITY_DESC" if feed == "popular" else "TRENDING_DESC"
                )
            )
            gql = """
            query ($search: String, $country: CountryCode, $sort: [MediaSort], $perPage: Int, $page: Int) {
              Page(page: $page, perPage: $perPage) {
                pageInfo { hasNextPage }
                media(type: ANIME, search: $search, countryOfOrigin: $country, sort: $sort, isAdult: false) {
                  id
                  title { romaji english }
                  seasonYear
                  averageScore
                  episodes
                  description(asHtml: false)
                  coverImage { large }
                }
              }
            }
            """
            per_page = 50
            max_pages = (limit + per_page - 1) // per_page
            for page_no in range(1, max_pages + 1):
                variables: Dict[str, Any] = {
                    "country": country_code,
                    "sort": [sort_enum],
                    "perPage": per_page,
                    "page": page_no,
                }
                if query:
                    variables["search"] = query
                r = meta_eng.HTTP_SESSION.post(
                    "https://graphql.anilist.co",
                    json={"query": gql, "variables": variables},
                    timeout=12,
                )
                if r.status_code != 200:
                    break
                page_data = (r.json().get("data") or {}).get("Page") or {}
                media_list = page_data.get("media") or []
                for m in media_list:
                    t_obj = m.get("title") or {}
                    title_val = t_obj.get("english") or t_obj.get("romaji") or ""
                    score = m.get("averageScore")
                    desc_clean = re.sub(r"<[^>]+>", "", m.get("description") or "")[:240]
                    results.append(
                        {
                            "id": f"anilist:{m.get('id')}",
                            "tmdb_id": None,
                            "anilist_id": m.get("id"),
                            "title": title_val,
                            "year": m.get("seasonYear"),
                            "rating": round(float(score) / 10.0, 1) if score else 0.0,
                            "episodes": m.get("episodes"),
                            "poster_url": (m.get("coverImage") or {}).get("large") or "",
                            "overview": desc_clean,
                            "category": category,
                            "search_query": title_val,
                        }
                    )
                    if len(results) >= limit:
                        break
                if len(results) >= limit or not (page_data.get("pageInfo") or {}).get("hasNextPage"):
                    break

        final_results = results[:limit]
        if final_results:
            with _DISCOVER_CACHE_LOCK:
                _DISCOVER_CACHE[cache_key] = (time.time(), final_results)
                if len(_DISCOVER_CACHE) > 80:
                    oldest_key = next(iter(_DISCOVER_CACHE))
                    del _DISCOVER_CACHE[oldest_key]

        return jsonify({
            "ok": True,
            "category": category,
            "feed": feed,
            "count": len(final_results),
            "results": final_results,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Discovery feed error: {exc}"}), 500


_PROXY_IMAGE_MAX_BYTES = 5 * 1024 * 1024
_PROXY_MAX_REDIRECTS = 5


def _poster_host_allowed(host: str) -> bool:
    """Optional suffix allowlist via TBH_POSTER_HOST_ALLOWLIST (comma separated).

    Off by default so provider CDN changes cannot silently break every cover.
    Set it to the provider and CDN suffixes you actually rely on to shrink the
    proxy from "any public image" to "these hosts only".
    """
    configured = (os.environ.get("TBH_POSTER_HOST_ALLOWLIST") or "").strip()
    if not configured:
        return True
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for entry in configured.split(","):
        candidate = entry.strip().lower().lstrip("*.").rstrip(".")
        if candidate and (host == candidate or host.endswith("." + candidate)):
            return True
    return False


@app.route("/api/discover/poster-proxy", methods=["GET"])
def api_discover_poster_proxy():
    """
    Proxies and caches external discovery cover images with valid Referer headers so
    hotlink-protected covers render cleanly in the browser.

    The Discovery UI now hotlinks TMDb and AniList covers directly, but the endpoint
    stays registered and hardened for providers whose CDNs block hotlinking.
    """
    raw_url = (request.args.get("url") or "").strip()
    if not raw_url or not raw_url.startswith(("http://", "https://")):
        return abort(400)

    allowed, _reason = meta_eng._poster_url_is_public(raw_url)
    if not allowed:
        return abort(403)
    if not _poster_host_allowed(urlparse(raw_url).hostname or ""):
        return abort(403)

    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if not host:
        return abort(403)

    cache_dir = os.path.join(db.CACHE_DIR, "discover_posters")
    os.makedirs(cache_dir, exist_ok=True)
    url_hash = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()[:24]

    for ext in (".jpg", ".webp", ".png"):
        cached_file = os.path.join(cache_dir, f"{url_hash}{ext}")
        if os.path.isfile(cached_file) and os.path.getsize(cached_file) > 256:
            mime = "image/webp" if ext == ".webp" else ("image/png" if ext == ".png" else "image/jpeg")
            return send_file(cached_file, mimetype=mime, max_age=86400)

    referer = f"{parsed.scheme}://{parsed.netloc}/"
    if any(k in host for k in ("missav", "fourhoi", "surrit")):
        referer = "https://missav.ws/"
    elif "dmm.co.jp" in host:
        referer = "https://www.dmm.co.jp/"
    elif "javdatabase" in host:
        referer = "https://www.javdatabase.com/"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": referer,
    }

    current_url = raw_url
    resp = None
    # One-shot session: poster hosts vary per cover, so keeping them in the shared
    # HTTP_SESSION pool only accumulates idle sockets. Covers are cached for a day,
    # so the extra handshake is negligible.
    session = requests.Session()
    try:
        for _hop in range(_PROXY_MAX_REDIRECTS + 1):
            allowed, _reason = meta_eng._poster_url_is_public(current_url)
            if not allowed:
                return abort(403)
            if not _poster_host_allowed(urlparse(current_url).hostname or ""):
                return abort(403)
            resp = session.get(
                current_url, headers=headers, timeout=(6, 12),
                allow_redirects=False, stream=True,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                location = (resp.headers.get("Location") or "").strip()
                resp.close()
                resp = None
                if not location:
                    return abort(502)
                # Each hop is re-validated: a public host may redirect to metadata.
                current_url = urljoin(current_url, location)
                continue
            break
        else:
            return abort(502)

        if resp.status_code != 200:
            return abort(404)
        content_type = (resp.headers.get("Content-Type") or "image/jpeg").lower()
        if "image" not in content_type and "octet-stream" not in content_type:
            return abort(415)

        declared = (resp.headers.get("Content-Length") or "").strip()
        if declared.isdigit() and int(declared) > _PROXY_IMAGE_MAX_BYTES:
            return abort(413)

        data, _read_error = meta_eng._read_poster_response(resp, max_bytes=_PROXY_IMAGE_MAX_BYTES)
        if data is None:
            return abort(413)
        if len(data) < 256:
            return abort(404)

        ext = ".webp" if "webp" in content_type else (".png" if "png" in content_type else ".jpg")
        out_file = os.path.join(cache_dir, f"{url_hash}{ext}")
        with open(out_file, "wb") as f:
            f.write(data)
        return send_file(out_file, mimetype=content_type.split(";")[0], max_age=86400)
    except HTTPException:
        raise
    except (RequestException, Timeout):
        return abort(502)
    except Exception:
        return abort(502)
    finally:
        if resp is not None:
            resp.close()
        session.close()


@app.route("/api/discover/search-cache", methods=["POST"])
def api_discover_search_cache():
    """
    TorBox Instant Cache search across library categories, for new media or quality upgrades.
    """
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    category = (payload.get("category") or "general_movie").strip()
    tmdb_id = payload.get("tmdb_id")
    imdb_id = (payload.get("imdb_id") or "").strip() or None
    year = payload.get("year")
    season = payload.get("season")
    episode = payload.get("episode")

    if not query and not tmdb_id and not imdb_id:
        return jsonify({"ok": False, "error": "Search query or metadata ID is required."}), 400

    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "TorBox API Key belum diatur di Settings."}), 400

    user_info = _get_torbox_user_info(api_key)
    is_pro = bool(user_info.get("is_pro"))

    torrent_cands = _search_universal_torrent_candidates(
        query=query,
        category=category,
        tmdb_id=int(tmdb_id) if tmdb_id else None,
        imdb_id=imdb_id,
        year=int(year) if year else None,
        season=int(season) if season else None,
        episode=int(episode) if episode else None,
    )
    nzb_cands = (
        _search_public_nzb_candidates(
            query,
            season_num=int(season) if season else None,
            missing_episodes=[int(episode)] if episode else None,
        )
        if is_pro and query
        else []
    )

    cached_torrent_hashes = _check_torbox_cached_hashes(
        api_key, "torrent", [c["hash"] for c in torrent_cands]
    )
    cached_nzb_hashes = (
        _check_torbox_cached_hashes(api_key, "usenet", [c["hash"] for c in nzb_cands])
        if is_pro and nzb_cands
        else set()
    )

    results: List[Dict[str, Any]] = []
    for c in torrent_cands:
        results.append({**c, "cached": c["hash"] in cached_torrent_hashes})
    for c in nzb_cands:
        results.append({**c, "cached": c["hash"] in cached_nzb_hashes})

    def _res_score(r: Dict[str, Any]) -> int:
        t = (r.get("title") or "").lower()
        if "2160p" in t or "4k" in t:
            return 0
        if "1080p" in t or "fhd" in t:
            return 1
        if "720p" in t:
            return 2
        return 3

    results.sort(
        key=lambda r: (
            0 if r["cached"] else 1,
            _res_score(r),
            0 if r["kind"] == "torrent" else 1,
        )
    )

    cached_count = sum(1 for r in results if r["cached"])
    return jsonify(
        {
            "ok": True,
            "is_pro": is_pro,
            "plan": user_info.get("plan", 0),
            "cached_count": cached_count,
            "total_checked": len(results),
            "results": results[:25],
        }
    )


@app.route("/api/series/episodes-status", methods=["GET"])
def api_series_episodes_status():
    """
    Computes season structure and completeness status for a TV Show, Anime, or Donghua.
    """
    series_title = (request.args.get("series_title") or "").strip()
    category = (request.args.get("category") or "tv_show").strip()
    season_arg = (request.args.get("season") or "").strip()
    force_refresh = request.args.get("refresh") == "1"

    if not series_title:
        return jsonify({"ok": False, "error": "series_title is required."}), 400

    all_series_items = [
        it for it in db.query_media_items(category=category, playlist_scope="all")
        if (it.get("series_title") or "").strip().lower() == series_title.lower()
    ]

    tmdb_id = next((it.get("tmdb_id") for it in all_series_items if it.get("tmdb_id")), None)
    anilist_id = next((it.get("anilist_id") for it in all_series_items if it.get("anilist_id")), None)

    cache_key = f"{category}:{series_title.lower()}"
    cached = None if force_refresh else db.get_cached_series_seasons(cache_key)
    if cached and cached.get("seasons"):
        official_seasons = cached["seasons"]
        ext_id = cached.get("external_id")
    else:
        settings = db.get_settings()
        ext_id, official_seasons = meta_eng.fetch_official_series_seasons(
            series_title,
            category=category,
            tmdb_api_key=settings.get("tmdb_api_key", ""),
            tmdb_id=tmdb_id,
            anilist_id=anilist_id,
        )
        if official_seasons:
            db.save_cached_series_seasons(cache_key, category, official_seasons, external_id=ext_id)

    season_map: Dict[int, Dict[str, Any]] = {}
    for s in official_seasons:
        sn = int(s.get("season_number") or 1)
        season_map[sn] = dict(s)

    for it in all_series_items:
        sn = int(it.get("season_num") or 1)
        if sn not in season_map:
            season_map[sn] = {
                "season_number": sn,
                "name": f"Season {sn}",
                "episode_count": 0,
                "year": it.get("year"),
                "source": "library",
            }

    if not season_map:
        season_map[1] = {"season_number": 1, "name": "Season 1", "episode_count": 0, "year": None, "source": "library"}

    seasons_sorted = [season_map[k] for k in sorted(season_map.keys())]
    active_season = int(season_arg) if season_arg.isdigit() else seasons_sorted[0]["season_number"]
    if active_season not in season_map:
        active_season = seasons_sorted[0]["season_number"]

    season_info = season_map[active_season]
    season_items = [
        it for it in all_series_items
        if int(it.get("season_num") or 1) == active_season
    ]
    season_items.sort(key=lambda x: (int(x.get("episode_num") or 9999), x.get("file_name") or ""))
    _attach_subtitle_track_summaries(season_items)

    present_ep_nums = sorted({int(it["episode_num"]) for it in season_items if it.get("episode_num") is not None})
    official_total = int(season_info.get("episode_count") or 0)
    max_present = max(present_ep_nums) if present_ep_nums else 0
    expected_total = official_total if official_total > 0 else max_present

    missing_episodes: List[int] = []
    if expected_total > 0:
        present_set = set(present_ep_nums)
        missing_episodes = [ep for ep in range(1, expected_total + 1) if ep not in present_set]

    is_complete = bool(expected_total > 0 and len(missing_episodes) == 0 and len(present_ep_nums) >= expected_total)
    status_message = (
        f"Anda memiliki full episode Season {active_season} ({len(present_ep_nums)}/{expected_total} Episodes)"
        if is_complete
        else (
            f"Season {active_season}: {len(present_ep_nums)}/{expected_total} Episodes tersedia ({len(missing_episodes)} missing)"
            if expected_total > 0
            else f"Season {active_season}: {len(season_items)} video tersedia di library"
        )
    )

    return jsonify(
        {
            "ok": True,
            "series_title": series_title,
            "category": category,
            "external_id": ext_id,
            "tmdb_id": tmdb_id,
            "anilist_id": anilist_id,
            "seasons": seasons_sorted,
            "active_season": active_season,
            "official_episode_count": official_total,
            "expected_total": expected_total,
            "present_episodes": present_ep_nums,
            "missing_episodes": missing_episodes,
            "is_complete": is_complete,
            "status_message": status_message,
            "items": season_items,
        }
    )


@app.route("/api/series/check-torbox-cache", methods=["POST"])
def api_series_check_torbox_cache():
    """
    On-demand TorBox Cache check (Torrents + Pro NZB/Usenet) for missing episodes.
    """
    payload = request.get_json(silent=True) or {}
    series_title = (payload.get("series_title") or "").strip()
    category = (payload.get("category") or "tv_show").strip()
    season_num = int(payload.get("season") or 1)
    missing_episodes = [int(x) for x in (payload.get("missing_episodes") or [])]
    tmdb_id = payload.get("tmdb_id")

    if not series_title:
        return jsonify({"ok": False, "error": "series_title is required."}), 400

    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "TorBox API Key belum diatur di Settings."}), 400

    user_info = _get_torbox_user_info(api_key)
    is_pro = bool(user_info.get("is_pro"))

    torrent_cands = _search_public_torrent_candidates(
        series_title, season_num, missing_episodes, category, tmdb_id=tmdb_id
    )
    nzb_cands = _search_public_nzb_candidates(series_title, season_num, missing_episodes) if is_pro else []

    cached_torrent_hashes = _check_torbox_cached_hashes(
        api_key, "torrent", [c["hash"] for c in torrent_cands]
    )
    cached_nzb_hashes = (
        _check_torbox_cached_hashes(api_key, "usenet", [c["hash"] for c in nzb_cands])
        if is_pro and nzb_cands
        else set()
    )

    results: List[Dict[str, Any]] = []
    for c in torrent_cands:
        is_cached = c["hash"] in cached_torrent_hashes
        results.append({**c, "cached": is_cached})

    for c in nzb_cands:
        is_cached = c["hash"] in cached_nzb_hashes
        results.append({**c, "cached": is_cached})

    missing_set = set(missing_episodes)
    results.sort(
        key=lambda r: (
            0 if r["cached"] else 1,
            0 if (r.get("episode") in missing_set) else 1,
            0 if r["kind"] == "torrent" else 1,
        )
    )

    cached_count = sum(1 for r in results if r["cached"])
    return jsonify(
        {
            "ok": True,
            "is_pro": is_pro,
            "plan": user_info.get("plan", 0),
            "cached_count": cached_count,
            "total_checked": len(results),
            "results": results[:20],
        }
    )


def _validate_usenet_link(raw_url: str) -> Tuple[bool, str]:
    """Validate an NZB link before TorBox Usenet fetches it.

    The URL travels to TorBox, not to this process, so this only rejects what
    can never work or should never be submitted: non-HTTP schemes, embedded
    credentials, over-long links, and local/private destinations. The Roxxy
    forwarder itself stays allowed, because a failed decode falls back to it.
    """
    text = str(raw_url or "").strip()
    if not text:
        return False, "NZB URL wajib diisi."
    if len(text) > 2048:
        return False, "NZB URL terlalu panjang (maksimum 2048 karakter)."
    try:
        parsed = urlparse(text)
    except Exception:
        return False, "NZB URL tidak valid."
    if parsed.scheme.lower() not in ("http", "https"):
        return False, "NZB URL harus memakai http atau https."
    if not parsed.hostname:
        return False, "NZB URL tidak memiliki nama host."
    if parsed.username or parsed.password:
        return False, "NZB URL yang memuat kredensial tidak diizinkan."
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        return False, "Host jaringan lokal tidak diizinkan."
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return False, "Alamat IP privat/reserved tidak diizinkan."
    return True, ""


@app.route("/api/torbox/add-cached", methods=["POST"])
def api_torbox_add_cached():
    """
    Adds a cached or uncached magnet torrent / NZB link directly to TorBox Cloud and monitors completion.
    Supports optional `replace_media_id` for Quality Upgrade (deletes old physical file after import).
    """
    payload = request.get_json(silent=True) or {}
    kind = (payload.get("kind") or "torrent").strip().lower()
    magnet = (payload.get("magnet") or "").strip()
    nzb_url = (payload.get("nzb_url") or "").strip()
    name = (payload.get("title") or "").strip()
    series_title = (payload.get("series_title") or "").strip()
    category = (payload.get("category") or "").strip().lower()
    season = int(payload.get("season") or 1)
    replace_media_id = payload.get("replace_media_id")
    delete_remote_on_replace = bool(payload.get("delete_remote_on_replace", True))
    hint_metadata = _sanitize_import_metadata(payload.get("metadata"))
    hint_subtitles = _sanitize_import_subtitles(payload.get("subtitles"))
    expected_filename = os.path.basename(
        str(payload.get("expected_filename") or "").strip()
    )[:200]
    poster_url = str(
        payload.get("poster_url") or hint_metadata.get("poster") or ""
    ).strip()[:2048]
    # Addon-sourced downloads keep the category their manifest defines
    # (English Adult/OnlyFans, IndonesiaMovies, Donghua); only unspecific
    # submissions are re-classified from the hint.
    category = _apply_manifest_category_lock(
        category,
        hint_metadata,
        expected_filename,
        str(payload.get("parent_name") or ""),
    )

    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "TorBox API Key is not configured."}), 400

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        if kind == "usenet":
            safe, url_error = _validate_usenet_link(nzb_url)
            if not safe:
                return jsonify({"ok": False, "error": url_error}), 400
            multipart_fields = {
                "link": (None, nzb_url),
                "name": (None, name or "Usenet Download"),
            }
            resp = meta_eng.HTTP_SESSION.post(
                f"{TORBOX_API_BASE}/usenet/createusenetdownload",
                headers=headers,
                files=multipart_fields,
                timeout=30,
            )
        else:
            if not magnet:
                return jsonify({"ok": False, "error": "Magnet link is missing."}), 400
            if name and "&dn=" not in magnet.lower():
                magnet = f"{magnet}&dn={quote_plus(name)}"
            multipart_fields = {
                "magnet": (None, magnet),
                "seed": (None, "1"),
                "allow_zip": (None, "true"),
                "add_only_if_cached": (None, "false"),
            }
            if name:
                multipart_fields["name"] = (None, name)

            resp = meta_eng.HTTP_SESSION.post(
                f"{TORBOX_API_BASE}/torrents/createtorrent",
                headers=headers,
                files=multipart_fields,
                timeout=30,
            )

        body: Dict[str, Any] = {}
        if resp.content:
            try:
                body = resp.json()
            except Exception:
                body = {"detail": resp.text[:250].strip()}

        if resp.status_code in (200, 201) and body.get("success", True):
            data_obj = body.get("data") or {}
            created_id = str(
                data_obj.get("torrent_id")
                or data_obj.get("usenet_id")
                or data_obj.get("id")
                or ""
            )
            if created_id:
                TORBOX_MONITOR.track(
                    kind=kind,
                    item_id=created_id,
                    title=name,
                    series_title=series_title,
                    category=category,
                    season=season,
                    replace_media_id=int(replace_media_id) if replace_media_id else None,
                    delete_remote_on_replace=delete_remote_on_replace,
                    poster_url=poster_url,
                    metadata=hint_metadata or None,
                    subtitles=hint_subtitles or None,
                    expected_filename=expected_filename,
                )

            detail_msg = body.get("detail") or f"Added '{name or kind}' to TorBox Cloud!"
            upgrade_note = " (Quality Upgrade: versi lama akan dihapus setelah import selesai)" if replace_media_id else ""
            return jsonify(
                {
                    "ok": True,
                    "tracked_id": created_id,
                    "message": f"{detail_msg}{upgrade_note} — Auto-monitoring aktif.",
                }
            )

        err_detail = body.get("detail") or body.get("error") or f"TorBox API HTTP {resp.status_code}"
        return jsonify({"ok": False, "error": err_detail}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to add to TorBox: {exc}"}), 500


_WEB_DL_HOSTERS_TTL = 86400  # 24 hours
_WEB_DL_HOSTERS_CACHE: Dict[str, Any] = {"names": [], "fetched_at": 0.0, "error": ""}
_WEB_DL_HOSTERS_LOCK = threading.Lock()
_WEB_DL_SUBMIT_PER_HOUR = 60
_WEB_DL_SUBMIT_TIMES: List[float] = []
_WEB_DL_RECENT_LINKS: Dict[str, float] = {}
_WEB_DL_SUBMIT_LOCK = threading.Lock()
_WEB_DL_DEDUPE_SECONDS = 900


def _torbox_webdl_hosters(force: bool = False) -> Dict[str, Any]:
    """Return a 24-hour cached TorBox WebDL hoster list; never raise on API hiccups."""
    now = time.time()
    with _WEB_DL_HOSTERS_LOCK:
        snapshot = dict(_WEB_DL_HOSTERS_CACHE)
    if not force and snapshot["names"] and now - snapshot["fetched_at"] < _WEB_DL_HOSTERS_TTL:
        return snapshot

    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        snapshot["error"] = "TorBox API Key is not configured."
        return snapshot

    try:
        resp = meta_eng.HTTP_SESSION.get(
            f"{TORBOX_API_BASE}/webdl/hosters",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=25,
        )
        body: Any = {}
        if resp.content:
            try:
                body = resp.json()
            except Exception:
                body = {}
        rows = body.get("data") if isinstance(body, dict) else body
        if isinstance(rows, dict):
            rows = rows.get("hosters") or []
        names: List[str] = []
        for row in rows or []:
            name = row.get("name") if isinstance(row, dict) else row
            name = str(name or "").strip()
            if name:
                names.append(name)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"HTTP {resp.status_code}")

        fresh = {
            "names": sorted(set(names), key=str.lower),
            "fetched_at": now,
            "error": "" if names else "TorBox returned an empty hoster list.",
        }
        with _WEB_DL_HOSTERS_LOCK:
            _WEB_DL_HOSTERS_CACHE.update(fresh)
        return fresh
    except Exception as exc:
        snapshot["error"] = f"Daftar hoster TorBox gagal dimuat: {exc}"
        return snapshot


def _webdl_submit_guard(url: str) -> Optional[str]:
    """Enforce 60 successful submissions/hour and reject duplicate links."""
    now = time.time()
    cutoff = now - 3600
    with _WEB_DL_SUBMIT_LOCK:
        while _WEB_DL_SUBMIT_TIMES and _WEB_DL_SUBMIT_TIMES[0] < cutoff:
            _WEB_DL_SUBMIT_TIMES.pop(0)
        if len(_WEB_DL_SUBMIT_TIMES) >= _WEB_DL_SUBMIT_PER_HOUR:
            return "Batas 60 pengiriman WebDL per jam tercapai. Coba lagi nanti."
        for old_url, added_at in list(_WEB_DL_RECENT_LINKS.items()):
            if now - added_at > _WEB_DL_DEDUPE_SECONDS:
                del _WEB_DL_RECENT_LINKS[old_url]
        if url in _WEB_DL_RECENT_LINKS:
            return "Link ini baru saja dikirim ke TorBox. Tunggu sekitar 15 menit sebelum mengulang."
    return None


def _record_webdl_submit(url: str) -> None:
    now = time.time()
    with _WEB_DL_SUBMIT_LOCK:
        _WEB_DL_SUBMIT_TIMES.append(now)
        _WEB_DL_RECENT_LINKS[url] = now
        while len(_WEB_DL_RECENT_LINKS) > 200:
            _WEB_DL_RECENT_LINKS.pop(next(iter(_WEB_DL_RECENT_LINKS)))


def _webdl_api_error(detail: Any, status_code: int) -> str:
    text = str(detail or "").strip()
    lower = text.lower()
    if "not supported" in lower:
        return (
            "TorBox tidak mendukung link ini sebagai WebDL. Gunakan halaman resmi "
            "hoster/video, atau unduh lewat Local Engine."
        )
    if "paid" in lower and ("plan" in lower or "subscription" in lower):
        return "WebDL TorBox hanya tersedia untuk plan berbayar."
    if "100" in lower and "gb" in lower:
        return "Ukuran download melebihi batas 100 GB per item TorBox WebDL."
    if "limit" in lower and "download" in lower:
        return "Batas download TorBox tercapai. Coba lagi nanti atau hapus item lama."
    return text or f"TorBox API HTTP {status_code}"


@app.route("/api/torbox/webdl/info", methods=["GET"])
def api_torbox_webdl_info():
    """Expose cached hoster coverage and the provider types Roxxy can submit."""
    info = _torbox_webdl_hosters()
    return jsonify(
        {
            "ok": True,
            "hoster_count": len(info["names"]),
            "hosters": info["names"],
            "providers": webdl.supported_providers(),
            "cached_at": int(info["fetched_at"] or 0),
            "warning": info["error"] or "",
            "limits": {
                "submit_per_hour": _WEB_DL_SUBMIT_PER_HOUR,
                "max_download_gb": 100,
            },
        }
    )


@app.route("/api/torbox/webdl/add", methods=["POST"])
def api_torbox_webdl_add():
    """Submit a public page/hoster link to TorBox WebDL and auto-monitor it."""
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or "").strip()
    provider_id = str(payload.get("provider_id") or payload.get("id") or "").strip()
    raw_url = str(payload.get("url") or "").strip()
    title = str(payload.get("title") or "").strip()[:200]
    series_title = str(payload.get("series_title") or "").strip()[:200]
    category = str(payload.get("category") or "").strip().lower()
    try:
        season = max(1, int(payload.get("season") or 1))
    except (TypeError, ValueError):
        season = 1
    hint_metadata = _sanitize_import_metadata(payload.get("metadata"))
    hint_subtitles = _sanitize_import_subtitles(payload.get("subtitles"))
    expected_filename = os.path.basename(
        str(payload.get("expected_filename") or "").strip()
    )[:200]
    poster_url = str(
        payload.get("poster_url") or hint_metadata.get("poster") or ""
    ).strip()[:2048]
    if poster_url and not poster_url.lower().startswith(("http://", "https://")):
        poster_url = ""

    url = webdl.resolve_page_url(provider, provider_id) if provider else ""
    if not url:
        url = raw_url
    if provider and not url and not raw_url:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        f"Provider '{provider}' belum punya pola URL WebDL yang terverifikasi. "
                        "Tempel link halaman secara manual."
                    ),
                }
            ),
            400,
        )

    safe, url_error = webdl.validate_webdl_url(url)
    if not safe:
        return jsonify({"ok": False, "error": url_error}), 400

    if not title:
        title = str(hint_metadata.get("name") or "").strip()[:200]
    if not title:
        title = f"WebDL {urlparse(url).hostname or 'link'}"[:200]

    # Addon-sourced downloads keep the category their manifest defines
    # (English Adult/OnlyFans, IndonesiaMovies, Donghua); only unspecific
    # submissions are re-classified from the hint.
    category = _apply_manifest_category_lock(
        category,
        hint_metadata,
        expected_filename,
        str(payload.get("parent_name") or ""),
    )

    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "TorBox API Key is not configured."}), 400

    guard_error = _webdl_submit_guard(url)
    if guard_error:
        return jsonify({"ok": False, "error": guard_error}), 429

    try:
        resp = meta_eng.HTTP_SESSION.post(
            f"{TORBOX_API_BASE}/webdl/createwebdownload",
            headers={"Authorization": f"Bearer {api_key}"},
            files={
                "link": (None, url),
                "name": (None, title),
                "as_queued": (None, "false"),
                "add_only_if_cached": (None, "false"),
            },
            timeout=60,
        )
        body: Dict[str, Any] = {}
        if resp.content:
            try:
                body = resp.json()
            except Exception:
                body = {"detail": resp.text[:250].strip()}

        if resp.status_code in (200, 201) and body.get("success", True):
            data_obj = body.get("data") or {}
            created_id = str(
                data_obj.get("webdownload_id")
                or data_obj.get("webdl_id")
                or data_obj.get("id")
                or ""
            )
            if created_id:
                TORBOX_MONITOR.track(
                    kind="webdl",
                    item_id=created_id,
                    title=title,
                    series_title=series_title,
                    category=category,
                    season=season,
                    poster_url=poster_url,
                    metadata=hint_metadata or None,
                    subtitles=hint_subtitles or None,
                    expected_filename=expected_filename,
                )
                _record_webdl_submit(url)

            detail_msg = str(body.get("detail") or "").strip() or "Link dikirim ke TorBox WebDL."
            return jsonify(
                {
                    "ok": True,
                    "tracked_id": created_id,
                    "message": f"{detail_msg} — Auto-monitoring aktif.",
                }
            )

        err_detail = body.get("detail") or body.get("error")
        return (
            jsonify({"ok": False, "error": _webdl_api_error(err_detail, resp.status_code)}),
            400,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to add WebDL to TorBox: {exc}"}), 500


@app.route("/api/torbox/watch-status", methods=["GET"])
def api_torbox_watch_status():
    """Returns progress of monitored TorBox downloads and any newly auto-imported items."""
    return jsonify({"ok": True, **TORBOX_MONITOR.status()})


@app.route("/api/players/detect", methods=["GET"])
def api_players_detect():
    players = detect_installed_players()
    return jsonify(
        {
            "ok": True,
            "os_platform": db.detect_runtime_os(),
            "players": players,
            "default_paths": db.get_default_player_paths(),
        }
    )


@app.route("/api/system/browse-file", methods=["POST"])
def api_system_browse_file():
    """Opens native OS file picker when GUI is available; returns headless flag otherwise."""
    payload = request.get_json(silent=True) or {}
    title = payload.get("title") or "Select Media Player Executable"
    env_info = _get_environment_info()
    if env_info["is_headless"] or env_info["os_platform"] == "android":
        return jsonify({"ok": False, "headless": True, "error": "Native file dialog unavailable in headless/Android mode."})

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if env_info["os_platform"] == "windows":
            ftypes = [("Executable Files", "*.exe;*.bat;*.cmd"), ("All Files", "*.*")]
        elif env_info["os_platform"] == "darwin":
            ftypes = [("Applications & Binaries", "*.app;*"), ("All Files", "*.*")]
        else:
            ftypes = [("All Files", "*")]
        selected = filedialog.askopenfilename(title=title, filetypes=ftypes)
        root.destroy()

        if selected:
            if env_info["os_platform"] == "darwin" and selected.endswith(".app") and os.path.isdir(selected):
                macos_bin_dir = os.path.join(selected, "Contents", "MacOS")
                if os.path.isdir(macos_bin_dir):
                    bins = [f for f in os.listdir(macos_bin_dir) if os.path.isfile(os.path.join(macos_bin_dir, f))]
                    if bins:
                        selected = os.path.join(macos_bin_dir, bins[0])
            return jsonify({"ok": True, "path": os.path.normpath(selected)})
        return jsonify({"ok": True, "path": ""})
    except Exception as exc:
        return jsonify({"ok": False, "headless": True, "error": f"Dialog unavailable: {exc}"})


@app.route("/api/system/browse-folder", methods=["POST"])
def api_system_browse_folder():
    """Opens native OS folder picker for Local Scan Folders."""
    env_info = _get_environment_info()
    if env_info["is_headless"] or env_info["os_platform"] == "android":
        return jsonify({"ok": False, "headless": True, "error": "Native folder dialog unavailable in headless/Android mode."})

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Select Media Folder to Scan")
        root.destroy()
        return jsonify({"ok": True, "path": os.path.normpath(selected) if selected else ""})
    except Exception as exc:
        return jsonify({"ok": False, "headless": True, "error": f"Dialog unavailable: {exc}"})


@app.route("/api/system/list-fs", methods=["GET"])
def api_system_list_fs():
    """Fallback server-side filesystem browser for headless servers, Docker, or Android Termux."""
    mode = request.args.get("mode", "file")
    req_path = (request.args.get("path") or "").strip()

    if not req_path:
        if os.name == "nt":
            import string
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return jsonify({"ok": True, "current_path": "", "parent_path": "", "entries": [{"name": d, "path": d, "is_dir": True} for d in drives]})
        req_path = os.path.expanduser("~")
        if not os.path.isdir(req_path):
            req_path = "/"

    abs_path = os.path.abspath(req_path)
    if not os.path.isdir(abs_path):
        abs_path = os.path.dirname(abs_path) if os.path.exists(abs_path) else "/"

    parent_path = os.path.dirname(abs_path)
    if parent_path == abs_path:
        parent_path = ""

    entries = []
    try:
        for name in sorted(os.listdir(abs_path), key=lambda x: x.lower()):
            if name.startswith("."):
                continue
            full = os.path.join(abs_path, name)
            is_dir = os.path.isdir(full)
            if mode == "dir" and not is_dir:
                continue
            entries.append({"name": name, "path": full, "is_dir": is_dir})
    except PermissionError:
        return jsonify({"ok": False, "error": f"Permission denied: {abs_path}"}), 403

    return jsonify({"ok": True, "current_path": abs_path, "parent_path": parent_path, "entries": entries})



@app.route("/api/subtitle-quality/audit", methods=["POST"])
def api_subtitle_quality_audit():
    """Audit one media item synchronously for the Individual Detail Card."""
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("media_ids") or []
    if not isinstance(raw_ids, list) or len(raw_ids) > 1:
        return jsonify({"ok": False, "error": "Individual audit accepts exactly one media ID."}), 400
    if not raw_ids:
        return jsonify({"ok": False, "error": "A media ID is required."}), 400
    result = _audit_one_media_item(raw_ids[0])
    if result is None:
        return jsonify({"ok": False, "error": "Media item not found."}), 404
    return jsonify({"ok": True, "results": [result], "audited": 1})


def _audit_one_media_item(raw_id: Any) -> Optional[Dict[str, Any]]:
    try:
        media_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    item = db.get_media_item(media_id)
    if not item:
        return None
    tracks = _get_item_subtitle_tracks(item)
    track_reports = []
    for track in tracks:
        path = str(track.get("subtitle_path") or "")
        language = str(track.get("language") or "").lower()
        if track.get("is_embedded") or path.startswith("embedded://"):
            track_reports.append({"language": language, "source": "embedded", "status": "not_applicable", "issues": ["embedded_track_not_file_audited"]})
            continue
        if not path or not os.path.isfile(path):
            track_reports.append({"language": language, "source": "external", "status": "invalid", "issues": ["subtitle_file_missing"]})
            continue
        report = subtitle_audit.audit_subtitle_file(path, expected_language=language)
        report.pop("path", None)
        track_reports.append({"language": language, "source": "external", **report})

    external = [r for r in track_reports if r.get("source") == "external"]
    if not external:
        overall = "not_applicable" if track_reports else "not_available"
    elif any(r.get("status") == "invalid" for r in external):
        overall = "invalid"
    elif any(r.get("status") in ("warning", "unsupported") for r in external):
        overall = "warning"
    else:
        overall = "healthy"
    return {
        "media_id": media_id,
        "title": item.get("title") or item.get("file_name") or f"Media {media_id}",
        "status": overall,
        "tracks": track_reports,
        "db_has_subtitle": bool(item.get("has_subtitle")),
        "verified_track_count": len(tracks),
    }


def _subtitle_quality_audit_worker(media_ids: List[int]) -> None:
    global _SUBTITLE_AUDIT_BATCH_RESULTS, _SUBTITLE_AUDIT_BATCH_SUMMARY
    results: List[Dict[str, Any]] = []
    counts = {key: 0 for key in ("healthy", "warning", "invalid", "not_applicable", "not_available")}
    db_flagged = 0
    track_backed = 0
    db_flag_only = 0
    track_only = 0
    total = len(media_ids)
    try:
        TASK_MGR.update(total=total, current=0, message=f"Subtitle Quality Audit (0/{total})...")
        for index, media_id in enumerate(media_ids, start=1):
            result = _audit_one_media_item(media_id)
            if result is not None:
                results.append(result)
                counts[result["status"]] = counts.get(result["status"], 0) + 1
                has_verified_tracks = result["verified_track_count"] > 0
                db_flagged += int(result["db_has_subtitle"])
                track_backed += int(has_verified_tracks)
                db_flag_only += int(result["db_has_subtitle"] and not has_verified_tracks)
                track_only += int(has_verified_tracks and not result["db_has_subtitle"])
            TASK_MGR.update(
                current=index,
                total=total,
                message=f"Subtitle Quality Audit ({index}/{total}): {result['title'] if result else 'Media unavailable'}",
                log_line=f"{index}/{total} | {result['status'] if result else 'SKIPPED'} | {result['title'] if result else media_id}",
            )
        summary = {
            "counts": counts,
            "audited": len(results),
            "db_has_subtitle_count": db_flagged,
            "verified_track_media_count": track_backed,
            "db_flag_without_verified_tracks": db_flag_only,
            "verified_tracks_without_db_flag": track_only,
        }
        with _SUBTITLE_AUDIT_BATCH_LOCK:
            _SUBTITLE_AUDIT_BATCH_RESULTS = results
            _SUBTITLE_AUDIT_BATCH_SUMMARY = summary
        TASK_MGR.finish(
            f"Subtitle Quality Audit completed: {len(results)} media | "
            f"{counts['healthy']} healthy, {counts['warning']} warning, {counts['invalid']} invalid, "
            f"{counts['not_applicable']} embedded-only, {counts['not_available']} without subtitle tracks."
        )
    except Exception as exc:
        TASK_MGR.finish(f"Subtitle Quality Audit failed: {exc}")


@app.route("/api/subtitle-quality/audit-visible", methods=["POST"])
def api_subtitle_quality_audit_visible():
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("media_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 500:
        return jsonify({"ok": False, "error": "media_ids must contain 1 to 500 IDs."}), 400
    media_ids = []
    for value in raw_ids:
        try:
            media_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not media_ids:
        return jsonify({"ok": False, "error": "No valid media IDs were supplied."}), 400
    if not TASK_MGR.start("subtitle_quality_audit", "Starting Subtitle Quality Audit...", total=len(media_ids)):
        return jsonify({"ok": False, "error": "Another background task is already running."}), 409
    with _SUBTITLE_AUDIT_BATCH_LOCK:
        _SUBTITLE_AUDIT_BATCH_RESULTS = []
        _SUBTITLE_AUDIT_BATCH_SUMMARY = {}
    threading.Thread(target=_subtitle_quality_audit_worker, args=(media_ids,), daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


@app.route("/api/subtitle-quality/audit-visible/results", methods=["GET"])
def api_subtitle_quality_audit_visible_results():
    with _SUBTITLE_AUDIT_BATCH_LOCK:
        return jsonify({"ok": True, "results": list(_SUBTITLE_AUDIT_BATCH_RESULTS), "summary": dict(_SUBTITLE_AUDIT_BATCH_SUMMARY)})


@app.route("/api/media", methods=["GET"])
def api_media_list():
    pl_scope = request.args.get("playlist_scope", "unassigned")
    if request.args.get("only_unassigned") == "1":
        pl_scope = "unassigned"
    items = db.query_media_items(
        search=request.args.get("search", ""),
        source=request.args.get("source", "all"),
        meta_status=request.args.get("meta_status", "all"),
        sub_status=request.args.get("sub_status", "all"),
        category=request.args.get("category", "all"),
        watched_status=request.args.get("watched_status", "all"),
        genre=request.args.get("genre", ""),
        sort_by=request.args.get("sort_by", "newest"),
        playlist_scope=pl_scope,
        classification_status=request.args.get("classification_status", "all"),
    )
    _attach_subtitle_track_summaries(items)
    return jsonify({"ok": True, "items": items, "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/poster", methods=["GET"])
def api_media_poster(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return ("", 404)
    lp = item.get("local_poster_path")
    if lp and item.get("has_poster"):
        return send_file(lp, max_age=120)
    return ("", 404)


@app.route("/api/media/<int:media_id>/poster/retry", methods=["POST"])
def api_media_poster_retry(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404

    poster_url = item.get("poster_url")
    result = None
    source = item.get("poster_source") or item.get("metadata_source") or "metadata_provider"
    if poster_url:
        result = meta_eng.download_poster_image_result(
            poster_url, item.get("unique_key") or str(media_id), force_download=True
        )
    if not poster_url or (result and result.get("poster_status") != "found" and not item.get("poster_is_manual")):
        # If the saved URL is stale or missing, refresh provider metadata but persist poster fields only.
        settings = db.get_settings()
        probe_ready_item = _attach_cloud_stream_for_probe(item, settings)
        enriched = meta_eng.enrich_media_item(probe_ready_item, settings)
        refreshed_url = enriched.get("poster_url")
        refreshed_result = {
            "local_poster_path": enriched.get("local_poster_path"),
            "poster_status": enriched.get("poster_status") or "missing",
            "poster_error": enriched.get("poster_error"),
        }
        if refreshed_result.get("poster_status") == "found" or not result or refreshed_url != poster_url:
            result = refreshed_result
            poster_url = refreshed_url
            source = enriched.get("poster_source") or enriched.get("metadata_source") or "metadata_provider"

    if result.get("poster_status") == "found" and result.get("local_poster_path"):
        db.update_media_poster_state(
            media_id, poster_url=poster_url,
            local_poster_path=result.get("local_poster_path"),
            status="manual" if item.get("poster_is_manual") else "found",
            error=None, source=source,
            allow_manual_overwrite=bool(item.get("poster_is_manual")),
            replace_url=(poster_url != item.get("poster_url")),
        )
    else:
        db.update_media_poster_state(
            media_id, poster_url=poster_url,
            status=result.get("poster_status") or "unreachable",
            error=result.get("poster_error"), source=source,
            allow_manual_overwrite=bool(item.get("poster_is_manual")),
            replace_url=(poster_url != item.get("poster_url")),
        )
    updated = db.get_media_item(media_id)
    return jsonify({"ok": result.get("poster_status") == "found", "result": result, "item": updated})


@app.route("/api/media/<int:media_id>/poster/manual", methods=["POST"])
def api_media_poster_manual(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404
    payload = request.get_json(silent=True) or {}
    poster_url = str(payload.get("poster_url") or "").strip()
    if not poster_url:
        return jsonify({"ok": False, "error": "Poster URL is required."}), 400
    result = meta_eng.download_poster_image_result(
        poster_url, item.get("unique_key") or str(media_id), force_download=True
    )
    if result.get("poster_status") != "found" or not result.get("local_poster_path"):
        return jsonify({"ok": False, "error": result.get("poster_error") or "Poster could not be validated.", "result": result}), 400
    db.update_media_poster_state(
        media_id, poster_url=poster_url,
        local_poster_path=result["local_poster_path"],
        status="manual", error=None, source="manual",
        is_manual=True, allow_manual_overwrite=True,
    )
    return jsonify({"ok": True, "item": db.get_media_item(media_id)})


@app.route("/api/media/<int:media_id>/poster/automatic", methods=["POST"])
def api_media_poster_automatic(media_id: int):
    if not db.get_media_item(media_id):
        return jsonify({"ok": False, "error": "Media not found."}), 404
    if not db.clear_manual_poster_selection(media_id):
        return jsonify({"ok": False, "error": "Unable to clear manual poster selection."}), 500
    return api_media_poster_retry(media_id)


@app.route("/api/media/<int:media_id>/poster/load-error", methods=["POST"])
def api_media_poster_load_error(media_id: int):
    if not db.get_media_item(media_id):
        return jsonify({"ok": False, "error": "Media not found."}), 404
    db.update_media_poster_state(
        media_id, status="load_error", error="Browser failed to render the cached poster.", source="browser"
    )
    return jsonify({"ok": True})


@app.route("/api/media/<int:media_id>/stream", methods=["GET"])
def api_media_http_stream(media_id: int):
    """Serves local video with HTTP Range support so remote LAN/mobile players can stream seamlessly."""
    item = db.get_media_item(media_id)
    if not item or item.get("source_type") != "local":
        return ("Local media not found", 404)
    fp = item.get("file_path")
    if not fp or not os.path.isfile(fp):
        return ("File missing on server", 404)
    return send_file(fp, conditional=True)


@app.route("/api/media/<int:media_id>/subtitle-file", methods=["GET"])
def api_media_http_subtitle(media_id: int):
    """Serves cached/sidecar subtitle over HTTP for remote LAN/mobile clients."""
    item = db.get_media_item(media_id)
    if not item:
        return ("Media not found", 404)
    sp = item.get("subtitle_path") or ""
    sp = dual_subs.resolve_external_path(sp, db.CACHE_DIR)
    if not sp or sp.startswith("embedded://") or not os.path.isfile(sp):
        return ("Subtitle file not available", 404)
    return send_file(sp, mimetype="text/plain; charset=utf-8", as_attachment=False)


@app.route("/api/media/<int:media_id>/subtitle-file/<language>", methods=["GET"])
def api_media_http_subtitle_language(media_id: int, language: str):
    """Serve one verified external subtitle track by language for compatible players."""
    language = (language or "").strip().lower()
    if language not in ("en", "id"):
        return ("Unsupported subtitle language", 400)
    item = db.get_media_item(media_id)
    if not item:
        return ("Media not found", 404)
    track = next((row for row in db.get_media_subtitles(media_id)
                  if row.get("language") == language and row.get("verified")), None)
    if not track:
        return ("Subtitle language not available", 404)
    sp = track.get("subtitle_path") or ""
    sp = dual_subs.resolve_external_path(sp, db.CACHE_DIR)
    if not sp or sp.startswith("embedded://") or not os.path.isfile(sp):
        return ("External subtitle file not available", 404)
    return send_file(sp, mimetype="text/plain; charset=utf-8", as_attachment=False)


def _get_playback_subtitle_tracks(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return verified per-language tracks, including embedded source metadata."""
    return _get_item_subtitle_tracks(item)


@app.route("/api/media/<int:media_id>/remote-m3u", methods=["GET"])
def api_media_remote_m3u(media_id: int):
    """Generates an on-the-fly single-item .m3u with HTTP URLs for remote/mobile clients."""
    item = db.get_media_item(media_id)
    if not item:
        return ("Media not found", 404)
    base_url = request.host_url.rstrip("/")
    content, _ = _generate_m3u_content(item.get("title") or item["file_name"], [item], remote_base_url=base_url)
    safe_name = re.sub(r"[^\w\-]+", "_", (item.get("title") or "stream"))[:40]
    return Response(
        content,
        mimetype="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.m3u"'},
    )


@app.route("/api/media/<int:media_id>/classification/preview", methods=["POST"])
def api_media_classification_preview(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404
    payload = request.get_json(silent=True) or {}
    normalized_title = (payload.get("normalized_title") or "").strip() or None
    result = class_eng.classify_media(
        item.get("file_name") or "",
        item.get("parent_name") or "",
        normalized_title=normalized_title,
    )
    return jsonify({"ok": True, "result": result, "current_category": item.get("content_category"),
                    "classification_locked": bool(item.get("classification_locked"))})


@app.route("/api/media/<int:media_id>/classification/apply", methods=["POST"])
def api_media_classification_apply(media_id: int):
    payload = request.get_json(silent=True) or {}
    category = (payload.get("category") or "").strip().lower()
    normalized_title = (payload.get("normalized_title") or "").strip()
    note = (payload.get("note") or "Manual confirmation from Needs Review workflow").strip()
    lock = bool(payload.get("lock", True))
    try:
        updated = db.apply_manual_classification(
            media_id, category=category, normalized_title=normalized_title,
            note=note, lock=lock,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not updated:
        return jsonify({"ok": False, "error": "Media not found."}), 404
    return jsonify({"ok": True, "item": updated, "history": db.get_classification_history(media_id, 10),
                    "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/classification/history", methods=["GET"])
def api_media_classification_history(media_id: int):
    if not db.get_media_item(media_id):
        return jsonify({"ok": False, "error": "Media not found."}), 404
    return jsonify({"ok": True, "history": db.get_classification_history(media_id)})


@app.route("/api/media/<int:media_id>/enrich", methods=["POST"])
def api_media_enrich_single(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404
    try:
        # Keep the current poster until a replacement has been downloaded and validated.
        settings = db.get_settings()
        probe_ready_item = _attach_cloud_stream_for_probe(item, settings)
        enriched = meta_eng.enrich_media_item(probe_ready_item, settings)
        enriched["unique_key"] = item["unique_key"]
        enriched["file_name"] = item["file_name"]
        enriched["_force_meta"] = True
        db.upsert_media_item(enriched)
        if enriched.get("poster_status"):
            db.update_media_poster_state(
                media_id,
                poster_url=enriched.get("poster_url"),
                local_poster_path=enriched.get("local_poster_path"),
                status=enriched.get("poster_status"),
                error=enriched.get("poster_error"),
                source=enriched.get("poster_source") or enriched.get("metadata_source"),
                replace_url=True,
            )
        updated = db.get_media_item(media_id)
        lookup = enriched.get("_lookup") or {}
        app.logger.info(
            "Single-item enrichment media_id=%s provider=%s status=%s query=%s poster_status=%s",
            media_id, lookup.get("provider"), lookup.get("status"), lookup.get("query"), lookup.get("poster_status")
        )
        return jsonify({"ok": True, "item": updated, "lookup": lookup, "stats": db.get_telemetry_stats()})
    except Exception as exc:
        app.logger.exception("Single-item metadata enrichment failed for media_id=%s", media_id)
        return jsonify({"ok": False, "error": f"Metadata enrichment failed: {exc}"}), 500


@app.route("/api/media/<int:media_id>/manual", methods=["POST"])
def api_media_manual_edit(media_id: int):
    payload = request.get_json(silent=True) or {}
    updated = db.update_media_manual(media_id, payload)
    if not updated:
        return jsonify({"ok": False, "error": "Media not found."}), 404
    return jsonify({"ok": True, "item": updated, "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/delete-physical", methods=["POST"])
def api_media_delete_physical(media_id: int):
    """Permanently deletes a media file from Local Disk and/or TorBox Cloud and removes its DB entry."""
    payload = request.get_json(silent=True) or {}
    delete_local = bool(payload.get("delete_local", True))
    delete_remote = bool(payload.get("delete_remote", True))

    res = delete_media_physical(media_id, delete_local=delete_local, delete_remote=delete_remote)
    if not res.get("ok"):
        return jsonify(res), 404
    return jsonify({**res, "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/watched", methods=["POST"])
def api_media_watched(media_id: int):
    payload = request.get_json(silent=True) or {}
    db.update_media_watched(media_id, int(payload.get("is_watched", 1)))
    return jsonify({"ok": True, "item": db.get_media_item(media_id), "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/rating", methods=["POST"])
def api_media_rating(media_id: int):
    payload = request.get_json(silent=True) or {}
    db.update_media_rating(media_id, int(payload.get("rating_star", 0)))
    return jsonify({"ok": True, "item": db.get_media_item(media_id), "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/subtitle/<language>", methods=["POST"])
def api_media_subtitle_language(media_id: int, language: str):
    """Find or download exactly one requested subtitle language."""
    language = (language or "").lower().strip()
    if language not in ("en", "id"):
        return jsonify({"ok": False, "error": "Language must be en or id."}), 400
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404

    existing = next((x for x in db.get_media_subtitles(media_id) if x.get("language") == language), None)
    if existing:
        return jsonify({"ok": True, "already_available": True, "subtitle": existing,
                        "item": db.get_media_item(media_id)})

    settings = db.get_settings()
    torbox_key = (settings.get("torbox_api_key") or "").strip()
    try:
        result = sub_eng.fetch_subtitle_for_language(item, language, torbox_api_key=torbox_key)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Language-specific subtitle lookup failed: media_id=%s language=%s", media_id, language)
        return jsonify({"ok": False, "error": f"Subtitle lookup failed: {exc}"}), 500

    if not result or not result.get("subtitle_path") or result.get("subtitle_lang") != language:
        return jsonify({"ok": False, "language": language,
                        "error": f"No verified {'English' if language == 'en' else 'Indonesian'} subtitle found."}), 404

    db.upsert_media_subtitle(
        media_id, language, result["subtitle_path"], result.get("subtitle_source") or "auto",
        is_embedded=bool(result.get("is_embedded", False)),
        stream_index=result.get("stream_index"), verified=True,
    )
    return jsonify({"ok": True, "already_available": False, "subtitle": result,
                    "item": db.get_media_item(media_id), "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/subtitle", methods=["POST"])
def api_media_subtitle_single(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404

    settings = db.get_settings()
    torbox_key = (settings.get("torbox_api_key") or "").strip()
    res = sub_eng.fetch_subtitle_for_item(item, torbox_api_key=torbox_key)
    if res and res.get("subtitle_path"):
        db.update_media_subtitle(
            media_id,
            res["subtitle_path"],
            res.get("subtitle_lang") or "en",
            res.get("subtitle_source") or "auto",
        )
        return jsonify({"ok": True, "item": db.get_media_item(media_id), "stats": db.get_telemetry_stats()})

    cat = item.get("content_category") or "uncategorized"
    if cat == "english_adult":
        msg = "Subtitle not found in local sidecar or TorBox Cloud (public movie providers are skipped for English Adult)."
    elif cat == "jav":
        msg = "Subtitle not found in local sidecar, TorBox Cloud, SubtitleCat, or OpenSubtitles."
    else:
        msg = "Subtitle not found in local sidecar, TorBox Cloud, embedded streams, or OpenSubtitles."
    return jsonify({"ok": False, "error": msg}), 404


def _launch_items_in_player(items: List[Dict[str, Any]], label_title: str) -> Tuple[bool, str, int]:
    """Launches 1 or more media items directly in the configured player (instant season or single-episode playback)."""
    if not items:
        return False, "No playable items found.", 400

    settings = db.get_settings()
    player_type, player_label, bin_path, extra_args = _resolve_player_binary(settings)
    if not bin_path:
        return False, f"{player_label} executable not found. Configure or Auto-Detect in Settings.", 400

    if len(items) == 1:
        item = items[0]
        target = item.get("file_path") if item.get("source_type") == "local" else item.get("stream_url")
        if (
            item.get("source_type") == "cloud"
            and settings.get("torbox_api_key")
            and item.get("torbox_item_id")
        ):
            fid_val = str(item.get("torbox_file_id")) if item.get("torbox_file_id") not in (None, "") else "0"
            fresh_url = _request_torbox_stream_url(
                settings["torbox_api_key"],
                item.get("torbox_kind") or "torrent",
                str(item["torbox_item_id"]),
                fid_val,
            )
            if fresh_url:
                target = fresh_url
                db.upsert_media_item({"unique_key": item["unique_key"], "file_name": item["file_name"], "stream_url": fresh_url})

        if not target:
            return False, "Media stream URL/path is unavailable.", 400

        subtitle_tracks = _get_playback_subtitle_tracks(item)
        # Load only the language missing from embedded tracks; if neither is embedded, load both.
        primary_external = dual_subs.vlc_playback_track(subtitle_tracks)
        sp = primary_external.get("subtitle_path") if primary_external else ""
        has_ext_sub = bool(sp)

        if bin_path.startswith("android://"):
            base_url = request.host_url.rstrip("/")
            android_target = (
                f"{base_url}/api/media/{item['id']}/stream"
                if item.get("source_type") == "local"
                else target
            )
            sub_http = (f"{base_url}/api/media/{item['id']}/subtitle-file/{primary_external.get('language', 'en')}"
                        if has_ext_sub and primary_external else None)
            ok, msg = _launch_android_intent(android_target, bin_path, sub_url=sub_http)
            return ok, msg, (200 if ok else 500)

        cmd = [bin_path]
        if extra_args:
            try:
                cmd.extend(shlex.split(extra_args, posix=(os.name != "nt")))
            except Exception:
                cmd.extend(extra_args.split())

        lower_bin = os.path.basename(bin_path).lower()
        is_mpv_like = player_type == "mpv" or "mpv" in lower_bin or "iina" in lower_bin or "celluloid" in lower_bin
        is_vlc_like = player_type == "vlc" or "vlc" in lower_bin
        is_mpc_like = "mpc-hc" in lower_bin or "mpc-be" in lower_bin
        is_pot_like = "potplayer" in lower_bin

        if is_mpv_like:
            # mpv supports multiple external subtitle tracks; selectable via its subtitle menu/hotkeys.
            cmd.extend(dual_subs.mpv_subtitle_arguments(subtitle_tracks))
        elif has_ext_sub:
            # VLC and common DirectShow players accept one explicit external subtitle on launch.
            # Prefer English; Indonesian remains available when English is absent. Embedded tracks
            # in the video remain selectable natively inside these players.
            if is_vlc_like:
                cmd.append(f"--sub-file={sp}")
            elif is_mpc_like or is_pot_like:
                cmd.extend(["/sub", sp])

        cmd.append(target)
        try:
            _launch_player_process(cmd)
            return True, f"Playing '{label_title}' in {player_label}.", 200
        except Exception as exc:
            return False, f"Failed to launch {player_label}: {exc}", 500

    os.makedirs(db.CACHE_DIR, exist_ok=True)
    temp_m3u = os.path.join(db.CACHE_DIR, "active_season_queue.m3u")
    _generate_m3u_file(label_title, items, existing_file_path=temp_m3u)

    if bin_path.startswith("android://"):
        return False, "Multi-episode direct intent is not supported on Android; save as Playlist first.", 400

    cmd = [bin_path]
    if extra_args:
        try:
            cmd.extend(shlex.split(extra_args, posix=(os.name != "nt")))
        except Exception:
            cmd.extend(extra_args.split())
    lower_bin = os.path.basename(bin_path).lower()
    is_mpv_like = player_type == "mpv" or "mpv" in lower_bin or "iina" in lower_bin or "celluloid" in lower_bin
    if is_mpv_like:
        entries = _read_m3u_media_entries(temp_m3u)
        tracks_by_media = {int(item["id"]): _get_playback_subtitle_tracks(item)
                           for item in items if item.get("id") is not None}
        cmd.extend(dual_subs.mpv_playlist_arguments(entries, tracks_by_media))
    else:
        cmd.append(temp_m3u)
    try:
        _launch_player_process(cmd)
        return True, f"Playing '{label_title}' ({len(items)} episodes) in {player_label}.", 200
    except Exception as exc:
        return False, f"Failed to launch {player_label}: {exc}", 500


@app.route("/api/media/<int:media_id>/play", methods=["POST"])
def api_media_play(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404
    ok, msg, code = _launch_items_in_player([item], item["title"])
    return (jsonify({"ok": True, "message": msg}), 200) if ok else (jsonify({"ok": False, "error": msg}), code)


@app.route("/api/media/play-batch", methods=["POST"])
def api_media_play_batch():
    payload = request.get_json(silent=True) or {}
    media_ids = [int(x) for x in (payload.get("media_ids") or [])]
    label = (payload.get("label") or "Season Queue").strip()
    items = [it for it in (db.get_media_item(mid) for mid in media_ids) if it]
    ok, msg, code = _launch_items_in_player(items, label)
    return (jsonify({"ok": True, "message": msg}), 200) if ok else (jsonify({"ok": False, "error": msg}), code)


@app.route("/api/tasks/sync-torbox", methods=["POST"])
def api_task_sync_torbox():
    payload = request.get_json(silent=True) or {}
    auto_enrich = bool(payload.get("auto_enrich", False))
    if not TASK_MGR.start("sync_torbox", "Starting TorBox Cloud sync..."):
        active = TASK_MGR.status()
        return jsonify({
            "ok": False,
            "error": f"Sync not started: background task '{active.get('task_type')}' is still running.",
            "active_task": active,
        }), 409
    threading.Thread(target=_sync_torbox_cloud_worker, args=(auto_enrich,), daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


@app.route("/api/tasks/scan-local", methods=["POST"])
def api_task_scan_local():
    payload = request.get_json(silent=True) or {}
    auto_enrich = bool(payload.get("auto_enrich", False))
    if not TASK_MGR.start("scan_local", "Starting local folder scan..."):
        return jsonify({"ok": False, "error": "Another background task is already running."}), 409
    threading.Thread(target=_scan_local_folders_worker, args=(auto_enrich,), daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


@app.route("/api/tasks/classify-pending", methods=["POST"])
def api_task_classify_pending():
    if not TASK_MGR.start("classify_pending", "Starting classification of new uncategorized media..."):
        return jsonify({"ok": False, "error": "Another background task is already running."}), 409
    threading.Thread(target=_classify_pending_worker, daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


@app.route("/api/tasks/batch-enrich", methods=["POST"])
def api_task_batch_enrich():
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", False))
    if not TASK_MGR.start("batch_enrich", "Starting metadata, poster & duration enrichment..."):
        return jsonify({"ok": False, "error": "Another background task is already running."}), 409
    threading.Thread(target=_batch_enrich_worker, args=(force,), daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


@app.route("/api/tasks/batch-subtitle", methods=["POST"])
def api_task_batch_subtitle():
    if not TASK_MGR.start("batch_subtitle", "Starting batch subtitle search..."):
        return jsonify({"ok": False, "error": "Another background task is already running."}), 409
    threading.Thread(target=_batch_subtitle_worker, daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


_TASK_STATS_TTL_SECONDS = 3.0
_task_stats_cache: Dict[str, Any] = {}
_task_stats_lock = threading.Lock()


def _task_telemetry_stats() -> Dict[str, Any]:
    """Telemetry counters for the progress banner.

    The banner polls every 1.2 s and get_telemetry_stats() costs ~6.5 ms because it
    opens a fresh SQLite connection per call. A short cache removes most of that.
    Scope is this endpoint only: write endpoints still compute their own stats, so
    their responses never go stale.
    """
    now = time.monotonic()
    with _task_stats_lock:
        cached = _task_stats_cache.get("value")
        if cached and now - cached[0] < _TASK_STATS_TTL_SECONDS:
            return cached[1]
    stats = db.get_telemetry_stats()
    with _task_stats_lock:
        _task_stats_cache["value"] = (now, stats)
    return stats


@app.route("/api/task/status", methods=["GET"])
def api_task_status():
    return jsonify({"ok": True, "task": TASK_MGR.status(), "stats": _task_telemetry_stats()})


def _create_playlist_worker(name: str, media_ids: List[int]) -> None:
    try:
        items = [it for it in (db.get_media_item(mid) for mid in media_ids) if it]
        if not items:
            TASK_MGR.finish("Failed: No valid media selected.")
            return
        TASK_MGR.update(total=len(items), current=0, message=f"Creating playlist '{name}' ({len(items)} videos)...")

        def _on_step(cur: int, tot: int, msg: str) -> None:
            TASK_MGR.update(current=cur, total=tot, message=msg, log_line=msg)

        m3u_path = _generate_m3u_file(name, items, progress_cb=_on_step)
        db.create_playlist_record(name, os.path.basename(m3u_path), m3u_path, [it["id"] for it in items])
        TASK_MGR.finish(f"Playlist '{name}' ({len(items)} videos) created successfully.")
    except Exception as exc:
        TASK_MGR.finish(f"Error creating playlist: {exc}")


def _append_playlist_worker(playlist_id: int, media_ids: List[int]) -> None:
    try:
        pl = db.add_items_to_playlist(playlist_id, media_ids)
        if not pl:
            TASK_MGR.finish("Failed: Playlist not found.")
            return
        items = pl.get("items") or []
        TASK_MGR.update(total=len(items), current=0, message=f"Updating playlist '{pl['name']}'...")

        def _on_step(cur: int, tot: int, msg: str) -> None:
            TASK_MGR.update(current=cur, total=tot, message=msg, log_line=msg)

        _generate_m3u_file(pl["name"], items, existing_file_path=pl["file_path"], progress_cb=_on_step)
        TASK_MGR.finish(f"Playlist '{pl['name']}' updated successfully ({len(items)} videos).")
    except Exception as exc:
        TASK_MGR.finish(f"Error updating playlist: {exc}")


@app.route("/api/playlists", methods=["GET", "POST"])
def api_playlists():
    if request.method == "GET":
        return jsonify({"ok": True, "playlists": db.get_all_playlists(), "stats": db.get_telemetry_stats()})

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    media_ids = [int(x) for x in (payload.get("media_ids") or [])]
    if not name:
        return jsonify({"ok": False, "error": "Playlist name is required."}), 400
    if not media_ids:
        return jsonify({"ok": False, "error": "Select at least 1 media item for the playlist."}), 400

    if not TASK_MGR.start("create_playlist", f"Preparing playlist '{name}' ({len(media_ids)} videos)..."):
        return jsonify({"ok": False, "error": "Another task is in progress. Please wait until 100% completed."}), 409

    threading.Thread(target=_create_playlist_worker, args=(name, media_ids), daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


@app.route("/api/playlists/<int:playlist_id>/items", methods=["POST"])
def api_playlist_add_items(playlist_id: int):
    payload = request.get_json(silent=True) or {}
    media_ids = [int(x) for x in (payload.get("media_ids") or [])]
    if not media_ids:
        return jsonify({"ok": False, "error": "Select at least 1 media item."}), 400
    pl = db.get_playlist_by_id(playlist_id)
    if not pl:
        return jsonify({"ok": False, "error": "Playlist not found."}), 404

    if not TASK_MGR.start("update_playlist", f"Adding {len(media_ids)} videos to playlist '{pl['name']}'..."):
        return jsonify({"ok": False, "error": "Another task is in progress. Please wait until 100% completed."}), 409

    threading.Thread(target=_append_playlist_worker, args=(playlist_id, media_ids), daemon=True).start()
    return jsonify({"ok": True, "task": TASK_MGR.status()})


@app.route("/api/playlists/<int:playlist_id>/items/<int:media_id>", methods=["DELETE"])
def api_playlist_remove_item(playlist_id: int, media_id: int):
    db.remove_item_from_playlist(playlist_id, media_id)
    pl = db.get_playlist_by_id(playlist_id)
    if pl:
        _generate_m3u_file(pl["name"], pl["items"], existing_file_path=pl["file_path"])
    return jsonify({"ok": True, "playlist": pl, "playlists": db.get_all_playlists(), "stats": db.get_telemetry_stats()})


@app.route("/api/playlists/<int:playlist_id>", methods=["DELETE"])
def api_playlist_delete(playlist_id: int):
    db.delete_playlist_record(playlist_id)
    return jsonify({"ok": True, "playlists": db.get_all_playlists(), "stats": db.get_telemetry_stats()})


@app.route("/api/playlists/<int:playlist_id>/download", methods=["GET"])
def api_playlist_download_remote(playlist_id: int):
    """Generates a remote/LAN-compatible .m3u playlist with HTTP URLs for mobile/remote devices."""
    pl = db.get_playlist_by_id(playlist_id)
    if not pl:
        return ("Playlist not found", 404)
    base_url = request.host_url.rstrip("/")
    content, _ = _generate_m3u_content(pl["name"], pl["items"], remote_base_url=base_url)
    return Response(
        content,
        mimetype="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{pl["file_name"]}"'},
    )


@app.route("/api/playlists/<int:playlist_id>/play", methods=["POST"])
def api_playlist_play(playlist_id: int):
    pl = db.get_playlist_by_id(playlist_id)
    if not pl:
        return jsonify({"ok": False, "error": "Playlist not found."}), 404

    settings = db.get_settings()
    player_type, player_label, bin_path, extra_args = _resolve_player_binary(settings)
    if not bin_path:
        return jsonify({"ok": False, "error": f"{player_label} not found. Configure or Auto-Detect in Settings."}), 400

    runtime_playlist_path = _resolve_runtime_playlist_path(pl)
    if not _is_m3u_file_fresh(runtime_playlist_path):
        _generate_m3u_file(pl["name"], pl["items"], existing_file_path=runtime_playlist_path)

    if bin_path.startswith("android://"):
        base_url = request.host_url.rstrip("/")
        pl_url = f"{base_url}/api/playlists/{playlist_id}/download"
        ok, msg = _launch_android_intent(pl_url, bin_path)
        return (jsonify({"ok": True, "message": msg}) if ok else (jsonify({"ok": False, "error": msg}), 500))

    cmd = [bin_path]
    if extra_args:
        try:
            cmd.extend(shlex.split(extra_args, posix=(os.name != "nt")))
        except Exception:
            cmd.extend(extra_args.split())

    lower_bin = os.path.basename(bin_path).lower()
    is_mpv_like = player_type == "mpv" or "mpv" in lower_bin or "iina" in lower_bin or "celluloid" in lower_bin

    if is_mpv_like:
        entries = _read_m3u_media_entries(runtime_playlist_path)
        items = pl.get("items") or []
        tracks_by_media = {int(item["id"]): _get_playback_subtitle_tracks(item)
                           for item in items if item.get("id") is not None}
        cmd.extend(dual_subs.mpv_playlist_arguments(entries, tracks_by_media))
    else:
        cmd.append(runtime_playlist_path)

    try:
        _launch_player_process(cmd)
        return jsonify({"ok": True, "message": f"Playlist '{pl['name']}' parsed & launched in {player_label}."})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to play playlist in {player_label}: {exc}"}), 500


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "settings": db.get_masked_settings(),
            "subtitle_preferences": db.get_subtitle_preferences(),
            "subtitle_language_stats": db.get_subtitle_language_stats(),
            "backups": db.list_db_backups(),
            "default_paths": db.get_default_player_paths(),
            "detected_players": detect_installed_players(),
            "os_platform": db.detect_runtime_os(),
            "env": _get_environment_info(),
        })
    payload = request.get_json(silent=True) or {}
    saved = db.save_settings(payload)
    subtitle_preferences = db.get_subtitle_preferences()
    if "indonesian_enabled" in payload:
        subtitle_preferences = db.save_subtitle_preferences(bool(payload.get("indonesian_enabled")))
    return jsonify({"ok": True, "settings": saved, "subtitle_preferences": subtitle_preferences})


@app.route("/api/db/backup", methods=["POST"])
def api_db_backup():
    res = db.create_db_backup()
    return jsonify({"ok": True, **res, "backups": db.list_db_backups()})


@app.route("/api/db/restore", methods=["POST"])
def api_db_restore():
    payload = request.get_json(silent=True) or {}
    ok = db.restore_db_backup(payload.get("file_name") or "")
    if not ok:
        return jsonify({"ok": False, "error": "Failed to restore database backup."}), 400
    return jsonify({"ok": True, "stats": db.get_telemetry_stats()})


@app.route("/api/db/maintenance", methods=["POST"])
def api_db_maintenance():
    res = db.run_db_maintenance()
    return jsonify({"ok": True, **res, "stats": db.get_telemetry_stats()})


@app.route("/api/db/reset-metadata", methods=["POST"])
def api_db_reset_metadata():
    res = db.reset_all_metadata()
    return jsonify({"ok": True, **res, "stats": db.get_telemetry_stats()})


@app.route("/api/db/erase-all", methods=["POST"])
def api_db_erase_all():
    res = db.erase_all_media_data()
    return jsonify({"ok": True, **res, "stats": db.get_telemetry_stats()})


if __name__ == "__main__":
    log_path = _setup_server_logging()
    print(f"[INFO] Log file: {log_path}", flush=True)
    db.init_db()
    from roxxy_download_engine import start_library_task_worker
    start_library_task_worker()
    host = os.environ.get("TBH_HOST", "127.0.0.1")
    port = int(os.environ.get("TBH_PORT", "5050"))
    if host not in _LOOPBACK_HOSTS:
        print(
            f"[WARN] Listening on {host}:{port}. There is no authentication: any client that can\n"
            "       reach this port can control downloads, delete files, and read settings.\n"
            "       Keep it on loopback, or put it behind a reverse proxy that authenticates.\n",
            flush=True,
        )
    app.run(host=host, port=port, debug=False, threaded=True)