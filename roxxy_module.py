"""Roxxy JAV Center: isolated UI/API integration for TorBox Media Hub.

This module intentionally reuses the existing V1.2 database and TorBox settings
without changing the V1.2 metadata, subtitle, classification or scan engines.
"""
from __future__ import annotations

import json
import os
import re
import base64
import threading
import time
from collections import OrderedDict
from urllib.parse import parse_qs, unquote
from urllib.parse import quote, urlparse
from typing import Any, Dict, Optional

import requests
from flask import Blueprint, current_app, jsonify, render_template, request

import database_manager as db

roxxy_bp = Blueprint("roxxy", __name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "TorBoxMediaHub-Roxxy/1.3", "Accept": "application/json"})
TIMEOUT = (8, 30)
DEFAULT_DOWNLOAD_PATH = r"E:\01 Imaulana\01 Isa Maulana\03 Ionos\Movies"
CODE_RE = re.compile(r"\b(?:FC2-PPV-\d{5,8}|[A-Z]{2,6}-\d{2,5})\b", re.I)


def _settings() -> Dict[str, str]:
    return db.get_settings()


def _save_private_settings(values: Dict[str, str]) -> None:
    # Separate Roxxy keys are stored in the existing settings table. They are
    # deliberately not added to DEFAULT_SETTINGS, so V1.2 settings UI ignores them.
    allowed = {"roxxy_manifest_url", "roxxy_download_path"}
    with db.get_connection() as conn:
        for key, value in values.items():
            if key not in allowed:
                continue
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value or "").strip()),
            )
        conn.commit()


def _manifest_url() -> str:
    s = _settings()
    return (s.get("roxxy_manifest_url") or os.environ.get("ROXXY_MANIFEST_URL") or "").strip()


def _base_url() -> str:
    raw = _manifest_url()
    p = urlparse(raw)
    if p.scheme != "https" or p.hostname != "hub.roxxy-tech.com":
        raise ValueError("Manifest URL harus HTTPS dari hub.roxxy-tech.com.")
    path = p.path
    if not path.endswith("/manifest.json"):
        raise ValueError("URL manifest harus diakhiri /manifest.json.")
    return f"{p.scheme}://{p.netloc}{path[:-len('manifest.json')]}"


def _roxxy_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = _base_url() + path.lstrip("/")
    response = HTTP.get(url, params=params or {}, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Respons Video Feeds bukan objek JSON.")
    return data


def _safe_error(exc: Exception) -> str:
    # Do not leak token-bearing manifest URL or signed stream URLs to UI/logs.
    msg = str(exc)
    msg = re.sub(r"https://hub\.roxxy-tech\.com/[^\s]+", "[Video Feeds URL disamarkan]", msg)
    return msg[:300]


def _extract_code(text: str) -> str:
    m = CODE_RE.search((text or "").upper())
    return m.group(0).upper() if m else ""


# --- Library presence -------------------------------------------------------
# A Roxxy meta carries a JAV production code only for JAV catalogs. English
# adult, Indo and general titles have to be matched by their stored title or
# file name, so the detail badge can say "IN LIBRARY" before the user downloads
# a title that already exists locally or in TorBox Cloud.
LIBRARY_MATCH_MIN_LENGTH = 6
_LIBRARY_FILE_SUFFIXES = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".wmv",
    ".mpg", ".mpeg", ".flv", ".srt", ".ass", ".vtt",
}
_LIBRARY_SELECT = (
    "id, title, file_name, parent_name, source_type, content_category, "
    "has_metadata, has_subtitle, season_num, episode_num, code_id"
)
# Badge checks happen once per card, so the index is shared for a short window.
_LIBRARY_INDEX_TTL_SECONDS = 20.0
_library_index_cache: Dict[str, Any] = {"index": None, "at": 0.0}
_library_index_lock = threading.Lock()


def _library_match_key(value: Any) -> str:
    """Fold a title or file name into an exact comparison key.

    Punctuation, casing, and a known video extension are dropped so
    ``Massage.Gone.Squirt.mp4`` and ``Massage Gone Squirt`` compare equal.
    Short or purely numeric keys are refused because they would match unrelated
    releases.
    """
    text = str(value or "").strip()
    stem, extension = os.path.splitext(text)
    if extension.casefold() in _LIBRARY_FILE_SUFFIXES:
        text = stem
    text = re.sub(r"[^0-9a-z]+", " ", text.casefold())
    key = " ".join(text.split())
    if len(key) < LIBRARY_MATCH_MIN_LENGTH or not any(ch.isalpha() for ch in key):
        return ""
    return key


def _build_library_index(rows: Any) -> Dict[str, Dict[str, Any]]:
    """Index library rows for one request; newest row wins on a shared key."""
    by_code: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        code = str(item.get("code_id") or "").strip().upper()
        if code:
            by_code.setdefault(code, item)
        # Episodes share their series title, so a title hit would not prove that
        # this exact episode is already available.
        if item.get("season_num") is not None or item.get("episode_num") is not None:
            continue
        for field in ("title", "file_name"):
            key = _library_match_key(item.get(field))
            if key:
                by_title.setdefault(key, item)
    return {"by_code": by_code, "by_title": by_title}


def _load_library_index() -> Dict[str, Dict[str, Any]]:
    """Return the library index, rebuilding it at most once per TTL.

    A "search all catalogs" run fans out to ~100 catalog requests. Without this
    cache every request would re-read all of ``media_items`` (measured at
    ~25-30 ms each), adding seconds of identical database work per search.
    """
    now = time.monotonic()
    with _library_index_lock:
        cached = _library_index_cache.get("index")
        cached_at = float(_library_index_cache.get("at") or 0.0)
        if cached is not None and now - cached_at < _LIBRARY_INDEX_TTL_SECONDS:
            return cached
        with db.get_connection() as conn:
            rows = conn.execute(
                f"SELECT {_LIBRARY_SELECT} FROM media_items ORDER BY id DESC"
            ).fetchall()
        index = _build_library_index(rows)
        _library_index_cache["index"] = index
        _library_index_cache["at"] = now
        return index


def invalidate_library_index() -> None:
    """Drop the cached index so the next badge check sees a new import."""
    with _library_index_lock:
        _library_index_cache["index"] = None
        _library_index_cache["at"] = 0.0


def _library_match_for_meta(
    meta: Any, index: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Return the library row that already holds this meta, if any."""
    if not isinstance(meta, dict) or not index:
        return None
    code = str(meta.get("jav_code") or "").strip().upper()
    if not code:
        code = _extract_code(
            f"{meta.get('id', '')} {meta.get('name', '')} {meta.get('description', '')}"
        )
    if code:
        match = index.get("by_code", {}).get(code)
        if match:
            return dict(match, match_reason="code")
    for field in ("name", "title", "original_name"):
        key = _library_match_key(meta.get(field))
        if not key:
            continue
        match = index.get("by_title", {}).get(key)
        if match:
            return dict(match, match_reason="title")
    return None


@roxxy_bp.get("/roxxy")
def page():
    return render_template("roxxy.html", download_path=(_settings().get("roxxy_download_path") or DEFAULT_DOWNLOAD_PATH))


@roxxy_bp.get("/api/roxxy/config")
def api_config():
    s = _settings()
    raw = s.get("roxxy_manifest_url") or os.environ.get("ROXXY_MANIFEST_URL") or ""
    p = urlparse(raw)
    masked = ""
    if raw:
        # Show only scheme/host and final path component; never echo the access token.
        masked = f"{p.scheme}://{p.netloc}/…/{p.path.rsplit('/', 1)[-1]}"
    return jsonify({
        "ok": True,
        "configured": bool(raw),
        "manifest_display": masked,
        "download_path": s.get("roxxy_download_path") or DEFAULT_DOWNLOAD_PATH,
    })


@roxxy_bp.post("/api/roxxy/config")
def api_save_config():
    payload = request.get_json(silent=True) or {}
    manifest = (payload.get("manifest_url") or "").strip()
    folder = (payload.get("download_path") or DEFAULT_DOWNLOAD_PATH).strip()
    if manifest:
        p = urlparse(manifest)
        if p.scheme != "https" or p.hostname != "hub.roxxy-tech.com" or not p.path.endswith("/manifest.json"):
            return jsonify({"ok": False, "error": "Manifest URL tidak valid. Gunakan URL manifest Video Feeds HTTPS yang berakhir /manifest.json."}), 400
    if not folder:
        return jsonify({"ok": False, "error": "Folder download tidak boleh kosong."}), 400
    _save_private_settings({"roxxy_manifest_url": manifest, "roxxy_download_path": folder})
    return jsonify({"ok": True, "message": "Konfigurasi Video Feeds disimpan. Token tidak ditampilkan kembali."})


@roxxy_bp.get("/api/roxxy/manifest")
def api_manifest():
    try:
        data = _roxxy_get("manifest.json")
        return jsonify({"ok": True, "name": data.get("name", "Video Feeds"), "version": data.get("version"),
                        "types": data.get("types", []), "catalogs": data.get("catalogs", []),
                        "resources": data.get("resources", [])})
    except Exception as exc:
        return jsonify({"ok": False, "error": _safe_error(exc)}), 502


@roxxy_bp.get("/api/roxxy/catalog/<catalog_type>/<path:catalog_id>")
def api_catalog(catalog_type: str, catalog_id: str):
    try:
        skip = max(0, int(request.args.get("skip", 0)))
        params: Dict[str, Any] = {"skip": skip}
        genre = (request.args.get("genre") or "").strip()
        search = (request.args.get("search") or "").strip()
        # Stremio addon catalog extras are encoded in the resource path, e.g.
        # /catalog/type/id/search=term.json, not as HTTP query parameters.
        extras = []
        if genre:
            extras.append(f"genre={quote(genre, safe='')}")
        if search:
            extras.append(f"search={quote(search, safe='')}")
        suffix = ("/" + "/".join(extras)) if extras else ""
        path = f"catalog/{quote(catalog_type, safe='')}/{quote(catalog_id, safe=':')}{suffix}.json"
        data = _roxxy_get(path, params)
        metas = data.get("metas") or []
        index = _load_library_index()
        for item in metas:
            item["jav_code"] = _extract_code(f"{item.get('id','')} {item.get('name','')} {item.get('description','')}")
            # Show the green badge on the grid itself so an owned title is
            # visible before the user opens the detail drawer.
            item["in_library"] = _library_match_for_meta(item, index) is not None
        return jsonify({"ok": True, "metas": metas, "cacheMaxAge": data.get("cacheMaxAge")})
    except Exception as exc:
        return jsonify({"ok": False, "error": _safe_error(exc)}), 502


@roxxy_bp.get("/api/roxxy/meta/<meta_type>/<path:meta_id>")
def api_meta(meta_type: str, meta_id: str):
    try:
        path = f"meta/{quote(meta_type, safe='')}/{quote(meta_id, safe=':')}.json"
        data = _roxxy_get(path)
        meta = data.get("meta") or {}
        meta["jav_code"] = _extract_code(f"{meta.get('id','')} {meta.get('name','')} {meta.get('description','')}")
        match = _library_match_for_meta(meta, _load_library_index())
        meta["in_library"] = match is not None
        meta["library_match"] = match
        return jsonify({"ok": True, "meta": meta})
    except Exception as exc:
        return jsonify({"ok": False, "error": _safe_error(exc)}), 502


@roxxy_bp.get("/api/roxxy/streams/<stream_type>/<path:stream_id>")
def api_streams(stream_type: str, stream_id: str):
    try:
        path = f"stream/{quote(stream_type, safe='')}/{quote(stream_id, safe=':')}.json"
        data = _roxxy_get(path)
        streams = data.get("streams") or []
        # Preserve Roxxy's original fields; add a conservative routing hint only.
        for stream in streams:
            url = (stream.get("url") or "").strip()
            lower = url.lower()
            if lower.startswith("magnet:"):
                stream["route_hint"] = "torbox_magnet"
            elif ".m3u8" in lower or ".mpd" in lower:
                stream["route_hint"] = "yt_dlp_candidate"
            elif ".mkv" in lower or "hevc" in (stream.get("name") or "").lower():
                stream["route_hint"] = "aria2_candidate"
            elif lower.startswith(("http://", "https://")):
                stream["route_hint"] = "inspect_http_before_engine"
            else:
                stream["route_hint"] = "manual_inspection"
            stream["quality"] = _stream_quality(stream)
        # Highest resolution first: the user should never scroll a randomly
        # ordered list to find the 1080p source.
        streams.sort(key=lambda s: _quality_rank(str(s.get("quality") or "")), reverse=True)
        return jsonify({"ok": True, "streams": streams, "subtitles": data.get("subtitles", [])})
    except Exception as exc:
        return jsonify({"ok": False, "error": _safe_error(exc)}), 502


def _decode_forwarded_magnet(raw_url: str) -> Optional[str]:
    """Extract a Magnet URI from Roxxy's known torrent/dl forwarding payload.

    Roxxy may mark this stream kind=direct even though its `d` parameter
    contains a base64 JSON object whose `m` field is the actual magnet.
    """
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.hostname not in {"rt-openclaw.roxxy-tech.com"}:
        return None
    if not parsed.path.endswith("/torrent/dl"):
        return None
    encoded = (parse_qs(parsed.query).get("d") or [""])[0]
    if not encoded:
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        magnet = unquote(str(payload.get("m") or "")).strip()
        if magnet.lower().startswith("magnet:?xt=urn:btih:"):
            return magnet
    except Exception:
        return None
    return None


_QUALITY_RE = re.compile(r"\b(2160|1440|1080|720|480|360)\s?p\b", re.I)
_INSPECT_CACHE: Dict[str, Any] = OrderedDict()
_INSPECT_TTL_SECONDS = 300.0
_INSPECT_CACHE_MAX = 64
_INSPECT_CACHE_LOCK = threading.Lock()


def _stream_quality(stream: Dict[str, Any]) -> str:
    """Resolution label from the stream name, title, description or URL path."""
    for field in ("name", "title", "description"):
        match = _QUALITY_RE.search(str(stream.get(field) or ""))
        if match:
            return f"{match.group(1)}p"
    path = urlparse(str(stream.get("url") or "")).path
    match = re.search(r"/(2160|1440|1080|720|480|360)(?:p)?/?$", path, re.I)
    return f"{match.group(1)}p" if match else ""


def _quality_rank(quality: str) -> int:
    return int(quality[:-1]) if quality.endswith("p") and quality[:-1].isdigit() else 0


def _resolve_remote_stream(url: str) -> Dict[str, Any]:
    """Inspect an extension-less forwarder URL once and cache the result.

    Roxxy hands out signed forwarder URLs such as ``.../1080p`` whose real type
    is only known from response headers. Static routing can never decide those,
    so the analysis endpoint performs the same inspection the downloader will
    use, instead of showing an unresolved engine to the user.
    """
    import hashlib
    import time

    cache_key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    now = time.time()
    with _INSPECT_CACHE_LOCK:
        cached = _INSPECT_CACHE.get(cache_key)
        if cached and now - cached[0] < _INSPECT_TTL_SECONDS:
            _INSPECT_CACHE.move_to_end(cache_key)
            return dict(cached[1])

    # Imported lazily: roxxy_download_engine imports the database/engines but
    # never this module, so a module-level import here would only add coupling.
    from roxxy_download_engine import _inspect

    last_exc: Optional[Exception] = None
    info: Dict[str, Any] = {}
    for attempt in range(2):
        try:
            info = {key: value for key, value in _inspect(url).items() if key != "url"}
            break
        except Exception as exc:  # transient forwarder/network failure
            last_exc = exc
            if attempt == 0:
                time.sleep(0.6)
    else:
        raise last_exc if last_exc else RuntimeError("Pemeriksaan header gagal.")

    with _INSPECT_CACHE_LOCK:
        # LRU eviction: keep the cache warm instead of flushing it wholesale, so a
        # burst of stream inspections cannot throw away entries still in use.
        _INSPECT_CACHE[cache_key] = (now, info)
        _INSPECT_CACHE.move_to_end(cache_key)
        while len(_INSPECT_CACHE) > _INSPECT_CACHE_MAX:
            _INSPECT_CACHE.popitem(last=False)
    return dict(info)


def _classify_stream(stream: Dict[str, Any]) -> Dict[str, Any]:
    """Conservative routing analysis; never trust Roxxy's generic `direct` kind."""
    url = str(stream.get("url") or "").strip()
    name = str(stream.get("name") or stream.get("title") or stream.get("description") or "")
    lower = url.lower()
    result = {"engine": "manual", "route": "manual_inspection", "reason": "URL belum dapat diklasifikasikan secara aman.", "magnet": None}
    magnet = url if lower.startswith("magnet:?") else _decode_forwarded_magnet(url)
    if magnet:
        result.update(engine="torbox", route="torbox_magnet", reason="Magnet URI terdeteksi dari URL stream atau payload forwarding Video Feeds.", magnet=magnet)
        return result
    path = urlparse(url).path.lower() if lower.startswith(("http://", "https://")) else ""
    if path.endswith((".m3u8", ".mpd")):
        result.update(engine="yt-dlp", route="yt_dlp", reason="Manifest HLS/DASH terdeteksi.")
    elif path.endswith(".mkv") or re.search(r"\bhevc\b|\bh265\b", name, re.I):
        result.update(engine="aria2c", route="aria2_file", reason="File MKV/HEVC diarahkan ke downloader file.")
    elif path.endswith(".mp4"):
        result.update(engine="yt-dlp", route="yt_dlp", reason="URL MP4 dikenali sebagai media URL; yt-dlp dipilih sesuai aturan Video Feeds.")
    elif lower.startswith(("http://", "https://")):
        result.update(engine="pending_http_inspection", route="inspect_http", reason="HTTP/HTTPS belum menunjukkan ekstensi. Perlu pemeriksaan header sebelum memilih aria2c atau yt-dlp.")
    return result


@roxxy_bp.post("/api/roxxy/analyze")
def api_analyze_stream():
    payload = request.get_json(silent=True) or {}
    stream = payload.get("stream") or {}
    if not isinstance(stream, dict) or not stream.get("url"):
        return jsonify({"ok": False, "error": "Objek stream atau URL tidak tersedia."}), 400
    analysis = _classify_stream(stream)
    analysis["quality"] = _stream_quality(stream)
    analysis["resolved"] = analysis.get("engine") in {"torbox", "yt-dlp", "aria2c"}
    analysis["content_length"] = None
    analysis["content_type"] = ""
    analysis["suggested_filename"] = ""

    url = str(stream.get("url") or "").strip()
    if analysis.get("engine") in {"pending_http_inspection", "manual"} and url.lower().startswith(("http://", "https://")):
        # The forwarder URL has no extension, so headers are the only truth.
        try:
            info = _resolve_remote_stream(url)
            engine = info.get("engine") or "manual"
            analysis["content_length"] = info.get("content_length")
            analysis["content_type"] = info.get("content_type") or ""
            analysis["suggested_filename"] = info.get("filename") or ""
            analysis["resolved"] = engine in {"yt-dlp", "aria2c"}
            if analysis["resolved"]:
                analysis["engine"] = engine
                analysis["route"] = info.get("kind") or analysis.get("route")
                size_note = ""
                if info.get("content_length"):
                    size_note = f", {round(int(info['content_length']) / (1024 ** 2))} MB"
                analysis["reason"] = (
                    f"Server mengembalikan {info.get('content_type') or 'tipe tidak diketahui'}"
                    f"{size_note}; diunduh dengan {'yt-dlp' if engine == 'yt-dlp' else 'aria2c'}."
                )
        except Exception as exc:
            analysis["resolved"] = False
            analysis["inspect_error"] = str(exc)[:200]

    # Never echo signed/forwarding URLs. The extracted magnet is returned only
    # to the authenticated local UI for explicit TorBox submission.
    return jsonify({"ok": True, "analysis": analysis})


@roxxy_bp.post("/api/roxxy/torbox-cache-check")
def api_torbox_cache_check():
    """Check a Roxxy Magnet against TorBox cache without submitting the torrent."""
    payload = request.get_json(silent=True) or {}
    magnet = str(payload.get("magnet") or "").strip()
    match = re.search(r"urn:btih:([A-Fa-f0-9]{40}|[A-Za-z2-7]{32})", magnet, re.I)
    if not magnet.lower().startswith("magnet:?") or not match:
        return jsonify({"ok": False, "error": "Magnet/hash torrent tidak valid."}), 400
    torrent_hash = match.group(1).lower()
    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "TorBox API Key belum dikonfigurasi."}), 400
    try:
        response = requests.get(
            "https://api.torbox.app/v1/api/torrents/checkcached",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"hash": torrent_hash, "format": "list"},
            timeout=(8, 20),
        )
        response.raise_for_status()
        body = response.json()
        if body.get("success") is False:
            return jsonify({"ok": False, "error": "TorBox tidak berhasil memeriksa cache."}), 502
        data = body.get("data") or []
        cached = False
        if isinstance(data, list):
            cached = any(str(entry.get("hash") or "").lower() == torrent_hash for entry in data if isinstance(entry, dict))
        elif isinstance(data, dict):
            cached = bool(data.get(torrent_hash) or data.get(torrent_hash.upper()))
        return jsonify({"ok": True, "cached": cached, "hash": torrent_hash})
    except requests.RequestException:
        return jsonify({"ok": False, "error": "TorBox cache check tidak dapat dihubungi."}), 502
    except Exception:
        return jsonify({"ok": False, "error": "Respons cache TorBox tidak dapat diproses."}), 502
