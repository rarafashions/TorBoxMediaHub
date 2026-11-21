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
from urllib.parse import quote_plus

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


def _detect_lang_from_name(name: str) -> str:
    low = name.lower()
    if any(k in low for k in (".id.", "_id.", "[id]", "(id)", "indonesian", "indonesia", "bahasa", ".ind.")):
        return "id"
    if any(k in low for k in (".en.", "_en.", "[en]", "(en)", "english", ".eng.")):
        return "en"
    if any(k in low for k in (".ms.", "_ms.", "malay", "melayu")):
        return "ms"
    if any(k in low for k in (".zh.", ".chs.", ".cht.", "chinese")):
        return "zh"
    if any(k in low for k in (".ja.", ".jpn.", "japanese")):
        return "ja"
    return "en"


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


def _save_subtitle_bytes(raw: bytes, dest_base_name: str, ext: str = ".srt") -> Optional[str]:
    if not _is_valid_subtitle_bytes(raw):
        return None
    if not ext.startswith("."):
        ext = f".{ext}"
    if ext.lower() not in SUBTITLE_EXTS:
        ext = ".srt"
    safe_base = re.sub(r"[^\w\-.]+", "_", dest_base_name)[:90]
    out_path = os.path.join(SUBTITLE_CACHE_DIR, f"{safe_base}{ext.lower()}")
    with open(out_path, "wb") as f:
        f.write(raw)
    return out_path


def _extract_subtitle_from_zip(raw_zip: bytes, dest_base_name: str) -> Optional[Tuple[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            candidates = [
                n for n in zf.namelist()
                if not n.endswith("/") and os.path.splitext(n)[1].lower() in SUBTITLE_EXTS
            ]
            if not candidates:
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
                return saved, _detect_lang_from_name(chosen)
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
    pid = str(item.get("torbox_item_id") or "")
    fid = str(item.get("torbox_file_id") or "")
    if not torbox_api_key or not pid or not fid:
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
            timeout=10,
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
    stem = os.path.splitext(os.path.basename(video_path))[0].lower()

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
            if stem in fn_low or fn_low.startswith(stem[: max(6, len(stem) // 2)]):
                lang = _detect_lang_from_name(fn)
                prio = 0 if lang == "en" else (1 if lang == "id" else 2)
                if best_match is None or prio < best_match[0]:
                    best_match = (prio, full_p, lang)
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
                                "file_id": str(fobj.get("id")),
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
) -> Optional[Tuple[str, str]]:
    if not torbox_api_key or item.get("source_type") != "cloud":
        return None
    kind = item.get("torbox_kind") or "torrent"
    pid = str(item.get("torbox_item_id") or "")
    candidates = sub_index.get((kind, pid)) or []
    if not candidates:
        return None

    vid_stem = os.path.splitext(item.get("file_name") or "")[0].lower()
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for c in candidates:
        c_stem = os.path.splitext(c["file_name"])[0].lower()
        lang = _detect_lang_from_name(c["file_name"])
        lang_prio = 0 if lang == "en" else (1 if lang == "id" else 2)
        name_match = 0 if (vid_stem and (vid_stem in c_stem or c_stem in vid_stem)) else 1
        scored.append((name_match * 10 + lang_prio, c))

    scored.sort(key=lambda x: x[0])
    chosen = scored[0][1]

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
                    saved = _save_subtitle_bytes(sub_resp.content, f"tb_{digest}", ext=ext)
                    if saved:
                        return saved, _detect_lang_from_name(chosen["file_name"])
    except Exception:
        pass
    return None


def find_torbox_cloud_subtitle_for_item(item: Dict[str, Any], torbox_api_key: str) -> Optional[Tuple[str, str]]:
    if not torbox_api_key or item.get("source_type") != "cloud":
        return None
    idx = build_torbox_subtitle_index(torbox_api_key)
    return download_torbox_cloud_subtitle_from_index(item, torbox_api_key, idx)


def _search_subtitlecat_jav(code_id: str, dest_base_name: str) -> Optional[Tuple[str, str]]:
    if not code_id:
        return None
    try:
        url = f"https://www.subtitlecat.com/index.php?search={quote_plus(code_id)}"
        resp = HTTP_SESSION.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        links = soup.select("table.sub-table tbody tr td a")
        target_href = None
        for a in links:
            txt = a.get_text(" ", strip=True).upper()
            if code_id.upper() in txt:
                target_href = a.get("href")
                break
        if not target_href:
            return None
        detail_url = target_href if target_href.startswith("http") else f"https://www.subtitlecat.com/{target_href.lstrip('/')}"
        d_resp = HTTP_SESSION.get(detail_url, timeout=10)
        if d_resp.status_code != 200:
            return None
        d_soup = BeautifulSoup(d_resp.text, "html.parser")
        dl_links = d_soup.select("a.green-link")
        chosen_dl = None
        chosen_lang = "en"
        for dl in dl_links:
            href = dl.get("href") or ""
            low = href.lower()
            label = dl.get_text(" ", strip=True).lower()
            if "-en." in low or "english" in label:
                chosen_dl = href
                chosen_lang = "en"
                break
            if ("-id." in low or "indonesian" in label) and not chosen_dl:
                chosen_dl = href
                chosen_lang = "id"
        if not chosen_dl and dl_links:
            chosen_dl = dl_links[0].get("href")
            chosen_lang = _detect_lang_from_name(chosen_dl or "")
        if chosen_dl:
            full_dl = chosen_dl if chosen_dl.startswith("http") else f"https://www.subtitlecat.com/{chosen_dl.lstrip('/')}"
            s_resp = HTTP_SESSION.get(full_dl, timeout=10)
            if s_resp.status_code == 200:
                saved = _save_subtitle_bytes(s_resp.content, dest_base_name, ext=".srt")
                if saved:
                    return saved, chosen_lang
    except Exception:
        pass
    return None


def _search_opensubtitles_rest(
    query_title: str,
    dest_base_name: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Optional[Tuple[str, str]]:
    if not query_title:
        return None
    for lang_code in ("eng", "ind"):
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
            best = rows[0]
            dl_link = best.get("SubDownloadLink") or best.get("ZipDownloadLink")
            if not dl_link:
                continue
            dl_resp = HTTP_SESSION.get(dl_link, timeout=12)
            if dl_resp.status_code != 200:
                continue

            raw = dl_resp.content
            short_lang = "en" if lang_code == "eng" else "id"
            if dl_link.endswith(".gz"):
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
                saved = _save_subtitle_bytes(raw, dest_base_name, ext=".srt")
                if saved:
                    return saved, short_lang
            elif zipfile.is_zipfile(io.BytesIO(raw)):
                extracted = _extract_subtitle_from_zip(raw, dest_base_name)
                if extracted:
                    return extracted
            else:
                saved = _save_subtitle_bytes(raw, dest_base_name, ext=".srt")
                if saved:
                    return saved, short_lang
        except Exception:
            continue
    return None


def fetch_subtitle_for_item(
    item: Dict[str, Any],
    torbox_api_key: str = "",
    torbox_sub_index: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    digest = hashlib.sha256((item.get("unique_key") or str(item.get("id"))).encode()).hexdigest()[:20]
    dest_base = f"sub_{digest}"
    category = item.get("content_category") or "uncategorized"

    # 1. Cek sidecar lokal
    if item.get("source_type") == "local":
        sidecar = find_local_sidecar_subtitle(item.get("file_path"))
        if sidecar:
            return {"subtitle_path": sidecar[0], "subtitle_lang": sidecar[1], "subtitle_source": "local_sidecar"}

    # 2. Cek file subtitle di dalam TorBox Cloud
    if item.get("source_type") == "cloud" and torbox_api_key:
        if torbox_sub_index is not None:
            tb_sub = download_torbox_cloud_subtitle_from_index(item, torbox_api_key, torbox_sub_index)
        else:
            tb_sub = find_torbox_cloud_subtitle_for_item(item, torbox_api_key)
        if tb_sub:
            return {"subtitle_path": tb_sub[0], "subtitle_lang": tb_sub[1], "subtitle_source": "torbox_cloud"}

    # 3. Cek embedded subtitle stream (file lokal maupun TorBox Cloud)
    emb = check_embedded_subtitle(item, torbox_api_key=torbox_api_key)
    if emb:
        return {"subtitle_path": emb[0], "subtitle_lang": emb[1], "subtitle_source": "embedded"}

    # 4. Hentikan pencarian publik untuk English Adult
    if category == "english_adult":
        return None

    # 5. JAV -> SubtitleCat lalu OpenSubtitles
    if category == "jav":
        code_id = item.get("code_id") or ""
        if code_id:
            sc_res = _search_subtitlecat_jav(code_id, dest_base)
            if sc_res:
                return {"subtitle_path": sc_res[0], "subtitle_lang": sc_res[1], "subtitle_source": "subtitlecat"}
            os_res = _search_opensubtitles_rest(code_id, dest_base)
            if os_res:
                return {"subtitle_path": os_res[0], "subtitle_lang": os_res[1], "subtitle_source": "opensubtitles"}
        return None

    # 6. Movie / TV Show / Anime / Donghua
    title_q = item.get("title") or item.get("file_name") or ""
    season = item.get("season_num")
    episode = item.get("episode_num")
    os_res = _search_opensubtitles_rest(title_q, dest_base, season=season, episode=episode)
    if os_res:
        return {"subtitle_path": os_res[0], "subtitle_lang": os_res[1], "subtitle_source": "opensubtitles"}

    return None