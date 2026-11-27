import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBTITLE_CACHE_DIR = os.path.join(BASE_DIR, "cache", "subtitles")
os.makedirs(SUBTITLE_CACHE_DIR, exist_ok=True)

SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,id-ID;q=0.8,id;q=0.7",
}

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(HEADERS)
_adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=1)
HTTP_SESSION.mount("http://", _adapter)
HTTP_SESSION.mount("https://", _adapter)

TORBOX_API_BASE = "https://api.torbox.app/v1/api"


def _detect_lang_from_name(name: str) -> Optional[str]:
    """Detect an explicit language marker; ambiguous filenames remain unknown."""
    stem = os.path.splitext(os.path.basename(name or ""))[0].lower()
    tokens = [t for t in re.split(r"[. _\-\[\]()]+", stem) if t]
    aliases = {
        "id": "id", "ind": "id", "indonesian": "id", "indonesia": "id", "bahasa": "id", "id-id": "id",
        "en": "en", "eng": "en", "english": "en", "en-us": "en", "en-gb": "en",
        "ms": "ms", "may": "ms", "malay": "ms", "melayu": "ms",
        "zh": "zh", "chs": "zh", "cht": "zh", "chinese": "zh",
        "ja": "ja", "jpn": "ja", "japanese": "ja",
    }
    for token in reversed(tokens):
        if token in aliases:
            return aliases[token]
    return None


def _extract_ep_tag(name: str) -> Optional[str]:
    """Extracts normalized SxxExx or episode number tag to avoid cross-episode sidecar mismatches."""
    m = re.search(r"(?i)(?:^|[\s._\-\[\(])S(\d{1,2})[\s._-]*E(\d{1,3})(?:$|[\s._\-\]\)])", name or "")
    if m:
        return f"s{int(m.group(1)):02d}e{int(m.group(2)):03d}"
    m_x = re.search(r"(?i)(?:^|[\s._\-\[\(])(\d{1,2})x(\d{1,3})(?:$|[\s._\-\]\)])", name or "")
    if m_x:
        return f"s{int(m_x.group(1)):02d}e{int(m_x.group(2)):03d}"
    m_ep = re.search(r"(?i)(?:^|[\s._\-\[\(])(?:Episode|Ep|E)[\s._-]*(\d{1,3})(?:$|[\s._\-\]\)])", name or "")
    if m_ep:
        return f"e{int(m_ep.group(1)):03d}"
    return None


def _is_valid_subtitle_bytes(raw: bytes) -> bool:
    if not raw or len(raw) < 25:
        return False
    text = raw[:4096].decode("utf-8", errors="ignore").lower()
    if "<html" in text or "<!doctype html" in text:
        return False
    return (
        "-->" in text
        or "[script info]" in text
        or "dialogue:" in text
        or "webvtt" in text
        or bool(re.search(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}", text))
    )


def _cleanup_old_cached_subtitles(dest_base_name: str) -> None:
    safe_base = re.sub(r"[^\w\-.]+", "_", dest_base_name)[:90]
    for ext in SUBTITLE_EXTS:
        old_fp = os.path.join(SUBTITLE_CACHE_DIR, f"{safe_base}{ext}")
        if os.path.isfile(old_fp):
            try:
                os.remove(old_fp)
            except OSError:
                pass


def _save_subtitle_bytes(raw: bytes, dest_base_name: str, ext: str = ".srt") -> Optional[str]:
    if not _is_valid_subtitle_bytes(raw):
        return None
    if not ext.startswith("."):
        ext = f".{ext}"
    if ext.lower() not in SUBTITLE_EXTS:
        ext = ".srt"
    safe_base = re.sub(r"[^\w\-.]+", "_", dest_base_name)[:90]
    _cleanup_old_cached_subtitles(safe_base)
    out_path = os.path.join(SUBTITLE_CACHE_DIR, f"{safe_base}{ext.lower()}")
    with open(out_path, "wb") as f:
        f.write(raw)
    return out_path


def _extract_subtitle_from_zip(raw_zip: bytes, dest_base_name: str, target_language: Optional[str] = None) -> Optional[Tuple[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            candidates = [
                n for n in zf.namelist()
                if not n.endswith("/") and os.path.splitext(n)[1].lower() in SUBTITLE_EXTS
            ]
            if not candidates:
                return None
            if target_language in ("en", "id"):
                detected = {n: _detect_lang_from_name(n) for n in candidates}
                matching = [n for n in candidates if detected[n] == target_language]
                if matching:
                    candidates = matching
                elif any(lang in ("en", "id") for lang in detected.values()):
                    # An explicitly tagged file for the other target language must never pass.
                    return None
                elif len(candidates) != 1:
                    return None
            candidates.sort(
                key=lambda n: (
                    0 if _detect_lang_from_name(n) == "en" else (1 if _detect_lang_from_name(n) == "id" else 2),
                    len(n),
                )
            )
            chosen = candidates[0]
            ext = os.path.splitext(chosen)[1].lower()
            sub_bytes = zf.read(chosen)
            saved = _save_subtitle_bytes(sub_bytes, dest_base_name, ext=ext)
            if saved:
                detected = _detect_lang_from_name(chosen)
                # OpenSubtitles query was already constrained to this language; a single
                # generically named archive member can safely inherit that query language.
                if target_language in ("en", "id") and len(candidates) == 1:
                    detected = target_language
                return saved, detected
    except Exception:
        pass
    return None


def _detect_embedded_subtitle_via_ffprobe(target: str) -> Optional[str]:
    if not target or not shutil.which("ffprobe"):
        return None
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-analyzeduration", "1500000",
        "-probesize", "1500000",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "s",
        target,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if proc.returncode != 0 or not proc.stdout:
            return None
        info = json.loads(proc.stdout)
        streams = info.get("streams") or []
        if not streams:
            return None
        langs = []
        for s in streams:
            tags = s.get("tags") or {}
            lang = (tags.get("language") or tags.get("LANGUAGE") or "").strip().lower()
            if lang:
                langs.append(lang)
        if "ind" in langs or "id" in langs:
            return "id"
        if "eng" in langs or "en" in langs:
            return "en"
        return langs[0] if langs else "embedded"
    except Exception:
        return None


def _get_torbox_temp_stream_url(item: Dict[str, Any], torbox_api_key: str) -> Optional[str]:
    kind = item.get("torbox_kind") or "torrent"
    pid = str(item.get("torbox_item_id") or "").strip()
    raw_fid = item.get("torbox_file_id")
    fid = str(raw_fid).strip() if raw_fid is not None and str(raw_fid).strip() != "" else "0"

    if not torbox_api_key or not pid:
        return None
    endpoint = "torrents" if kind == "torrent" else "usenet"
    id_param = "torrent_id" if kind == "torrent" else "usenet_id"
    try:
        resp = HTTP_SESSION.get(
            f"{TORBOX_API_BASE}/{endpoint}/requestdl",
            headers={"Authorization": f"Bearer {torbox_api_key.strip()}"},
            params={
                "token": torbox_api_key.strip(),
                id_param: pid,
                "file_id": fid,
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


def check_embedded_subtitle(item: Dict[str, Any], torbox_api_key: str = "") -> Optional[Tuple[str, str]]:
    if item.get("source_type") == "local":
        fp = item.get("file_path")
        if fp and os.path.isfile(fp):
            lang = _detect_embedded_subtitle_via_ffprobe(fp)
            if lang:
                return f"embedded://{fp}", lang
    elif item.get("source_type") == "cloud" and torbox_api_key:
        stream_url = _get_torbox_temp_stream_url(item, torbox_api_key)
        if stream_url:
            lang = _detect_embedded_subtitle_via_ffprobe(stream_url)
            if lang:
                ukey = item.get("unique_key") or str(item.get("id"))
                return f"embedded://torbox/{ukey}", lang
    return None


def find_local_sidecar_subtitle(video_path: Optional[str]) -> Optional[Tuple[str, str]]:
    if not video_path or not os.path.isfile(video_path):
        return None
    folder = os.path.dirname(video_path)
    vid_base = os.path.basename(video_path)
    stem = os.path.splitext(vid_base)[0].lower()
    vid_ep_tag = _extract_ep_tag(stem)

    best_match: Optional[Tuple[int, str, str]] = None
    try:
        for fn in os.listdir(folder):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SUBTITLE_EXTS:
                continue
            full_p = os.path.join(folder, fn)
            if not os.path.isfile(full_p) or os.path.getsize(full_p) < 25:
                continue
            fn_low = fn.lower()
            sub_stem = os.path.splitext(fn_low)[0]

            # If video has an explicit episode tag (e.g., S01E01), enforce exact episode match
            if vid_ep_tag:
                sub_ep_tag = _extract_ep_tag(sub_stem)
                if sub_ep_tag != vid_ep_tag:
                    continue

            exact_stem_match = sub_stem == stem or sub_stem.startswith(f"{stem}.") or sub_stem.startswith(f"{stem}_") or sub_stem.startswith(f"{stem} ")
            contains_stem = stem in sub_stem

            if exact_stem_match or contains_stem:
                lang = _detect_lang_from_name(fn)
                lang_prio = 0 if lang == "en" else (1 if lang == "id" else 2)
                match_prio = 0 if exact_stem_match else 10
                total_prio = match_prio + lang_prio
                if best_match is None or total_prio < best_match[0]:
                    best_match = (total_prio, full_p, lang)
    except OSError:
        return None

    if best_match:
        return best_match[1], best_match[2]
    return None


def build_torbox_subtitle_index(torbox_api_key: str) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    if not torbox_api_key:
        return index
    headers = {"Authorization": f"Bearer {torbox_api_key.strip()}"}
    endpoints = [
        ("torrent", f"{TORBOX_API_BASE}/torrents/mylist"),
        ("usenet", f"{TORBOX_API_BASE}/usenet/mylist"),
    ]
    for kind, url in endpoints:
        try:
            resp = HTTP_SESSION.get(url, headers=headers, params={"bypass_cache": "true"}, timeout=15)
            if resp.status_code != 200:
                continue
            rows = resp.json().get("data") or []
            if isinstance(rows, dict):
                rows = [rows]
            for parent in rows:
                pid = str(parent.get("id") or "")
                sub_files = []
                for fobj in (parent.get("files") or []):
                    fn = fobj.get("short_name") or fobj.get("name") or ""
                    if os.path.splitext(fn)[1].lower() in SUBTITLE_EXTS:
                        sub_files.append(
                            {
                                "file_id": str(fobj.get("id") if fobj.get("id") is not None else "0"),
                                "file_name": fn,
                            }
                        )
                if pid and sub_files:
                    index[(kind, pid)] = sub_files
        except Exception:
            continue
    return index


def download_torbox_cloud_subtitle_from_index(
    item: Dict[str, Any],
    torbox_api_key: str,
    sub_index: Dict[Tuple[str, str], List[Dict[str, Any]]],
    target_language: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    if not torbox_api_key or item.get("source_type") != "cloud":
        return None
    if target_language not in (None, "en", "id"):
        return None
    kind = item.get("torbox_kind") or "torrent"
    pid = str(item.get("torbox_item_id") or "")
    candidates = sub_index.get((kind, pid)) or []
    if not candidates:
        return None

    vid_stem = os.path.splitext(item.get("file_name") or "")[0].lower()
    vid_ep_tag = _extract_ep_tag(vid_stem)
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for candidate in candidates:
        lang = _detect_lang_from_name(candidate.get("file_name", ""))
        if lang not in ("en", "id"):
            continue
        if target_language and lang != target_language:
            continue
        c_stem = os.path.splitext(candidate.get("file_name") or "")[0].lower()
        c_ep_tag = _extract_ep_tag(c_stem)
        # When the video identifies an episode, do not risk attaching an untagged or
        # differently-tagged subtitle from another episode in the same TorBox item.
        if vid_ep_tag and c_ep_tag != vid_ep_tag:
            continue
        name_match = 0 if (vid_stem and (vid_stem in c_stem or c_stem in vid_stem)) else 1
        lang_prio = 0 if lang == "en" else 1
        scored.append((name_match * 10 + lang_prio, candidate))

    if not scored:
        return None
    scored.sort(key=lambda row: row[0])
    chosen = scored[0][1]
    chosen_lang = _detect_lang_from_name(chosen.get("file_name", ""))
    if chosen_lang not in ("en", "id") or (target_language and chosen_lang != target_language):
        return None

    headers = {"Authorization": f"Bearer {torbox_api_key.strip()}"}
    req_url = f"{TORBOX_API_BASE}/{'torrents' if kind == 'torrent' else 'usenet'}/requestdl"
    try:
        r = HTTP_SESSION.get(
            req_url,
            headers=headers,
            params={
                "token": torbox_api_key.strip(),
                "torrent_id" if kind == "torrent" else "usenet_id": pid,
                "file_id": chosen["file_id"],
                "zip_link": "false",
            },
            timeout=12,
        )
        if r.status_code == 200:
            dl_url = r.json().get("data")
            if dl_url and isinstance(dl_url, str) and dl_url.startswith("http"):
                sub_resp = HTTP_SESSION.get(dl_url, timeout=12)
                if sub_resp.status_code == 200:
                    ext = os.path.splitext(chosen["file_name"])[1].lower() or ".srt"
                    digest = hashlib.sha256((item.get("unique_key") or str(item.get("id"))).encode()).hexdigest()[:20]
                    saved = _save_subtitle_bytes(sub_resp.content, f"tb_{digest}_{chosen_lang}", ext=ext)
                    if saved:
                        return saved, chosen_lang
    except Exception:
        pass
    return None


def find_torbox_cloud_subtitle_for_item(
    item: Dict[str, Any], torbox_api_key: str, target_language: Optional[str] = None
) -> Optional[Tuple[str, str]]:
    if not torbox_api_key or item.get("source_type") != "cloud":
        return None
    idx = build_torbox_subtitle_index(torbox_api_key)
    return download_torbox_cloud_subtitle_from_index(item, torbox_api_key, idx, target_language)


def _search_subtitlecat_jav(
    code_id: str, dest_base_name: str, target_language: str
) -> Optional[Tuple[str, str]]:
    """Search SubtitleCat by JAV code and download only an explicitly tagged language."""
    if not code_id or target_language not in ("en", "id"):
        return None
    try:
        url = f"https://www.subtitlecat.com/index.php?search={quote_plus(code_id)}"
        resp = HTTP_SESSION.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        target_href = None
        for anchor in soup.select("table.sub-table tbody tr td a"):
            if code_id.upper() in anchor.get_text(" ", strip=True).upper():
                target_href = anchor.get("href")
                break
        if not target_href:
            return None
        detail_url = target_href if target_href.startswith("http") else f"https://www.subtitlecat.com/{target_href.lstrip('/')}"
        detail_resp = HTTP_SESSION.get(detail_url, timeout=10)
        if detail_resp.status_code != 200:
            return None
        detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
        chosen_dl = None
        for link in detail_soup.select("a.green-link"):
            href = link.get("href") or ""
            label = link.get_text(" ", strip=True)
            detected = _detect_lang_from_name(f"{href} {label}")
            if detected == target_language:
                chosen_dl = href
                break
        if not chosen_dl:
            return None
        full_dl = chosen_dl if chosen_dl.startswith("http") else f"https://www.subtitlecat.com/{chosen_dl.lstrip('/')}"
        sub_resp = HTTP_SESSION.get(full_dl, timeout=10)
        if sub_resp.status_code != 200:
            return None
        saved = _save_subtitle_bytes(sub_resp.content, f"{dest_base_name}_{target_language}", ext=".srt")
        return (saved, target_language) if saved else None
    except Exception:
        return None


def _search_opensubtitles_rest(
    query_title: str,
    dest_base_name: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    target_language: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    if not query_title:
        return None
    language_codes = {"en": "eng", "id": "ind"}
    requested_codes = [language_codes[target_language]] if target_language in language_codes else ["eng", "ind"]
    for lang_code in requested_codes:
        try:
            parts = [f"https://rest.opensubtitles.org/search/query-{quote_plus(query_title)}"]
            if season is not None:
                parts.append(f"season-{int(season)}")
            if episode is not None:
                parts.append(f"episode-{int(episode)}")
            parts.append(f"sublanguageid-{lang_code}")
            search_url = "/".join(parts)

            resp = HTTP_SESSION.get(
                search_url,
                headers={"User-Agent": "TemporaryUserAgent", "Accept": "application/json"},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                continue
            # Phase 7.1: rank provider candidates using available metadata and
            # discard only explicit language/year/season/episode conflicts.
            try:
                from subtitle_match_validator import rank_candidates
                ranked = rank_candidates(
                    rows,
                    title=query_title,
                    year=None,
                    season=season,
                    episode=episode,
                    language=("en" if lang_code == "eng" else "id"),
                )
                if not ranked:
                    continue
                best = ranked[0][0]
            except Exception:
                # Preserve provider availability if optional diagnostics fail.
                best = rows[0]
            dl_link = best.get("SubDownloadLink") or best.get("ZipDownloadLink")
            if not dl_link:
                continue
            dl_resp = HTTP_SESSION.get(dl_link, timeout=12)
            if dl_resp.status_code != 200:
                continue

            raw = dl_resp.content
            short_lang = "en" if lang_code == "eng" else "id"
            lang_dest = f"{dest_base_name}_{short_lang}"
            if dl_link.endswith(".gz"):
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
                saved = _save_subtitle_bytes(raw, lang_dest, ext=".srt")
                if saved:
                    return saved, short_lang
            elif zipfile.is_zipfile(io.BytesIO(raw)):
                extracted = _extract_subtitle_from_zip(raw, lang_dest, target_language=short_lang)
                if extracted:
                    return extracted
            else:
                saved = _save_subtitle_bytes(raw, lang_dest, ext=".srt")
                if saved:
                    return saved, short_lang
        except Exception:
            continue
    return None


def fetch_subtitle_for_language(
    item: Dict[str, Any],
    language: str,
    torbox_api_key: str = "",
    torbox_sub_index: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one explicitly requested language, reusing local/Cloud tracks before providers."""
    language = (language or "").strip().lower()
    if language not in ("en", "id"):
        raise ValueError("Language must be 'en' or 'id'.")

    if item.get("source_type") == "local":
        from dual_subtitle_engine import scan_item_subtitles
        for row in scan_item_subtitles(item):
            if row.get("language") == language:
                return {"subtitle_path": row["subtitle_path"], "subtitle_lang": language,
                        "subtitle_source": row.get("subtitle_source", "local"),
                        "is_embedded": row.get("is_embedded", False),
                        "stream_index": row.get("stream_index")}

    category = item.get("content_category") or "uncategorized"
    if category == "english_adult":
        return None

    digest = hashlib.sha256((item.get("unique_key") or str(item.get("id"))).encode()).hexdigest()[:20]
    dest_base = f"sub_{digest}"

    # Cloud subtitle files are checked before external providers. A caller may pass a
    # prebuilt index for batch work; API/manual calls build the index on demand.
    if item.get("source_type") == "cloud" and torbox_api_key:
        if torbox_sub_index is None:
            torbox_sub_index = build_torbox_subtitle_index(torbox_api_key)
        cloud_result = download_torbox_cloud_subtitle_from_index(
            item, torbox_api_key, torbox_sub_index, target_language=language
        )
        if cloud_result and cloud_result[1] == language:
            return {"subtitle_path": cloud_result[0], "subtitle_lang": language,
                    "subtitle_source": "torbox_cloud", "is_embedded": False}

    season = item.get("season_num")
    episode = item.get("episode_num")
    if category == "jav":
        code_id = (item.get("code_id") or "").strip()
        if code_id:
            sc_result = _search_subtitlecat_jav(code_id, dest_base, target_language=language)
            if sc_result and sc_result[1] == language:
                return {"subtitle_path": sc_result[0], "subtitle_lang": language,
                        "subtitle_source": "subtitlecat", "is_embedded": False}
            # JAV fallback uses the catalog code, not a potentially noisy display title.
            os_result = _search_opensubtitles_rest(
                code_id, dest_base, target_language=language
            )
        else:
            # Without a catalog code, use the best available title conservatively.
            title_q = item.get("title") or item.get("file_name") or ""
            os_result = _search_opensubtitles_rest(
                title_q, dest_base, target_language=language
            )
    else:
        if season is not None or episode is not None:
            title_q = item.get("series_title") or item.get("title") or item.get("file_name") or ""
        else:
            title_q = item.get("title") or item.get("file_name") or ""
        os_result = _search_opensubtitles_rest(
            title_q, dest_base, season=season, episode=episode, target_language=language
        )

    if os_result:
        path, detected_language = os_result
        if detected_language == language:
            return {"subtitle_path": path, "subtitle_lang": language,
                    "subtitle_source": "opensubtitles", "is_embedded": False}
    return None


_SUBTITLE_LANG_ALIASES = {
    "en": "en", "eng": "en", "english": "en", "en-us": "en", "en-gb": "en",
    "id": "id", "ind": "id", "indonesian": "id", "indonesia": "id", "bahasa": "id", "id-id": "id",
}
_SUBTITLE_CONTENT_TYPE_EXTS = {
    "application/x-subrip": ".srt",
    "text/srt": ".srt",
    "text/vtt": ".vtt",
    "text/x-ssa": ".ssa",
    "text/x-ass": ".ass",
    "application/x-ass": ".ass",
}
PROVIDED_SUBTITLE_MAX_BYTES = 5 * 1024 * 1024
PROVIDED_SUBTITLE_LIMIT = 8
PROVIDED_SUBTITLE_MAX_REDIRECTS = 3


def normalize_subtitle_language(value: Any) -> Optional[str]:
    """Map a provider language tag to the two Library languages (en/id)."""
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return None
    if text in _SUBTITLE_LANG_ALIASES:
        return _SUBTITLE_LANG_ALIASES[text]
    return _SUBTITLE_LANG_ALIASES.get(re.split(r"[-.]", text)[0])


def _provided_subtitle_url_is_public(url: str) -> bool:
    """Reuse the poster SSRF validator for provider-supplied subtitle URLs."""
    try:
        import metadata_engine as meta_engine
        allowed, _reason = meta_engine._poster_url_is_public(url)
        return bool(allowed)
    except Exception:
        return False


def _read_subtitle_response(response: Any, max_bytes: int) -> Optional[bytes]:
    try:
        length = (getattr(response, "headers", {}) or {}).get("Content-Length")
        if length and int(length) > max_bytes:
            return None
    except (TypeError, ValueError):
        pass
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks: List[bytes] = []
        total = 0
        for chunk in iterator(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    raw = getattr(response, "content", b"") or b""
    return raw if len(raw) <= max_bytes else None


def _download_provided_subtitle(url: str, dest_base: str, max_bytes: int) -> Optional[str]:
    current = str(url or "").strip()
    for _hop in range(PROVIDED_SUBTITLE_MAX_REDIRECTS + 1):
        if not current or not _provided_subtitle_url_is_public(current):
            return None
        try:
            response = HTTP_SESSION.get(
                current, stream=True, allow_redirects=False, timeout=(6, 15)
            )
        except requests.RequestException:
            return None
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            if status in (301, 302, 303, 307, 308):
                location = (getattr(response, "headers", {}) or {}).get("Location") or ""
                current = urljoin(current, location)
                continue
            if status != 200:
                return None
            content_type = str(
                (getattr(response, "headers", {}) or {}).get("Content-Type") or ""
            ).split(";")[0].strip().lower()
            raw = _read_subtitle_response(response, max_bytes)
        finally:
            try:
                response.close()
            except Exception:
                pass
        if not raw:
            return None
        ext = os.path.splitext(urlparse(current).path)[1].lower()
        if ext not in SUBTITLE_EXTS:
            ext = _SUBTITLE_CONTENT_TYPE_EXTS.get(content_type, ".srt")
        return _save_subtitle_bytes(raw, dest_base, ext)
    return None


def fetch_provided_subtitles(
    entries: Any,
    item: Dict[str, Any],
    max_bytes: int = PROVIDED_SUBTITLE_MAX_BYTES,
) -> List[Dict[str, Any]]:
    """Download subtitle tracks supplied by the Roxxy stream response.

    Only English/Indonesian tracks are accepted; the URL is validated per
    redirect hop with the existing poster SSRF guard and capped in size.
    """
    if not isinstance(entries, list):
        return []
    digest = hashlib.sha256(
        str(item.get("unique_key") or item.get("id") or item.get("file_name") or "").encode()
    ).hexdigest()[:20]
    results: List[Dict[str, Any]] = []
    seen = set()
    for entry in entries[:PROVIDED_SUBTITLE_LIMIT]:
        if not isinstance(entry, dict):
            continue
        language = normalize_subtitle_language(
            entry.get("lang") or entry.get("language") or entry.get("langName") or entry.get("name")
        )
        if language not in ("en", "id") or language in seen:
            continue
        url = str(entry.get("url") or "").strip()
        if not url or len(url) > 2048:
            continue
        path = _download_provided_subtitle(url, f"sub_{digest}_{language}", max_bytes)
        if not path:
            continue
        seen.add(language)
        results.append(
            {
                "subtitle_path": path,
                "subtitle_lang": language,
                "subtitle_source": "roxxy",
                "is_embedded": False,
            }
        )
    return results


def fetch_subtitle_for_item(
    item: Dict[str, Any],
    torbox_api_key: str = "",
    torbox_sub_index: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    digest = hashlib.sha256((item.get("unique_key") or str(item.get("id"))).encode()).hexdigest()[:20]
    dest_base = f"sub_{digest}"
    category = item.get("content_category") or "uncategorized"

    # 1. Check local sidecar
    if item.get("source_type") == "local":
        sidecar = find_local_sidecar_subtitle(item.get("file_path"))
        if sidecar and sidecar[1] in ("en", "id"):
            return {"subtitle_path": sidecar[0], "subtitle_lang": sidecar[1], "subtitle_source": "local_sidecar"}

    # 2. Check subtitle file inside TorBox Cloud
    if item.get("source_type") == "cloud" and torbox_api_key:
        if torbox_sub_index is not None:
            tb_sub = download_torbox_cloud_subtitle_from_index(item, torbox_api_key, torbox_sub_index)
        else:
            tb_sub = find_torbox_cloud_subtitle_for_item(item, torbox_api_key)
        if tb_sub and tb_sub[1] in ("en", "id"):
            return {"subtitle_path": tb_sub[0], "subtitle_lang": tb_sub[1], "subtitle_source": "torbox_cloud"}

    # 3. Check embedded subtitle stream (local file or TorBox Cloud)
    emb = check_embedded_subtitle(item, torbox_api_key=torbox_api_key)
    if emb:
        return {"subtitle_path": emb[0], "subtitle_lang": emb[1], "subtitle_source": "embedded"}

    # 4. Stop public search for English Adult
    if category == "english_adult":
        return None

    # 5. JAV -> SubtitleCat then OpenSubtitles
    if category == "jav":
        code_id = item.get("code_id") or ""
        if code_id:
            sc_res = _search_subtitlecat_jav(code_id, dest_base, target_language="en")
            if not sc_res:
                sc_res = _search_subtitlecat_jav(code_id, dest_base, target_language="id")
            if sc_res:
                return {"subtitle_path": sc_res[0], "subtitle_lang": sc_res[1], "subtitle_source": "subtitlecat"}
            os_res = _search_opensubtitles_rest(code_id, dest_base)
            if os_res:
                return {"subtitle_path": os_res[0], "subtitle_lang": os_res[1], "subtitle_source": "opensubtitles"}
        return None

    # 6. Movie / TV Show / Anime / Donghua
    season = item.get("season_num")
    episode = item.get("episode_num")
    if season is not None or episode is not None:
        title_q = item.get("series_title") or item.get("title") or item.get("file_name") or ""
    else:
        title_q = item.get("title") or item.get("file_name") or ""

    os_res = _search_opensubtitles_rest(title_q, dest_base, season=season, episode=episode)
    if os_res:
        return {"subtitle_path": os_res[0], "subtitle_lang": os_res[1], "subtitle_source": "opensubtitles"}

    return None