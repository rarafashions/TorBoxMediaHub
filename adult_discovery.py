"""English Adult discovery: providers -> catalog/search -> page URL.

The flow mirrors Roxxy in shape (source -> catalog -> item -> submit) without
touching ``roxxy_module``. The important difference is the last step: TorBox
WebDL needs the public *page* URL, so no media extraction or signed stream
resolution happens here.

Design rules:

- One slow or blocked provider must never fail the whole request. Every
  provider reports its own status and the response carries warnings.
- All network input is built from :mod:`adult_providers`; user text only ever
  reaches an encoded query parameter.
- Responses are cached in memory (10 minutes). A circuit breaker stops repeat
  hits on providers that block or break, with the remaining cooldown exposed
  to the UI instead of a stack trace.
- Error text never contains a URL, an exception body, or provider HTML.
"""

import hashlib
import ipaddress
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
)

import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException, Timeout

import adult_providers as providers

LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
HTTP_TIMEOUT = (4, 8)
MAX_BODY_BYTES = 3 * 1024 * 1024
CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 256
MAX_PAGE = 20
MAX_AGGREGATE_ITEMS = 60
BREAKER_FAILURES = 3
BREAKER_COOLDOWN_SECONDS = 900
MIN_REQUEST_INTERVAL_SECONDS = 1.5
MIN_QUERY_LEN = 2
MAX_QUERY_LEN = 120
MAX_TAGS = 8
MIN_PAGE_YIELD = 10

HTTP_SESSION = requests.Session()

_CACHE: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_STATE: Dict[str, Dict[str, Any]] = {}
_STATE_LOCK = threading.Lock()

_ERROR_MESSAGES = {
    "blocked": "{label} memblokir permintaan otomatis. Buka manual di browser lalu tempel URL-nya.",
    "rate_limited": "{label} membatasi permintaan. Coba lagi dalam beberapa menit.",
    "timeout": "{label} tidak merespons dalam 8 detik.",
    "network": "Koneksi ke {label} gagal.",
    "parse_error": "Struktur data {label} berubah; hasil dilewati.",
    "http_error": "{label} mengembalikan HTTP {status}.",
    "too_large": "Respons {label} terlalu besar untuk diproses.",
    "cooldown": "{label} sedang dibatasi. Coba lagi dalam {seconds} detik.",
    "mode_unsupported": "Mode katalog ini tidak tersedia di {label}.",
    "unknown_provider": "Sumber '{provider}' tidak dikenal dan dilewati.",
    "dropped_invalid": "{label}: {count} kartu dilewati karena URL halaman tidak valid.",
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class AdultDiscoveryError(Exception):
    """A request-level failure that maps to a 4xx/5xx API response."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Small state helpers
# --------------------------------------------------------------------------- #


def _state(provider: str) -> Dict[str, Any]:
    with _STATE_LOCK:
        state = _STATE.get(provider)
        if state is None:
            state = {
                "failures": 0,
                "cooldown_until": 0.0,
                "last_call": 0.0,
                "last_error": "",
                "last_ok_at": 0.0,
            }
            _STATE[provider] = state
        return state


def _breaker_remaining(provider: str) -> int:
    state = _state(provider)
    with _STATE_LOCK:
        remaining = state["cooldown_until"] - time.monotonic()
    return max(0, int(remaining + 0.999)) if remaining > 0 else 0


def _record_success(provider: str) -> None:
    state = _state(provider)
    with _STATE_LOCK:
        state["failures"] = 0
        state["cooldown_until"] = 0.0
        state["last_error"] = ""
        state["last_ok_at"] = time.time()


def _record_failure(provider: str, code: str) -> None:
    state = _state(provider)
    with _STATE_LOCK:
        state["last_error"] = code
        if code == "blocked":
            state["cooldown_until"] = time.monotonic() + BREAKER_COOLDOWN_SECONDS
            state["failures"] = BREAKER_FAILURES
            return
        state["failures"] = int(state["failures"]) + 1
        if state["failures"] >= BREAKER_FAILURES:
            state["cooldown_until"] = time.monotonic() + BREAKER_COOLDOWN_SECONDS


def _wait_turn(provider: str) -> None:
    """Reserve a per-provider slot so a fan-out stays polite."""
    state = _state(provider)
    with _STATE_LOCK:
        now = time.monotonic()
        earliest = float(state["last_call"]) + MIN_REQUEST_INTERVAL_SECONDS
        delay = max(0.0, min(MIN_REQUEST_INTERVAL_SECONDS, earliest - now))
        state["last_call"] = max(now, earliest)
    if delay > 0:
        time.sleep(delay)


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        stored_at, value = entry
        if now - stored_at > CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return value


def _cache_set(key: str, value: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)


def reset_state_for_tests() -> None:
    """Clear cache and breaker state. Tests only."""
    with _CACHE_LOCK:
        _CACHE.clear()
    with _STATE_LOCK:
        _STATE.clear()


def cached_item(provider: Any, provider_id: Any = "", page_url: Any = "") -> Optional[Dict[str, Any]]:
    """Find one item in the cached catalogue pages.

    Detail views need the full card (thumb, rating, tags) without re-fetching a
    listing. Only live cache entries are searched; a miss simply tells the UI
    to open the catalogue again or paste the page URL.
    """
    key = str(provider or "").strip().lower()
    pid = str(provider_id or "").strip()
    url = str(page_url or "").strip()
    if not key or not (pid or url):
        return None
    now = time.monotonic()
    with _CACHE_LOCK:
        entries = list(_CACHE.items())
    for _cache_key, (stored_at, payload) in entries:
        if now - stored_at > CACHE_TTL_SECONDS:
            continue
        for item in payload.get("items") or ():
            if str(item.get("provider") or "").lower() != key:
                continue
            if (pid and str(item.get("provider_id") or "") == pid) or (
                url and item.get("page_url") == url
            ):
                return dict(item)
    return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _read_body(response: Any) -> Tuple[Optional[str], bool]:
    """Read at most ``MAX_BODY_BYTES``; the second value marks truncation."""
    chunks: List[bytes] = []
    size = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        for chunk in iterator(65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                return None, True
            chunks.append(chunk)
        raw = b"".join(chunks)
    else:
        raw = getattr(response, "content", b"") or b""
        if len(raw) > MAX_BODY_BYTES:
            return None, True
    encoding = getattr(response, "encoding", None) or "utf-8"
    try:
        return raw.decode(encoding, "replace"), False
    except LookupError:
        return raw.decode("utf-8", "replace"), False


def _close(response: Any) -> None:
    closer = getattr(response, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _fetch(url: str, params: Optional[Dict[str, Any]], provider: str, accept: str) -> Dict[str, Any]:
    """GET a page and classify failures into stable codes."""
    _wait_turn(provider)
    try:
        response = HTTP_SESSION.get(
            url,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=HTTP_TIMEOUT,
            stream=True,
        )
    except Timeout:
        return {"ok": False, "code": "timeout"}
    except RequestException:
        return {"ok": False, "code": "network"}
    except Exception:
        return {"ok": False, "code": "network"}

    try:
        status = int(getattr(response, "status_code", 0) or 0)
        text, truncated = _read_body(response)
        if truncated or text is None:
            return {"ok": False, "code": "too_large"}
        lowered = text[:4000].lower()
        if status == 403 or "just a moment" in lowered or "cf-mitigated" in lowered:
            return {"ok": False, "code": "blocked"}
        if status == 429:
            return {"ok": False, "code": "rate_limited"}
        if status != 200:
            return {"ok": False, "code": "http_error", "status": status}
        if _is_login_wall(getattr(response, "url", ""), text):
            return {"ok": False, "code": "blocked"}
        return {"ok": True, "text": text}
    finally:
        _close(response)


_LOGIN_PATHS = ("/login", "/signin", "/sign-in", "/register", "/age-verification")
_LOGIN_TITLES = ("<title>login", "<title>sign in", "<title>log in", "<title>register")


def _is_login_wall(final_url: Any, text: str) -> bool:
    """Detect a soft login wall served with HTTP 200.

    YouPorn started answering unauthenticated listings with a redirect to
    ``/login/`` and a login page instead of videos. Reporting ``blocked`` is
    honest: retrying would only hammer the same wall. Only the final path and
    the page title are used; login forms embedded in a normal page must not
    flag the whole provider.
    """
    try:
        path = urlparse(str(final_url or "")).path.lower()
    except Exception:
        path = ""
    if path.startswith(_LOGIN_PATHS):
        return True
    head = str(text or "")[:4000].lower()
    return any(marker in head for marker in _LOGIN_TITLES)


def _request_json(url: str, params: Dict[str, Any], provider: str) -> Dict[str, Any]:
    result = _fetch(url, params, provider, "application/json, text/plain;q=0.8")
    if not result["ok"]:
        return result
    try:
        payload = json.loads(result["text"])
    except (TypeError, ValueError):
        return {"ok": False, "code": "parse_error"}
    if not isinstance(payload, dict):
        return {"ok": False, "code": "parse_error"}
    return {"ok": True, "payload": payload}


def _request_html(url: str, provider: str) -> Dict[str, Any]:
    result = _fetch(url, None, provider, "text/html,application/xhtml+xml;q=0.9")
    if not result["ok"]:
        return result
    return {"ok": True, "html": result["text"]}


# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #


def _text(node: Any, limit: int = 400) -> str:
    if node is None:
        return ""
    try:
        raw = node.get_text(" ")
    except Exception:
        raw = str(node)
    return " ".join(raw.split())[:limit]


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _compact_int(value: Any) -> int:
    """Read ``1234``, ``1,234``, ``1.2K`` and ``3.4M``."""
    text = str(value or "").strip()
    match = re.search(r"([\d]+(?:[.,]\d+)?)\s*([KMB])?", text, re.IGNORECASE)
    if not match:
        return 0
    number = match.group(1).replace(",", "")
    try:
        amount = float(number)
    except ValueError:
        return 0
    suffix = (match.group(2) or "").upper()
    factor = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return max(0, int(round(amount * factor)))


def _parse_clock(value: Any) -> int:
    """Accept ``770``, ``MM:SS`` and ``HH:MM:SS``."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    text = str(value or "").strip()
    if not text:
        return 0
    if ":" in text:
        parts = text.split(":")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return 0
        if len(numbers) == 2:
            return max(0, numbers[0] * 60 + numbers[1])
        if len(numbers) == 3:
            return max(0, numbers[0] * 3600 + numbers[1] * 60 + numbers[2])
        return 0
    return max(0, _as_int(text))


def _parse_duration_text(value: Any) -> int:
    """Duration from listing markup: ``12:34``, ``1:02:03``, ``12min``.

    Only explicit minute/second words are accepted; a bare ``m`` would match
    the ``M`` in a view count such as ``14.2M``.
    """
    text = str(value or "").strip()
    if not text:
        return 0
    clock = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", text)
    if clock:
        return _parse_clock(clock.group(1))
    hours = re.search(r"\b(\d{1,2})\s*(?:hr|hrs|hour|hours)\b", text, re.IGNORECASE)
    minutes = re.search(r"\b(\d{1,3})\s*(?:min|mins|minute|minutes)\b", text, re.IGNORECASE)
    if hours:
        total = int(hours.group(1)) * 3600
        if minutes:
            total += int(minutes.group(1)) * 60
        return total
    if minutes:
        return max(0, int(minutes.group(1)) * 60)
    seconds = re.search(r"\b(\d{1,4})\s*(?:sec|secs|second|seconds)\b", text, re.IGNORECASE)
    if seconds:
        return max(0, int(seconds.group(1)))
    return 0


def _views_from_text(value: Any) -> int:
    """Prefer ``32.6k views`` over the first number in a metadata line."""
    text = str(value or "")
    match = re.search(r"([\d.,]+\s*[KMB]?)\s*views?\b", text, re.IGNORECASE)
    if match:
        return _compact_int(match.group(1))
    return _compact_int(text)


def _normalise_rating(value: Any, scale: int) -> float:
    rating = _as_float(value)
    if rating <= 0:
        return 0.0
    if scale == 5:
        rating *= 2
    elif scale == 100:
        rating /= 10
    return round(max(0.0, min(10.0, rating)), 1)


def _rating_from_text(value: Any) -> float:
    """Rating from ``85%``, ``4.5`` or ``8/10`` text; assumes 0-10 output."""
    text = str(value or "").strip()
    if not text:
        return 0.0
    percent = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    if percent:
        return _normalise_rating(percent.group(1), 100)
    fraction = re.search(r"(\d{1,2}(?:\.\d+)?)\s*/\s*(\d{1,2})", text)
    if fraction:
        numerator = _as_float(fraction.group(1))
        denominator = _as_float(fraction.group(2)) or 10.0
        if denominator <= 5.5:
            return _normalise_rating(numerator, 5)
        return round(max(0.0, min(10.0, numerator * 10.0 / denominator)), 1)
    decimal = re.search(r"\b(\d{1,2}(?:\.\d+)?)\b", text)
    if decimal:
        rating = _as_float(decimal.group(1))
        return round(max(0.0, min(10.0, rating * 2 if rating <= 5.0 else rating)), 1)
    return 0.0


def _published(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return text[:32]


def _published_from_text(value: Any) -> str:
    """Convert ``Nov 27, 2026`` / ``27 Nov 2026`` / ISO into ``YYYY-MM-DD``."""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"\b(?:ago|just now|today|yesterday)\b", text, re.IGNORECASE):
        return ""
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return iso.group(0)
    month_first = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if month_first:
        month = _MONTHS.get(month_first.group(1).lower())
        if month:
            return f"{month_first.group(3)}-{month:02d}-{int(month_first.group(2)):02d}"
    day_first = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.??\s+(\d{4})", text)
    if day_first:
        month = _MONTHS.get(day_first.group(2).lower())
        if month:
            return f"{day_first.group(3)}-{month:02d}-{int(day_first.group(1)):02d}"
    return _published(text)


def _host_is_private(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def _safe_page_url(raw: Any, provider: str) -> str:
    text = str(raw or "").strip()
    if not text or len(text) > 2048:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    if parsed.port not in (None, 443):
        return ""
    if not providers.host_allowed(provider, parsed.hostname):
        return ""
    if _host_is_private(parsed.hostname):
        return ""
    return text.split("#", 1)[0]


def _safe_thumb(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get("src") or raw.get("url") or ""
    text = str(raw or "").strip()
    if not text or len(text) > 2048:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    if parsed.username or parsed.password or _host_is_private(parsed.hostname):
        return ""
    return text


def _normalise_tags(value: Any) -> List[str]:
    if isinstance(value, str):
        raw_items = value.replace("|", ",").split(",")
    elif isinstance(value, list):
        raw_items = []
        for entry in value:
            if isinstance(entry, str):
                raw_items.append(entry)
            elif isinstance(entry, dict):
                for key in ("tag_name", "category", "name", "title"):
                    if entry.get(key):
                        raw_items.append(str(entry[key]))
                        break
    else:
        return []
    seen: List[str] = []
    for item in raw_items:
        text = " ".join(str(item or "").split())[:40]
        if text and text.lower() not in {tag.lower() for tag in seen}:
            seen.append(text)
        if len(seen) >= MAX_TAGS:
            break
    return seen


def _attr_id(raw: Any) -> str:
    """Read an id from a plain attribute or a JSON-encoded one.

    XVideos and XNXX put ``{"id": "1j1fu4db", ...}`` in ``data-video``, so
    passing the attribute through would store a JSON blob as the dedupe key.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return ""
        if not isinstance(data, dict):
            return ""
        text = str(data.get("id") or "").strip()
    return re.sub(r"[^0-9A-Za-z_.-]", "", text)[:64]


def _provider_id(provider: str, explicit: Any, page_url: str) -> str:
    """Stable id for dedupe: explicit attribute, else digits, else path hash."""
    text = str(explicit or "").strip()
    if text:
        return text[:64]
    path = urlparse(page_url).path or page_url
    best = max(re.findall(r"\d{3,}", path), key=len, default="")
    if best:
        return best[:64]
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# JSON providers
# --------------------------------------------------------------------------- #


def _normalise_eporner(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    provider = "eporner"
    page_url = _safe_page_url(raw.get("url"), provider)
    title = " ".join(str(raw.get("title") or "").split())
    if not page_url or not title:
        return None
    thumb = _safe_thumb(raw.get("default_thumb"))
    if not thumb:
        candidates = [entry for entry in (raw.get("thumbs") or []) if isinstance(entry, dict)]
        candidates.sort(key=lambda entry: _as_int(entry.get("width")), reverse=True)
        for entry in candidates:
            thumb = _safe_thumb(entry)
            if thumb:
                break
    return {
        "provider": provider,
        "provider_id": _provider_id(provider, raw.get("id"), page_url),
        "page_url": page_url,
        "title": title[:400],
        "thumb": thumb,
        "duration_sec": _parse_clock(raw.get("length_sec")),
        "views": max(0, _as_int(raw.get("views"))),
        "rating": _normalise_rating(raw.get("rate"), 5),
        "published": _published(raw.get("added")),
        "tags": _normalise_tags(raw.get("keywords")),
        "site_label": providers.PROVIDERS[provider]["label"],
        "source_kind": "api",
    }


def _normalise_pornhub(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    provider = "pornhub"
    page_url = _safe_page_url(raw.get("url"), provider)
    title = " ".join(str(raw.get("title") or "").split())
    if not page_url or not title:
        return None
    return {
        "provider": provider,
        "provider_id": _provider_id(provider, raw.get("video_id"), page_url),
        "page_url": page_url,
        "title": title[:400],
        "thumb": _safe_thumb(raw.get("default_thumb") or raw.get("thumb")),
        "duration_sec": _parse_clock(raw.get("duration")),
        "views": max(0, _as_int(raw.get("views"))),
        "rating": _normalise_rating(raw.get("rating"), 100),
        "published": _published(raw.get("publish_date")),
        "tags": _normalise_tags(raw.get("tags")),
        "site_label": providers.PROVIDERS[provider]["label"],
        "source_kind": "api",
    }


# --------------------------------------------------------------------------- #
# HTML parsers
#
# Each parser returns raw rows; normalisation happens once in
# ``_normalise_html_row``. Keep selectors here so a markup change is a
# single-file edit.
# --------------------------------------------------------------------------- #


def _parse_xhamster(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for card in soup.find_all("div", class_="video-thumb"):
        name = card.find(class_="video-thumb-info__name")
        if name is None:
            continue
        image = card.find(class_="thumb-image-container__image")
        rows.append(
            {
                "href": name.get("href") or "",
                "title": name.get("title") or _text(name),
                "thumb": (image.get("src") if image is not None else "") or "",
                "duration": _text(card.find(class_="thumb-image-container__duration"), 24),
                "views": _text(card.find(class_="video-thumb-views"), 30),
                "rating": "",
                "published": "",
                "id": card.get("data-video-id") or "",
            }
        )
    return rows


def _parse_youjizz(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for card in soup.find_all("div", class_="video-item"):
        frame = card.find("a", class_="frame")
        title_link = card.find("a", class_="agego-content-title")
        if frame is None or title_link is None:
            continue
        image = card.find("img")
        rating_node = card.find("select", class_="video-bar-rating-view")
        rows.append(
            {
                "href": title_link.get("href") or frame.get("href") or "",
                "title": title_link.get("title") or _text(title_link),
                "thumb": (image.get("data-original") or image.get("src") or "") if image is not None else "",
                "duration": _text(card.find(class_="time"), 20),
                "views": _text(card.find(class_="format-views"), 20),
                "rating": (rating_node.get("data-value") if rating_node is not None else "") or "",
                "published": "",
                "id": frame.get("data-video-id") or "",
            }
        )
    return rows


def _parse_xvideos(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Handles both layouts: browse (``a.thumb-link``) and search frames."""
    rows: List[Dict[str, Any]] = []
    for card in soup.find_all("div", class_="thumb-block"):
        link = card.find("a", class_="thumb-link")
        if link is None:
            thumb = card.find("div", class_="thumb")
            link = thumb.find("a", href=True) if thumb is not None else None
        title_node = card.find(class_="title")
        title = ""
        if title_node is not None:
            anchor = title_node.find("a", href=True)
            title = (
                title_node.get("title")
                or (anchor.get("title") if anchor is not None else "")
                or _text(title_node)
            )
        if link is None or not title:
            continue
        image = card.find("img")
        views_node = card.find(class_="views-count")
        if views_node is not None:
            views = " ".join(
                part for part in (
                    _text(views_node, 20),
                    _text(card.find(class_="views-text"), 20),
                ) if part
            )
        else:
            views = _text(card.find(class_="metadata"), 60)
        rows.append(
            {
                "href": link.get("href") or "",
                "title": title,
                "thumb": (image.get("data-src") or image.get("src") or "") if image is not None else "",
                "duration": _text(card.find(class_="duration"), 20),
                "views": views,
                "rating": "",
                "published": "",
                "id": _attr_id(card.get("data-video") or card.get("data-id"))
            }
        )
    return rows


def _parse_xnxx(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Handles both layouts: browse (``a.title``) and search frames."""
    rows: List[Dict[str, Any]] = []
    for card in soup.find_all("div", class_="thumb-block"):
        link = card.find("a", class_="thumb-link")
        if link is None:
            thumb = card.find("div", class_="thumb")
            link = thumb.find("a", href=True) if thumb is not None else None
        title_node = card.find("a", class_="title")
        if title_node is None:
            under = card.find("div", class_="thumb-under")
            if under is not None:
                for anchor in under.find_all("a", href=True):
                    if anchor.get("title"):
                        title_node = anchor
                        break
        if link is None or title_node is None:
            continue
        title = title_node.get("title") or _text(title_node)
        if not title:
            continue
        image = card.find("img")
        right = _text(card.find(class_="right"), 40)
        left = _text(card.find(class_="left"), 60)
        duration_text = left or _text(card.find("p", class_="metadata"), 60)
        rows.append(
            {
                "href": link.get("href") or "",
                "title": title,
                "thumb": (image.get("data-src") or image.get("src") or "") if image is not None else "",
                "duration": duration_text,
                "views": right,
                "rating": right,
                "published": "",
                "id": _attr_id(card.get("data-video") or card.get("data-id"))
            }
        )
    return rows


def _parse_youporn(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for card in soup.find_all("article", class_="video-box"):
        link = card.find("a", class_="video-box-image")
        title_link = card.find("a", class_="video-title-text")
        if link is None or title_link is None:
            continue
        image = card.find("img", class_="thumb-image")
        info = card.find_all(class_="info-views")
        views = _text(info[0], 40) if info else ""
        rating = _text(info[1], 20) if len(info) > 1 else ""
        rows.append(
            {
                "href": link.get("href") or "",
                "title": _text(title_link),
                "thumb": (image.get("data-src") or image.get("src") or "") if image is not None else "",
                "duration": _text(card.find(class_="video-duration"), 20),
                "views": views,
                "rating": rating,
                "published": "",
                "id": card.get("data-video-id") or "",
            }
        )
    return rows


def _parse_xxxymovies(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for card in soup.find_all("div", class_="item"):
        title_node = card.find("h3", class_="infotitle")
        if title_node is None:
            continue
        link = card.find("a", href=True)
        if link is None:
            continue
        image = card.find("img")
        added = _text(card.find(class_="added"), 60)
        views_match = re.search(r"([\d.,]+\s*[KMB]?)\s*views?\b", added, re.IGNORECASE)
        views = views_match.group(1) if views_match else ""
        duration_match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*$", added)
        duration = duration_match.group(1) if duration_match else ""
        rows.append(
            {
                "href": link.get("href") or "",
                "title": link.get("title") or _text(title_node),
                "thumb": (image.get("data-original") or image.get("src") or "") if image is not None else "",
                "duration": duration,
                "views": views,
                "rating": _text(card.find(class_="item_rating"), 12),
                "published": added,
                "id": "",
            }
        )
    return rows


def _parse_sunporno(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for card in soup.find_all("a", class_="item"):
        href = card.get("href") or ""
        if "/v/" not in href:
            continue
        image = card.find("img", class_="thumb")
        rows.append(
            {
                "href": href,
                "title": _text(card.find(class_="video-title")),
                "thumb": (image.get("data-webp") or image.get("data-src") or image.get("src") or "") if image is not None else "",
                "duration": _text(card.find(class_="duration"), 20),
                "views": _text(card.find(class_="video-views"), 30),
                "rating": "",
                "published": "",
                "id": "",
            }
        )
    return rows


_HTML_PARSERS = {
    "xhamster": _parse_xhamster,
    "youjizz": _parse_youjizz,
    "xvideos": _parse_xvideos,
    "xnxx": _parse_xnxx,
    "youporn": _parse_youporn,
    "xxxymovies": _parse_xxxymovies,
    "sunporno": _parse_sunporno,
}


def _normalise_html_row(provider: str, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    origin = providers.PROVIDERS[provider].get("origin") or ""
    href = str(raw.get("href") or "").strip()
    if not href:
        return None
    joined = urljoin(origin + "/", href)
    page_url = _safe_page_url(joined, provider)
    title = " ".join(str(raw.get("title") or "").split())
    if not page_url or not title:
        return None
    return {
        "provider": provider,
        "provider_id": _provider_id(provider, raw.get("id"), page_url),
        "page_url": page_url,
        "title": title[:400],
        "thumb": _safe_thumb(raw.get("thumb")),
        "duration_sec": _parse_duration_text(raw.get("duration")),
        "views": _views_from_text(raw.get("views")),
        "rating": _rating_from_text(raw.get("rating")),
        "published": _published_from_text(raw.get("published")),
        "tags": [],
        "site_label": providers.PROVIDERS[provider]["label"],
        "source_kind": "html",
    }


_NORMALISERS = {
    "eporner": _normalise_eporner,
    "pornhub": _normalise_pornhub,
}


# --------------------------------------------------------------------------- #
# URL building
# --------------------------------------------------------------------------- #


def _feed_url(provider: str, mode: str, page: int) -> Optional[str]:
    spec = providers.PROVIDERS[provider]
    entry = (spec.get("feed") or {}).get(mode)
    if not entry:
        return None
    first, paged = entry
    if page > 1 and paged:
        return paged.format(page=page)
    return first


def _search_url(provider: str, query: str, page: int) -> Optional[str]:
    spec = providers.PROVIDERS[provider]
    entry = spec.get("search_urls")
    if not entry:
        return None
    first, paged = entry
    encoded = quote(query, safe="")
    if page > 1 and paged:
        return paged.format(query=encoded, page=page)
    return first.format(query=encoded)


def _supports_search(spec: Dict[str, Any]) -> bool:
    """JSON providers search through their API URL; HTML ones need a template."""
    if not spec.get("search"):
        return False
    return bool(spec.get("api_url") or spec.get("search_urls"))


# --------------------------------------------------------------------------- #
# Provider queries
# --------------------------------------------------------------------------- #


def _eporner_query(mode: str, page: int, query: str, is_search: bool) -> Dict[str, Any]:
    spec = providers.PROVIDERS["eporner"]
    params: Dict[str, Any] = {
        "query": query,
        "per_page": spec["page_size"],
        "page": page,
        "format": "json",
        "thumbsize": "big",
    }
    if not is_search:
        params.update(spec["feed_params"].get(mode) or {})
    result = _request_json(spec["api_url"], params, "eporner")
    if not result["ok"]:
        return {"error": result["code"], "status": result.get("status", 0)}
    payload = result["payload"]
    raw_items = [item for item in (payload.get("videos") or []) if isinstance(item, dict)]
    items = [item for item in (_normalise_eporner(raw) for raw in raw_items) if item]
    total_pages = _as_int(payload.get("total_pages"))
    has_more = page < total_pages if total_pages > 0 else len(raw_items) >= spec["page_size"]
    return {"items": items, "has_more": has_more, "dropped": len(raw_items) - len(items)}


def _pornhub_query(mode: str, page: int, query: str, is_search: bool) -> Dict[str, Any]:
    spec = providers.PROVIDERS["pornhub"]
    params: Dict[str, Any] = {"search": query, "page": page}
    if not is_search:
        params.update(spec["feed_params"].get(mode) or {})
    result = _request_json(spec["api_url"], params, "pornhub")
    if not result["ok"]:
        return {"error": result["code"], "status": result.get("status", 0)}
    payload = result["payload"]
    raw_items = [item for item in (payload.get("videos") or []) if isinstance(item, dict)]
    items = [item for item in (_normalise_pornhub(raw) for raw in raw_items) if item]
    return {
        "items": items,
        "has_more": len(raw_items) >= spec["page_size"],
        "dropped": len(raw_items) - len(items),
    }


def _html_query(provider: str, mode: str, page: int, query: str, is_search: bool) -> Dict[str, Any]:
    spec = providers.PROVIDERS[provider]
    if is_search:
        entry = spec.get("search_urls") or (None, None)
        if page > 1 and not entry[1]:
            return {"items": [], "has_more": False, "dropped": 0}
        url = _search_url(provider, query, page)
    else:
        entry = (spec.get("feed") or {}).get(mode) or (None, None)
        if page > 1 and not entry[1]:
            return {"items": [], "has_more": False, "dropped": 0}
        url = _feed_url(provider, mode, page)
    if not url:
        return {"error": "mode_unsupported"}
    result = _request_html(url, provider)
    if not result["ok"]:
        return {"error": result["code"], "status": result.get("status", 0)}

    soup = BeautifulSoup(result["html"], "html.parser")
    tag, css_class = spec.get("container") or ("", "")
    containers = soup.find_all(tag, class_=css_class) if tag and css_class else []
    try:
        rows = _HTML_PARSERS[provider](soup)
    except Exception as exc:  # defensive: never leak provider HTML
        LOGGER.warning("adult parser %s crashed: %s", provider, type(exc).__name__)
        return {"error": "parse_error"}

    if not rows:
        if containers or is_search:
            return {"items": [], "has_more": False, "dropped": 0}
        return {"error": "parse_error"}

    items = [item for item in (_normalise_html_row(provider, raw) for raw in rows) if item]
    paged = bool(spec.get("paged"))
    threshold = int(spec.get("min_page_yield") or MIN_PAGE_YIELD)
    has_more = paged and page < MAX_PAGE and len(rows) >= threshold
    return {"items": items, "has_more": has_more, "dropped": len(rows) - len(items)}


_QUERIES = {
    "eporner": _eporner_query,
    "pornhub": _pornhub_query,
    "xhamster": lambda mode, page, query, is_search: _html_query("xhamster", mode, page, query, is_search),
    "xvideos": lambda mode, page, query, is_search: _html_query("xvideos", mode, page, query, is_search),
    "xnxx": lambda mode, page, query, is_search: _html_query("xnxx", mode, page, query, is_search),
    "youporn": lambda mode, page, query, is_search: _html_query("youporn", mode, page, query, is_search),
    "youjizz": lambda mode, page, query, is_search: _html_query("youjizz", mode, page, query, is_search),
    "xxxymovies": lambda mode, page, query, is_search: _html_query("xxxymovies", mode, page, query, is_search),
    "sunporno": lambda mode, page, query, is_search: _html_query("sunporno", mode, page, query, is_search),
}


def _provider_result(
    provider: str, mode: str, page: int, query: str, is_search: bool
) -> Dict[str, Any]:
    cache_key = "{}|{}|{}|{}|{}".format(
        provider, "search" if is_search else "feed", mode, page, query.strip().lower()
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True, "ms": 0}

    remaining = _breaker_remaining(provider)
    if remaining:
        return {"error": "cooldown", "cooldown": remaining, "ms": 0}

    handler = _QUERIES.get(provider)
    if handler is None:
        return {"error": "disabled", "ms": 0}

    started = time.monotonic()
    try:
        result = handler(mode, page, query, is_search)
    except Exception as exc:  # defensive: never leak provider internals
        LOGGER.warning("adult provider %s crashed: %s", provider, type(exc).__name__)
        result = {"error": "parse_error"}
    elapsed = int((time.monotonic() - started) * 1000)
    result["ms"] = elapsed

    if result.get("error"):
        _record_failure(provider, result["error"])
        return result

    _record_success(provider)
    _cache_set(
        cache_key,
        {
            "items": result.get("items") or [],
            "has_more": bool(result.get("has_more")),
            "dropped": int(result.get("dropped") or 0),
        },
    )
    return result


def _gather(
    keys: Tuple[str, ...], mode: str, page: int, query: str, is_search: bool
) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    if not keys:
        return results
    workers = min(4, len(keys))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_provider_result, key, mode, page, query, is_search): key
            for key in keys
        }
        for future, key in futures.items():
            try:
                results[key] = future.result()
            except Exception as exc:
                LOGGER.warning("adult provider %s failed: %s", key, type(exc).__name__)
                results[key] = {"error": "parse_error", "ms": 0}
    return {key: results[key] for key in keys if key in results}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _message(code: str, label: str, **extra: Any) -> str:
    template = _ERROR_MESSAGES.get(code, "{label} gagal dimuat.")
    return template.format(label=label, **extra)


def _clean_query(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:MAX_QUERY_LEN]


def _normalise_page(raw: Any, default: int = 1) -> int:
    try:
        page = int(raw)
    except (TypeError, ValueError):
        page = default
    return max(1, min(MAX_PAGE, page))


def _interleave(results: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    lists = [result.get("items") or [] for result in results.values()]
    available = sum(len(items) for items in lists)
    merged: List[Dict[str, Any]] = []
    seen = set()
    index = 0
    while len(merged) < MAX_AGGREGATE_ITEMS and any(index < len(items) for items in lists):
        for items in lists:
            if index >= len(items) or len(merged) >= MAX_AGGREGATE_ITEMS:
                continue
            item = items[index]
            key = (item.get("provider"), item.get("provider_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        index += 1
    return merged, available


def _assemble(
    results: Dict[str, Dict[str, Any]],
    page: int,
    skipped: Optional[List[str]] = None,
    extra_warnings: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    skipped = skipped or []
    items, available = _interleave(results)
    report: List[Dict[str, Any]] = []
    warnings: List[Dict[str, str]] = list(extra_warnings or [])

    for provider, result in results.items():
        spec = providers.PROVIDERS.get(provider) or {}
        label = spec.get("label") or provider
        error = result.get("error")
        if error:
            warnings.append(
                {
                    "provider": provider,
                    "code": error,
                    "message": _message(error, label, seconds=result.get("cooldown", 0), status=result.get("status", 0)),
                }
            )
            report.append(
                {
                    "id": provider,
                    "label": label,
                    "status": error,
                    "count": 0,
                    "ms": result.get("ms", 0),
                    "cached": bool(result.get("cached")),
                }
            )
            continue
        dropped = int(result.get("dropped") or 0)
        if dropped:
            warnings.append(
                {
                    "provider": provider,
                    "code": "dropped_invalid",
                    "message": _message("dropped_invalid", label, count=dropped),
                }
            )
        report.append(
            {
                "id": provider,
                "label": label,
                "status": "ok",
                "count": len(result.get("items") or []),
                "ms": result.get("ms", 0),
                "cached": bool(result.get("cached")),
            }
        )

    for provider in skipped:
        spec = providers.PROVIDERS.get(provider) or {}
        label = spec.get("label") or provider
        warnings.append(
            {
                "provider": provider,
                "code": "mode_unsupported",
                "message": _message("mode_unsupported", label),
            }
        )
        report.append(
            {
                "id": provider,
                "label": label,
                "status": "mode_unsupported",
                "count": 0,
                "ms": 0,
                "cached": False,
            }
        )

    return {
        "ok": True,
        "items": items,
        "page": page,
        "has_more": any(bool(result.get("has_more")) for result in results.values()),
        "providers": report,
        "warnings": warnings,
        "available": available,
        "truncated": available > len(items),
    }


# --------------------------------------------------------------------------- #
# Manual page links
#
# SpankBang answers automated requests with a Cloudflare challenge, so its
# catalogue stays disabled. A URL pasted by hand is still usable: it is
# validated against the provider allowlist and passed through unchanged. The
# page itself is never fetched here; TorBox WebDL resolves it at submit time.
# --------------------------------------------------------------------------- #

_LISTING_SEGMENTS = frozenset(
    {
        "account", "amateur", "best", "cams", "categories", "category", "channel",
        "channels", "favorites", "gay", "latest-updates", "lesbian", "live", "login",
        "model", "models", "most-popular", "newest", "newest-clips", "playlists",
        "popular", "porn", "pornstar", "pornstars", "premium", "register", "search",
        "searches", "settings", "shemale", "signup", "subscriptions", "tag", "tags",
        "top", "top-rated", "top-rated-month", "top_rated", "trending", "upload", "vr",
    }
)

_QUERY_LISTING_KEYS = frozenset({"page", "q", "k", "query", "search", "view"})
_TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "msclkid", "igshid", "ref_src", "ref_url", "_ga", "_gl", "mc_cid", "mc_eid"}
)
_GENERIC_SEGMENTS = frozenset(
    {"video", "videos", "watch", "view", "v", "p", "play", "sample", "embed", "embedplayer", "movie", "movies"}
)
_ID_LIKE = re.compile(r"^(?:[0-9a-f]{8,}|[0-9]{3,})$", re.IGNORECASE)
_EXTENSION = re.compile(r"\.(?:html?|php|aspx?)$", re.IGNORECASE)


def _looks_like_id(text: str) -> bool:
    compact = re.sub(r"[-_\s]+", "", str(text or ""))
    if not compact:
        return True
    return bool(_ID_LIKE.fullmatch(compact)) and any(ch.isdigit() for ch in compact)


def _title_from_path(path: str) -> str:
    """Best-effort title from a page slug: ``/video-abc/sample-two`` -> ``Sample two``."""
    segments = [segment for segment in str(path or "").split("/") if segment]
    if not segments:
        return ""
    stem = _EXTENSION.sub("", unquote(segments[-1]))
    if _looks_like_id(stem):
        if len(segments) < 2:
            return ""
        stem = _EXTENSION.sub("", unquote(segments[-2]))
    words = " ".join(re.sub(r"[-_+]+", " ", stem).split())
    tokens = words.lower().split(" ")
    if len(words) < 3 or _looks_like_id(words) or all(token in _GENERIC_SEGMENTS for token in tokens):
        return ""
    return (words[:1].upper() + words[1:])[:400]


def _query_token(url: str) -> str:
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=False):
        if key.lower() in {"viewkey", "v", "id", "video_id", "vid"}:
            token = re.sub(r"[^0-9A-Za-z_-]", "", value)[:32]
            if len(token) >= 3:
                return token
    return ""


def _strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _TRACKING_KEYS or lowered.startswith("utm_"):
            continue
        kept.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept, doseq=True), ""))


def _manual_page_url(raw: Any, provider: str) -> str:
    """Validate one pasted page URL and return it without tracking noise."""
    label = providers.PROVIDERS[provider]["label"]
    text = str(raw or "").strip()
    if not text:
        raise AdultDiscoveryError("Tempel URL halaman video terlebih dahulu.")
    if len(text) > 2048:
        raise AdultDiscoveryError("URL terlalu panjang.")
    if text.startswith("//"):
        text = "https:" + text
    if text.lower().startswith("http://"):
        text = "https://" + text[len("http://"):]
    if not text.lower().startswith("https://"):
        raise AdultDiscoveryError("Hanya URL https:// yang diterima.")
    try:
        url = _safe_page_url(text, provider)
    except ValueError:
        url = ""
    if not url:
        raise AdultDiscoveryError(f"URL bukan halaman dari {label}.")
    url = _strip_tracking(url)
    parts = urlsplit(url)
    path = parts.path.strip("/")
    if not path:
        raise AdultDiscoveryError("URL mengarah ke halaman daftar, bukan halaman video.")
    if path.split("/")[0].lower() in _LISTING_SEGMENTS:
        raise AdultDiscoveryError(
            "URL ini halaman daftar atau pencarian. Buka halaman videonya lalu tempel URL itu."
        )
    query_keys = {key.lower() for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if query_keys & _QUERY_LISTING_KEYS:
        raise AdultDiscoveryError(
            "URL ini halaman daftar atau pencarian. Buka halaman videonya lalu tempel URL itu."
        )
    return url


def link(raw_url: Any) -> Dict[str, Any]:
    """Turn a page URL pasted by hand into one catalogue item.

    Nothing is fetched: blocked providers (SpankBang) never work automatically,
    and the submit step needs the page URL rather than site metadata.
    """
    text = str(raw_url or "").strip()
    if not text:
        raise AdultDiscoveryError("Tempel URL halaman video terlebih dahulu.")
    if len(text) > 2048:
        raise AdultDiscoveryError("URL terlalu panjang.")
    if text.startswith("//"):
        text = "https:" + text
    try:
        host = urlparse(text).hostname or ""
    except ValueError:
        host = ""
    provider_key = providers.provider_for_host(host)
    if not provider_key:
        raise AdultDiscoveryError(
            "Host tidak dikenal. Tempel halaman dari salah satu sumber yang terdaftar."
        )
    spec = providers.PROVIDERS[provider_key]
    page_url = _manual_page_url(text, provider_key)
    provider_id = _provider_id(provider_key, "", page_url)
    title = _title_from_path(urlsplit(page_url).path)
    placeholder = not title
    if placeholder:
        title = f"{spec['label']} · {_query_token(page_url) or provider_id}"
    return {
        "ok": True,
        "manual": True,
        "provider": {
            "id": provider_key,
            "label": spec["label"],
            "enabled": bool(spec.get("enabled")),
            "availability": spec.get("availability") or "disabled",
            "manual": bool(spec.get("manual")),
        },
        "item": {
            "provider": provider_key,
            "provider_id": provider_id,
            "page_url": page_url,
            "title": title[:400],
            "thumb": "",
            "duration_sec": 0,
            "views": 0,
            "rating": 0.0,
            "published": "",
            "tags": [],
            "site_label": spec["label"],
            "source_kind": "manual",
            "title_is_placeholder": placeholder,
        },
        "note": "TorBox WebDL mengambil halaman ini; metadata terisi setelah unduhan.",
    }


def feed(provider: Any, mode: Any, page: Any = 1) -> Dict[str, Any]:
    """Return a browse page for one provider or for every enabled provider."""
    mode_key = str(mode or "popular").strip().lower()
    if mode_key not in providers.MODES:
        raise AdultDiscoveryError("Mode katalog tidak dikenal.")
    page_number = _normalise_page(page)
    provider_key = str(provider or "all").strip().lower()

    if provider_key == "all":
        keys = providers.enabled_keys()
        if not keys:
            raise AdultDiscoveryError("Belum ada sumber yang aktif.", 503)
        supported = tuple(key for key in keys if mode_key in providers.PROVIDERS[key]["modes"])
        skipped = [key for key in keys if key not in supported]
        if not supported:
            raise AdultDiscoveryError("Belum ada sumber dengan mode katalog ini.", 503)
    else:
        spec = providers.get_spec(provider_key)
        if not spec:
            raise AdultDiscoveryError("Sumber tidak dikenal.")
        label = spec.get("label") or provider_key
        if not spec.get("enabled"):
            raise AdultDiscoveryError(f"Sumber {label} belum diaktifkan.", 503)
        if mode_key not in spec["modes"]:
            raise AdultDiscoveryError(
                f"Mode {providers.MODE_LABELS[mode_key]} tidak tersedia di {label}."
            )
        supported = (provider_key,)
        skipped = []

    results = _gather(supported, mode_key, page_number, "", False)
    payload = _assemble(results, page_number, skipped=skipped)
    payload["mode"] = mode_key
    return payload


def search(query: Any, provider_filter: Any = "", page: Any = 1) -> Dict[str, Any]:
    """Search one or more providers for ``query``."""
    cleaned = _clean_query(query)
    if len(cleaned) < MIN_QUERY_LEN:
        raise AdultDiscoveryError(f"Kata kunci minimal {MIN_QUERY_LEN} karakter.")
    page_number = _normalise_page(page)

    enabled = list(providers.enabled_keys())
    requested = str(provider_filter or "").strip().lower()
    warnings: List[Dict[str, str]] = []
    if requested and requested != "all":
        keys: List[str] = []
        for part in requested.split(","):
            key = part.strip().lower()
            if not key or key in keys:
                continue
            spec = providers.get_spec(key)
            if not spec:
                warnings.append(
                    {
                        "provider": key,
                        "code": "unknown_provider",
                        "message": _message("unknown_provider", key, provider=key),
                    }
                )
                continue
            if not spec.get("enabled") or key not in enabled:
                raise AdultDiscoveryError(f"Sumber {spec.get('label') or key} belum diaktifkan.", 503)
            if not _supports_search(spec):
                warnings.append(
                    {
                        "provider": key,
                        "code": "mode_unsupported",
                        "message": _message("mode_unsupported", spec.get("label") or key),
                    }
                )
                continue
            keys.append(key)
        if not keys:
            raise AdultDiscoveryError("Tidak ada sumber valid untuk pencarian.")
    else:
        keys = enabled

    if not keys:
        raise AdultDiscoveryError("Belum ada sumber yang aktif.", 503)

    results = _gather(tuple(keys), "", page_number, cleaned, True)
    payload = _assemble(results, page_number, extra_warnings=warnings)
    payload["query"] = cleaned
    return payload


def providers_payload() -> Dict[str, Any]:
    """Describe every known provider, including disabled ones."""
    rows: List[Dict[str, Any]] = []
    for key in providers.PROVIDER_ORDER:
        spec = providers.PROVIDERS[key]
        remaining = _breaker_remaining(key)
        state = _state(key)
        with _STATE_LOCK:
            last_error = state["last_error"]
        availability = spec.get("availability") or "disabled"
        if not spec.get("enabled"):
            status = availability
        elif remaining:
            status = "cooldown"
        elif availability != "ok":
            status = availability
        else:
            status = "ok"
        rows.append(
            {
                "id": key,
                "label": spec.get("label") or key,
                "transport": spec.get("transport") or "",
                "enabled": bool(spec.get("enabled")),
                "search": bool(spec.get("search")),
                "manual": bool(spec.get("manual")),
                "modes": list(spec.get("modes") or ()),
                "status": status,
                "cooldown_seconds": remaining,
                "last_error": last_error,
                "note": spec.get("note") or "",
            }
        )
    return {
        "ok": True,
        "modes": [
            {"id": mode, "label": providers.MODE_LABELS[mode]} for mode in providers.MODES
        ],
        "providers": rows,
        "manual_providers": [
            key for key in providers.PROVIDER_ORDER if providers.PROVIDERS[key].get("manual")
        ],
        "page_limit": MAX_PAGE,
        "aggregate_limit": MAX_AGGREGATE_ITEMS,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
    }