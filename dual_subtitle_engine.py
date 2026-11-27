"""Phase 6B foundation: detect English/Indonesian embedded and local sidecar tracks."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess

import platform_support
from typing import Any, Dict, List, Optional

TARGET_LANGUAGES = ("en", "id")
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")


def normalize_language(value: str) -> Optional[str]:
    value = (value or "").strip().lower().replace("_", "-")
    if value in {"en", "eng", "english", "en-us", "en-gb"} or value.startswith("en-"):
        return "en"
    if value in {"id", "ind", "in", "indonesian", "bahasa", "id-id"} or value.startswith("id-"):
        return "id"
    return None


def _sidecar_language(filename: str) -> Optional[str]:
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    # Require a recognizable language token; unknown sidecars are not guessed as English.
    tokens = [t for t in re.split(r"[. _\-\[\]()]+", stem) if t]
    for token in reversed(tokens):
        lang = normalize_language(token)
        if lang:
            return lang
    return None


def scan_local_sidecars(video_path: str) -> List[Dict[str, Any]]:
    if not video_path or not os.path.isfile(video_path):
        return []
    folder = os.path.dirname(video_path)
    video_stem = os.path.splitext(os.path.basename(video_path))[0].lower()
    results: List[Dict[str, Any]] = []
    try:
        for name in os.listdir(folder):
            ext = os.path.splitext(name)[1].lower()
            if ext not in SUBTITLE_EXTENSIONS:
                continue
            path = os.path.join(folder, name)
            if not os.path.isfile(path) or os.path.getsize(path) < 20:
                continue
            lang = _sidecar_language(name)
            if lang not in TARGET_LANGUAGES:
                continue
            side_stem = os.path.splitext(name)[0].lower()
            if side_stem != video_stem and not side_stem.startswith(video_stem + ".") and not side_stem.startswith(video_stem + "_") and not side_stem.startswith(video_stem + " "):
                continue
            results.append({"language": lang, "subtitle_path": path, "subtitle_source": "local_sidecar", "is_embedded": False, "verified": True})
    except OSError:
        return []
    # Stable deterministic choice, one candidate per language.
    chosen: Dict[str, Dict[str, Any]] = {}
    for row in sorted(results, key=lambda x: (len(x["subtitle_path"]), x["subtitle_path"].lower())):
        chosen.setdefault(row["language"], row)
    return list(chosen.values())


def scan_embedded_tracks(target: str, timeout: int = 20) -> List[Dict[str, Any]]:
    ffprobe = platform_support.resolve_executable("ffprobe")
    if not target or not ffprobe:
        return []
    cmd = [ffprobe, "-v", "error", "-analyzeduration", "5000000", "-probesize", "5000000",
           "-print_format", "json", "-show_streams", "-select_streams", "s", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 or not proc.stdout:
            return []
        data = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    found: Dict[str, Dict[str, Any]] = {}
    for stream in data.get("streams") or []:
        tags = stream.get("tags") or {}
        lang = normalize_language(tags.get("language") or tags.get("LANGUAGE") or "")
        if lang and lang not in found:
            found[lang] = {"language": lang, "subtitle_path": f"embedded://stream/{stream.get('index')}",
                           "subtitle_source": "embedded", "is_embedded": True,
                           "stream_index": stream.get("index"), "verified": True}
    return [found[k] for k in TARGET_LANGUAGES if k in found]


def scan_item_subtitles(item: Dict[str, Any], torbox_api_key: str = "") -> List[Dict[str, Any]]:
    """Scan local sidecars and, when possible, embedded tracks from local media."""
    found: Dict[str, Dict[str, Any]] = {}
    if item.get("source_type") == "local":
        path = item.get("file_path") or ""
        for row in scan_local_sidecars(path):
            found[row["language"]] = row
        for row in scan_embedded_tracks(path):
            # Prefer an explicit sidecar over embedded track for deterministic external playback.
            found.setdefault(row["language"], row)
    return [found[k] for k in TARGET_LANGUAGES if k in found]


def filter_usable_subtitle_tracks(tracks: List[Dict[str, Any]], cache_dir: str) -> List[Dict[str, Any]]:
    """Keep only verified ENG/IND tracks whose embedded reference or file is usable."""
    usable: List[Dict[str, Any]] = []
    for row in tracks or []:
        if row.get("verified", True) is False or row.get("language") not in TARGET_LANGUAGES:
            continue
        path = str(row.get("subtitle_path") or "")
        if not path:
            continue
        if path.startswith("embedded://") or row.get("is_embedded"):
            usable.append(row)
            continue
        resolved = resolve_external_path(path, cache_dir)
        try:
            if os.path.isfile(resolved) and os.path.getsize(resolved) >= 20:
                usable.append(row)
        except OSError:
            continue
    return usable


def plan_missing_languages(found: List[Dict[str, Any]], indonesian_enabled: bool = True) -> List[str]:
    """Return only target languages not already verified; English is always required."""
    return missing_languages(found, indonesian_enabled=indonesian_enabled)


def missing_languages(found: List[Dict[str, Any]], indonesian_enabled: bool = True) -> List[str]:
    available = {r.get("language") for r in found if r.get("verified", True)}
    required = ["en"] + (["id"] if indonesian_enabled else [])
    return [lang for lang in required if lang not in available]


def subtitle_status(found: List[Dict[str, Any]], indonesian_enabled: bool = True) -> str:
    available = {r.get("language") for r in found if r.get("verified", True)}
    if "en" not in available:
        return "Partial" if "id" in available else "Not Ready"
    if indonesian_enabled and "id" not in available:
        return "Ready"
    return "Complete"


def resolve_external_path(path_value: str, cache_dir: str) -> str:
    """Resolve a stored subtitle path on the current OS without changing the DB value.

    Existing absolute paths are preserved. If a database was moved between operating
    systems, recognize legacy ``cache/subtitle(s)/...`` suffixes and remap them under
    the current runtime cache directory. The function never creates or moves files.
    """
    raw = str(path_value or "").strip()
    if not raw or raw.startswith("embedded://"):
        return raw
    if os.path.isfile(raw):
        return os.path.abspath(raw)

    normalized = raw.replace("\\", "/")
    lowered = normalized.lower()
    markers = ("cache/subtitles/", "cache/subtitle/", "subtitles/", "subtitle/")
    for marker in markers:
        index = lowered.rfind(marker)
        if index >= 0:
            suffix = normalized[index + len(marker):]
            if marker.startswith("cache/"):
                relative = marker[len("cache/"):] + suffix
            else:
                relative = marker + suffix
            candidate = os.path.join(cache_dir, *relative.split("/"))
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

    if not os.path.isabs(raw):
        candidate = os.path.join(cache_dir, raw)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return raw


def _is_embedded_track(row: Dict[str, Any]) -> bool:
    return bool(row.get("is_embedded")) or str(row.get("subtitle_path") or "").startswith("embedded://")


def playback_plan(tracks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve which external languages to load while avoiding embedded-language duplicates.

    Policy: if English is embedded, load external Indonesian only; if Indonesian is
    embedded, load external English only; if neither is embedded, load both available
    verified external target languages. If both are embedded, load no external files.
    """
    verified = [r for r in tracks if r.get("verified", True) and r.get("language") in TARGET_LANGUAGES]
    by_language: Dict[str, Dict[str, Any]] = {}
    for language in TARGET_LANGUAGES:
        row = next((r for r in verified if r.get("language") == language), None)
        if row:
            by_language[language] = row

    embedded = {lang for lang, row in by_language.items() if _is_embedded_track(row)}
    if "en" in embedded and "id" in embedded:
        wanted_external = []
    elif "en" in embedded:
        wanted_external = ["id"]
    elif "id" in embedded:
        wanted_external = ["en"]
    else:
        wanted_external = ["en", "id"]

    external = []
    for language in wanted_external:
        row = by_language.get(language)
        path = row.get("subtitle_path") if row else None
        if path and not _is_embedded_track(row) and os.path.isfile(path):
            external.append(row)
    return {"embedded_languages": sorted(embedded), "external_tracks": external,
            "available_languages": [lang for lang in TARGET_LANGUAGES if lang in by_language]}


def external_playback_tracks(tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return only verified external files required by the embedded/external policy."""
    return playback_plan(tracks)["external_tracks"]


def mpv_subtitle_arguments(tracks: List[Dict[str, Any]]) -> List[str]:
    """Build MPV arguments for dual subtitle rendering and track selection."""
    plan = playback_plan(tracks)
    args = ["--sub-auto=no", "--slang=en,id"]
    for row in plan["external_tracks"]:
        args.append(f"--sub-file={os.fspath(row['subtitle_path'])}")
    args.extend([
        "--sid=auto", "--secondary-sid=auto",
        "--sub-visibility=yes", "--secondary-sub-visibility=yes",
    ])
    return args


def mpv_playlist_arguments(entries: List[Dict[str, Any]],
                           tracks_by_media: Optional[Dict[int, List[Dict[str, Any]]]] = None) -> List[str]:
    """Build MPV CLI arguments with isolated subtitle options for each playlist entry.

    Each entry has ``media_id`` and ``target`` fields. This avoids positional drift when
    a playlist item has no playable URI and was omitted from the generated M3U.
    """
    tracks_by_media = tracks_by_media or {}
    args = ["--slang=en,id"]
    for entry in entries:
        media_id = entry.get("media_id")
        plan = playback_plan(tracks_by_media.get(int(media_id), [])) if media_id is not None else playback_plan([])
        args.append("--{")
        args.append("--sub-auto=no")
        for row in plan["external_tracks"]:
            args.append(f"--sub-file={os.fspath(row['subtitle_path'])}")
        args.extend([
            "--sid=auto", "--secondary-sid=auto",
            "--sub-visibility=yes", "--secondary-sub-visibility=yes",
            entry["target"], "--}",
        ])
    return args


def vlc_playback_track(tracks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return one external subtitle for VLC, respecting embedded-language priority."""
    external = playback_plan(tracks)["external_tracks"]
    return external[0] if external else None
