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
from typing import Any, Dict, List, Optional

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
    allowed = {
        "roxxy_manifest_url",
        "roxxy_extra_manifest_url",
        "roxxy_manifest_url_jav",
        "roxxy_manifest_url_english_adult",
        "roxxy_manifest_url_onlyfans",
        "roxxy_manifest_url_indo_movie",
        "roxxy_manifest_url_donghua",
        "roxxy_download_path",
    }
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


# One Roxxy installation can hold several addons. Each addon has its own
# token-protected manifest, so catalog/meta/stream requests carry a `source`
# selector and the UI groups catalogs under these five sources.
ROXXY_SOURCES = ("jav", "english_adult", "onlyfans", "indo_movie", "donghua")
ROXXY_SOURCE_LABELS = {
    "jav": "JAV",
    "english_adult": "English Adult",
    "onlyfans": "OnlyFans",
    "indo_movie": "Indonesia Movies",
    "donghua": "Donghua",
}
# Category enforced for downloads that come from a dedicated addon. The
# IndonesiaMovies addon always lands in "indo_movie"; the adult and Donghua
# addons reuse the existing categories. JAV is deliberately absent: the JAV
# Library surface requires a verified production code (D3 refuses a "jav" row
# without one) and JAV catalogues also publish code-less leaks. A JAV download
# is therefore filed as "jav" only when the classifier extracts that code;
# otherwise the Indo/English Adult evidence decides, and a non-adult verdict is
# left uncategorized for review instead of being filed as a family title.
ROXXY_SOURCE_CATEGORIES = {
    "english_adult": "english_adult",
    "onlyfans": "english_adult",
    "indo_movie": "indo_movie",
    "donghua": "donghua",
}
# Older installs used one hoster manifest ("main") and one optional second
# addon ("extra"). Both selectors keep working as aliases.
ROXXY_SOURCE_ALIASES = {"main": "english_adult", "extra": "donghua"}
ROXXY_ALL_SOURCES = ROXXY_SOURCES + tuple(ROXXY_SOURCE_ALIASES)
_MANIFEST_SETTINGS = {
    "jav": "roxxy_manifest_url_jav",
    "english_adult": "roxxy_manifest_url_english_adult",
    "onlyfans": "roxxy_manifest_url_onlyfans",
    "indo_movie": "roxxy_manifest_url_indo_movie",
    "donghua": "roxxy_manifest_url_donghua",
}
_MANIFEST_LEGACY_SETTINGS = {
    "english_adult": ("roxxy_manifest_url",),
    "donghua": ("roxxy_extra_manifest_url",),
}
_MANIFEST_ENV = {
    "jav": "ROXXY_JAV_MANIFEST_URL",
    "english_adult": "ROXXY_ENGLISH_ADULT_MANIFEST_URL",
    "onlyfans": "ROXXY_ONLYFANS_MANIFEST_URL",
    "indo_movie": "ROXXY_INDO_MOVIE_MANIFEST_URL",
    "donghua": "ROXXY_DONGHUA_MANIFEST_URL",
}
_MANIFEST_LEGACY_ENV = {
    "english_adult": ("ROXXY_MANIFEST_URL",),
    "donghua": ("ROXXY_EXTRA_MANIFEST_URL",),
}
_MANIFEST_ERROR = (
    "Manifest URL harus HTTPS dari hub.roxxy-tech.com dan diakhiri /manifest.json."
)


def _canonical_source(source: str) -> str:
    """Map legacy selectors to the five canonical addon sources."""
    value = str(source or "").strip().lower()
    value = ROXXY_SOURCE_ALIASES.get(value, value)
    return value if value in ROXXY_SOURCES else "english_adult"


def source_category(source: str) -> str:
    """Category a manifest selector declares, including the legacy aliases.

    Returns an empty string for JAV (code-verified instead) and for anything
    that is not a known selector, so an unknown payload value can never be
    filed as English Adult by the default in ``_canonical_source``.
    """
    value = str(source or "").strip().lower()
    if value not in ROXXY_ALL_SOURCES:
        return ""
    return ROXXY_SOURCE_CATEGORIES.get(_canonical_source(value), "")


def _manifest_url(source: str = "main") -> str:
    canonical = _canonical_source(source)
    s = _settings()
    value = (
        s.get(_MANIFEST_SETTINGS[canonical])
        or os.environ.get(_MANIFEST_ENV.get(canonical, ""))
        or ""
    ).strip()
    if value:
        return value
    for key in _MANIFEST_LEGACY_SETTINGS.get(canonical, ()):
        value = (s.get(key) or "").strip()
        if value:
            return value
    for env_key in _MANIFEST_LEGACY_ENV.get(canonical, ()):
        value = (os.environ.get(env_key) or "").strip()
        if value:
            return value
    return ""


def _validate_manifest_url(raw: str) -> None:
    p = urlparse(raw or "")
    if p.scheme != "https" or p.hostname != "hub.roxxy-tech.com" or not p.path.endswith("/manifest.json"):
        raise ValueError(_MANIFEST_ERROR)


def _base_url(source: str = "main") -> str:
    canonical = _canonical_source(source)
    raw = _manifest_url(canonical)
    if not raw:
        label = ROXXY_SOURCE_LABELS.get(canonical, canonical)
        raise ValueError(f"Manifest {label} belum diatur di Settings.")
    _validate_manifest_url(raw)
    p = urlparse(raw)
    path = p.path
    return f"{p.scheme}://{p.netloc}{path[:-len('manifest.json')]}"


def _masked_manifest(source: str = "main") -> str:
    raw = _manifest_url(source)
    if not raw:
        return ""
    p = urlparse(raw)
    # Only scheme/host/last component: the token segment must never be echoed.
    return f"{p.scheme}://{p.netloc}/…/{p.path.rsplit('/', 1)[-1]}"


def _source_arg() -> str:
    """Read the addon selector; unknown values fall back to the legacy main addon."""
    value = (request.args.get("source") or "main").strip().lower()
    return value if value in ROXXY_ALL_SOURCES else "main"


def _roxxy_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    source: str = "main",
) -> Dict[str, Any]:
    url = _base_url(source) + path.lstrip("/")
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


# --- Series/season structure ------------------------------------------------
# Stremio-style meta resources list every episode in `videos[]`. Roxxy keeps the
# list untouched and adds a grouped, sorted view so the UI can show a season
# selector plus an episode grid instead of a flat stream list.
_SEASON_SUFFIX_RE = re.compile(r"(?i)\s*(?:season\s*\d+|s\d{1,2})\s*$")
_EPISODE_PREFIX_RE = re.compile(
    r"(?i)^(?:season\s*\d+\s*)?(?:(?:episode|ep\.?)\s*\d+)"
)


def _series_base_name(name: Any) -> str:
    """Drop a trailing "Season 5"/"S05" so both forms can be compared."""
    return _SEASON_SUFFIX_RE.sub("", str(name or "")).strip(" -:–|")


def _as_positive_int(value: Any, fallback: Optional[int] = None) -> Optional[int]:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _episode_display_title(title: Any, series_name: Any) -> str:
    """Shorten "Show Season 5 Episode 219 Multiple Subtitles" to its suffix."""
    text = str(title or "").strip()
    if not text:
        return ""
    for candidate in (str(series_name or "").strip(), _series_base_name(series_name)):
        if candidate and text.casefold().startswith(candidate.casefold()):
            text = text[len(candidate):].strip(" -:–|")
            break
    text = _EPISODE_PREFIX_RE.sub("", text).strip(" -:–|")
    return text


def _build_series_index(videos: Any, series_name: Any) -> Optional[Dict[str, Any]]:
    """Group ``videos[]`` into seasons with ascending episode numbers."""
    if not isinstance(videos, list) or not videos:
        return None
    grouped: Dict[int, list] = {}
    for video in videos:
        if not isinstance(video, dict):
            continue
        video_id = str(video.get("id") or "").strip()
        if not video_id:
            continue
        season = _as_positive_int(video.get("season"), 1) or 1
        episode = _as_positive_int(video.get("episode"))
        title = str(video.get("title") or "").strip()
        grouped.setdefault(season, []).append({
            "id": video_id,
            "season": season,
            "episode": episode,
            "title": title,
            "display_title": _episode_display_title(title, series_name),
            "label": f"E{episode}" if episode is not None else "E?",
        })
    if not grouped:
        return None
    seasons = []
    for season in sorted(grouped):
        episodes = sorted(
            grouped[season],
            key=lambda item: (item["episode"] is None, item["episode"] or 0, item["title"].casefold()),
        )
        seasons.append({"season": season, "episodes": episodes})
    multi_season = len(seasons) > 1
    if multi_season:
        for group in seasons:
            for episode in group["episodes"]:
                if episode["episode"] is not None:
                    episode["label"] = f"S{group['season']:02d}·E{episode['episode']}"
    newest = seasons[-1]["episodes"][-1]
    return {
        "seasons": seasons,
        "episode_count": sum(len(group["episodes"]) for group in seasons),
        "season_count": len(seasons),
        "multi_season": multi_season,
        "default_episode_id": newest["id"],
        "default_season": seasons[-1]["season"],
    }


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
# Tombstones keep the identity fields but carry no stream URL or media id.
_TOMBSTONE_SELECT = (
    "title, file_name, code_id, series_title, season_num, episode_num, deleted_at"
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


def _series_episode_key(series_title: Any, season: Any, episode: Any) -> str:
    """Key for one exact episode: normalized series title plus S/E numbers."""
    base = _library_match_key(series_title)
    season_number = _as_positive_int(season)
    episode_number = _as_positive_int(episode)
    if not base or season_number is None or episode_number is None:
        return ""
    return f"{base}|s{season_number}|e{episode_number}"


def _build_library_index(rows: Any) -> Dict[str, Dict[str, Any]]:
    """Index library rows for one request; newest row wins on a shared key."""
    by_code: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}
    by_episode_title: Dict[str, Dict[str, Any]] = {}
    by_series: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        code = str(item.get("code_id") or "").strip().upper()
        if code:
            by_code.setdefault(code, item)
        # Episodes share their series title, so a title hit would not prove that
        # this exact episode is already available. They are indexed by their own
        # title plus a series/season/episode key instead.
        if item.get("season_num") is not None or item.get("episode_num") is not None:
            for field in ("title", "file_name"):
                key = _library_match_key(item.get(field))
                if key:
                    by_episode_title.setdefault(key, item)
            series_name = (
                item.get("series_title") or item.get("title") or item.get("file_name")
            )
            for candidate in (series_name, _series_base_name(series_name)):
                key = _series_episode_key(
                    candidate, item.get("season_num"), item.get("episode_num")
                )
                if key:
                    by_series.setdefault(key, item)
            continue
        for field in ("title", "file_name"):
            key = _library_match_key(item.get(field))
            if key:
                by_title.setdefault(key, item)
    return {
        "by_code": by_code,
        "by_title": by_title,
        "by_episode_title": by_episode_title,
        "by_series": by_series,
    }


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
            try:
                tombstone_rows = conn.execute(
                    f"SELECT {_TOMBSTONE_SELECT} FROM media_tombstones ORDER BY id DESC"
                ).fetchall()
            except Exception:
                # Older database snapshots may predate the tombstone table.
                tombstone_rows = []
        index = _build_library_index(rows)
        # Removed titles live alongside the live index so one cache build and
        # one TTL invalidation cover both badge states.
        index["tombstones"] = _build_library_index(tombstone_rows)
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


def _tombstone_index(index: Any) -> Dict[str, Dict[str, Any]]:
    """Nested removed-title index attached by ``_load_library_index``."""
    if not isinstance(index, dict):
        return {}
    tombstones = index.get("tombstones")
    return tombstones if isinstance(tombstones, dict) else {}


def _library_state_for_meta(meta: Any, index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Badge state for a meta: in library, removed earlier, or never seen."""
    match = _library_match_for_meta(meta, index)
    if match:
        return {"state": "in_library", "match": match, "was_match": None}
    was_match = _library_match_for_meta(meta, _tombstone_index(index))
    state = "was_in_library" if was_match else "not_in_library"
    return {"state": state, "match": None, "was_match": was_match}


def _episode_library_match(
    series_name: Any,
    season: Any,
    episode: Any,
    episode_title: Any,
    index: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the library row holding this exact episode, if any.

    The episode title is tried first because it is unique per episode; the
    series/season/episode key covers rows imported from other sources that only
    carry the series title.
    """
    if not index:
        return None
    title_key = _library_match_key(episode_title)
    if title_key:
        match = index.get("by_episode_title", {}).get(title_key)
        if match:
            return dict(match, match_reason="episode_title")
    for candidate in (series_name, _series_base_name(series_name)):
        key = _series_episode_key(candidate, season, episode)
        if not key:
            continue
        match = index.get("by_series", {}).get(key)
        if match:
            return dict(match, match_reason="series_episode")
    return None


_EPISODE_MATCH_FIELDS = ("id", "title", "file_name", "season_num", "episode_num")
_TOMBSTONE_MATCH_FIELDS = ("title", "file_name", "season_num", "episode_num", "deleted_at")


def _annotate_episodes(meta: Dict[str, Any], index: Dict[str, Dict[str, Any]]) -> bool:
    """Attach the grouped series view and per-episode IN LIBRARY flags."""
    series = _build_series_index(meta.get("videos"), meta.get("name"))
    if not series:
        return False
    series_name = str(meta.get("name") or "")
    tombstones = _tombstone_index(index)
    owned = 0
    for group in series["seasons"]:
        for episode in group["episodes"]:
            match = _episode_library_match(
                series_name, episode["season"], episode["episode"], episode["title"], index
            )
            was_match = None
            if match is None and tombstones:
                was_match = _episode_library_match(
                    series_name,
                    episode["season"],
                    episode["episode"],
                    episode["title"],
                    tombstones,
                )
            episode["in_library"] = match is not None
            episode["was_in_library"] = was_match is not None
            episode["library_state"] = (
                "in_library"
                if match
                else ("was_in_library" if was_match else "not_in_library")
            )
            if match:
                owned += 1
                episode["library_match"] = {
                    field: match.get(field) for field in _EPISODE_MATCH_FIELDS
                }
                episode["library_match"]["match_reason"] = match.get("match_reason")
            else:
                episode["library_match"] = None
            episode["was_library_match"] = (
                {
                    field: was_match.get(field)
                    for field in _TOMBSTONE_MATCH_FIELDS
                }
                if was_match
                else None
            )
    series["library_count"] = owned
    meta["series"] = series
    meta["is_series"] = True
    return True


@roxxy_bp.get("/roxxy")
def page():
    return render_template("roxxy.html", download_path=(_settings().get("roxxy_download_path") or DEFAULT_DOWNLOAD_PATH))


@roxxy_bp.get("/api/roxxy/config")
def api_config():
    s = _settings()
    manifests = {}
    for source in ROXXY_SOURCES:
        manifests[source] = {
            "label": ROXXY_SOURCE_LABELS[source],
            "configured": bool(_manifest_url(source)),
            "display": _masked_manifest(source),
        }
    donghua = manifests["donghua"]
    return jsonify({
        "ok": True,
        "configured": any(item["configured"] for item in manifests.values()),
        # Legacy keys keep older clients working.
        "manifest_display": manifests["english_adult"]["display"],
        "extra_configured": donghua["configured"],
        "extra_manifest_display": donghua["display"],
        "manifests": manifests,
        "download_path": s.get("roxxy_download_path") or DEFAULT_DOWNLOAD_PATH,
    })


@roxxy_bp.post("/api/roxxy/config")
def api_save_config():
    payload = request.get_json(silent=True) or {}
    manifest = (payload.get("manifest_url") or "").strip()
    extra = (payload.get("extra_manifest_url") or "").strip()
    folder = (payload.get("download_path") or DEFAULT_DOWNLOAD_PATH).strip()
    incoming = payload.get("manifests")
    candidates: Dict[str, str] = {}
    if isinstance(incoming, dict):
        for source, raw in incoming.items():
            if raw is None or isinstance(raw, bool):
                continue
            text = str(raw).strip()
            if text:
                candidates[_canonical_source(source)] = text
    if manifest:
        candidates["english_adult"] = manifest
    if extra:
        candidates["donghua"] = extra
    try:
        for source, raw in candidates.items():
            _validate_manifest_url(raw)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not folder:
        return jsonify({"ok": False, "error": "Folder download tidak boleh kosong."}), 400
    # The settings form leaves a token field empty unless it is replaced; an
    # empty value must not wipe an already configured manifest.
    current = _settings()
    values: Dict[str, str] = {"roxxy_download_path": folder}
    updated: List[str] = []
    for source, raw in candidates.items():
        values[_MANIFEST_SETTINGS[source]] = raw
        # Compare against the effective value so a re-saved token (including a
        # legacy/env one) is not reported as a fresh update.
        if _manifest_url(source) != raw:
            updated.append(source)
    # Legacy columns are still written because older jobs/UI read them directly.
    if "english_adult" in candidates:
        values["roxxy_manifest_url"] = candidates["english_adult"]
    if "donghua" in candidates:
        values["roxxy_extra_manifest_url"] = candidates["donghua"]
    _save_private_settings(values)
    folder_changed = (current.get("roxxy_download_path") or DEFAULT_DOWNLOAD_PATH).strip() != folder
    changed = len(updated)
    if updated:
        labels = ", ".join(ROXXY_SOURCE_LABELS.get(src, src) for src in updated)
        message = (
            f"Konfigurasi Video Feeds disimpan ({changed} manifest diperbarui: {labels}). "
            + ("Folder download juga diperbarui. " if folder_changed else "")
            + "Token tidak ditampilkan kembali."
        )
    elif folder_changed:
        message = (
            "Konfigurasi Video Feeds disimpan (folder download diperbarui, "
            "tidak ada manifest yang berubah). Token tidak ditampilkan kembali."
        )
    else:
        message = "Konfigurasi Video Feeds tidak berubah; tidak ada manifest atau folder yang perlu diperbarui."
    return jsonify({
        "ok": True,
        "changed": changed,
        "updated_sources": updated,
        "folder_changed": folder_changed,
        "message": message,
    })


@roxxy_bp.get("/api/roxxy/manifest")
def api_manifest():
    """Merge every configured addon into one catalog list tagged by source."""
    try:
        configured = [source for source in ROXXY_SOURCES if _manifest_url(source)]
        if not configured:
            raise ValueError("Belum ada Manifest URL yang diatur di Settings.")
        catalogs: list = []
        types: list = []
        name = "Video Feeds"
        version = None
        source_errors: Dict[str, str] = {}
        source_counts: Dict[str, int] = {}
        for position, source in enumerate(configured):
            try:
                data = _roxxy_get("manifest.json", source=source)
            except Exception as exc:
                source_errors[source] = _safe_error(exc)
                continue
            if position == 0 or name == "Video Feeds":
                name = data.get("name", name)
                if version is None:
                    version = data.get("version")
            count = 0
            for item in data.get("catalogs") or []:
                if not isinstance(item, dict):
                    continue
                catalogs.append({**item, "source": source})
                count += 1
            source_counts[source] = count
            for item_type in data.get("types") or []:
                if item_type not in types:
                    types.append(item_type)
        if not catalogs and source_errors:
            raise ValueError("Semua manifest gagal dimuat: " + "; ".join(
                f"{ROXXY_SOURCE_LABELS[src]}: {err}" for src, err in source_errors.items()
            ))
        sources = [
            {
                "key": source,
                "label": ROXXY_SOURCE_LABELS[source],
                "configured": bool(_manifest_url(source)),
                "catalog_count": source_counts.get(source, 0),
                "error": source_errors.get(source, ""),
            }
            for source in ROXXY_SOURCES
        ]
        return jsonify({
            "ok": True,
            "name": name,
            "version": version,
            "types": types,
            "catalogs": catalogs,
            "sources": sources,
            "source_errors": source_errors,
            "extra_error": source_errors.get("donghua", ""),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": _safe_error(exc)}), 502


@roxxy_bp.get("/api/roxxy/catalog/<catalog_type>/<path:catalog_id>")
def api_catalog(catalog_type: str, catalog_id: str):
    try:
        source = _source_arg()
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
        data = _roxxy_get(path, params, source=source)
        metas = data.get("metas") or []
        index = _load_library_index()
        for item in metas:
            item["jav_code"] = _extract_code(f"{item.get('id','')} {item.get('name','')} {item.get('description','')}")
            # Show the green badge on the grid itself so an owned title is
            # visible before the user opens the detail drawer. A removed title
            # keeps a muted "WAS IN LIBRARY" badge instead of vanishing.
            state = _library_state_for_meta(item, index)
            item["library_state"] = state["state"]
            item["in_library"] = state["state"] == "in_library"
            item["was_in_library"] = state["state"] == "was_in_library"
        return jsonify({"ok": True, "metas": metas, "cacheMaxAge": data.get("cacheMaxAge")})
    except Exception as exc:
        return jsonify({"ok": False, "error": _safe_error(exc)}), 502


@roxxy_bp.get("/api/roxxy/meta/<meta_type>/<path:meta_id>")
def api_meta(meta_type: str, meta_id: str):
    try:
        source = _source_arg()
        path = f"meta/{quote(meta_type, safe='')}/{quote(meta_id, safe=':')}.json"
        data = _roxxy_get(path, source=source)
        meta = data.get("meta") or {}
        meta["jav_code"] = _extract_code(f"{meta.get('id','')} {meta.get('name','')} {meta.get('description','')}")
        index = _load_library_index()
        state = _library_state_for_meta(meta, index)
        meta["library_state"] = state["state"]
        meta["in_library"] = state["state"] == "in_library"
        meta["was_in_library"] = state["state"] == "was_in_library"
        meta["library_match"] = state["match"]
        meta["was_library_match"] = state["was_match"]
        # Donghua/general series arrive as one title with every episode in
        # `videos[]`; group them and flag the episodes already in the Library.
        _annotate_episodes(meta, index)
        return jsonify({"ok": True, "meta": meta})
    except Exception as exc:
        return jsonify({"ok": False, "error": _safe_error(exc)}), 502


@roxxy_bp.get("/api/roxxy/streams/<stream_type>/<path:stream_id>")
def api_streams(stream_type: str, stream_id: str):
    try:
        source = _source_arg()
        path = f"stream/{quote(stream_type, safe='')}/{quote(stream_id, safe=':')}.json"
        data = _roxxy_get(path, source=source)
        streams = data.get("streams") or []
        # Preserve Roxxy's original fields; add a conservative routing hint only.
        for stream in streams:
            url = (stream.get("url") or "").strip()
            lower = url.lower()
            if lower.startswith("magnet:"):
                stream["route_hint"] = "torbox_magnet"
            elif _looks_like_nzb_url(url):
                stream["route_hint"] = "torbox_usenet"
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


# Roxxy serves signed forwarder links from its own domain. Only payloads from
# these domains may be decoded; anything else is left untouched so a look-alike
# host cannot smuggle a URL into a TorBox submission. `ROXXY_FORWARD_HOSTS`
# accepts extra comma-separated domains for other deployments.
ROXXY_FORWARD_DOMAINS = tuple(
    entry.strip().lstrip(".").rstrip(".").lower()
    for entry in (os.environ.get("ROXXY_FORWARD_HOSTS") or "roxxy-tech.com").split(",")
    if entry.strip()
)
_FORWARD_PAYLOAD_MAX = 8192


def _is_forward_host(host: Optional[str]) -> bool:
    """True when ``host`` is an allowlisted Roxxy forwarder domain (or subdomain)."""
    value = str(host or "").strip().rstrip(".").lower()
    if not value:
        return False
    return any(
        value == domain or value.endswith("." + domain)
        for domain in ROXXY_FORWARD_DOMAINS
    )


def _decode_forwarded_payload(raw_url: str) -> Dict[str, Any]:
    """Decode the signed ``d=<base64url JSON>`` payload of a forwarder URL.

    Roxxy marks several stream kinds as ``direct`` even though the real target
    (magnet, NZB or media URL) only exists inside this payload, sometimes on a
    path that does not carry a file extension. The host allowlist decides which
    URLs may be decoded at all, the payload is bounded, and a payload that is a
    bare URL is returned as ``{"u": ...}``.
    """
    parsed = urlparse(str(raw_url or ""))
    if parsed.scheme not in ("http", "https"):
        return {}
    if not _is_forward_host(parsed.hostname):
        return {}
    encoded = (parse_qs(parsed.query).get("d") or [""])[0]
    if not encoded or len(encoded) > _FORWARD_PAYLOAD_MAX:
        return {}
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return {}
    text = decoded.strip()[: _FORWARD_PAYLOAD_MAX]
    if text.startswith(("http://", "https://")):
        return {"u": text}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _decode_forwarded_magnet(raw_url: str) -> Optional[str]:
    """Extract a Magnet URI from Roxxy's forwarding payload.

    Roxxy may mark this stream kind=direct even though its ``d`` parameter
    contains the actual magnet in the ``m`` field. Only the known torrent
    forwarder path is decoded, so a magnet-shaped field elsewhere cannot
    hijack a stream that was routed differently.
    """
    parsed = urlparse(str(raw_url or ""))
    if not parsed.path.endswith("/torrent/dl"):
        return None
    payload = _decode_forwarded_payload(raw_url)
    for key in ("m", "magnet"):
        magnet = unquote(str(payload.get(key) or "")).strip()
        if magnet.lower().startswith("magnet:?xt=urn:btih:"):
            return magnet
    return None


def _decode_forwarded_nzb(raw_url: str) -> Optional[str]:
    """Extract an NZB link from Roxxy's usenet forwarding payload.

    The hoster addon wraps NZB downloads in a signed ``/nzb/dl?d=<base64>`` URL.
    TorBox needs the real NZB URL, not the forwarder. Explicit ``n``/``nzb``
    fields are trusted on any forwarder path, while the weaker ``u``/``url``/...
    aliases only count when the path itself declares an NZB; that keeps a
    forwarded HLS/media payload from being misrouted to Usenet.
    """
    payload = _decode_forwarded_payload(raw_url)
    if not payload:
        return None
    path = urlparse(str(raw_url or "")).path.lower()
    declares_nzb = "/nzb/" in path or path.endswith((".nzb", "/nzb"))
    keys = ["n", "nzb"]
    if declares_nzb:
        keys += ["u", "url", "link", "download", "file"]
    for key in keys:
        value = unquote(str(payload.get(key) or "")).strip()
        if value.startswith(("http://", "https://")):
            return value
    return None


def _looks_like_nzb_url(raw_url: str) -> bool:
    """Path/extension hint for NZB links that are not forwarder payloads."""
    lower = str(raw_url or "").lower()
    if lower.endswith(".nzb"):
        return True
    parsed = urlparse(lower)
    return "/nzb/" in parsed.path or parsed.path.endswith(".nzb")


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
    result = {
        "engine": "manual",
        "route": "manual_inspection",
        "reason": "URL belum dapat diklasifikasikan secara aman.",
        "magnet": None,
        "nzb_url": None,
    }
    magnet = url if lower.startswith("magnet:?") else _decode_forwarded_magnet(url)
    if magnet:
        result.update(engine="torbox", route="torbox_magnet", reason="Magnet URI terdeteksi dari URL stream atau payload forwarding Video Feeds.", magnet=magnet)
        return result
    nzb = _decode_forwarded_nzb(url)
    if nzb or _looks_like_nzb_url(url):
        result.update(
            engine="torbox",
            route="torbox_usenet",
            reason="Link NZB terdeteksi; diarahkan ke Usenet TorBox.",
            nzb_url=nzb or url,
        )
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


def _stream_download_options(analysis: Dict[str, Any], url: str) -> list:
    """Every route the user may choose for one analysed stream.

    Torrents can go to TorBox cached or uncached, NZBs go to TorBox Usenet, and
    plain HTTP media can be fetched locally by either yt-dlp or aria2c.
    """
    if analysis.get("magnet"):
        return [
            {"engine": "torbox", "kind": "torrent", "mode": "cached", "label": "Cached TorBox"},
            {"engine": "torbox", "kind": "torrent", "mode": "uncached", "label": "Torrent ke TorBox"},
        ]
    if analysis.get("nzb_url"):
        return [{"engine": "torbox", "kind": "usenet", "mode": "nzb", "label": "NZB ke TorBox"}]
    lower = str(url or "").lower()
    if not lower.startswith(("http://", "https://")):
        return []
    preferred = analysis.get("engine")
    options = [
        {"engine": "yt-dlp", "mode": "local", "label": "yt-dlp (lokal)"},
        {"engine": "aria2c", "mode": "local", "label": "aria2c (lokal)"},
    ]
    resolved = bool(analysis.get("resolved"))
    for option in options:
        option["recommended"] = option["engine"] == preferred and resolved
        if not resolved:
            # Header inspection failed, so the downloader cannot yet tell a
            # media stream from a random HTTP response. Offer the route but
            # keep it unusable instead of letting the click fail with a 400.
            option["disabled"] = True
            option["disabled_reason"] = (
                "Perlu probe header server: tipe konten belum teridentifikasi."
            )
    return options


_ROXXY_EPISODE_SEARCH_TTL = 600.0
_ROXXY_EPISODE_SEARCH_MAX = 64
_EPISODE_SEARCH_CACHE: Dict[str, Any] = OrderedDict()
_EPISODE_SEARCH_LOCK = threading.Lock()


def _title_key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _title_matches(query: Any, candidate: Any) -> bool:
    """Loose series-title match used to keep a catalog search on the right show."""
    pairs = ((query, candidate), (_series_base_name(query), _series_base_name(candidate)))
    for left_raw, right_raw in pairs:
        left, right = _title_key(left_raw), _title_key(right_raw)
        if not left or not right:
            continue
        if left == right:
            return True
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if len(shorter) >= 8 and shorter in longer:
            return True
    return False


def _search_title_variants(title: Any) -> List[str]:
    """Queries to try when a catalog indexes a shorter title than the library row."""
    clean = " ".join(str(title or "").split()).strip()
    if not clean:
        return []
    variants = [clean]
    base = _series_base_name(clean)
    if base and base.casefold() != clean.casefold():
        variants.append(base)
    stripped = re.sub(
        r"(?i)\s+(?:nf|nfs|hd|tv|series|complete|uncut|uncensored)$", "", base or clean
    ).strip(" -:–|")
    if stripped and stripped.casefold() not in {item.casefold() for item in variants}:
        variants.append(stripped)
    return variants[:3]


def _catalog_supports_search(catalog: Dict[str, Any]) -> bool:
    extra = catalog.get("extra")
    entries = extra if isinstance(extra, list) else ([extra] if isinstance(extra, dict) else [])
    return any(
        isinstance(entry, dict) and str(entry.get("name") or "").strip() == "search"
        for entry in entries
    )


def search_episode_releases(
    series_title: str,
    season: int,
    episodes: list,
    source: str = "donghua",
    max_catalogs: int = 4,
    max_metas: int = 3,
    max_stream_calls: int = 8,
) -> Dict[str, Any]:
    """Find torrent/NZB releases for missing episodes through the Roxxy addon.

    Anime and Donghua catalogs publish per-episode streams; a title search is
    resolved to a meta, then only the requested episodes are asked for streams.
    Results are bounded and cached so one "search all episodes" click cannot
    fan out into an unbounded number of addon requests.
    """
    canonical = _canonical_source(source)
    label = ROXXY_SOURCE_LABELS.get(canonical, canonical)
    wanted = sorted({int(value) for value in (episodes or []) if str(value).strip().isdigit() and int(value) > 0})
    cache_key = f"{canonical}|{str(series_title or '').strip().lower()}|{int(season)}|{','.join(map(str, wanted))}"
    now = time.time()
    with _EPISODE_SEARCH_LOCK:
        cached = _EPISODE_SEARCH_CACHE.get(cache_key)
        if cached and now - cached[0] < _ROXXY_EPISODE_SEARCH_TTL:
            _EPISODE_SEARCH_CACHE.move_to_end(cache_key)
            return dict(cached[1])

    result: Dict[str, Any] = {
        "ok": True,
        "source": canonical,
        "label": label,
        "catalogs": 0,
        "metas": 0,
        "stream_calls": 0,
        "direct_streams": 0,
        "candidates": [],
        "errors": [],
    }
    if not _manifest_url(canonical):
        result.update(ok=False, error=f"Manifest {label} belum diatur di Settings.")
        return result

    try:
        manifest = _roxxy_get("manifest.json", source=canonical)
    except Exception as exc:
        result.update(ok=False, error=_safe_error(exc))
        return result

    searchable = [
        catalog for catalog in (manifest.get("catalogs") or [])
        if isinstance(catalog, dict) and catalog.get("id") and _catalog_supports_search(catalog)
    ][: max(1, int(max_catalogs))]
    if not searchable:
        result.update(ok=False, error=f"Tidak ada katalog {label} yang mendukung pencarian.")
        return result

    seen_meta: set = set()
    seen_stream: set = set()
    metas = []
    for catalog in searchable:
        ctype = str(catalog.get("type") or "").strip()
        cid = str(catalog.get("id") or "").strip()
        if not ctype or not cid:
            continue
        result["catalogs"] += 1
        variant_found = False
        for variant in _search_title_variants(series_title):
            path = (
                f"catalog/{quote(ctype, safe='')}/{quote(cid, safe=':')}"
                f"/search={quote(variant, safe='')}.json"
            )
            try:
                data = _roxxy_get(path, source=canonical)
            except Exception as exc:
                result["errors"].append(f"{catalog.get('name') or cid}: {_safe_error(exc)}")
                break
            for meta in data.get("metas") or []:
                if not isinstance(meta, dict) or not meta.get("id"):
                    continue
                if not _title_matches(variant, meta.get("name")):
                    continue
                key = (ctype, str(meta["id"]))
                if key in seen_meta:
                    continue
                seen_meta.add(key)
                metas.append((ctype, meta, str(catalog.get("name") or "")))
                variant_found = True
            if variant_found:
                break

    # Prefer the closest title match and the requested season, then keep the
    # request count bounded.
    def _meta_rank(item):
        name = str(item[1].get("name") or "")
        normalized = _title_key(_series_base_name(name))
        requested = _title_key(_series_base_name(series_title))
        exact = normalized == requested
        season_hint = bool(re.search(rf"(?i)\b(?:season\s*{int(season)}|s{int(season):02d})\b", name))
        return (0 if exact else 1, 0 if season_hint else 1, len(name))

    metas.sort(key=_meta_rank)
    metas = metas[: max(1, int(max_metas))]
    result["metas"] = len(metas)

    for ctype, meta, catalog_name in metas:
        try:
            detail = _roxxy_get(
                f"meta/{quote(ctype, safe='')}/{quote(str(meta['id']), safe=':')}.json",
                source=canonical,
            ).get("meta") or {}
        except Exception as exc:
            result["errors"].append(f"{catalog_name}: {_safe_error(exc)}")
            continue
        index = _build_series_index(detail.get("videos"), detail.get("name") or meta.get("name"))
        if not index:
            continue
        groups = index.get("seasons") or []
        group = next((g for g in groups if int(g.get("season") or 1) == int(season)), None)
        if group is None and len(groups) == 1:
            group = groups[0]
        if group is None:
            continue
        if wanted:
            targets = [ep for ep in group.get("episodes") or [] if int(ep.get("episode") or -1) in wanted]
        else:
            targets = list(group.get("episodes") or [])[: max_stream_calls]

        for episode in targets:
            if result["stream_calls"] >= int(max_stream_calls):
                break
            ep_num = int(episode.get("episode") or 0) or None
            video_id = str(episode.get("id") or "").strip()
            if not video_id:
                continue
            result["stream_calls"] += 1
            try:
                streams = _roxxy_get(
                    f"stream/{quote(ctype, safe='')}/{quote(video_id, safe=':')}.json",
                    source=canonical,
                ).get("streams") or []
            except Exception as exc:
                result["errors"].append(f"{catalog_name} E{ep_num}: {_safe_error(exc)}")
                continue
            for stream in streams[:12]:
                if not isinstance(stream, dict) or not stream.get("url"):
                    continue
                analysis = _classify_stream(stream)
                magnet = analysis.get("magnet") or ""
                nzb_url = analysis.get("nzb_url") or ""
                if not magnet and not nzb_url:
                    result["direct_streams"] += 1
                    continue
                stream_key = magnet or nzb_url
                if stream_key in seen_stream:
                    continue
                seen_stream.add(stream_key)
                result["candidates"].append({
                    "kind": "torrent" if magnet else "usenet",
                    "source": f"Roxxy {label}",
                    "indexer": catalog_name,
                    "title": str(stream.get("name") or stream.get("title") or episode.get("title") or detail.get("name") or "").strip()[:300],
                    "episode": ep_num,
                    "magnet": magnet,
                    "nzb_url": nzb_url,
                    "quality": _stream_quality(stream),
                    "seeders": None,
                    "leechers": None,
                    "size": None,
                })

    with _EPISODE_SEARCH_LOCK:
        _EPISODE_SEARCH_CACHE[cache_key] = (now, dict(result))
        _EPISODE_SEARCH_CACHE.move_to_end(cache_key)
        while len(_EPISODE_SEARCH_CACHE) > _ROXXY_EPISODE_SEARCH_MAX:
            _EPISODE_SEARCH_CACHE.popitem(last=False)
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

    analysis["options"] = _stream_download_options(analysis, url)

    # Never echo signed/forwarding URLs. The extracted magnet is returned only
    # to the authenticated local UI for explicit TorBox submission.
    return jsonify({"ok": True, "analysis": analysis})


_CACHE_CHECK_MAX_HASHES = 100
_MAGNET_HASH_RE = re.compile(r"urn:btih:([A-Fa-f0-9]{40}|[A-Za-z2-7]{32})", re.I)


def _magnet_hash(magnet: str) -> str:
    """Normalised torrent hash from a Magnet URI, or '' when malformed."""
    match = _MAGNET_HASH_RE.search(str(magnet or ""))
    return match.group(1).lower() if match else ""


@roxxy_bp.post("/api/roxxy/torbox-cache-check")
def api_torbox_cache_check():
    """Check one or many Roxxy magnets against TorBox cache in a single call.

    TorBox accepts comma separated hashes on one request (about 100 per call),
    so a stream list is checked at once instead of hammering the endpoint once
    per magnet.
    """
    payload = request.get_json(silent=True) or {}
    single = str(payload.get("magnet") or "").strip()
    listed = payload.get("magnets")
    if isinstance(listed, list) and listed:
        magnets = [str(item or "").strip() for item in listed]
    elif single:
        magnets = [single]
    else:
        return jsonify({"ok": False, "error": "Magnet/hash torrent tidak valid."}), 400
    if len(magnets) > _CACHE_CHECK_MAX_HASHES:
        return jsonify({
            "ok": False,
            "error": f"Maksimal {_CACHE_CHECK_MAX_HASHES} magnet per pemeriksaan.",
        }), 400
    hashes: list = []
    for magnet in magnets:
        torrent_hash = _magnet_hash(magnet)
        if not magnet.lower().startswith("magnet:?") or not torrent_hash:
            return jsonify({"ok": False, "error": "Magnet/hash torrent tidak valid."}), 400
        if torrent_hash not in hashes:
            hashes.append(torrent_hash)
    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "TorBox API Key belum dikonfigurasi."}), 400
    try:
        response = requests.get(
            "https://api.torbox.app/v1/api/torrents/checkcached",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"hash": ",".join(hashes), "format": "list"},
            timeout=(8, 25),
        )
        response.raise_for_status()
        body = response.json()
        if body.get("success") is False:
            return jsonify({"ok": False, "error": "TorBox tidak berhasil memeriksa cache."}), 502
        data = body.get("data") or []
        cached_hashes = set()
        if isinstance(data, list):
            cached_hashes = {
                str(entry.get("hash") or "").lower()
                for entry in data
                if isinstance(entry, dict)
            }
        elif isinstance(data, dict):
            cached_hashes = {
                str(key).lower() for key, value in data.items() if value
            }
        results = {torrent_hash: torrent_hash in cached_hashes for torrent_hash in hashes}
        return jsonify({
            "ok": True,
            "results": results,
            "hashes": hashes,
            # Legacy single-magnet fields keep older clients working.
            "cached": results[hashes[0]],
            "hash": hashes[0],
        })
    except requests.RequestException:
        return jsonify({"ok": False, "error": "TorBox cache check tidak dapat dihubungi."}), 502
    except Exception:
        return jsonify({"ok": False, "error": "Respons cache TorBox tidak dapat diproses."}), 502
