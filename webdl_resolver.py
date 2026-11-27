"""Resolve safe, public page URLs for TorBox WebDL submissions.

TorBox WebDL accepts a *page* URL and resolves the media internally with
yt-dlp. Direct CDN/media URLs (for example a raw ``.mp4`` or an HLS manifest)
are commonly rejected with "The site you are trying to download from is not
supported.", so Roxxy provider pages are translated to their canonical public
page URL instead of forwarding signed stream URLs.

The module is intentionally free of network calls so it can be unit tested
offline. ``app.py`` performs the actual TorBox API request.
"""

import ipaddress
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Provider type (lower-case) -> (display label, page URL template).
# Every pattern below was verified to return HTTP 200 for a real Roxxy id and
# is accepted by the bundled yt-dlp extractor set.
PROVIDER_PAGE_PATTERNS: Dict[str, Tuple[str, str]] = {
    "pornhub": ("Pornhub", "https://www.pornhub.com/view_video.php?viewkey={id}"),
    "eporner": ("Eporner", "https://www.eporner.com/video-{id}"),
    "xhamster": ("XHamster", "https://xhamster.com/videos/{id}"),
    "redgifs": ("RedGifs", "https://www.redgifs.com/watch/{id}"),
    "xvideos": ("XVideos", "https://www.xvideos.com/embedframe/{id}"),
    "youjizz": ("YouJizz", "https://www.youjizz.com/videos/embed/{id}"),
    "tnaflix": ("TNAFlix", "https://player.tnaflix.com/video/{id}"),
    "empflix": ("EMPFlix", "https://player.empflix.com/video/{id}"),
}

# Hoster domains TorBox documents as unsupported. Rejecting them early gives a
# clear message instead of a remote API failure.
UNSUPPORTED_HOSTS = (
    "keep2share.cc",
    "k2s.cc",
    "filejoker.net",
    "uploadgig.com",
    "takefile.link",
    "filesmonster.com",
    "oboom.com",
    "devuploads.com",
    "moondl.com",
    "hotlink.cc",
    "daofile.com",
    "novafile.com",
    "twojplik.pl",
    "fastfile.cc",
    "elitefile.com",
    "spotify.com",
    "netflix.com",
    "primevideo.com",
    "music.amazon.com",
    "music.apple.com",
)

# Our own HLS proxy host must never be forwarded to TorBox: it needs the local
# access token and resolves to a media manifest, not a downloadable page.
INTERNAL_PROXY_SUFFIXES = ("roxxy-tech.com",)

_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def normalize_provider(provider: Any) -> str:
    return str(provider or "").strip().lower()


def provider_label(provider: Any) -> str:
    entry = PROVIDER_PAGE_PATTERNS.get(normalize_provider(provider))
    return entry[0] if entry else ""


def supported_providers() -> List[Dict[str, str]]:
    """Return the provider types that can be submitted as a WebDL page URL."""
    return [
        {"id": key, "label": label, "kind": "page_url"}
        for key, (label, _) in PROVIDER_PAGE_PATTERNS.items()
    ]


def normalize_provider_id(provider: Any, raw_id: Any) -> str:
    """Strip a leading ``<provider>:`` prefix and reject unsafe id characters."""
    text = str(raw_id or "").strip()
    key = normalize_provider(provider)
    if ":" in text:
        prefix, rest = text.split(":", 1)
        if normalize_provider(prefix) == key or normalize_provider(prefix) in PROVIDER_PAGE_PATTERNS:
            text = rest.strip()
    if not _PROVIDER_ID_RE.match(text):
        return ""
    return text


def resolve_page_url(provider: Any, provider_id: Any) -> str:
    """Build a canonical public page URL for a supported provider, or ``""``."""
    key = normalize_provider(provider)
    entry = PROVIDER_PAGE_PATTERNS.get(key)
    if not entry:
        return ""
    pid = normalize_provider_id(key, provider_id)
    if not pid:
        return ""
    return entry[1].format(id=pid)


def _host_matches(host: str, domains: Tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def validate_webdl_url(raw_url: Any) -> Tuple[bool, str]:
    """Validate a manually pasted WebDL link without performing DNS lookups.

    TorBox performs the actual fetch, so this only blocks unsafe schemes,
    credentials, private/local destinations, our internal proxy and the
    hosters TorBox documents as unsupported.
    """
    text = str(raw_url or "").strip()
    if not text:
        return False, "Link wajib diisi."
    if len(text) > 2048:
        return False, "Link terlalu panjang (maksimum 2048 karakter)."
    try:
        parsed = urlparse(text)
    except Exception:
        return False, "Link tidak valid."
    if parsed.scheme.lower() not in ("http", "https"):
        return False, "Link harus memakai http atau https."
    if not parsed.hostname:
        return False, "Link tidak memiliki nama host."
    if parsed.username or parsed.password:
        return False, "Link yang memuat kredensial tidak diizinkan."

    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        return False, "Host jaringan lokal tidak diizinkan."
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return False, "Alamat IP privat/reserved tidak diizinkan."
    if _host_matches(host, INTERNAL_PROXY_SUFFIXES):
        return False, "Link proxy internal Roxxy tidak bisa dikirim sebagai WebDL."
    if _host_matches(host, UNSUPPORTED_HOSTS):
        return False, "Hoster ini tidak didukung TorBox WebDL."
    return True, ""