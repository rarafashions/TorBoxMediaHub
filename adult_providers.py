"""Declarative registry for English Adult discovery providers.

The registry describes *where* a provider lives, which catalog modes it offers
and how its listing pages are addressed. HTML parsing lives in
:mod:`adult_discovery` so site markup changes stay in one file.

Rules that keep the surface safe:

- Only providers whose canonical *page* URL is accepted by TorBox WebDL are
  enabled here. TorBox resolves the page itself; this app never extracts a
  media URL.
- A provider is enabled only when its transport was verified against the live
  site. Entries added ahead of their adapter stay disabled and are reported as
  such by the API.
- ``hosts`` is an allowlist of registrable domains. Any subdomain is accepted,
  nothing else.
- ``feed`` and ``search`` hold URL pairs: ``(first_page_url, paged_url)``.
  ``{query}`` is replaced with a URL-encoded query, ``{page}`` with the page
  number. ``None`` for the second value means the provider has no pagination.
"""

from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

PROVIDER_ORDER: Tuple[str, ...] = (
    "eporner",
    "pornhub",
    "xhamster",
    "xvideos",
    "xnxx",
    "youporn",
    "youjizz",
    "xxxymovies",
    "sunporno",
    "spankbang",
)

MODES: Tuple[str, ...] = ("popular", "new", "top_rated")
MODE_LABELS: Dict[str, str] = {
    "popular": "Populer",
    "new": "Terbaru",
    "top_rated": "Rating Tertinggi",
}

# Shared defaults; per-provider entries only override what differs.
_BASE: Dict[str, Any] = {
    "enabled": False,
    "transport": "html",
    "availability": "disabled",
    "search": True,
    "paged": False,
    "modes": (),
    "hosts": (),
    "page_size": 24,
    "min_page_yield": 10,
    "origin": "",
    "feed": {},
    "search_urls": None,
    "api_url": "",
    "feed_params": {},
    "search_param": "search",
    "container": None,
    "note": "",
}

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "eporner": {
        **_BASE,
        "label": "Eporner",
        "hosts": ("eporner.com",),
        "origin": "https://www.eporner.com",
        "transport": "json",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("popular", "new", "top_rated"),
        "page_size": 24,
        "api_url": "https://www.eporner.com/api/v2/video/search/",
        "search_param": "query",
        "feed_params": {
            "popular": {"order": "top-weekly"},
            "new": {"order": "latest"},
            "top_rated": {"order": "top-rated"},
        },
        "note": "API v2 publik, tanpa key.",
    },
    "pornhub": {
        **_BASE,
        "label": "Pornhub",
        "hosts": ("pornhub.com",),
        "origin": "https://www.pornhub.com",
        "transport": "json",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("popular", "new", "top_rated"),
        "page_size": 30,
        "api_url": "https://www.pornhub.com/webmasters/search",
        "search_param": "search",
        "feed_params": {
            "popular": {"ordering": "mostviewed"},
            "new": {"ordering": "newest"},
            "top_rated": {"ordering": "rating"},
        },
        "note": "Endpoint webmasters publik; urutan katalog bersifat best-effort.",
    },
    "xhamster": {
        **_BASE,
        "label": "XHamster",
        "hosts": ("xhamster.com", "xhamster.desi", "xhamster.one"),
        "origin": "https://xhamster.com",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("popular", "new", "top_rated"),
        "page_size": 24,
        "container": ("div", "video-thumb"),
        "min_page_yield": 5,
        "feed": {
            "popular": ("https://xhamster.com/best", "https://xhamster.com/best/{page}"),
            "new": ("https://xhamster.com/newest", "https://xhamster.com/newest/{page}"),
            "top_rated": ("https://xhamster.com/best/weekly", "https://xhamster.com/best/weekly/{page}"),
        },
        "search_urls": (
            "https://xhamster.com/search/{query}",
            "https://xhamster.com/search/{query}?page={page}",
        ),
        "note": "HTML; hanya sekitar 7 kartu dirender server, sisanya lazy-load.",
    },
    "xvideos": {
        **_BASE,
        "label": "XVideos",
        "hosts": ("xvideos.com",),
        "origin": "https://www.xvideos.com",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("popular", "new"),
        "page_size": 27,
        "container": ("div", "thumb-block"),
        "feed": {
            "popular": ("https://www.xvideos.com/best", "https://www.xvideos.com/best/{page}"),
            "new": ("https://www.xvideos.com/new/1", "https://www.xvideos.com/new/{page}"),
        },
        "search_urls": (
            "https://www.xvideos.com/?k={query}",
            "https://www.xvideos.com/?k={query}&p={page}",
        ),
        "note": "HTML; tidak punya mode rating tertinggi.",
    },
    "xnxx": {
        **_BASE,
        "label": "XNXX",
        "hosts": ("xnxx.com",),
        "origin": "https://www.xnxx.com",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("popular",),
        "page_size": 36,
        "container": ("div", "thumb-block"),
        "feed": {
            "popular": ("https://www.xnxx.com/best", "https://www.xnxx.com/best/{page}"),
        },
        "search_urls": (
            "https://www.xnxx.com/search/{query}",
            "https://www.xnxx.com/search/{query}/{page}",
        ),
        "note": "HTML; hanya mode populer; durasi ditulis sebagai menit.",
    },
    "youporn": {
        **_BASE,
        "label": "YouPorn",
        "hosts": ("youporn.com",),
        "origin": "https://www.youporn.com",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("popular", "top_rated"),
        "page_size": 28,
        "container": ("article", "video-box"),
        "feed": {
            "popular": (
                "https://www.youporn.com/most_viewed/",
                "https://www.youporn.com/most_viewed/?page={page}",
            ),
            "top_rated": (
                "https://www.youporn.com/top_rated/",
                "https://www.youporn.com/top_rated/?page={page}",
            ),
        },
        "search_urls": (
            "https://www.youporn.com/search/?query={query}",
            "https://www.youporn.com/search/?query={query}&page={page}",
        ),
        "note": "HTML; halaman relatif besar (sekitar 500 KB).",
    },
    "youjizz": {
        **_BASE,
        "label": "YouJizz",
        "hosts": ("youjizz.com",),
        "origin": "https://www.youjizz.com",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("popular", "new", "top_rated"),
        "page_size": 24,
        "container": ("div", "video-item"),
        "feed": {
            "popular": (
                "https://www.youjizz.com/most-popular/1.html",
                "https://www.youjizz.com/most-popular/{page}.html",
            ),
            "new": (
                "https://www.youjizz.com/newest-clips/1.html",
                "https://www.youjizz.com/newest-clips/{page}.html",
            ),
            "top_rated": (
                "https://www.youjizz.com/top-rated-month/1.html",
                "https://www.youjizz.com/top-rated-month/{page}.html",
            ),
        },
        "search_urls": (
            "https://www.youjizz.com/search/{query}-1.html",
            "https://www.youjizz.com/search/{query}-{page}.html",
        ),
        "note": "HTML; rating dari select data-value.",
    },
    "xxxymovies": {
        **_BASE,
        "label": "XXXYMovies",
        "hosts": ("xxxymovies.com",),
        "origin": "https://xxxymovies.com",
        "enabled": True,
        "availability": "ok",
        "paged": True,
        "modes": ("new",),
        "page_size": 48,
        "container": ("div", "item"),
        "feed": {
            "new": (
                "https://xxxymovies.com/latest-updates/",
                "https://xxxymovies.com/latest-updates/{page}/",
            ),
        },
        "search_urls": ("https://xxxymovies.com/search/{query}/", None),
        "note": "HTML; pencarian hanya halaman pertama.",
    },
    "sunporno": {
        **_BASE,
        "label": "SunPorno",
        "hosts": ("sunporno.com",),
        "origin": "https://www.sunporno.com",
        "enabled": True,
        "availability": "search_only",
        "paged": False,
        "modes": (),
        "page_size": 8,
        "container": ("a", "item"),
        "search_urls": ("https://www.sunporno.com/search/{query}/", None),
        "note": "Hanya pencarian; sekitar 8 hasil, tanpa paginasi.",
    },
    "spankbang": {
        **_BASE,
        "label": "SpankBang",
        "hosts": ("spankbang.com",),
        "origin": "https://spankbang.com",
        "enabled": False,
        "availability": "blocked",
        "search": False,
        "modes": (),
        "note": "Cloudflare menolak permintaan otomatis; pakai tempel URL manual.",
    },
}


def get_spec(provider: Any) -> Optional[Dict[str, Any]]:
    """Return the spec for a provider key, case-insensitively."""
    key = str(provider or "").strip().lower()
    return PROVIDERS.get(key)


def enabled_keys() -> Tuple[str, ...]:
    """Provider keys enabled for automated listing, in stable display order."""
    return tuple(key for key in PROVIDER_ORDER if PROVIDERS[key].get("enabled"))


def host_allowed(provider: Any, host: Any) -> bool:
    """True when ``host`` is the provider domain or one of its subdomains."""
    spec = get_spec(provider)
    if not spec:
        return False
    candidate = str(host or "").strip().strip(".").lower()
    if not candidate:
        return False
    for domain in spec.get("hosts") or ():
        base = str(domain).lower()
        if candidate == base or candidate.endswith("." + base):
            return True
    return False


def url_belongs_to(provider: Any, raw_url: Any) -> bool:
    """Validate scheme/host of a page URL found in a provider response."""
    text = str(raw_url or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    return parsed.scheme == "https" and host_allowed(provider, parsed.hostname)


def provider_for_host(host: Any) -> str:
    """Return the provider that owns ``host``, or an empty string."""
    for key in PROVIDER_ORDER:
        if host_allowed(key, host):
            return key
    return ""