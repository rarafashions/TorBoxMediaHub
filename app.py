import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, quote_plus, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from requests.exceptions import RequestException, Timeout

import database_manager as db
import metadata_engine as meta_eng
import subtitle_engine as sub_eng

APP_VERSION = "v1.1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".m2ts")
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
TORBOX_API_BASE = "https://api.torbox.app/v1/api"
M3U_FRESH_TTL_SECONDS = 1800  # 30 minutes cache for freshly generated playlist M3U

DISCOVER_CACHE_TTL = 600  # 10 minutes cache for Discovery feeds
_DISCOVER_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_DISCOVER_CACHE_LOCK = threading.Lock()

MISSAV_DOMAINS = [
    "https://missav.ws",
    "https://missav.ai",
    "https://missav.live",
    "https://missav123.com",
]

JAV_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

JAV_CODE_PATTERN = re.compile(r"\b(FC2-PPV-\d{5,8}|[A-Z]{2,6}-\d{2,5})\b", re.IGNORECASE)


def _extract_jav_code_id(text: str) -> str:
    m = JAV_CODE_PATTERN.search((text or "").upper())
    return m.group(1).upper() if m else ""


def _scrape_missav_multi_page(feed: str = "trending", query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Mengambil daftar JAV dari MissAV secara multi-halaman (page 1..4) agar bisa mencapai hingga 50 item.
    """
    q = (query or "").strip()
    if feed == "search" and q:
        base_rel = f"/en/search/{quote_plus(q)}"
    elif feed == "popular":
        base_rel = "/en/today-hot"
    elif feed == "top_rated":
        base_rel = "/en/monthly-hot"
    else:
        base_rel = "/en/release"

    active_base = ""
    first_html = ""
    headers = {**JAV_SCRAPE_HEADERS, "Referer": "https://missav.ws/"}

    for domain in MISSAV_DOMAINS:
        try:
            resp = meta_eng.HTTP_SESSION.get(f"{domain}{base_rel}", headers=headers, timeout=(6, 12))
            if resp.status_code == 200 and len(resp.text) > 1500:
                active_base = domain
                first_html = resp.text
                break
        except Exception:
            continue

    if not active_base or not first_html:
        return []

    results: List[Dict[str, Any]] = []
    seen_keys = set()
    max_pages = max(2, (limit // 12) + 1)

    for page in range(1, max_pages + 1):
        if len(results) >= limit:
            break

        if page == 1:
            html = first_html
        else:
            sep = "&" if "?" in base_rel else "?"
            page_url = f"{active_base}{base_rel}{sep}page={page}"
            try:
                resp = meta_eng.HTTP_SESSION.get(
                    page_url,
                    headers={**headers, "Referer": f"{active_base}/"},
                    timeout=(6, 12),
                )
                if resp.status_code != 200 or len(resp.text) <= 1500:
                    break
                html = resp.text
            except Exception:
                break

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div.thumbnail, div.group, article")
        if not cards:
            break

        added_in_page = 0
        for card in cards:
            link_el = card.select_one("a[href*='/en/'], a[href]")
            img_el = card.select_one("img")
            if not link_el or not img_el:
                continue

            href = (link_el.get("href") or "").strip()
            if not href or any(skip in href for skip in ["/actresses", "/makers", "/genres", "/tags", "/search"]):
                continue

            poster = (
                img_el.get("data-src")
                or img_el.get("data-original")
                or img_el.get("data-lazy-src")
                or img_el.get("src")
                or ""
            ).strip()

            if not poster or poster.startswith("data:"):
                continue
            if poster.startswith("//"):
                poster = f"https:{poster}"
            elif poster.startswith("/"):
                poster = f"{active_base}{poster}"

            raw_title = (
                img_el.get("alt")
                or link_el.get("title")
                or card.get_text(" ", strip=True)
                or ""
            ).strip()
            raw_title = re.sub(r"\s+", " ", raw_title)
            if not raw_title or len(raw_title) < 3:
                continue

            code_id = _extract_jav_code_id(f"{href} {raw_title}")
            dedup_key = code_id or href
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            clean_title = raw_title
            if code_id and clean_title.upper().startswith(code_id):
                clean_title = clean_title[len(code_id):].lstrip(" -:_") or code_id

            results.append(
                {
                    "id": f"missav:{dedup_key}",
                    "title": clean_title,
                    "code_id": code_id,
                    "year": None,
                    "rating": 0.0,
                    "poster_url": poster,
                    "overview": f"Source: MissAV ({active_base.replace('https://', '')})",
                    "category": "jav",
                    "search_query": code_id or clean_title,
                }
            )
            added_in_page += 1
            if len(results) >= limit:
                break

        if added_in_page == 0:
            break

    return results


def _scrape_javdatabase_multi_page(feed: str = "trending", query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Provider kedua untuk JAV Discovery (JAVDatabase multi-page) sebagai kombinasi dengan MissAV.
    """
    q = (query or "").strip()
    results: List[Dict[str, Any]] = []
    seen_keys = set()
    headers = {**JAV_SCRAPE_HEADERS, "Referer": "https://www.javdatabase.com/"}
    max_pages = max(2, (limit // 15) + 1)

    for page in range(1, max_pages + 1):
        if len(results) >= limit:
            break

        if feed == "search" and q:
            url = (
                f"https://www.javdatabase.com/?s={quote_plus(q)}"
                if page == 1
                else f"https://www.javdatabase.com/page/{page}/?s={quote_plus(q)}"
            )
        elif feed == "popular":
            url = (
                "https://www.javdatabase.com/movies/?orderby=views"
                if page == 1
                else f"https://www.javdatabase.com/movies/page/{page}/?orderby=views"
            )
        elif feed == "top_rated":
            url = (
                "https://www.javdatabase.com/movies/?orderby=rating"
                if page == 1
                else f"https://www.javdatabase.com/movies/page/{page}/?orderby=rating"
            )
        else:
            url = (
                "https://www.javdatabase.com/movies/"
                if page == 1
                else f"https://www.javdatabase.com/movies/page/{page}/"
            )

        try:
            resp = meta_eng.HTTP_SESSION.get(url, headers=headers, timeout=(6, 12))
            if resp.status_code != 200 or len(resp.text) < 1500:
                break
        except Exception:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.card, article, div.movie-item, div.post")
        if not cards:
            break

        added_in_page = 0
        for card in cards:
            link_el = card.select_one("a[href*='/movies/'], a[title], a[href]")
            img_el = card.select_one("img")
            if not link_el or not img_el:
                continue

            href = (link_el.get("href") or "").strip()
            if not href or any(skip in href for skip in ["/idols/", "/studios/", "/genres/", "/tags/", "/category/"]):
                continue

            poster = (
                img_el.get("data-src")
                or img_el.get("data-lazy-src")
                or img_el.get("src")
                or ""
            ).strip()
            if not poster or poster.startswith("data:"):
                continue
            if poster.startswith("//"):
                poster = f"https:{poster}"
            elif poster.startswith("/"):
                poster = f"https://www.javdatabase.com{poster}"

            raw_title = (
                link_el.get("title")
                or img_el.get("alt")
                or link_el.get_text(" ", strip=True)
                or ""
            ).strip()
            raw_title = re.sub(r"\s+", " ", raw_title)
            if not raw_title or len(raw_title) < 3:
                continue

            code_id = _extract_jav_code_id(f"{href} {raw_title}")
            dedup_key = code_id or href
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            clean_title = raw_title
            if code_id and clean_title.upper().startswith(code_id):
                clean_title = clean_title[len(code_id):].lstrip(" -:_") or code_id

            results.append(
                {
                    "id": f"javdb:{dedup_key}",
                    "title": clean_title,
                    "code_id": code_id,
                    "year": None,
                    "rating": 0.0,
                    "poster_url": poster,
                    "overview": "Source: JAVDatabase",
                    "category": "jav",
                    "search_query": code_id or clean_title,
                }
            )
            added_in_page += 1
            if len(results) >= limit:
                break

        if added_in_page == 0:
            break

    return results


def fetch_tpdb_scenes(api_key: str, query: str = "", feed: str = "trending", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Mengambil data scene dari ThePornDB (TPDB) menggunakan pagination per_page=25,
    timeout=(8, 22), serta retry otomatis agar terhindar dari ReadTimeoutError pada limit 50.
    """
    if not api_key:
        return []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": f"TorBoxMediaHub/{APP_VERSION}",
    }

    per_page = min(limit, 25)
    max_pages = (limit + per_page - 1) // per_page
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        params: Dict[str, Any] = {
            "per_page": per_page,
            "page": page,
        }
        if query:
            params["q"] = query
        elif feed == "popular":
            params["orderBy"] = "views"
        elif feed == "top_rated":
            params["orderBy"] = "rating"
        else:
            params["orderBy"] = "date"

        data = None
        for attempt in range(2):
            try:
                resp = requests.get(
                    "https://api.theporndb.net/scenes",
                    headers=headers,
                    params=params,
                    timeout=(8, 22),
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Timeout:
                if attempt == 1 and page == 1 and per_page > 15:
                    params["per_page"] = 15
                    try:
                        resp = requests.get(
                            "https://api.theporndb.net/scenes",
                            headers=headers,
                            params=params,
                            timeout=(8, 25),
                        )
                        resp.raise_for_status()
                        data = resp.json()
                    except Exception:
                        pass
                time.sleep(0.4)
            except RequestException as exc:
                print(f"[TPDB] Request error on page {page}: {exc}")
                break

        if not data or not isinstance(data.get("data"), list):
            break

        items = data["data"]
        if not items:
            break

        for item in items:
            item_id = item.get("id") or item.get("slug")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            results.append(item)
            if len(results) >= limit:
                return results

        if len(items) < params["per_page"]:
            break

    return results[:limit]


def fetch_jav_discover(
    feed: str = "trending",
    query: str = "",
    limit: int = 50,
    tpdb_key: str = "",
) -> List[Dict[str, Any]]:
    """
    Mengombinasikan beberapa provider JAV secara paralel (MissAV multi-page + JAVDatabase multi-page),
    lalu melengkapi sisa kuota hingga `limit` (50) via TPDB JAV dan Sukebei RSS jika diperlukan.
    """
    combined: List[Dict[str, Any]] = []
    seen_keys = set()

    def _merge_items(batch: List[Dict[str, Any]]) -> None:
        for item in batch:
            key = (item.get("code_id") or item.get("title") or "").strip().upper()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            combined.append(item)
            if len(combined) >= limit:
                return

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_missav = pool.submit(_scrape_missav_multi_page, feed, query, limit)
        fut_javdb = pool.submit(_scrape_javdatabase_multi_page, feed, query, limit)

        missav_items: List[Dict[str, Any]] = []
        javdb_items: List[Dict[str, Any]] = []
        try:
            missav_items = fut_missav.result()
        except Exception:
            pass
        try:
            javdb_items = fut_javdb.result()
        except Exception:
            pass

        _merge_items(missav_items)
        if len(combined) < limit:
            _merge_items(javdb_items)

    # Fallback 1: ThePornDB dengan kata kunci JAV jika hasil scraping masih < limit
    if len(combined) < limit and tpdb_key:
        tpdb_q = f"JAV {query}".strip() if query else "JAV"
        remaining = limit - len(combined)
        scenes = fetch_tpdb_scenes(tpdb_key, query=tpdb_q, feed=feed, limit=remaining)
        tpdb_mapped: List[Dict[str, Any]] = []
        for sc in scenes:
            sc_title = sc.get("title") or ""
            code_id = _extract_jav_code_id(sc_title)
            date_str = sc.get("date") or ""
            year_val = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None
            tpdb_mapped.append(
                {
                    "id": f"tpdb-jav:{sc.get('id')}",
                    "title": sc_title,
                    "code_id": code_id,
                    "year": year_val,
                    "rating": round(float(sc.get("rating") or 0), 1),
                    "poster_url": sc.get("poster") or sc.get("image") or "",
                    "overview": (sc.get("description") or "Source: ThePornDB JAV")[:240],
                    "category": "jav",
                    "search_query": code_id or sc_title,
                }
            )
        _merge_items(tpdb_mapped)

    # Fallback 2: Nyaa Sukebei RSS jika masih di bawah target limit
    if len(combined) < limit:
        remaining = limit - len(combined)
        q_term = query if query else ("FHD" if feed == "top_rated" else ("Uncensored" if feed == "popular" else ""))
        rss_url = f"https://sukebei.nyaa.si/?page=rss&c=2_2&q={quote_plus(q_term)}"
        try:
            r = meta_eng.HTTP_SESSION.get(rss_url, timeout=10)
            if r.status_code == 200 and "<item>" in r.text:
                soup = BeautifulSoup(r.text, "xml")
                rss_mapped: List[Dict[str, Any]] = []
                for idx, item in enumerate(soup.find_all("item")[: remaining + 15], start=1):
                    t_txt = item.title.get_text(strip=True) if item.title else ""
                    ih_tag = item.find("nyaa:infoHash")
                    ih = ih_tag.get_text(strip=True) if ih_tag else ""
                    size_tag = item.find("nyaa:size")
                    size_txt = size_tag.get_text(strip=True) if size_tag else ""
                    code_id = _extract_jav_code_id(t_txt)
                    rss_mapped.append(
                        {
                            "id": f"jav:{ih or idx}",
                            "title": t_txt,
                            "code_id": code_id,
                            "year": None,
                            "rating": 0.0,
                            "poster_url": "",
                            "overview": f"Release Size: {size_txt}" if size_txt else "Nyaa Sukebei Release",
                            "category": "jav",
                            "search_query": code_id or t_txt[:45],
                            "direct_hash": ih,
                        }
                    )
                _merge_items(rss_mapped)
        except Exception:
            pass

    return combined[:limit]


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
    ) -> None:
        if not item_id:
            return
        key = f"{kind}:{item_id}"
        with self._lock:
            self.watched_items[key] = {
                "key": key,
                "kind": kind,
                "item_id": str(item_id),
                "title": title or f"{kind} #{item_id}",
                "series_title": series_title,
                "category": category,
                "season": season,
                "replace_media_id": int(replace_media_id) if replace_media_id else None,
                "delete_remote_on_replace": bool(delete_remote_on_replace),
                "progress": 0.0,
                "download_state": "queued",
                "finished": False,
                "added_at": time.time(),
            }
            if not self._thread_started:
                self._thread_started = True
                threading.Thread(target=self._loop, daemon=True).start()

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
            time.sleep(5)
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

            for kind, ep in (("torrent", "torrents/mylist"), ("usenet", "usenet/mylist")):
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
                        completed_entries.append(dict(entry))
                        del self.watched_items[key]

                if completed_entries:
                    self.syncing_items.extend(completed_entries)

            if completed_entries:
                try:
                    for _ in range(60):
                        if not TASK_MGR.running:
                            break
                        time.sleep(2)

                    started = TASK_MGR.start(
                        "auto_import_torbox",
                        f"Auto-importing {len(completed_entries)} completed TorBox download(s)...",
                    )
                    if started:
                        try:
                            _sync_torbox_cloud_worker(auto_enrich=True)
                        except Exception as exc:
                            TASK_MGR.finish(f"Auto-import error: {exc}")
                    else:
                        _sync_torbox_cloud_worker(auto_enrich=True)

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
                    with self._lock:
                        done_keys = {e["key"] for e in completed_entries}
                        self.syncing_items = [
                            s for s in self.syncing_items if s["key"] not in done_keys
                        ]
                        self.recently_imported.extend(completed_entries)


TORBOX_MONITOR = TorboxDownloadMonitor()


def _is_video_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS


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
        "ffprobe_available": bool(shutil.which("ffprobe")),
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

    return found


def _request_torbox_stream_url(api_key: str, kind: str, item_id: str, file_id: str) -> Optional[str]:
    endpoint = "torrents" if kind == "torrent" else "usenet"
    id_param = "torrent_id" if kind == "torrent" else "usenet_id"
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
    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        TASK_MGR.finish("Failed: TorBox API Key is not configured in Settings.")
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    discovered: List[Dict[str, Any]] = []

    for kind, ep in (("torrent", "torrents/mylist"), ("usenet", "usenet/mylist")):
        TASK_MGR.update(message=f"Fetching {kind} list from TorBox Cloud...")
        try:
            resp = meta_eng.HTTP_SESSION.get(
                f"{TORBOX_API_BASE}/{ep}",
                headers=headers,
                params={"bypass_cache": "true"},
                timeout=20,
            )
            if resp.status_code != 200:
                TASK_MGR.update(log_line=f"Warning {kind}: HTTP {resp.status_code}")
                continue
            data_list = resp.json().get("data") or []
            if isinstance(data_list, dict):
                data_list = [data_list]
            for parent in data_list:
                if not parent.get("download_finished", True) and parent.get("download_present") is False:
                    continue
                raw_pid = parent.get("id")
                pid = str(raw_pid) if raw_pid is not None else ""
                parent_title = (parent.get("name") or "").strip()
                for fobj in (parent.get("files") or []):
                    raw_name = fobj.get("short_name") or os.path.basename(fobj.get("name") or "")
                    if not raw_name or not _is_video_file(raw_name):
                        continue
                    raw_fid = fobj.get("id")
                    fid = str(raw_fid) if raw_fid is not None else "0"
                    discovered.append(
                        {
                            "kind": kind,
                            "item_id": pid,
                            "file_id": fid,
                            "parent_name": parent_title,
                            "file_name": raw_name,
                            "file_size": int(fobj.get("size") or 0),
                        }
                    )
        except Exception as exc:
            TASK_MGR.update(log_line=f"Connection error ({kind}): {exc}")

    TASK_MGR.update(total=len(discovered), current=0, message=f"Saving {len(discovered)} items to database...")
    new_or_updated_ids: List[int] = []

    with db.get_connection() as conn:
        for idx, entry in enumerate(discovered, start=1):
            ukey = f"torbox:{entry['kind']}:{entry['item_id']}:{entry['file_id']}"
            mid = db.upsert_media_item(
                {
                    "unique_key": ukey,
                    "source_type": "cloud",
                    "torbox_kind": entry["kind"],
                    "torbox_item_id": entry["item_id"],
                    "torbox_file_id": entry["file_id"],
                    "parent_name": entry["parent_name"],
                    "file_name": entry["file_name"],
                    "file_size": entry["file_size"],
                    "title": entry["file_name"],
                },
                conn=conn,
            )
            new_or_updated_ids.append(mid)
            if idx % 25 == 0 or idx == len(discovered):
                TASK_MGR.update(current=idx, message=f"Syncing Cloud ({idx}/{len(discovered)})...")
        conn.commit()

    if auto_enrich and new_or_updated_ids:
        _enrich_ids_internal(new_or_updated_ids, force=False)

    TASK_MGR.finish(f"TorBox sync completed: {len(discovered)} files processed.")


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


def _enrich_ids_internal(media_ids: List[int], force: bool = False) -> int:
    settings = db.get_settings()
    items_to_process: List[Dict[str, Any]] = []

    for mid in media_ids:
        item = db.get_media_item(mid)
        if not item:
            continue
        if (
            not force
            and item.get("has_metadata") == 1
            and item.get("content_category") != "uncategorized"
            and item.get("has_poster")
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
                db.upsert_media_item(enriched)
                enriched_count += 1
                TASK_MGR.update(
                    current=completed,
                    message=f"Enrich ({completed}/{total}): [{enriched.get('content_category')}] {it['file_name']}",
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
    db.verify_subtitle_files()
    targets = db.query_media_items(missing_subtitle_only=True)
    if not targets:
        TASK_MGR.finish("All media items already have subtitles.")
        return

    settings = db.get_settings()
    torbox_key = (settings.get("torbox_api_key") or "").strip()

    TASK_MGR.update(total=len(targets), current=0, message="Building TorBox Cloud subtitle index...")
    torbox_sub_index = sub_eng.build_torbox_subtitle_index(torbox_key) if torbox_key else {}

    found_count = 0
    completed = 0
    total = len(targets)

    def _sub_worker(it: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[str]]:
        try:
            res = sub_eng.fetch_subtitle_for_item(it, torbox_api_key=torbox_key, torbox_sub_index=torbox_sub_index)
            return it, res, None
        except Exception as e:
            return it, None, str(e)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_sub_worker, it) for it in targets]
        for fut in as_completed(futures):
            completed += 1
            it, res, err = fut.result()
            if res and res.get("subtitle_path"):
                db.update_media_subtitle(
                    it["id"],
                    res["subtitle_path"],
                    res.get("subtitle_lang") or "en",
                    res.get("subtitle_source") or "auto",
                )
                found_count += 1
                TASK_MGR.update(
                    current=completed,
                    message=f"Find Subtitles ({completed}/{total}): {it['file_name']}",
                    log_line=f"OK [{res.get('subtitle_source')}]: {it['file_name']}",
                )
            else:
                TASK_MGR.update(
                    current=completed,
                    message=f"Find Subtitles ({completed}/{total}): {it['file_name']}",
                )

    TASK_MGR.finish(f"Subtitle search completed: found {found_count} of {total} items.")


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

    lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
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

        lines.append(f"#EXTINF:{dur},{disp_title}")
        sp = it.get("subtitle_path") or ""
        if sp and not sp.startswith("embedded://") and os.path.isfile(sp):
            if remote_base_url:
                sub_url = f"{remote_base_url.rstrip('/')}/api/media/{it['id']}/subtitle-file"
                lines.append(f"#EXTVLCOPT:sub-file={sub_url}")
            else:
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

    which_bin = (shutil.which(raw_path) if raw_path else None) or (
        shutil.which(player_type) if player_type in ("vlc", "mpv") else None
    )
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
    Universal multi-indexer search across all 6 video categories.
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

    # C. JAV & ENGLISH ADULT
    elif category in ("jav", "english_adult"):
        if clean_q:
            try:
                sukebei_url = f"https://sukebei.nyaa.si/?page=rss&c=2_2&q={quote_plus(clean_q)}"
                r = meta_eng.HTTP_SESSION.get(sukebei_url, timeout=9)
                if r.status_code == 200 and "<item>" in r.text:
                    soup = BeautifulSoup(r.text, "xml")
                    for item in soup.find_all("item")[:15]:
                        t_txt = item.title.get_text(strip=True) if item.title else ""
                        ih_tag = item.find("nyaa:infoHash")
                        ih = ih_tag.get_text(strip=True) if ih_tag else ""
                        _add(ih, t_txt, "Sukebei")
            except Exception:
                pass

            try:
                r = meta_eng.HTTP_SESSION.get("https://apibay.org/q.php", params={"q": clean_q, "cat": "500"}, timeout=8)
                if r.status_code == 200 and isinstance(r.json(), list):
                    for row in r.json()[:12]:
                        ih = row.get("info_hash") or ""
                        if ih != "0000000000000000000000000000000000000000":
                            _add(ih, row.get("name") or "", "TPB-XXX")
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
    )
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

@app.route("/api/discover", methods=["GET"])
def api_discover():
    category = (request.args.get("category") or "general_movie").strip().lower()
    feed = (request.args.get("feed") or "trending").strip().lower()
    query = (request.args.get("query") or "").strip()
    try:
        limit = min(max(int(request.args.get("limit") or 50), 1), 100)
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
    tpdb_key = (settings.get("tpdb_api_key") or "").strip()
    results: List[Dict[str, Any]] = []

    try:
        # 1. GENERAL MOVIE & TV SHOW (TMDb Paginated up to 50 items)
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

        # 2. ANIME & DONGHUA (AniList GraphQL perPage=50)
        elif category in ("anime", "donghua"):
            country_code = "CN" if category == "donghua" else "JP"
            sort_enum = "SEARCH_MATCH" if query else (
                "SCORE_DESC" if feed == "top_rated" else (
                    "POPULARITY_DESC" if feed == "popular" else "TRENDING_DESC"
                )
            )
            gql = """
            query ($search: String, $country: CountryCode, $sort: [MediaSort], $perPage: Int) {
              Page(page: 1, perPage: $perPage) {
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
            variables: Dict[str, Any] = {
                "country": country_code,
                "sort": [sort_enum],
                "perPage": min(limit, 50),
            }
            if query:
                variables["search"] = query
            r = meta_eng.HTTP_SESSION.post(
                "https://graphql.anilist.co",
                json={"query": gql, "variables": variables},
                timeout=12,
            )
            if r.status_code == 200:
                media_list = (((r.json().get("data") or {}).get("Page") or {}).get("media") or [])
                for m in media_list[:limit]:
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

        # 3. JAV (Multi-Provider: MissAV + JAVDatabase + TPDB JAV + Sukebei RSS up to 50 items)
        elif category == "jav":
            results = fetch_jav_discover(feed=feed, query=query, limit=limit, tpdb_key=tpdb_key)

        # 4. ENGLISH ADULT (ThePornDB Paginated API up to 50 items + Sukebei XXX fallback)
        elif category == "english_adult":
            if tpdb_key:
                scenes = fetch_tpdb_scenes(tpdb_key, query=query, feed=feed, limit=limit)
                for sc in scenes[:limit]:
                    sc_title = sc.get("title") or ""
                    site_name = (sc.get("site") or {}).get("name") or ""
                    date_str = sc.get("date") or ""
                    year_val = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None
                    q_str = f"{site_name} {sc_title}".strip() if site_name else sc_title
                    results.append(
                        {
                            "id": f"tpdb:{sc.get('id')}",
                            "title": f"[{site_name}] {sc_title}" if site_name else sc_title,
                            "year": year_val,
                            "rating": round(float(sc.get("rating") or 0), 1),
                            "poster_url": sc.get("poster") or sc.get("image") or "",
                            "overview": (sc.get("description") or "")[:240],
                            "category": "english_adult",
                            "search_query": q_str,
                        }
                    )

            if len(results) < limit:
                remaining = limit - len(results)
                seen_adult_titles = {r.get("title", "").lower() for r in results}
                q_term = query if query else "1080p"
                rss_url = f"https://sukebei.nyaa.si/?page=rss&c=2_2&q={quote_plus(q_term)}"
                try:
                    r = meta_eng.HTTP_SESSION.get(rss_url, timeout=10)
                    if r.status_code == 200 and "<item>" in r.text:
                        soup = BeautifulSoup(r.text, "xml")
                        for idx, item in enumerate(soup.find_all("item")[: remaining + 10], start=1):
                            t_txt = item.title.get_text(strip=True) if item.title else ""
                            if not t_txt or t_txt.lower() in seen_adult_titles:
                                continue
                            seen_adult_titles.add(t_txt.lower())
                            ih_tag = item.find("nyaa:infoHash")
                            ih = ih_tag.get_text(strip=True) if ih_tag else ""
                            size_tag = item.find("nyaa:size")
                            size_txt = size_tag.get_text(strip=True) if size_tag else ""
                            results.append(
                                {
                                    "id": f"adult:{ih or idx}",
                                    "title": t_txt,
                                    "year": None,
                                    "rating": 0.0,
                                    "poster_url": "",
                                    "overview": f"Release Size: {size_txt}" if size_txt else "Public XXX Release",
                                    "category": "english_adult",
                                    "search_query": t_txt[:45],
                                    "direct_hash": ih,
                                }
                            )
                            if len(results) >= limit:
                                break
                except Exception:
                    pass

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


@app.route("/api/discover/poster-proxy", methods=["GET"])
def api_discover_poster_proxy():
    """
    Proxies and caches external discovery cover images (MissAV, Fourhoi, Surrit, DMM, ThePornDB)
    with valid Referer headers so hotlink-protected covers render cleanly in the browser.
    """
    raw_url = (request.args.get("url") or "").strip()
    if not raw_url or not raw_url.startswith(("http://", "https://")):
        return abort(400)

    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0"}:
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

    try:
        resp = meta_eng.HTTP_SESSION.get(raw_url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return abort(404)
        content_type = (resp.headers.get("Content-Type") or "image/jpeg").lower()
        if "image" not in content_type and "octet-stream" not in content_type:
            return abort(415)

        ext = ".webp" if "webp" in content_type else (".png" if "png" in content_type else ".jpg")
        out_file = os.path.join(cache_dir, f"{url_hash}{ext}")
        data = resp.content
        if len(data) < 256:
            return abort(404)
        with open(out_file, "wb") as f:
            f.write(data)
        return send_file(out_file, mimetype=content_type.split(";")[0], max_age=86400)
    except Exception:
        return abort(502)


@app.route("/api/discover/search-cache", methods=["POST"])
def api_discover_search_cache():
    """
    Universal TorBox Instant Cache search across all 6 video categories (for new media OR quality upgrades).
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
    category = (payload.get("category") or "").strip()
    season = int(payload.get("season") or 1)
    replace_media_id = payload.get("replace_media_id")
    delete_remote_on_replace = bool(payload.get("delete_remote_on_replace", True))

    settings = db.get_settings()
    api_key = (settings.get("torbox_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "TorBox API Key is not configured."}), 400

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        if kind == "usenet":
            if not nzb_url:
                return jsonify({"ok": False, "error": "NZB URL is missing."}), 400
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
    )
    return jsonify({"ok": True, "items": items, "stats": db.get_telemetry_stats()})


@app.route("/api/media/<int:media_id>/poster", methods=["GET"])
def api_media_poster(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return ("", 404)
    lp = item.get("local_poster_path")
    if lp and os.path.isfile(lp) and os.path.getsize(lp) > 1500:
        return send_file(lp, max_age=120)
    return ("", 404)


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
    if not sp or sp.startswith("embedded://") or not os.path.isfile(sp):
        return ("Subtitle file not available", 404)
    return send_file(sp, mimetype="text/plain; charset=utf-8", as_attachment=False)


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


@app.route("/api/media/<int:media_id>/enrich", methods=["POST"])
def api_media_enrich_single(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404
    lp = item.get("local_poster_path")
    if lp and os.path.isfile(lp):
        try:
            os.remove(lp)
        except OSError:
            pass
    settings = db.get_settings()
    probe_ready_item = _attach_cloud_stream_for_probe(item, settings)
    enriched = meta_eng.enrich_media_item(probe_ready_item, settings)
    enriched["unique_key"] = item["unique_key"]
    enriched["file_name"] = item["file_name"]
    enriched["_force_meta"] = True
    db.upsert_media_item(enriched)
    return jsonify({"ok": True, "item": db.get_media_item(media_id), "stats": db.get_telemetry_stats()})


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

        sp = item.get("subtitle_path") or ""
        has_ext_sub = bool(sp and not sp.startswith("embedded://") and os.path.isfile(sp))

        if bin_path.startswith("android://"):
            base_url = request.host_url.rstrip("/")
            android_target = (
                f"{base_url}/api/media/{item['id']}/stream"
                if item.get("source_type") == "local"
                else target
            )
            sub_http = f"{base_url}/api/media/{item['id']}/subtitle-file" if has_ext_sub else None
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

        if has_ext_sub:
            if is_mpv_like:
                mpv_sub = sp.replace("\\", "/")
                cmd.extend([f"--sub-file={mpv_sub}", "--sid=1", "--sub-visibility=yes"])
            elif is_vlc_like:
                cmd.append(f"--sub-file={sp}")
            elif is_mpc_like or is_pot_like:
                cmd.extend(["/sub", sp])
        elif is_mpv_like:
            cmd.append("--sub-auto=fuzzy")

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
        return jsonify({"ok": False, "error": "Another background task is already running."}), 409
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


@app.route("/api/task/status", methods=["GET"])
def api_task_status():
    return jsonify({"ok": True, "task": TASK_MGR.status(), "stats": db.get_telemetry_stats()})


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

    if not _is_m3u_file_fresh(pl.get("file_path")):
        _generate_m3u_file(pl["name"], pl["items"], existing_file_path=pl["file_path"])

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
        sub_files = []
        for it in pl.get("items") or []:
            sp = it.get("subtitle_path") or ""
            if sp and not sp.startswith("embedded://") and os.path.isfile(sp):
                sub_files.append(sp.replace("\\", "/"))
        cmd.extend(["--sub-auto=fuzzy", "--sid=1", "--sub-visibility=yes"])
        if len(sub_files) == 1:
            cmd.append(f"--sub-file={sub_files[0]}")
        elif len(sub_files) > 1:
            sep = ";" if os.name == "nt" else ":"
            cmd.append(f"--sub-files={sep.join(sub_files)}")
        cmd.append(pl["file_path"])
    else:
        cmd.append(pl["file_path"])

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
            "backups": db.list_db_backups(),
            "default_paths": db.get_default_player_paths(),
            "detected_players": detect_installed_players(),
            "os_platform": db.detect_runtime_os(),
            "env": _get_environment_info(),
        })
    payload = request.get_json(silent=True) or {}
    saved = db.save_settings(payload)
    return jsonify({"ok": True, "settings": saved})


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
    db.init_db()
    host = os.environ.get("TBH_HOST", "127.0.0.1")
    port = int(os.environ.get("TBH_PORT", "5050"))
    app.run(host=host, port=port, debug=False, threaded=True)