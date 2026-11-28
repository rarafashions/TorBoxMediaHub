"""Optional direct-stream extraction for English Adult discovery items.

TorBox WebDL resolves a provider page on its own; the catalogue therefore only
needs the page URL. The detail modal also offers the local Roxxy engines
(yt-dlp/aria2c), and those need a direct media URL. Rather than guessing, each
adapter below reads only URLs the provider page already publishes in its
markup (JSON-LD/player init JSON/flashvars).

Safety rules:

- Only ``https`` media URLs are accepted; credentials, private hosts and
  non-media schemes are rejected.
- A provider without an adapter is reported as unsupported instead of being
  fetched speculatively.
- Results are cached for ten minutes so reopening a modal does not refetch.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import adult_discovery as adult_disc
import adult_providers as adult_providers

_MAX_STREAMS = 12
_CACHE_TTL_SECONDS = 10 * 60
_CACHE_MAX = 120
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

_LABELS: Dict[str, str] = {
    key: str(spec.get("label") or key.title())
    for key, spec in adult_providers.PROVIDERS.items()
}

# Providers whose page keeps its media URLs behind a browser-token player or a
# login/Cloudflare wall. They stay catalogue- and WebDL-capable; only the local
# stream list is unavailable, and the UI says so.
_UNSUPPORTED: Dict[str, str] = {
    "xhamster": (
        "XHamster memuat url stream lewat pemutar ber-token yang tidak dapat "
        "dibaca tanpa browser. Gunakan TorBox WebDL atau buka halaman asli."
    ),
    "youporn": (
        "YouPorn sedang login-walled, jadi sumber stream tidak dapat diambil. "
        "Gunakan TorBox WebDL atau buka halaman asli."
    ),
    "spankbang": (
        "SpankBang memblokir akses otomatis (Cloudflare), jadi sumber stream "
        "tidak dapat diambil. Tempel halaman ke TorBox WebDL atau buka manual."
    ),
}

_FETCH_MESSAGES: Dict[str, str] = {
    "timeout": "Waktu permintaan halaman provider habis. Coba lagi.",
    "blocked": (
        "Provider menolak akses otomatis (Cloudflare/login). Gunakan TorBox "
        "WebDL atau tempel URL manual."
    ),
    "rate_limited": "Provider membatasi permintaan. Coba lagi beberapa menit.",
    "too_large": "Halaman provider terlalu besar untuk dianalisis.",
    "http_error": "Provider mengembalikan respons HTTP yang tidak valid.",
    "network": "Tidak dapat menghubungi halaman provider.",
    "parse_error": "Struktur halaman provider tidak dikenali.",
}

_MEDIA_EXT_RE = re.compile(r"\.(mp4|m3u8)$", re.I)
_BAD_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def reset_state_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# --------------------------------------------------------------------------- #
# URL and stream normalisation
# --------------------------------------------------------------------------- #


def _media_url(raw: Any) -> str:
    """Return a safe https media URL or an empty string."""
    text = html_lib.unescape(str(raw or "").strip())
    if text.startswith("//"):
        text = "https:" + text
    elif text.startswith("http://"):
        text = "https://" + text[7:]
    if not text.startswith("https://"):
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if not parsed.hostname or parsed.username or parsed.password:
        return ""
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return ""
    if adult_disc._host_is_private(parsed.hostname):
        return ""
    return text


def _extension(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return ".m3u8"
    return ".mp4"


def _filename(title: Any, url: str) -> str:
    stem = _BAD_FILENAME_RE.sub(" ", str(title or "")).strip(" .")
    return (stem[:110] or "video") + _extension(url)


def _quality_rank(value: Any) -> int:
    text = str(value or "").lower()
    match = re.search(r"(\d{3,4})", text)
    if match:
        return int(match.group(1))
    if "hd" in text:
        return 720
    if "sd" in text:
        return 480
    if "hls" in text:
        return 100
    return 0


def _qualify(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return f"{text}p"
    return text


def _stream(
    provider: str,
    title: str,
    url: str,
    quality: str = "",
    label: str = "",
) -> Optional[Dict[str, Any]]:
    safe = _media_url(url)
    if not safe:
        return None
    site = _LABELS.get(provider, provider.title())
    ext = _extension(safe)
    suffix = label or ("HLS" if ext == ".m3u8" else "MP4")
    parts = [site]
    # "MP4" is the implicit default; only a meaningful renditions label (HLS,
    # an explicit quality tag) is spelled out next to the site name.
    if suffix and suffix != "MP4":
        parts.append(suffix)
    if quality and quality != suffix:
        parts.append(quality)
    if not quality and suffix == "MP4":
        parts.append("MP4")
    display = " · ".join(parts)
    return {
        "name": display,
        "title": display,
        "url": safe,
        "quality": quality,
        "behaviorHints": {"filename": _filename(title, safe)},
        "playable": True,
        "route_hint": "yt_dlp_candidate" if ext == ".m3u8" else "aria2_candidate",
        "site": provider,
        "site_label": site,
    }


def _finalise(provider: str, title: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    streams: List[Dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            continue
        stream = _stream(
            provider,
            title,
            url,
            _qualify(row.get("quality")),
            str(row.get("label") or ""),
        )
        if stream:
            seen.add(url)
            streams.append(stream)
    streams.sort(key=lambda s: _quality_rank(s.get("quality")), reverse=True)
    return streams[:_MAX_STREAMS]


def _text(markup: str) -> str:
    return html_lib.unescape(str(markup or "").replace("\\/", "/"))


# --------------------------------------------------------------------------- #
# Page metadata (cover, synopsis, duration published by the provider)
# --------------------------------------------------------------------------- #

_META_MAX_DESCRIPTION = 600


def _clean_text(value: Any, limit: int = _META_MAX_DESCRIPTION) -> str:
    text = " ".join(html_lib.unescape(str(value or "")).split())
    return text[:limit]


_GENERIC_PAGE_TITLE = re.compile(
    r"age[- ]restricted|embed video|just a moment|attention required|access denied|"
    r"enter your birth|robots? check|verify you are human",
    re.I,
)


def _page_title(raw: Any) -> str:
    """Drop boilerplate page titles (age walls, embed shells, bot checks)."""
    text = _clean_text(raw, 300)
    if not text or _GENERIC_PAGE_TITLE.search(text):
        return ""
    return text


def _poster_url(raw: Any) -> str:
    """Accept a public http(s) cover URL; mirrors the media URL guard."""
    text = html_lib.unescape(str(raw or "").strip())
    if text.startswith("//"):
        text = "https:" + text
    if not text.lower().startswith(("http://", "https://")):
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if not parsed.hostname or parsed.username or parsed.password:
        return ""
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return ""
    if adult_disc._host_is_private(parsed.hostname):
        return ""
    return text


_THUMB_SIZE = re.compile(r"_(\d{2,4})\.(?:jpe?g|webp|png)", re.I)


def _thumb_rank(url: str) -> int:
    match = _THUMB_SIZE.search(str(url or ""))
    return int(match.group(1)) if match else 0


def _iso_duration(value: Any) -> int:
    """``PT12M34S`` (JSON-LD) to seconds; plain numbers are accepted as-is."""
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text, re.I)
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else 0


def _jsonld_videos(payload: Any, depth: int = 0) -> List[Dict[str, Any]]:
    """Every ``VideoObject``-ish node in a JSON-LD payload."""
    found: List[Dict[str, Any]] = []
    if depth > 6:
        return found
    if isinstance(payload, list):
        for entry in payload:
            found.extend(_jsonld_videos(entry, depth + 1))
        return found
    if not isinstance(payload, dict):
        return found
    node_type = payload.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    if any(str(item or "").lower() in {"videoobject", "videoplayer"} for item in types):
        found.append(payload)
    for key in ("@graph", "itemListElement", "video"):
        if key in payload:
            found.extend(_jsonld_videos(payload[key], depth + 1))
    return found


def page_metadata(markup: Any) -> Dict[str, Any]:
    """Cover, synopsis and duration the provider page publishes about itself.

    Custom videos usually have no TPDB entry, but every provider still ships an
    OpenGraph card (and often a JSON-LD ``VideoObject``). Reading those fields
    directly keeps the cover and synopsis honest without guessing.
    """
    text = str(markup or "")
    if not text:
        return {}
    try:
        soup = BeautifulSoup(text, "html.parser")
    except Exception:
        return {}

    def meta_content(*names: str) -> str:
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find(
                "meta", attrs={"name": name}
            )
            if tag:
                value = str(tag.get("content") or "").strip()
                if value:
                    return value
        return ""

    title = _clean_text(_page_title(meta_content("og:title", "twitter:title")), 300)
    described = _clean_text(
        meta_content("og:description", "twitter:description", "description")
    )
    poster = _poster_url(
        meta_content("og:image:secure_url", "og:image", "twitter:image")
    )
    duration = _iso_duration(meta_content("og:video:duration"))
    thumbnails: List[str] = []

    if not title:
        tag = soup.find("title")
        if tag:
            title = _clean_text(_page_title(tag.get_text()), 300)

    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for video in _jsonld_videos(payload):
            json_title = _page_title(video.get("name"))
            json_description = _clean_text(video.get("description"))
            thumbnail = video.get("thumbnailUrl")
            if isinstance(thumbnail, str):
                thumbnails.append(thumbnail)
            elif isinstance(thumbnail, list):
                thumbnails.extend(str(entry) for entry in thumbnail if entry)
            # A provider's JSON-LD description is usually the full synopsis,
            # while OpenGraph carries the short social blurb.
            if len(json_description) > len(described):
                described = json_description
            if not title and json_title:
                title = json_title
            if not duration:
                duration = _iso_duration(video.get("duration"))

    # JSON-LD often lists the same cover at several sizes; keep the largest
    # one that passes the public-URL guard.
    candidates = [poster] + [_poster_url(entry) for entry in thumbnails]
    candidates = [entry for entry in candidates if entry]
    if candidates:
        poster = max(candidates, key=_thumb_rank)

    result: Dict[str, Any] = {}
    if title:
        result["title"] = title
    if described:
        result["description"] = described
    if poster:
        result["poster"] = poster
    if duration:
        result["duration_sec"] = duration
    return result


# --------------------------------------------------------------------------- #
# Provider adapters (pure functions over page markup)
# --------------------------------------------------------------------------- #


def _parse_eporner(markup: str, title: str) -> List[Dict[str, Any]]:
    """Eporner's embed page publishes the direct ``gvideo`` file."""
    text = _text(markup)
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    def add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            rows.append({"url": url, "quality": "", "label": "MP4"})

    for match in re.finditer(
        r"https://gvideo\.eporner\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)\.mp4",
        text,
    ):
        if match.group(1) == match.group(2):
            add(match.group(0))
    for match in re.finditer(
        r'data-vid="([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)\.mp4"', text
    ):
        add(f"https://gvideo.eporner.com/{match.group(1)}/{match.group(2)}.mp4")
    return rows


def _parse_html5player(markup: str, title: str) -> List[Dict[str, Any]]:
    """XVideos/XNXX ``embedframe`` publishes the SD MP4 and the HLS manifest."""
    text = _text(markup)
    rows: List[Dict[str, Any]] = []
    for pattern, quality, label in (
        (r"setVideoUrlHigh\('([^']+)'\)", "SD", "MP4"),
        (r"setVideoHLS\('([^']+)'\)", "HLS", "HLS"),
    ):
        for match in re.finditer(pattern, text):
            rows.append({"url": match.group(1), "quality": quality, "label": label})
    return rows


def _parse_youjizz(markup: str, title: str) -> List[Dict[str, Any]]:
    """YouJizz embeds ``var dataEncodings = [...]`` with every rendition."""
    text = _text(markup)
    match = re.search(r"var\s+dataEncodings\s*=\s*(\[.*?\])\s*;", text, re.S)
    if not match:
        return []
    try:
        entries = json.loads(match.group(1))
    except (TypeError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("filename") or "").strip()
        if not url:
            continue
        quality = _qualify(entry.get("quality"))
        label = "HLS" if urlparse(url).path.lower().endswith(".m3u8") else "MP4"
        rows.append({"url": url, "quality": quality, "label": label})
    return rows


def _parse_flashvars(markup: str, title: str) -> List[Dict[str, Any]]:
    """XXXYMovies/SunPorno keep ``video_url`` (and sometimes a login alt) in JS."""
    text = _text(markup)
    rows: List[Dict[str, Any]] = []
    for match in re.finditer(r"video_url(?:_\d+)?\s*:\s*'([^']+)'", text):
        rows.append({"url": match.group(1), "quality": "", "label": "MP4"})
    for match in re.finditer(r"video_alt_url(?:_\d+)?\s*:\s*'([^']+)'", text):
        url = match.group(1)
        low = url.lower()
        if any(word in low for word in ("signup", "login", "register", "javascript:")):
            continue
        if "/get_file/" in low or low.endswith((".mp4", ".mp4/")):
            rows.append({"url": url, "quality": "HD", "label": "MP4"})
    return rows


def _parse_pornhub(markup: str, title: str) -> List[Dict[str, Any]]:
    """Pornhub lists renditions inside ``mediaDefinitions`` in flashvars JSON."""
    text = _text(markup)
    rows: List[Dict[str, Any]] = []
    for match in re.finditer(r'"mediaDefinitions"\s*:\s*(\[.*?\])\s*,\s*"', text, re.S):
        try:
            definitions = json.loads(match.group(1))
        except (TypeError, ValueError):
            continue
        if not isinstance(definitions, list):
            continue
        for entry in definitions:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("videoUrl") or entry.get("url") or "").strip()
            if not url:
                continue
            quality = _qualify(entry.get("quality"))
            is_hls = str(entry.get("format") or "").lower() == "hls" or ".m3u8" in url.lower()
            rows.append(
                {
                    "url": url,
                    "quality": quality,
                    "label": "HLS" if is_hls else "MP4",
                }
            )
    return rows


_ADAPTERS: Dict[str, Callable[[str, str], List[Dict[str, Any]]]] = {
    "eporner": _parse_eporner,
    "xvideos": _parse_html5player,
    "xnxx": _parse_html5player,
    "youjizz": _parse_youjizz,
    "xxxymovies": _parse_flashvars,
    "sunporno": _parse_flashvars,
    "pornhub": _parse_pornhub,
}


# --------------------------------------------------------------------------- #
# Fetching and caching
# --------------------------------------------------------------------------- #


def _safe_id(item: Dict[str, Any]) -> str:
    raw = str(item.get("provider_id") or "").strip()
    return re.sub(r"[^A-Za-z0-9._-]", "", raw)[:80]


def _eporner_page(item: Dict[str, Any]) -> str:
    """Prefer the id embedded in the page URL: a pasted link has a hashed id."""
    match = re.search(r"/video-([A-Za-z0-9]+)", str(item.get("page_url") or ""))
    ident = match.group(1) if match else _safe_id(item)
    return f"https://www.eporner.com/embed/{ident}/" if ident else ""


def _xvideos_page(item: Dict[str, Any]) -> str:
    match = re.search(r"/video\.([A-Za-z0-9]+)", str(item.get("page_url") or ""))
    ident = match.group(1) if match else _safe_id(item)
    return f"https://www.xvideos.com/embedframe/{ident}" if ident else ""


def _xnxx_page(item: Dict[str, Any]) -> str:
    match = re.search(r"/video-([A-Za-z0-9]+)", str(item.get("page_url") or ""))
    ident = match.group(1) if match else _safe_id(item)
    return f"https://www.xnxx.com/embedframe/{ident}" if ident else ""


def _page_url(item: Dict[str, Any]) -> str:
    return str(item.get("page_url") or "").strip()


_PAGE_BUILDERS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "eporner": _eporner_page,
    "xvideos": _xvideos_page,
    "xnxx": _xnxx_page,
}

# Providers whose stream page is an embed shell, so the real page is also read
# for its OpenGraph card. Eporner is the opposite: the embed page carries the
# card and the real page is a JavaScript shell.
_META_FROM_REAL_PAGE = {"xvideos", "xnxx"}


def _fetch_html(url: str, provider: str) -> Dict[str, Any]:
    """Indirection point so tests never touch the network."""
    return adult_disc._request_html(url, provider)


def _fetch_provider_page(url: str, provider: str) -> Dict[str, Any]:
    """One polite retry for transient transport failures, then breaker state."""
    page = _fetch_html(url, provider)
    if page.get("ok"):
        adult_disc._record_success(provider)
        return page
    code = str(page.get("code") or "network")
    if code in {"network", "timeout", "http_error"}:
        time.sleep(0.8)
        page = _fetch_html(url, provider)
        if page.get("ok"):
            adult_disc._record_success(provider)
            return page
        code = str(page.get("code") or code)
    adult_disc._record_failure(provider, code)
    return page


def _provider_metadata(
    provider: str, page_url: str, fetch_url: str, markup: str
) -> Dict[str, Any]:
    """Cover and synopsis from the page a visitor sees, not the embed shell.

    XVideos/XNXX serve streams from ``embedframe`` pages whose OpenGraph card is
    generic ("Xvideos.com embed video"), so their real page is read as well.
    Any failure there is silent: streams still work, the card just shows less.
    """
    meta = page_metadata(markup)
    if provider not in _META_FROM_REAL_PAGE:
        return meta
    if not page_url or page_url.rstrip("/") == str(fetch_url or "").rstrip("/"):
        return meta
    try:
        page = _fetch_html(page_url, provider)
    except Exception:
        return meta
    if not page.get("ok"):
        return meta
    merged = page_metadata(page.get("html"))
    for key, value in meta.items():
        if not merged.get(key):
            merged[key] = value
    return merged


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        stored_at, payload = entry
        if now - stored_at > _CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
    return dict(payload)


def _cache_set(key: str, payload: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), dict(payload))
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))


def _fetch_error(provider: str, code: Any) -> adult_disc.AdultDiscoveryError:
    label = _LABELS.get(provider, provider.title())
    message = _FETCH_MESSAGES.get(str(code or ""), "Gagal mengambil halaman provider.")
    return adult_disc.AdultDiscoveryError(f"{label}: {message}", 502)


def streams_for(item: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """Extract the local-download stream list for one catalogue item."""
    provider = str(item.get("provider") or "").strip().lower()
    spec = adult_providers.get_spec(provider)
    if not spec:
        raise adult_disc.AdultDiscoveryError("Sumber adult tidak dikenal.", 400)
    page_url = _page_url(item)
    if not adult_providers.url_belongs_to(provider, page_url):
        raise adult_disc.AdultDiscoveryError(
            "URL halaman bukan milik sumber tersebut.", 400
        )
    key = f"{provider}:{item.get('provider_id') or page_url}"
    if not force:
        cached = _cache_get(key)
        if cached is not None:
            return {**cached, "cached": True}

    if provider in _UNSUPPORTED:
        result: Dict[str, Any] = {
            "streams": [],
            "page_meta": {},
            "warnings": [
                {
                    "provider": provider,
                    "code": "streams_unsupported",
                    "message": _UNSUPPORTED[provider],
                }
            ],
        }
        _cache_set(key, result)
        return {**result, "cached": False}

    adapter = _ADAPTERS.get(provider)
    if not adapter:
        result = {
            "streams": [],
            "page_meta": {},
            "warnings": [
                {
                    "provider": provider,
                    "code": "streams_unsupported",
                    "message": (
                        f"Sumber stream langsung untuk {_LABELS.get(provider, provider)} "
                        "belum didukung. Gunakan TorBox WebDL atau buka halaman asli."
                    ),
                }
            ],
        }
        _cache_set(key, result)
        return {**result, "cached": False}

    fetch_url = _PAGE_BUILDERS.get(provider, _page_url)(item)
    if not fetch_url:
        raise adult_disc.AdultDiscoveryError(
            "ID item tidak cukup untuk mengambil halaman stream; tempel URL halaman.",
            400,
        )
    page = _fetch_provider_page(fetch_url, provider)
    if not page.get("ok"):
        raise _fetch_error(provider, page.get("code"))

    title = str(item.get("title") or "")
    html = str(page.get("html") or "")
    rows = adapter(html, title)
    streams = _finalise(provider, title, rows)
    warnings: List[Dict[str, Any]] = []
    if not streams:
        warnings.append(
            {
                "provider": provider,
                "code": "streams_not_found",
                "message": (
                    f"Halaman {_LABELS.get(provider, provider)} tidak memuat URL media "
                    "langsung. Gunakan TorBox WebDL atau buka halaman asli."
                ),
            }
        )
    # The provider's own card beats a TPDB guess for titles the database does
    # not know, so it becomes part of the cached payload.
    result = {
        "streams": streams,
        "warnings": warnings,
        "page_meta": _provider_metadata(provider, page_url, fetch_url, html),
    }
    _cache_set(key, result)
    return {**result, "cached": False}