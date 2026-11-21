import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "database")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
PLAYLIST_DIR = os.path.join(BASE_DIR, "playlists")
DB_PATH = os.path.join(DB_DIR, "torbox_media_hub.db")

VALID_CATEGORIES = {
    "uncategorized",
    "jav",
    "english_adult",
    "general_movie",
    "tv_show",
    "anime",
    "donghua",
}

DEFAULT_SETTINGS: Dict[str, str] = {
    "torbox_api_key": "",
    "tmdb_api_key": "",
    "tpdb_api_key": "",
    "local_scan_paths": "[]",
    "vlc_path": r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    "auto_sync_enabled": "0",
    "auto_sync_interval_min": "30",
}


def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(PLAYLIST_DIR, exist_ok=True)

    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_key TEXT UNIQUE NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'cloud',
                torbox_kind TEXT,
                torbox_item_id TEXT,
                torbox_file_id TEXT,
                parent_name TEXT,
                file_name TEXT NOT NULL,
                file_path TEXT,
                stream_url TEXT,
                file_size INTEGER DEFAULT 0,
                code_id TEXT,
                title TEXT NOT NULL,
                original_title TEXT,
                year INTEGER,
                release_date TEXT,
                runtime INTEGER DEFAULT 0,
                studio TEXT,
                director TEXT,
                plot TEXT,
                content_category TEXT NOT NULL DEFAULT 'uncategorized',
                season_num INTEGER,
                episode_num INTEGER,
                is_adult INTEGER DEFAULT 0,
                has_metadata INTEGER DEFAULT 0,
                metadata_source TEXT DEFAULT 'none',
                poster_url TEXT,
                local_poster_path TEXT,
                samples_json TEXT DEFAULT '[]',
                actresses_json TEXT DEFAULT '[]',
                genres_json TEXT DEFAULT '[]',
                rating REAL DEFAULT 0.0,
                rating_star INTEGER DEFAULT 0,
                is_watched INTEGER DEFAULT 0,
                has_subtitle INTEGER DEFAULT 0,
                subtitle_path TEXT,
                subtitle_lang TEXT,
                subtitle_source TEXT DEFAULT 'none',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                media_id INTEGER NOT NULL,
                sort_order INTEGER DEFAULT 0,
                added_at TEXT NOT NULL,
                UNIQUE(playlist_id, media_id),
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_media_category ON media_items(content_category);
            CREATE INDEX IF NOT EXISTS idx_media_has_meta ON media_items(has_metadata);
            CREATE INDEX IF NOT EXISTS idx_media_has_sub ON media_items(has_subtitle);
            """
        )
        # Migrasi aman untuk database lama agar memiliki kolom parent_name
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(media_items)").fetchall()]
        if "parent_name" not in cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN parent_name TEXT;")

        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()


def get_settings() -> Dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = dict(DEFAULT_SETTINGS)
    for r in rows:
        result[r["key"]] = r["value"]
    return result


SECRET_SETTING_KEYS = ("torbox_api_key", "tmdb_api_key", "tpdb_api_key")


def _mask_secret(val: str) -> str:
    s = (val or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return "••••"
    return f"{'•' * 8}{s[-4:]}"


def get_masked_settings() -> Dict[str, Any]:
    raw = get_settings()
    masked: Dict[str, Any] = dict(raw)
    for k in SECRET_SETTING_KEYS:
        secret_val = raw.get(k) or ""
        masked[f"has_{k}"] = bool(secret_val.strip())
        masked[k] = _mask_secret(secret_val)
    return masked


def save_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    with get_connection() as conn:
        for k, v in payload.items():
            if k not in DEFAULT_SETTINGS:
                continue
            val_str = json.dumps(v) if isinstance(v, (list, dict)) else str(v if v is not None else "")
            # Jangan timpa API key asli jika user tidak mengubah input (masih kosong atau berisi karakter mask '•')
            if k in SECRET_SETTING_KEYS:
                cleaned = val_str.strip()
                if not cleaned or "•" in cleaned:
                    continue
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, val_str),
            )
        conn.commit()
    return get_masked_settings()


def _safe_json_loads(raw: Optional[str], fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _is_valid_poster_file(path: Optional[str]) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        return os.path.getsize(path) > 1500
    except OSError:
        return False


def serialize_row(row: sqlite3.Row, playlist_map: Optional[Dict[int, List[str]]] = None) -> Dict[str, Any]:
    d = dict(row)
    d["samples"] = _safe_json_loads(d.get("samples_json"), [])
    d["actresses"] = _safe_json_loads(d.get("actresses_json"), [])
    d["genres"] = _safe_json_loads(d.get("genres_json"), [])
    d["has_poster"] = _is_valid_poster_file(d.get("local_poster_path"))
    pls = (playlist_map or {}).get(d["id"], [])
    d["playlist_names"] = pls
    d["in_playlist"] = len(pls) > 0
    return d


def _get_media_playlist_map(conn: sqlite3.Connection) -> Dict[int, List[str]]:
    rows = conn.execute(
        """
        SELECT pi.media_id, p.name
        FROM playlist_items pi
        JOIN playlists p ON p.id = pi.playlist_id
        ORDER BY p.name ASC
        """
    ).fetchall()
    mp: Dict[int, List[str]] = {}
    for r in rows:
        mp.setdefault(int(r["media_id"]), []).append(r["name"])
    return mp


def upsert_media_item(data: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    unique_key = data["unique_key"]
    force_meta = bool(data.get("_force_meta", False))

    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        existing = conn.execute("SELECT * FROM media_items WHERE unique_key = ?", (unique_key,)).fetchone()
        if existing:
            ex = dict(existing)
            preserve_existing = (not force_meta) and (
                ex.get("has_metadata") == 1 or ex.get("content_category") != "uncategorized"
            )

            if preserve_existing:
                code_id = ex["code_id"]
                title = ex["title"]
                original_title = ex["original_title"]
                year = ex["year"]
                release_date = ex["release_date"]
                runtime = ex["runtime"]
                studio = ex["studio"]
                director = ex["director"]
                plot = ex["plot"]
                content_category = ex["content_category"]
                season_num = ex["season_num"]
                episode_num = ex["episode_num"]
                is_adult = ex["is_adult"]
                has_metadata = ex["has_metadata"]
                metadata_source = ex["metadata_source"]
                poster_url = ex["poster_url"]
                local_poster_path = ex["local_poster_path"]
                samples_json = ex["samples_json"]
                actresses_json = ex["actresses_json"]
                genres_json = ex["genres_json"]
                rating = ex["rating"]
            else:
                code_id = data.get("code_id") if data.get("code_id") is not None else ex["code_id"]
                title = data.get("title") or ex["title"]
                original_title = data.get("original_title") if data.get("original_title") is not None else ex["original_title"]
                year = data.get("year") if data.get("year") is not None else ex["year"]
                release_date = data.get("release_date") if data.get("release_date") is not None else ex["release_date"]
                runtime = data.get("runtime") if data.get("runtime") is not None else ex["runtime"]
                studio = data.get("studio") if data.get("studio") is not None else ex["studio"]
                director = data.get("director") if data.get("director") is not None else ex["director"]
                plot = data.get("plot") if data.get("plot") is not None else ex["plot"]
                cat_cand = data.get("content_category") or ex["content_category"] or "uncategorized"
                content_category = cat_cand if cat_cand in VALID_CATEGORIES else "uncategorized"
                season_num = data.get("season_num") if data.get("season_num") is not None else ex["season_num"]
                episode_num = data.get("episode_num") if data.get("episode_num") is not None else ex["episode_num"]
                is_adult = int(data.get("is_adult", ex["is_adult"]))
                has_metadata = int(data.get("has_metadata", ex["has_metadata"]))
                metadata_source = data.get("metadata_source") or ex["metadata_source"]
                poster_url = data.get("poster_url") if data.get("poster_url") is not None else ex["poster_url"]
                local_poster_path = data.get("local_poster_path") if data.get("local_poster_path") is not None else ex["local_poster_path"]
                samples_json = json.dumps(data.get("samples", [])) if "samples" in data else ex["samples_json"]
                actresses_json = json.dumps(data.get("actresses", [])) if "actresses" in data else ex["actresses_json"]
                genres_json = json.dumps(data.get("genres", [])) if "genres" in data else ex["genres_json"]
                rating = float(data.get("rating") if data.get("rating") is not None else ex["rating"])

            has_sub = int(data.get("has_subtitle", 0)) or int(ex.get("has_subtitle", 0))
            sub_path = data.get("subtitle_path") or ex.get("subtitle_path")
            sub_lang = data.get("subtitle_lang") or ex.get("subtitle_lang")
            sub_src = data.get("subtitle_source") if data.get("subtitle_path") else ex.get("subtitle_source", "none")

            conn.execute(
                """
                UPDATE media_items SET
                    source_type = ?,
                    torbox_kind = COALESCE(?, torbox_kind),
                    torbox_item_id = COALESCE(?, torbox_item_id),
                    torbox_file_id = COALESCE(?, torbox_file_id),
                    parent_name = COALESCE(?, parent_name),
                    file_name = ?,
                    file_path = COALESCE(?, file_path),
                    stream_url = COALESCE(?, stream_url),
                    file_size = CASE WHEN ? > 0 THEN ? ELSE file_size END,
                    code_id = ?,
                    title = ?,
                    original_title = ?,
                    year = ?,
                    release_date = ?,
                    runtime = ?,
                    studio = ?,
                    director = ?,
                    plot = ?,
                    content_category = ?,
                    season_num = ?,
                    episode_num = ?,
                    is_adult = ?,
                    has_metadata = ?,
                    metadata_source = ?,
                    poster_url = ?,
                    local_poster_path = ?,
                    samples_json = ?,
                    actresses_json = ?,
                    genres_json = ?,
                    rating = ?,
                    has_subtitle = ?,
                    subtitle_path = ?,
                    subtitle_lang = ?,
                    subtitle_source = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    data.get("source_type", ex["source_type"]),
                    data.get("torbox_kind"),
                    data.get("torbox_item_id"),
                    data.get("torbox_file_id"),
                    data.get("parent_name"),
                    data.get("file_name", ex["file_name"]),
                    data.get("file_path"),
                    data.get("stream_url"),
                    int(data.get("file_size") or 0),
                    int(data.get("file_size") or 0),
                    code_id,
                    title,
                    original_title,
                    year,
                    release_date,
                    int(runtime or 0),
                    studio,
                    director,
                    plot,
                    content_category,
                    season_num,
                    episode_num,
                    is_adult,
                    has_metadata,
                    metadata_source,
                    poster_url,
                    local_poster_path,
                    samples_json,
                    actresses_json,
                    genres_json,
                    float(rating or 0.0),
                    has_sub,
                    sub_path,
                    sub_lang,
                    sub_src,
                    now,
                    ex["id"],
                ),
            )
            if own_conn:
                conn.commit()
            return int(ex["id"])

        cat_insert = data.get("content_category") or "uncategorized"
        if cat_insert not in VALID_CATEGORIES:
            cat_insert = "uncategorized"

        cur = conn.execute(
            """
            INSERT INTO media_items (
                unique_key, source_type, torbox_kind, torbox_item_id, torbox_file_id,
                parent_name, file_name, file_path, stream_url, file_size, code_id, title, original_title,
                year, release_date, runtime, studio, director, plot, content_category,
                season_num, episode_num, is_adult, has_metadata, metadata_source,
                poster_url, local_poster_path, samples_json, actresses_json, genres_json,
                rating, rating_star, is_watched, has_subtitle, subtitle_path, subtitle_lang,
                subtitle_source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                unique_key,
                data.get("source_type", "cloud"),
                data.get("torbox_kind"),
                data.get("torbox_item_id"),
                data.get("torbox_file_id"),
                data.get("parent_name"),
                data["file_name"],
                data.get("file_path"),
                data.get("stream_url"),
                int(data.get("file_size") or 0),
                data.get("code_id"),
                data.get("title") or data["file_name"],
                data.get("original_title"),
                data.get("year"),
                data.get("release_date"),
                int(data.get("runtime") or 0),
                data.get("studio"),
                data.get("director"),
                data.get("plot"),
                cat_insert,
                data.get("season_num"),
                data.get("episode_num"),
                int(data.get("is_adult", 0)),
                int(data.get("has_metadata", 0)),
                data.get("metadata_source", "none"),
                data.get("poster_url"),
                data.get("local_poster_path"),
                json.dumps(data.get("samples", [])),
                json.dumps(data.get("actresses", [])),
                json.dumps(data.get("genres", [])),
                float(data.get("rating") or 0.0),
                int(data.get("has_subtitle", 0)),
                data.get("subtitle_path"),
                data.get("subtitle_lang"),
                data.get("subtitle_source", "none"),
                now,
                now,
            ),
        )
        if own_conn:
            conn.commit()
        return int(cur.lastrowid)
    finally:
        if own_conn:
            conn.close()


def get_media_item(media_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM media_items WHERE id = ?", (media_id,)).fetchone()
        if not row:
            return None
        pl_map = _get_media_playlist_map(conn)
    return serialize_row(row, pl_map)


def verify_subtitle_files() -> int:
    cleared = 0
    with get_connection() as conn:
        rows = conn.execute("SELECT id, subtitle_path FROM media_items WHERE has_subtitle = 1").fetchall()
        for r in rows:
            sp = r["subtitle_path"] or ""
            if sp.startswith("embedded://"):
                continue
            if not sp or not os.path.isfile(sp) or os.path.getsize(sp) < 20:
                conn.execute(
                    """
                    UPDATE media_items SET
                        has_subtitle = 0,
                        subtitle_path = NULL,
                        subtitle_lang = NULL,
                        subtitle_source = 'none'
                    WHERE id = ?
                    """,
                    (r["id"],),
                )
                cleared += 1
        conn.commit()
    return cleared


def query_media_items(
    search: str = "",
    source: str = "all",
    meta_status: str = "all",
    sub_status: str = "all",
    category: str = "all",
    watched_status: str = "all",
    genre: str = "",
    sort_by: str = "newest",
    playlist_scope: str = "all",
    only_unassigned: bool = False,
    needs_enrichment: bool = False,
    missing_subtitle_only: bool = False,
) -> List[Dict[str, Any]]:
    clauses = ["1=1"]
    params: List[Any] = []

    effective_scope = "unassigned" if only_unassigned else playlist_scope
    if effective_scope == "unassigned":
        clauses.append("id NOT IN (SELECT media_id FROM playlist_items)")
    elif effective_scope == "assigned":
        clauses.append("id IN (SELECT media_id FROM playlist_items)")

    if missing_subtitle_only:
        clauses.append("(has_subtitle = 0 OR subtitle_path IS NULL OR subtitle_path = '')")

    if search:
        like = f"%{search.strip()}%"
        clauses.append(
            "(title LIKE ? OR original_title LIKE ? OR code_id LIKE ? OR file_name LIKE ? OR parent_name LIKE ? OR actresses_json LIKE ? OR studio LIKE ? OR genres_json LIKE ?)"
        )
        params.extend([like, like, like, like, like, like, like, like])

    if genre and genre.strip():
        clauses.append("genres_json LIKE ?")
        params.append(f"%{genre.strip()}%")

    if source in ("cloud", "local"):
        clauses.append("source_type = ?")
        params.append(source)

    if meta_status == "has":
        clauses.append("has_metadata = 1")
    elif meta_status == "missing":
        clauses.append("has_metadata = 0")

    if sub_status == "has":
        clauses.append("has_subtitle = 1")
    elif sub_status == "missing":
        clauses.append("has_subtitle = 0")

    if category in VALID_CATEGORIES:
        clauses.append("content_category = ?")
        params.append(category)

    if watched_status == "watched":
        clauses.append("is_watched = 1")
    elif watched_status == "unwatched":
        clauses.append("is_watched = 0")

    sort_map = {
        "newest": "updated_at DESC, id DESC",
        "title_asc": "title COLLATE NOCASE ASC, season_num ASC, episode_num ASC",
        "year_desc": "COALESCE(year, 0) DESC, title COLLATE NOCASE ASC",
        "rating_desc": "COALESCE(rating, 0) DESC, title COLLATE NOCASE ASC",
        "star_desc": "COALESCE(rating_star, 0) DESC, COALESCE(rating, 0) DESC",
        "size_desc": "COALESCE(file_size, 0) DESC",
        "runtime_desc": "COALESCE(runtime, 0) DESC",
    }
    order_clause = sort_map.get(sort_by, "updated_at DESC, id DESC")

    sql = f"""
        SELECT * FROM media_items
        WHERE {' AND '.join(clauses)}
        ORDER BY {order_clause}
    """
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        pl_map = _get_media_playlist_map(conn)

    items = [serialize_row(r, pl_map) for r in rows]
    if needs_enrichment:
        items = [
            it
            for it in items
            if it.get("has_metadata") != 1
            or it.get("content_category") == "uncategorized"
            or not it.get("has_poster")
        ]
    return items


def update_media_manual(media_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat(timespec="seconds")
    cat = payload.get("content_category") or "general_movie"
    if cat not in VALID_CATEGORIES:
        cat = "general_movie"
    is_adult = 1 if cat in ("jav", "english_adult") else 0
    year_val = int(payload["year"]) if str(payload.get("year") or "").strip().isdigit() else None

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE media_items SET
                title = ?,
                code_id = ?,
                content_category = ?,
                is_adult = ?,
                year = ?,
                studio = ?,
                plot = ?,
                has_metadata = 1,
                metadata_source = 'manual',
                updated_at = ?
            WHERE id = ?
            """,
            (
                (payload.get("title") or "").strip() or "Untitled",
                (payload.get("code_id") or "").strip() or None,
                cat,
                is_adult,
                year_val,
                (payload.get("studio") or "").strip() or None,
                (payload.get("plot") or "").strip() or None,
                now,
                media_id,
            ),
        )
        conn.commit()
    return get_media_item(media_id)


def update_media_watched(media_id: int, is_watched: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE media_items SET is_watched = ?, updated_at = ? WHERE id = ?",
            (1 if int(is_watched) else 0, now, media_id),
        )
        conn.commit()


def update_media_rating(media_id: int, rating_star: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    star = max(0, min(5, int(rating_star)))
    with get_connection() as conn:
        conn.execute(
            "UPDATE media_items SET rating_star = ?, updated_at = ? WHERE id = ?",
            (star, now, media_id),
        )
        conn.commit()


def update_media_subtitle(media_id: int, sub_path: str, sub_lang: str, sub_source: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE media_items SET
                has_subtitle = 1,
                subtitle_path = ?,
                subtitle_lang = ?,
                subtitle_source = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (sub_path, sub_lang, sub_source, now, media_id),
        )
        conn.commit()


def get_telemetry_stats() -> Dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN id NOT IN (SELECT media_id FROM playlist_items) THEN 1 ELSE 0 END) AS available_for_playlist_count,
                SUM(CASE WHEN id IN (SELECT media_id FROM playlist_items) THEN 1 ELSE 0 END) AS assigned_to_playlist_count,
                SUM(CASE WHEN source_type = 'cloud' THEN 1 ELSE 0 END) AS cloud_count,
                SUM(CASE WHEN source_type = 'local' THEN 1 ELSE 0 END) AS local_count,
                SUM(CASE WHEN has_metadata = 1 THEN 1 ELSE 0 END) AS has_metadata_count,
                SUM(CASE WHEN has_metadata = 0 THEN 1 ELSE 0 END) AS missing_metadata_count,
                SUM(CASE WHEN has_subtitle = 1 THEN 1 ELSE 0 END) AS has_subtitle_count,
                SUM(CASE WHEN has_subtitle = 0 THEN 1 ELSE 0 END) AS missing_subtitle_count,
                SUM(CASE WHEN content_category = 'uncategorized' THEN 1 ELSE 0 END) AS uncategorized_count,
                SUM(CASE WHEN content_category = 'jav' THEN 1 ELSE 0 END) AS jav_count,
                SUM(CASE WHEN content_category = 'english_adult' THEN 1 ELSE 0 END) AS english_adult_count,
                SUM(CASE WHEN content_category = 'general_movie' THEN 1 ELSE 0 END) AS general_count,
                SUM(CASE WHEN content_category = 'tv_show' THEN 1 ELSE 0 END) AS series_count,
                SUM(CASE WHEN content_category = 'anime' THEN 1 ELSE 0 END) AS anime_count,
                SUM(CASE WHEN content_category = 'donghua' THEN 1 ELSE 0 END) AS donghua_count,
                SUM(CASE WHEN is_watched = 1 THEN 1 ELSE 0 END) AS watched_count
            FROM media_items
            """
        ).fetchone()
        pl_count = conn.execute("SELECT COUNT(*) AS cnt FROM playlists").fetchone()["cnt"]

    db_size_kb = round(os.path.getsize(DB_PATH) / 1024, 1) if os.path.exists(DB_PATH) else 0
    d = {k: (row[k] or 0) for k in row.keys()}
    d["playlists_count"] = pl_count or 0
    d["db_size_kb"] = db_size_kb
    return d


def create_playlist_record(name: str, file_name: str, file_path: str, media_ids: List[int]) -> Dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO playlists (name, file_name, file_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, file_name, file_path, now, now),
        )
        pl_id = int(cur.lastrowid)
        for idx, mid in enumerate(media_ids):
            conn.execute(
                "INSERT OR IGNORE INTO playlist_items (playlist_id, media_id, sort_order, added_at) VALUES (?, ?, ?, ?)",
                (pl_id, int(mid), idx, now),
            )
        conn.commit()
    return get_playlist_by_id(pl_id)


def add_items_to_playlist(playlist_id: int, media_ids: List[int]) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        pl = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if not pl:
            return None
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS mx FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()["mx"]
        for offset, mid in enumerate(media_ids, start=1):
            conn.execute(
                "INSERT OR IGNORE INTO playlist_items (playlist_id, media_id, sort_order, added_at) VALUES (?, ?, ?, ?)",
                (playlist_id, int(mid), max_order + offset, now),
            )
        conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
        conn.commit()
    return get_playlist_by_id(playlist_id)


def remove_item_from_playlist(playlist_id: int, media_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM playlist_items WHERE playlist_id = ? AND media_id = ?",
            (playlist_id, media_id),
        )
        conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
        conn.commit()


def delete_playlist_record(playlist_id: int) -> None:
    with get_connection() as conn:
        row = conn.execute("SELECT file_path FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if row and row["file_path"] and os.path.exists(row["file_path"]):
            try:
                os.remove(row["file_path"])
            except Exception:
                pass
        conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        conn.commit()


def get_playlist_by_id(playlist_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        pl = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if not pl:
            return None
        items = conn.execute(
            """
            SELECT m.* FROM media_items m
            JOIN playlist_items pi ON pi.media_id = m.id
            WHERE pi.playlist_id = ?
            ORDER BY pi.sort_order ASC, pi.id ASC
            """,
            (playlist_id,),
        ).fetchall()
        pl_map = _get_media_playlist_map(conn)
    d = dict(pl)
    d["items"] = [serialize_row(r, pl_map) for r in items]
    d["item_count"] = len(d["items"])
    return d


def get_all_playlists() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        pls = conn.execute("SELECT id FROM playlists ORDER BY updated_at DESC, id DESC").fetchall()
    return [p for p in (get_playlist_by_id(r["id"]) for r in pls) if p]


def create_db_backup() -> Dict[str, Any]:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"torbox_hub_backup_{ts}.db"
    dest = os.path.join(BACKUP_DIR, fname)
    with get_connection() as conn:
        bconn = sqlite3.connect(dest)
        conn.backup(bconn)
        bconn.close()
    return {
        "backup_file": fname,
        "size_kb": round(os.path.getsize(dest) / 1024, 1),
    }


def list_db_backups() -> List[Dict[str, Any]]:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    result = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if f.endswith(".db"):
            fp = os.path.join(BACKUP_DIR, f)
            st = os.stat(fp)
            result.append(
                {
                    "file_name": f,
                    "created_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "size_kb": round(st.st_size / 1024, 1),
                }
            )
    return result


def restore_db_backup(file_name: str) -> bool:
    safe_name = os.path.basename(file_name)
    src = os.path.join(BACKUP_DIR, safe_name)
    if not os.path.isfile(src):
        return False
    src_conn = None
    try:
        src_conn = sqlite3.connect(src, timeout=30)
        integrity = src_conn.execute("PRAGMA integrity_check;").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            return False
        if os.path.exists(DB_PATH):
            try:
                create_db_backup()
            except Exception:
                pass
        with get_connection() as dst_conn:
            src_conn.backup(dst_conn)
        return True
    except Exception:
        return False
    finally:
        if src_conn is not None:
            try:
                src_conn.close()
            except Exception:
                pass


def run_db_maintenance() -> Dict[str, Any]:
    removed_local = 0
    cleared_subs = verify_subtitle_files()
    with get_connection() as conn:
        local_rows = conn.execute("SELECT id, file_path FROM media_items WHERE source_type = 'local'").fetchall()
        for r in local_rows:
            fp = r["file_path"]
            if fp and not os.path.exists(fp):
                conn.execute("DELETE FROM media_items WHERE id = ?", (r["id"],))
                removed_local += 1
        conn.execute("DELETE FROM playlist_items WHERE media_id NOT IN (SELECT id FROM media_items)")
        conn.commit()
        conn.isolation_level = None
        conn.execute("VACUUM")
    return {"removed_missing_local_files": removed_local, "cleared_missing_subtitles": cleared_subs}


def _clear_cache_folders(clear_subtitles: bool = False) -> int:
    removed_files = 0
    subfolders = ["posters", "samples"] + (["subtitles"] if clear_subtitles else [])
    for sub in subfolders:
        target_dir = os.path.join(CACHE_DIR, sub)
        if not os.path.isdir(target_dir):
            continue
        for fn in os.listdir(target_dir):
            if fn == ".gitkeep":
                continue
            fp = os.path.join(target_dir, fn)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                    removed_files += 1
                except OSError:
                    pass
    return removed_files


def reset_all_metadata() -> Dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE media_items SET
                title = file_name,
                code_id = NULL,
                original_title = NULL,
                year = NULL,
                release_date = NULL,
                runtime = 0,
                studio = NULL,
                director = NULL,
                plot = NULL,
                content_category = 'uncategorized',
                season_num = NULL,
                episode_num = NULL,
                is_adult = 0,
                poster_url = NULL,
                local_poster_path = NULL,
                samples_json = '[]',
                actresses_json = '[]',
                genres_json = '[]',
                rating = 0.0,
                has_metadata = 0,
                metadata_source = 'none',
                updated_at = ?
            """,
            (now,),
        )
        affected = cur.rowcount
        conn.commit()
    cleared_cache = _clear_cache_folders(clear_subtitles=False)
    verify_subtitle_files()
    return {"reset_items": affected, "cleared_cache_files": cleared_cache}


def erase_all_media_data() -> Dict[str, Any]:
    with get_connection() as conn:
        deleted_media = conn.execute("DELETE FROM media_items").rowcount
        deleted_playlists = conn.execute("DELETE FROM playlists").rowcount
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('media_items', 'playlists', 'playlist_items')")
        conn.commit()
        conn.isolation_level = None
        conn.execute("VACUUM")

    cleared_cache = _clear_cache_folders(clear_subtitles=True)
    if os.path.isdir(PLAYLIST_DIR):
        for fn in os.listdir(PLAYLIST_DIR):
            if fn.endswith(".m3u"):
                try:
                    os.remove(os.path.join(PLAYLIST_DIR, fn))
                except OSError:
                    pass

    return {
        "deleted_media": deleted_media,
        "deleted_playlists": deleted_playlists,
        "cleared_cache_files": cleared_cache,
    }