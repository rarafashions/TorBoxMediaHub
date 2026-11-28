import json
import os
import platform

import platform_support
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get("TBH_DATA_DIR") or BASE_DIR)

DB_DIR = os.path.join(DATA_DIR, "database")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PLAYLIST_DIR = os.path.join(DATA_DIR, "playlists")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(DB_DIR, "torbox_media_hub.db")

VALID_CATEGORIES = {
    "uncategorized",
    "jav",
    "english_adult",
    "indo",
    "indo_movie",
    "general_movie",
    "tv_show",
    "anime",
    "donghua",
}


VALID_CLASSIFICATION_STATUSES = {
    "pending",
    "classified",
    "needs_review",
    "manually_classified",
}
CLASSIFICATION_MIGRATION_ID = "tbh_v1_2_phase2_classification_schema"
POSTER_MIGRATION_ID = "tbh_v1_2_phase4_poster_reliability"
TITLE_OVERRIDE_MIGRATION_ID = "tbh_v1_2_manual_title_override"
DUAL_SUBTITLE_MIGRATION_ID = "tbh_v1_2_phase6b_dual_subtitles"
TORBOX_HINT_MIGRATION_ID = "tbh_v1_3d4_torbox_hint_metadata"
CLASSIFIER_VERSION_MIGRATION_ID = "tbh_v1_3d5_multicat5_indo"
VALID_POSTER_STATUSES = {"not_checked", "found", "missing", "invalid_url", "invalid_image", "unreachable", "load_error", "manual"}


def detect_runtime_os() -> str:
    """Detects the current runtime OS, including Android/Termux."""
    return platform_support.detect_runtime_os()


def get_default_player_paths() -> Dict[str, str]:
    """Return discovered player paths, falling back to conventional install paths."""
    os_name = detect_runtime_os()
    if os_name == "android":
        return {"vlc": "android://org.videolan.vlc", "mpv": "android://is.xyz.mpv", "custom": "android://auto"}
    defaults = {
        "vlc": platform_support.resolve_executable("vlc") or (r"C:\Program Files\VideoLAN\VLC\vlc.exe" if os_name == "windows" else ("/Applications/VLC.app/Contents/MacOS/VLC" if os_name == "darwin" else "/usr/bin/vlc")),
        "mpv": platform_support.resolve_executable("mpv") or (r"C:\Program Files\mpv\mpv.exe" if os_name == "windows" else ("/opt/homebrew/bin/mpv" if os_name == "darwin" else "/usr/bin/mpv")),
        "custom": "",
    }
    if os_name == "windows":
        defaults.update({"potplayer": r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe", "mpchc": r"C:\Program Files\MPC-HC\mpc-hc64.exe"})
    if os_name == "darwin":
        defaults["iina"] = "/Applications/IINA.app/Contents/MacOS/iina-cli"
    return defaults


_DEFAULT_PATHS = get_default_player_paths()

DEFAULT_SETTINGS: Dict[str, str] = {
    "torbox_api_key": "",
    "tmdb_api_key": "",
    "tpdb_api_key": "",
    "nzbgeek_api_key": "",
    "local_scan_paths": "[]",
    "media_player": "vlc",
    "vlc_path": _DEFAULT_PATHS.get("vlc", ""),
    "mpv_path": _DEFAULT_PATHS.get("mpv", ""),
    "custom_player_path": "",
    "custom_player_args": "",
}


def derive_series_title(raw_title: str) -> str:
    """Normalizes a title into its base series title without episode/season suffixes."""
    t = os.path.splitext(raw_title or "")[0]
    t = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", t)
    t = re.sub(r"[._]+", " ", t)
    t = re.split(
        r"(?i)(?:^|\s)(?:"
        r"S\d{1,2}\s*E\d{1,3}|"
        r"\d{1,2}x\d{1,3}|"
        r"S\d{1,2}|"
        r"Season\s*\d+|"
        r"Episode\s*\d+|"
        r"Ep\.?\s*\d+|"
        r"Part\s*\d+|"
        r"1080p|720p|2160p|480p|4K|WEB-?DL|WEBRip|BluRay|BDRip|DVDRip|HDRip|"
        r"x264|x265|H264|H265|HEVC|10bit|AAC|AC3|DTS|FLAC|MULTI|DUAL|ITA|ENG|SUB"
        r")(?:$|\s)",
        t,
    )[0]
    t = re.sub(r"(?:^|\s)(19[5-9]\d|20[0-3]\d)(?:$|\s).*$", "", t)
    t = re.sub(r"\s*-\s*\d{1,3}\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -_")
    return t or (raw_title or "Untitled Series")


class ClosingSQLiteConnection(sqlite3.Connection):
    """SQLite connection that also closes when used as a context manager.

    sqlite3.Connection.__exit__ commits or rolls back, but does not close the
    connection. Closing here prevents Windows file locks from surviving a
    ``with get_connection()`` block while preserving normal transaction rules.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingSQLiteConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _classifier_version() -> str:
    """Return the active classifier revision without a module-level import cycle."""
    try:
        from classification_engine import CLASSIFIER_VERSION

        return CLASSIFIER_VERSION
    except Exception:
        return "d2-multicat-5"


def _run_vacuum_standalone() -> None:
    """Runs VACUUM on an isolated autocommit connection outside active transactions."""
    if not os.path.exists(DB_PATH):
        return
    vconn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    try:
        vconn.execute("PRAGMA busy_timeout = 30000;")
        vconn.execute("VACUUM;")
    finally:
        vconn.close()


def consolidate_series_groups(conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Menyatukan seluruh episode yang berasal dari serial yang sama berdasarkan:
    1. Ekstraksi ulang SxxExx / NxNN dari file_name & parent_name
    2. tmdb_id / anilist_id yang sama
    3. Prefix judul serial kanonik (misal 'House of the Dragon 3x03 ...' -> 'House of the Dragon')
    4. Memastikan item episodik tidak tertinggal di kategori 'general_movie' atau 'uncategorized'
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    updated_rows = 0
    try:
        rows = conn.execute("SELECT * FROM media_items").fetchall()
        items = [dict(r) for r in rows]

        # Langkah A: Perbaiki item episodik yang salah kategori atau belum punya season/episode/series_title bersih
        for it in items:
            raw_name = f"{it.get('parent_name') or ''} {it.get('file_name') or ''}".strip()
            m_sxe = re.search(r"(?i)(?:^|[\s._\-\[\(])S(\d{1,2})[\s._-]*E(\d{1,3})(?:$|[\s._\-\]\)])", raw_name)
            m_nxn = re.search(r"(?i)(?:^|[\s._\-\[\(])(\d{1,2})x(\d{1,3})(?:$|[\s._\-\]\)])", raw_name)

            new_s = it.get("season_num")
            new_e = it.get("episode_num")
            if m_sxe:
                new_s = new_s if new_s is not None else int(m_sxe.group(1))
                new_e = new_e if new_e is not None else int(m_sxe.group(2))
            elif m_nxn:
                new_s = new_s if new_s is not None else int(m_nxn.group(1))
                new_e = new_e if new_e is not None else int(m_nxn.group(2))

            cur_st = (it.get("series_title") or "").strip()
            clean_st = derive_series_title(cur_st or it.get("title") or it.get("file_name") or "")

            new_cat = it.get("content_category")
            if (new_s is not None or new_e is not None) and new_cat in ("general_movie", "uncategorized"):
                new_cat = "tv_show"

            if (
                new_s != it.get("season_num")
                or new_e != it.get("episode_num")
                or clean_st != cur_st
                or new_cat != it.get("content_category")
            ):
                conn.execute(
                    """
                    UPDATE media_items
                    SET season_num = ?, episode_num = ?, series_title = ?, content_category = ?
                    WHERE id = ?
                    """,
                    (new_s, new_e, clean_st, new_cat, it["id"]),
                )
                it["season_num"] = new_s
                it["episode_num"] = new_e
                it["series_title"] = clean_st
                it["content_category"] = new_cat
                updated_rows += 1

        # Langkah B: Kumpulkan master canonical series berdasarkan tmdb_id, anilist_id, dan judul terpendek yang bersih
        by_ext_id: Dict[str, Dict[str, Any]] = {}
        canonical_titles: List[Dict[str, Any]] = []

        for it in items:
            if it.get("content_category") not in ("tv_show", "anime", "donghua"):
                continue
            ext_key = None
            if it.get("tmdb_id"):
                ext_key = f"tmdb:{it['tmdb_id']}"
            elif it.get("anilist_id"):
                ext_key = f"anilist:{it['anilist_id']}"

            if ext_key:
                cur_master = by_ext_id.get(ext_key)
                if not cur_master or (it.get("local_poster_path") and not cur_master.get("local_poster_path")):
                    by_ext_id[ext_key] = it

            st = (it.get("series_title") or "").strip()
            if st and len(st) >= 3:
                canonical_titles.append(it)

        canonical_titles.sort(
            key=lambda x: (
                0 if (x.get("tmdb_id") or x.get("anilist_id")) else 1,
                0 if x.get("local_poster_path") else 1,
                len((x.get("series_title") or "").strip()),
            )
        )

        # Langkah C: Satukan semua episode yang cocok ke master serialnya
        for it in items:
            if it.get("content_category") not in ("tv_show", "anime", "donghua", "general_movie", "uncategorized"):
                continue

            master = None
            if it.get("tmdb_id") and f"tmdb:{it['tmdb_id']}" in by_ext_id:
                master = by_ext_id[f"tmdb:{it['tmdb_id']}"]
            elif it.get("anilist_id") and f"anilist:{it['anilist_id']}" in by_ext_id:
                master = by_ext_id[f"anilist:{it['anilist_id']}"]
            else:
                st_low = (it.get("series_title") or "").strip().lower()
                for cand in canonical_titles:
                    c_low = (cand.get("series_title") or "").strip().lower()
                    if not c_low:
                        continue
                    if st_low == c_low:
                        master = cand
                        break
                    if st_low.startswith(c_low + " ") or st_low.startswith(c_low + "-"):
                        master = cand
                        break

            if master:
                target_st = master["series_title"]
                target_cat = master["content_category"]
                target_tmdb = it.get("tmdb_id") or master.get("tmdb_id")
                target_ani = it.get("anilist_id") or master.get("anilist_id")
                target_poster = it.get("local_poster_path") or master.get("local_poster_path")
                target_poster_url = it.get("poster_url") or master.get("poster_url")

                if (
                    it.get("series_title") != target_st
                    or it.get("content_category") != target_cat
                    or it.get("tmdb_id") != target_tmdb
                    or it.get("anilist_id") != target_ani
                    or (not it.get("local_poster_path") and target_poster)
                ):
                    conn.execute(
                        """
                        UPDATE media_items
                        SET series_title = ?,
                            content_category = ?,
                            tmdb_id = ?,
                            anilist_id = ?,
                            local_poster_path = COALESCE(local_poster_path, ?),
                            poster_url = COALESCE(poster_url, ?)
                        WHERE id = ?
                        """,
                        (target_st, target_cat, target_tmdb, target_ani, target_poster, target_poster_url, it["id"]),
                    )
                    updated_rows += 1

        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
    return updated_rows


def _classification_migration_already_applied() -> bool:
    """Return True for a new/empty DB or when the Phase 2 migration is recorded."""
    if not os.path.isfile(DB_PATH):
        return True
    probe = sqlite3.connect(DB_PATH, timeout=30)
    try:
        media_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_items'"
        ).fetchone()
        if not media_table:
            return True
        migrations_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not migrations_table:
            return False
        return probe.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
            (CLASSIFICATION_MIGRATION_ID,),
        ).fetchone() is not None
    finally:
        probe.close()


def _poster_migration_already_applied() -> bool:
    """Return True when the Phase 4 poster reliability migration is recorded."""
    if not os.path.isfile(DB_PATH):
        return True
    probe = sqlite3.connect(DB_PATH, timeout=30)
    try:
        media_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_items'"
        ).fetchone()
        if not media_table:
            return True
        migrations_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not migrations_table:
            return False
        return probe.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
            (POSTER_MIGRATION_ID,),
        ).fetchone() is not None
    finally:
        probe.close()


def _title_override_migration_already_applied() -> bool:
    """Return True when the manual-title override migration is recorded."""
    if not os.path.isfile(DB_PATH):
        return True
    probe = sqlite3.connect(DB_PATH, timeout=30)
    try:
        media_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_items'"
        ).fetchone()
        if not media_table:
            return True
        migrations_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not migrations_table:
            return False
        return probe.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
            (TITLE_OVERRIDE_MIGRATION_ID,),
        ).fetchone() is not None
    finally:
        probe.close()


def _torbox_hint_metadata_migration_already_applied() -> bool:
    """Return True when the TorBox import-hint metadata column is recorded."""
    if not os.path.isfile(DB_PATH):
        return True
    probe = sqlite3.connect(DB_PATH, timeout=30)
    try:
        hint_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'torbox_import_hints'"
        ).fetchone()
        if not hint_table:
            return False
        migrations_table = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not migrations_table:
            return False
        return probe.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
            (TORBOX_HINT_MIGRATION_ID,),
        ).fetchone() is not None
    finally:
        probe.close()


def init_db() -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)

    # Back up an existing v1.1 database before the first schema mutation.
    if not (
        _classification_migration_already_applied()
        and _poster_migration_already_applied()
        and _title_override_migration_already_applied()
        and _torbox_hint_metadata_migration_already_applied()
    ):
        backup_result = create_db_backup()
        if not backup_result or not backup_result.get("backup_file"):
            raise RuntimeError("Schema migration aborted: pre-migration database backup failed.")

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
                series_title TEXT,
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
                tmdb_id INTEGER,
                anilist_id INTEGER,
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

            CREATE TABLE IF NOT EXISTS series_season_cache (
                series_key TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                external_id TEXT,
                seasons_json TEXT NOT NULL DEFAULT '[]',
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

            CREATE TABLE IF NOT EXISTS torbox_import_hints (
                torbox_kind TEXT NOT NULL,
                torbox_item_id TEXT NOT NULL,
                content_category TEXT NOT NULL DEFAULT 'uncategorized',
                poster_url TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (torbox_kind, torbox_item_id)
            );

            CREATE TABLE IF NOT EXISTS roxxy_library_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_key TEXT NOT NULL UNIQUE,
                download_job_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL DEFAULT 100,
                file_path TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                stream_json TEXT NOT NULL DEFAULT '{}',
                file_size INTEGER NOT NULL DEFAULT 0,
                validation_json TEXT NOT NULL DEFAULT '{}',
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS media_tombstones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_key TEXT NOT NULL UNIQUE,
                title TEXT,
                file_name TEXT,
                code_id TEXT,
                series_title TEXT,
                season_num INTEGER,
                episode_num INTEGER,
                year INTEGER,
                tmdb_id INTEGER,
                anilist_id INTEGER,
                content_category TEXT,
                torbox_kind TEXT,
                torbox_item_id TEXT,
                unique_key TEXT,
                delete_reason TEXT,
                deleted_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_media_category ON media_items(content_category);
            CREATE INDEX IF NOT EXISTS idx_media_has_meta ON media_items(has_metadata);
            CREATE INDEX IF NOT EXISTS idx_media_has_sub ON media_items(has_subtitle);
            CREATE INDEX IF NOT EXISTS idx_tombstones_deleted_at ON media_tombstones(deleted_at);
            CREATE INDEX IF NOT EXISTS idx_tombstones_category ON media_tombstones(content_category);
            """
        )
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(media_items)").fetchall()]
        if "parent_name" not in cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN parent_name TEXT;")
        if "series_title" not in cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN series_title TEXT;")
        if "tmdb_id" not in cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN tmdb_id INTEGER;")
        if "anilist_id" not in cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN anilist_id INTEGER;")

        # Additive, idempotent migration. Existing content_category values are not rewritten.
        migrate_classification_schema(conn)
        migrate_poster_schema(conn)
        migrate_manual_title_override_schema(conn)
        migrate_dual_subtitle_schema(conn)
        migrate_torbox_import_hint_schema(conn)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_series_title ON media_items(series_title);")

        # Konsolidasi & perbaiki seluruh grouping serial yang sudah ada di DB
        consolidate_series_groups(conn)
        conn.commit()


def migrate_classification_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Apply the additive Phase 2 classification schema exactly once and safely.

    Existing category values and media rows are deliberately left untouched.
    A savepoint keeps the migration atomic and also permits use inside init_db().
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    savepoint = "tbh_classification_schema_migration"
    try:
        conn.execute(f"SAVEPOINT {savepoint}")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_items'"
        ).fetchone()
        if not table:
            raise RuntimeError("Cannot migrate classification schema: media_items table is missing.")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )

        columns = {r["name"] for r in conn.execute("PRAGMA table_info(media_items)").fetchall()}
        additions = [
            ("normalized_title", "TEXT"),
            ("classification_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("classification_method", "TEXT"),
            ("category_confidence", "REAL"),
            ("classification_signals_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("classification_locked", "INTEGER NOT NULL DEFAULT 0"),
            ("classification_updated_at", "TEXT"),
            ("classifier_version", "TEXT"),
        ]
        for name, declaration in additions:
            if name not in columns:
                conn.execute(f"ALTER TABLE media_items ADD COLUMN {name} {declaration}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_classification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                previous_category TEXT,
                new_category TEXT,
                previous_normalized_title TEXT,
                new_normalized_title TEXT,
                previous_status TEXT,
                new_status TEXT,
                previous_locked INTEGER,
                new_locked INTEGER,
                classification_method TEXT,
                category_confidence REAL,
                signals_json TEXT NOT NULL DEFAULT '[]',
                changed_by TEXT NOT NULL DEFAULT 'system',
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_classification_status "
            "ON media_items(classification_status, classification_locked)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_classifier_version "
            "ON media_items(classifier_version, content_category)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_classification_history_media "
            "ON media_classification_history(media_id, id DESC)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (CLASSIFICATION_MIGRATION_ID, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (CLASSIFIER_VERSION_MIGRATION_ID, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_conn:
            conn.commit()
    except Exception:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            pass
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()



def migrate_poster_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Add poster diagnostics fields without rewriting existing metadata or image files."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    savepoint = "tbh_poster_reliability_migration"
    try:
        conn.execute(f"SAVEPOINT {savepoint}")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_items'"
        ).fetchone()
        if not table:
            raise RuntimeError("Cannot migrate poster schema: media_items table is missing.")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )"""
        )
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(media_items)").fetchall()}
        additions = [
            ("poster_status", "TEXT NOT NULL DEFAULT 'not_checked'"),
            ("poster_checked_at", "TEXT"),
            ("poster_error", "TEXT"),
            ("poster_source", "TEXT"),
            ("poster_is_manual", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for name, declaration in additions:
            if name not in columns:
                conn.execute(f"ALTER TABLE media_items ADD COLUMN {name} {declaration}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_poster_status ON media_items(poster_status, poster_is_manual)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (POSTER_MIGRATION_ID, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_conn:
            conn.commit()
    except Exception:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            pass
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def migrate_manual_title_override_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Add a durable lock for user-edited media titles and repair prior manual edits.

    Existing rows are only backfilled when metadata_source='manual', which is the
    marker written by the existing Save Changes workflow. Other rows are untouched.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    savepoint = "tbh_manual_title_override_migration"
    try:
        conn.execute(f"SAVEPOINT {savepoint}")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_items'"
        ).fetchone()
        if not table:
            raise RuntimeError("Cannot migrate manual title override: media_items table is missing.")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )"""
        )
        already_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
            (TITLE_OVERRIDE_MIGRATION_ID,),
        ).fetchone()
        if already_applied:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            if own_conn:
                conn.commit()
            return
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(media_items)").fetchall()}
        if "normalized_title" not in columns:
            conn.execute("ALTER TABLE media_items ADD COLUMN normalized_title TEXT")
        if "title_is_manual" not in columns:
            conn.execute("ALTER TABLE media_items ADD COLUMN title_is_manual INTEGER NOT NULL DEFAULT 0")

        # Repair the exact legacy failure mode: Save Changes wrote title/series_title
        # and metadata_source='manual', but left normalized_title stale.
        conn.execute(
            """UPDATE media_items
               SET normalized_title = CASE
                   WHEN content_category IN ('tv_show', 'anime', 'donghua')
                       THEN COALESCE(NULLIF(TRIM(series_title), ''), title)
                   ELSE title
               END,
               title_is_manual = 1
               WHERE metadata_source = 'manual'
                 AND title IS NOT NULL AND TRIM(title) <> ''"""
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (TITLE_OVERRIDE_MIGRATION_ID, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_conn:
            conn.commit()
    except Exception:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            pass
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def update_media_poster_state(
    media_id: int,
    *,
    poster_url: Optional[str] = None,
    local_poster_path: Optional[str] = None,
    status: str = "not_checked",
    error: Optional[str] = None,
    source: Optional[str] = None,
    is_manual: Optional[bool] = None,
    allow_manual_overwrite: bool = False,
    replace_url: bool = False,
) -> bool:
    """Persist poster-only state. Manual selections are protected from automated updates."""
    status = str(status or "not_checked").strip().lower()
    if status not in VALID_POSTER_STATUSES:
        raise ValueError(f"Invalid poster status: {status}")
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT poster_is_manual FROM media_items WHERE id = ?", (media_id,)
        ).fetchone()
        if not row:
            return False
        currently_manual = bool(row["poster_is_manual"])
        if currently_manual and not allow_manual_overwrite and is_manual is not True:
            # A client-side load error may be recorded without changing the chosen image/source.
            if status == "load_error":
                conn.execute(
                    "UPDATE media_items SET poster_status = ?, poster_error = ?, poster_checked_at = ? WHERE id = ?",
                    (status, error, now, media_id),
                )
            return True
        conn.execute(
            """UPDATE media_items SET
                poster_url = CASE WHEN ? = 1 THEN ? ELSE COALESCE(?, poster_url) END,
                local_poster_path = COALESCE(?, local_poster_path),
                poster_status = ?, poster_checked_at = ?, poster_error = ?, poster_source = ?,
                poster_is_manual = COALESCE(?, poster_is_manual),
                updated_at = ?
               WHERE id = ?""",
            (
                1 if replace_url else 0,
                poster_url,
                poster_url,
                local_poster_path,
                status,
                now,
                error,
                source,
                (1 if is_manual else 0) if is_manual is not None else None,
                now,
                media_id,
            ),
        )
    return True


def clear_manual_poster_selection(media_id: int) -> bool:
    """Release a manual poster lock so future metadata enrichment may choose a poster."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE media_items SET poster_url = NULL, local_poster_path = NULL,
               poster_is_manual = 0, poster_status = 'not_checked', poster_checked_at = ?,
               poster_error = NULL, poster_source = NULL, updated_at = ? WHERE id = ?""",
            (now, now, media_id),
        )
        return cur.rowcount > 0

def _classification_json(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else [], ensure_ascii=False)
    except (TypeError, ValueError):
        return "[]"


def save_classification_result(
    media_id: int,
    result: Dict[str, Any],
    *,
    changed_by: str = "classifier",
    note: Optional[str] = None,
    force: bool = False,
    apply_category: bool = True,
    apply_title: bool = True,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Persist a classifier result; Needs Review never overwrites the current category.

    ``apply_category=False`` records the verdict (status, method, confidence,
    signals, version) while keeping the stored category — used when a row's
    category is authoritative (manifest lock, provider metadata) but its
    classification status was never written. ``apply_title=False`` keeps the
    stored search title, so a provider-canonical title is not replaced by a
    filename guess.

    Pass ``conn`` to reuse a caller-managed transaction (the caller commits).
    Manual classifications are only overwritten when ``force=True``.
    """
    status = str(result.get("classification_status") or "").strip().lower()
    if status not in ("classified", "needs_review"):
        raise ValueError("Classifier status must be 'classified' or 'needs_review'.")

    category = result.get("detected_category")
    if status == "classified" and category not in VALID_CATEGORIES - {"uncategorized"}:
        raise ValueError("A classified result must contain a valid detected_category.")
    if status == "needs_review":
        category = None

    normalized_title = (result.get("normalized_title") or "").strip() or None
    method = (result.get("classification_method") or "classifier").strip()
    confidence = result.get("category_confidence")
    confidence = float(confidence) if confidence is not None else None
    signals_json = _classification_json(result.get("signals", []))
    version = str(result.get("classifier_version") or "").strip() or _classifier_version()
    now = datetime.now().isoformat(timespec="seconds")

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM media_items WHERE id = ?", (media_id,)).fetchone()
        if not row:
            return None
        old = dict(row)
        if int(old.get("classification_locked") or 0) and not force:
            raise PermissionError("This media classification is locked for manual review.")
        if old.get("classification_status") == "manually_classified" and not force:
            raise PermissionError("This media classification is manual; pass force=True to override.")

        new_category = (
            category if (category is not None and apply_category) else old["content_category"]
        )
        new_title = normalized_title if apply_title else old.get("normalized_title")
        new_status = status
        conn.execute(
            """UPDATE media_items SET
                normalized_title = ?,
                content_category = ?,
                is_adult = ?,
                classification_status = ?,
                classification_method = ?,
                category_confidence = ?,
                classification_signals_json = ?,
                classifier_version = ?,
                classification_updated_at = ?,
                updated_at = ?
            WHERE id = ?""",
            (new_title, new_category,
             int(new_category in ("jav", "english_adult", "indo")), new_status, method, confidence,
             signals_json, version, now, now, media_id),
        )
        changed = (
            old.get("content_category") != new_category
            or old.get("normalized_title") != new_title
            or old.get("classification_status") != new_status
            or old.get("classification_method") != method
            or old.get("category_confidence") != confidence
            or old.get("classification_signals_json", "[]") != signals_json
        )
        if changed:
            conn.execute(
                """INSERT INTO media_classification_history (
                    media_id, previous_category, new_category,
                    previous_normalized_title, new_normalized_title,
                    previous_status, new_status, previous_locked, new_locked,
                    classification_method, category_confidence, signals_json,
                    changed_by, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (media_id, old.get("content_category"), new_category,
                 old.get("normalized_title"), new_title,
                 old.get("classification_status"), new_status,
                 int(old.get("classification_locked") or 0),
                 int(old.get("classification_locked") or 0),
                 method, confidence, signals_json, changed_by, note, now),
            )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
    return get_media_item(media_id)


def apply_manual_classification(
    media_id: int,
    *,
    category: str,
    normalized_title: str,
    note: Optional[str] = None,
    lock: bool = True,
) -> Optional[Dict[str, Any]]:
    """Apply a user-confirmed category and optionally lock it against auto-classification."""
    if category not in VALID_CATEGORIES - {"uncategorized"}:
        raise ValueError("Manual classification requires a supported content category.")
    clean_title = (normalized_title or "").strip()
    if not clean_title:
        raise ValueError("A normalized title is required for manual classification.")

    now = datetime.now().isoformat(timespec="seconds")
    signals_json = _classification_json(["manual_user_override"])
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM media_items WHERE id = ?", (media_id,)).fetchone()
        if not row:
            return None
        old = dict(row)
        conn.execute(
            """UPDATE media_items SET
                normalized_title = ?, content_category = ?, is_adult = ?,
                classification_status = 'manually_classified',
                classification_method = 'manual', category_confidence = 1.0,
                classification_signals_json = ?, classification_locked = ?,
                classification_updated_at = ?, updated_at = ?
            WHERE id = ?""",
            (clean_title, category, int(category in ("jav", "english_adult", "indo")),
             signals_json, 1 if lock else 0, now, now, media_id),
        )
        conn.execute(
            """INSERT INTO media_classification_history (
                media_id, previous_category, new_category,
                previous_normalized_title, new_normalized_title,
                previous_status, new_status, previous_locked, new_locked,
                classification_method, category_confidence, signals_json,
                changed_by, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1.0, ?, 'user', ?, ?)""",
            (media_id, old.get("content_category"), category,
             old.get("normalized_title"), clean_title,
             old.get("classification_status"), "manually_classified",
             int(old.get("classification_locked") or 0), 1 if lock else 0,
             signals_json, note, now),
        )
        conn.commit()
    return get_media_item(media_id)


def set_classification_lock(media_id: int, locked: bool) -> Optional[Dict[str, Any]]:
    """Lock or unlock a record; the change is included in classification history."""
    now = datetime.now().isoformat(timespec="seconds")
    new_locked = 1 if locked else 0
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM media_items WHERE id = ?", (media_id,)).fetchone()
        if not row:
            return None
        old = dict(row)
        old_locked = int(old.get("classification_locked") or 0)
        if old_locked != new_locked:
            conn.execute(
                "UPDATE media_items SET classification_locked = ?, updated_at = ? WHERE id = ?",
                (new_locked, now, media_id),
            )
            conn.execute(
                """INSERT INTO media_classification_history (
                    media_id, previous_category, new_category,
                    previous_normalized_title, new_normalized_title,
                    previous_status, new_status, previous_locked, new_locked,
                    classification_method, category_confidence, signals_json,
                    changed_by, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?, ?)""",
                (media_id, old.get("content_category"), old.get("content_category"),
                 old.get("normalized_title"), old.get("normalized_title"),
                 old.get("classification_status"), old.get("classification_status"),
                 old_locked, new_locked, old.get("classification_method"),
                 old.get("category_confidence"), old.get("classification_signals_json") or "[]",
                 "classification lock changed", now),
            )
        conn.commit()
    return get_media_item(media_id)


def get_classification_history(media_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM media_classification_history WHERE media_id = ? ORDER BY id DESC LIMIT ?",
            (media_id, safe_limit),
        ).fetchall()
    result = [dict(r) for r in rows]
    for item in result:
        item["signals"] = _safe_json_loads(item.get("signals_json"), [])
    return result


def get_classification_queue(status: str = "needs_review", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    if status not in VALID_CLASSIFICATION_STATUSES:
        raise ValueError("Unsupported classification status.")
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM media_items
               WHERE classification_status = ? AND classification_locked = 0
               ORDER BY classification_updated_at ASC, id ASC
               LIMIT ? OFFSET ?""",
            (status, safe_limit, safe_offset),
        ).fetchall()
        pl_map = _get_media_playlist_map(conn)
    return [serialize_row(r, pl_map) for r in rows]


def get_pending_classification_candidates(limit: int = 500) -> List[Dict[str, Any]]:
    """Return only pending, uncategorized records; legacy categorized media are excluded."""
    safe_limit = max(1, min(int(limit), 5000))
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM media_items
               WHERE classification_status = 'pending'
                 AND classification_locked = 0
                 AND content_category = 'uncategorized'
               ORDER BY id ASC LIMIT ?""",
            (safe_limit,),
        ).fetchall()
        pl_map = _get_media_playlist_map(conn)
    return [serialize_row(r, pl_map) for r in rows]


# Batch reclassification records a verdict for every candidate. Which categories
# may actually move is decided in app._reclassification_category_change_allowed():
# provisional buckets accept a verdict, adult surfaces can be recovered, the
# episodic family can be refined, and manifest-locked categories never move.


def get_reclassify_candidates(limit: int = 5000) -> List[Dict[str, Any]]:
    """Return locked-free rows that need a verdict recorded.

    Includes rows whose stored verdict predates the active classifier and every
    row still in ``pending``: a pending status means the category was written by
    an import/enrich path that never recorded the classifier verdict, so the
    version stamp alone cannot retire it.
    """
    safe_limit = max(1, min(int(limit), 5000))
    version = _classifier_version()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM media_items
               WHERE classification_locked = 0
                 AND classification_status IN ('pending', 'classified', 'needs_review')
                 AND (
                     COALESCE(classifier_version, '') <> ?
                     OR classification_status = 'pending'
                 )
               ORDER BY id ASC LIMIT ?""",
            (version, safe_limit),
        ).fetchall()
        pl_map = _get_media_playlist_map(conn)
    return [serialize_row(r, pl_map) for r in rows]


def mark_classifier_version(media_id: int, version: Optional[str] = None) -> bool:
    """Stamp the active classifier revision without changing category or status.

    This records only the revision; a row left in ``pending`` still returns from
    ``get_reclassify_candidates()`` because a missing verdict is the signal that
    matters. Callers should prefer ``save_classification_result()`.
    """
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE media_items SET classifier_version = ?, updated_at = ? WHERE id = ?",
            ((version or _classifier_version()), now, int(media_id)),
        )
        return cur.rowcount > 0


def get_cached_series_seasons(series_key: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM series_season_cache WHERE series_key = ?", (series_key,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["seasons"] = _safe_json_loads(d.get("seasons_json"), [])
    return d


def save_cached_series_seasons(
    series_key: str,
    category: str,
    seasons: List[Dict[str, Any]],
    external_id: Optional[str] = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO series_season_cache (series_key, category, external_id, seasons_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(series_key) DO UPDATE SET
                category = excluded.category,
                external_id = COALESCE(excluded.external_id, series_season_cache.external_id),
                seasons_json = excluded.seasons_json,
                updated_at = excluded.updated_at
            """,
            (series_key, category, external_id, json.dumps(seasons), now),
        )
        conn.commit()


def _is_path_from_other_os(path_val: str, current_os: str) -> bool:
    p = (path_val or "").strip()
    if not p:
        return False
    if current_os != "windows" and (len(p) >= 2 and p[1] == ":" or "\\" in p):
        return True
    if current_os == "windows" and (p.startswith("/") or p.startswith("android://")):
        return True
    if current_os != "android" and p.startswith("android://"):
        return True
    return False


def get_settings() -> Dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    current_os = detect_runtime_os()
    os_defaults = get_default_player_paths()

    defaults = dict(DEFAULT_SETTINGS)
    defaults.update({
        "vlc_path": os_defaults.get("vlc", ""),
        "mpv_path": os_defaults.get("mpv", ""),
    })
    result = dict(defaults)
    for r in rows:
        result[r["key"]] = r["value"]

    if os.environ.get("TORBOX_API_KEY") and not result.get("torbox_api_key"):
        result["torbox_api_key"] = os.environ["TORBOX_API_KEY"].strip()
    if os.environ.get("TMDB_API_KEY") and not result.get("tmdb_api_key"):
        result["tmdb_api_key"] = os.environ["TMDB_API_KEY"].strip()
    if os.environ.get("TPDB_API_KEY") and not result.get("tpdb_api_key"):
        result["tpdb_api_key"] = os.environ["TPDB_API_KEY"].strip()
    if os.environ.get("NZBGEEK_API_KEY") and not result.get("nzbgeek_api_key"):
        result["nzbgeek_api_key"] = os.environ["NZBGEEK_API_KEY"].strip()

    if not (result.get("media_player") or "").strip():
        result["media_player"] = "vlc"

    if not (result.get("vlc_path") or "").strip() or _is_path_from_other_os(result["vlc_path"], current_os):
        result["vlc_path"] = defaults["vlc_path"]
    if not (result.get("mpv_path") or "").strip() or _is_path_from_other_os(result["mpv_path"], current_os):
        result["mpv_path"] = defaults["mpv_path"]
    if _is_path_from_other_os(result.get("custom_player_path", ""), current_os):
        result["custom_player_path"] = ""

    return result


SECRET_SETTING_KEYS = ("torbox_api_key", "tmdb_api_key", "tpdb_api_key", "nzbgeek_api_key")

# Manifest URLs carry the addon token in the path. They stay in the database and
# are read by the server; any payload sent to the browser shows a masked form.
MANIFEST_SETTING_KEYS = (
    "roxxy_manifest_url",
    "roxxy_extra_manifest_url",
    "roxxy_manifest_url_jav",
    "roxxy_manifest_url_english_adult",
    "roxxy_manifest_url_onlyfans",
    "roxxy_manifest_url_indo_movie",
    "roxxy_manifest_url_donghua",
)


def _mask_manifest_url(val: str) -> str:
    """Keep scheme/host and the file name, hide the token path segment."""
    s = (val or "").strip()
    if not s:
        return ""
    try:
        parsed = urlsplit(s)
    except ValueError:
        return "••••manifest"
    if not parsed.scheme or not parsed.netloc:
        return "••••manifest"
    name = parsed.path.rstrip("/").rsplit("/", 1)[-1] or "manifest.json"
    return f"{parsed.scheme}://{parsed.netloc}/…/{name}"


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
    for k in MANIFEST_SETTING_KEYS:
        if k in masked:
            masked[k] = _mask_manifest_url(raw.get(k) or "")
    return masked


def save_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    with get_connection() as conn:
        for k, v in payload.items():
            if k not in DEFAULT_SETTINGS:
                continue
            val_str = json.dumps(v) if isinstance(v, (list, dict)) else str(v if v is not None else "")
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
        if os.path.getsize(path) <= 1500:
            return False
        with open(path, "rb") as image_file:
            header = image_file.read(16)
        return (
            header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"\x89PNG\r\n\x1a\n")
            or (len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
        )
    except OSError:
        return False


def serialize_row(row: sqlite3.Row, playlist_map: Optional[Dict[int, List[str]]] = None) -> Dict[str, Any]:
    d = dict(row)
    if not d.get("series_title"):
        d["series_title"] = derive_series_title(d.get("title") or d.get("file_name") or "")
    d["samples"] = _safe_json_loads(d.get("samples_json"), [])
    d["actresses"] = _safe_json_loads(d.get("actresses_json"), [])
    d["genres"] = _safe_json_loads(d.get("genres_json"), [])
    d["has_poster"] = _is_valid_poster_file(d.get("local_poster_path"))
    d["poster_status"] = d.get("poster_status") or "not_checked"
    if d["has_poster"] and d["poster_status"] == "not_checked":
        d["poster_status"] = "manual" if d.get("poster_is_manual") else "found"
    elif not d["has_poster"] and d["poster_status"] in ("found", "manual"):
        d["poster_status"] = "load_error"
        d["poster_error"] = d.get("poster_error") or "Cached poster file is missing or invalid."
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



def save_torbox_import_hint(kind: str, item_id: str, category: str, poster_url: str = "",
                            metadata: Optional[Dict[str, Any]] = None) -> None:
    """Persist Discovery/Roxxy category, poster and metadata hints until import."""
    if not kind or not item_id:
        return
    safe_category = category if category in VALID_CATEGORIES else "uncategorized"
    try:
        serialized = json.dumps(metadata or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = "{}"
    if len(serialized) > 200_000:
        serialized = "{}"
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO torbox_import_hints
                 (torbox_kind, torbox_item_id, content_category, poster_url, metadata_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(torbox_kind, torbox_item_id) DO UPDATE SET
                 content_category=excluded.content_category,
                 poster_url=COALESCE(excluded.poster_url, torbox_import_hints.poster_url),
                 metadata_json=CASE
                   WHEN excluded.metadata_json IS NULL OR excluded.metadata_json='{}'
                   THEN torbox_import_hints.metadata_json
                   ELSE excluded.metadata_json END""",
            (str(kind), str(item_id), safe_category, poster_url or None, serialized),
        )
        conn.commit()


def get_torbox_import_hint(kind: str, item_id: str) -> Dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT content_category, poster_url, metadata_json FROM torbox_import_hints "
            "WHERE torbox_kind=? AND torbox_item_id=?",
            (str(kind), str(item_id)),
        ).fetchone()
    if not row:
        return {}
    hint = dict(row)
    try:
        hint["metadata"] = json.loads(hint.pop("metadata_json", None) or "{}")
    except Exception:
        hint["metadata"] = {}
    return hint


def enqueue_roxxy_library_task(download_job_id: str, file_path: str,
                                metadata: Dict[str, Any], stream: Dict[str, Any]) -> int:
    """Persist a completed Roxxy download for serialized post-download processing."""
    import hashlib
    now = datetime.now().isoformat(timespec="seconds")
    normalized_path = os.path.normcase(os.path.abspath(file_path))
    task_key = hashlib.sha256(normalized_path.encode("utf-8", errors="replace")).hexdigest()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO roxxy_library_tasks
               (task_key,download_job_id,status,file_path,metadata_json,stream_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(task_key) DO UPDATE SET
                 metadata_json=CASE WHEN roxxy_library_tasks.status IN ('failed','validated') THEN excluded.metadata_json ELSE roxxy_library_tasks.metadata_json END,
                 stream_json=CASE WHEN roxxy_library_tasks.status IN ('failed','validated') THEN excluded.stream_json ELSE roxxy_library_tasks.stream_json END,
                 updated_at=excluded.updated_at""",
            (task_key, str(download_job_id), "queued", normalized_path,
             json.dumps(metadata or {}, ensure_ascii=False), json.dumps(stream or {}, ensure_ascii=False), now, now),
        )
        row = conn.execute("SELECT id FROM roxxy_library_tasks WHERE task_key=?", (task_key,)).fetchone()
        return int(row["id"])


def recover_roxxy_library_tasks() -> int:
    """Recover interrupted D1 validation and D3 registration work after restart."""
    now = datetime.now().isoformat(timespec="seconds")
    recovered = 0
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE roxxy_library_tasks SET status='queued',
               error='Recovered after application restart', updated_at=?
               WHERE status='validating'""",
            (now,),
        )
        recovered += int(cur.rowcount or 0)
        cur = conn.execute(
            """UPDATE roxxy_library_tasks SET status='identified',
               error='D3 registration recovered after application restart', updated_at=?
               WHERE status='registering'""",
            (now,),
        )
        recovered += int(cur.rowcount or 0)
        return recovered


def claim_roxxy_registration_task() -> Optional[Dict[str, Any]]:
    """Atomically claim one D2-identified task for idempotent D3 Library registration."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM roxxy_library_tasks
               WHERE status='identified'
               ORDER BY priority ASC, id ASC LIMIT 1"""
        ).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute(
            """UPDATE roxxy_library_tasks SET status='registering',
               attempts=attempts+1, error=NULL, updated_at=? WHERE id=?""",
            (now, row["id"]),
        )
        task = dict(row)
        task["status"] = "registering"
        task["attempts"] = int(task.get("attempts") or 0) + 1
        conn.commit()
    for key in ("metadata_json", "stream_json", "validation_json"):
        try:
            task[key.replace("_json", "")] = json.loads(task.get(key) or "{}")
        except Exception:
            task[key.replace("_json", "")] = {}
    return task


def promote_roxxy_validated_tasks_for_identification() -> int:
    """Queue tasks not yet processed by the current D2 classifier revision.

    Only tasks whose downloaded file still exists are re-queued. Re-identifying a
    record whose file was moved or deleted would turn a healthy entry into a
    failed task, so those rows keep their previous verdict.
    """
    now = datetime.now().isoformat(timespec="seconds")
    classifier_version = _classifier_version()
    promoted = 0
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, status, file_path, validation_json FROM roxxy_library_tasks "
            "WHERE status IN ('validated','identified','needs_review','duplicate')"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["validation_json"] or "{}")
            except Exception:
                payload = {}
            identification = payload.get("d2_identification") or {}
            if identification.get("classifier_version") == classifier_version:
                continue
            path = str(row["file_path"] or "")
            if not path or not os.path.exists(path):
                continue
            conn.execute(
                "UPDATE roxxy_library_tasks SET status='queued', error=NULL, updated_at=? WHERE id=?",
                (now, int(row["id"])),
            )
            promoted += 1
    return promoted


def claim_roxxy_library_task() -> Optional[Dict[str, Any]]:
    """Atomically claim the oldest queued/retryable task."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM roxxy_library_tasks
               WHERE status IN ('queued','retry')
               ORDER BY priority ASC, id ASC LIMIT 1"""
        ).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute(
            """UPDATE roxxy_library_tasks SET status='validating', attempts=attempts+1,
               error=NULL, updated_at=? WHERE id=?""", (now, row["id"])
        )
        task = dict(row)
        task["status"] = "validating"
        task["attempts"] = int(task.get("attempts") or 0) + 1
        conn.commit()
    for key in ("metadata_json", "stream_json", "validation_json"):
        try:
            task[key.replace("_json", "")] = json.loads(task.get(key) or "{}")
        except Exception:
            task[key.replace("_json", "")] = {}
    return task


def update_roxxy_library_task(task_id: int, status: str, file_size: int = 0,
                              validation: Optional[Dict[str, Any]] = None,
                              error: Optional[str] = None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    completed = now if status in ("validated", "completed", "failed", "duplicate", "needs_review") else None
    with get_connection() as conn:
        conn.execute(
            """UPDATE roxxy_library_tasks SET status=?, file_size=?,
               validation_json=?, error=?, updated_at=?, completed_at=COALESCE(?,completed_at)
               WHERE id=?""",
            (status, int(file_size or 0), json.dumps(validation or {}, ensure_ascii=False),
             (str(error)[:500] if error else None), now, completed, int(task_id)),
        )


def list_roxxy_library_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM roxxy_library_tasks ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("metadata_json", "stream_json", "validation_json"):
            try:
                item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
            except Exception:
                item[key.replace("_json", "")] = {}
        item.pop("metadata_json", None)
        item.pop("stream_json", None)
        result.append(item)
    return result


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
                series_title = ex.get("series_title") or derive_series_title(ex["title"])
                original_title = ex["original_title"]
                year = ex["year"]
                release_date = ex["release_date"]
                runtime = ex["runtime"] if int(ex.get("runtime") or 0) > 0 else int(data.get("runtime") or 0)
                studio = ex["studio"]
                director = ex["director"]
                plot = ex["plot"]
                content_category = ex["content_category"]
                season_num = ex["season_num"]
                episode_num = ex["episode_num"]
                tmdb_id = ex.get("tmdb_id")
                anilist_id = ex.get("anilist_id")
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
                # A user-edited title is authoritative even when metadata is force-refreshed.
                # Provider fields (plot, poster, runtime, IDs, etc.) may still be refreshed.
                if force_meta and int(ex.get("title_is_manual") or 0):
                    title = ex["title"]
                    series_title = ex.get("series_title") or derive_series_title(title)
                else:
                    title = data.get("title") or ex["title"]
                    series_title = (
                        data.get("series_title")
                        or ex.get("series_title")
                        or derive_series_title(title)
                    )
                original_title = data.get("original_title") if data.get("original_title") is not None else ex["original_title"]
                year = data.get("year") if data.get("year") is not None else ex["year"]
                release_date = data.get("release_date") if data.get("release_date") is not None else ex["release_date"]
                incoming_rt = int(data.get("runtime") or 0)
                runtime = incoming_rt if incoming_rt > 0 else int(ex.get("runtime") or 0)
                studio = data.get("studio") if data.get("studio") is not None else ex["studio"]
                director = data.get("director") if data.get("director") is not None else ex["director"]
                plot = data.get("plot") if data.get("plot") is not None else ex["plot"]
                cat_cand = data.get("content_category") or ex["content_category"] or "uncategorized"
                content_category = cat_cand if cat_cand in VALID_CATEGORIES else "uncategorized"
                season_num = data.get("season_num") if data.get("season_num") is not None else ex["season_num"]
                episode_num = data.get("episode_num") if data.get("episode_num") is not None else ex["episode_num"]
                tmdb_id = data.get("tmdb_id") if data.get("tmdb_id") is not None else ex.get("tmdb_id")
                anilist_id = data.get("anilist_id") if data.get("anilist_id") is not None else ex.get("anilist_id")
                is_adult = int(
                    data.get("is_adult")
                    if data.get("is_adult") is not None
                    else (1 if content_category in ("jav", "english_adult", "indo") else ex["is_adult"])
                )
                has_metadata = int(data.get("has_metadata", ex["has_metadata"]))
                metadata_source = data.get("metadata_source") or ex["metadata_source"]
                if int(ex.get("poster_is_manual") or 0):
                    poster_url = ex.get("poster_url")
                    local_poster_path = ex.get("local_poster_path")
                else:
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
                    series_title = ?,
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
                    tmdb_id = ?,
                    anilist_id = ?,
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
                    series_title,
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
                    tmdb_id,
                    anilist_id,
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
            consolidate_series_groups(conn)
            if own_conn:
                conn.commit()
            return int(ex["id"])

        cat_insert = data.get("content_category") or "uncategorized"
        if cat_insert not in VALID_CATEGORIES:
            cat_insert = "uncategorized"

        title_ins = data.get("title") or data["file_name"]
        series_title_ins = data.get("series_title") or derive_series_title(title_ins)

        cur = conn.execute(
            """
            INSERT INTO media_items (
                unique_key, source_type, torbox_kind, torbox_item_id, torbox_file_id,
                parent_name, file_name, file_path, stream_url, file_size, code_id, title, series_title, original_title,
                year, release_date, runtime, studio, director, plot, content_category,
                season_num, episode_num, tmdb_id, anilist_id, is_adult, has_metadata, metadata_source,
                poster_url, local_poster_path, samples_json, actresses_json, genres_json,
                rating, rating_star, is_watched, has_subtitle, subtitle_path, subtitle_lang,
                subtitle_source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
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
                title_ins,
                series_title_ins,
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
                data.get("tmdb_id"),
                data.get("anilist_id"),
                int(
                    data.get("is_adult")
                    if data.get("is_adult") is not None
                    else cat_insert in ("jav", "english_adult", "indo")
                ),
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
        new_id = int(cur.lastrowid)
        consolidate_series_groups(conn)
        if own_conn:
            conn.commit()
        return new_id
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
    classification_status: str = "all",
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
            "(title LIKE ? OR series_title LIKE ? OR original_title LIKE ? OR code_id LIKE ? OR file_name LIKE ? OR parent_name LIKE ? OR actresses_json LIKE ? OR studio LIKE ? OR genres_json LIKE ?)"
        )
        params.extend([like, like, like, like, like, like, like, like, like])

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

    if classification_status in VALID_CLASSIFICATION_STATUSES:
        clauses.append("classification_status = ?")
        params.append(classification_status)

    sort_map = {
        "newest": "updated_at DESC, id DESC",
        "title_asc": "COALESCE(series_title, title) COLLATE NOCASE ASC, COALESCE(season_num, 1) ASC, COALESCE(episode_num, 0) ASC, title COLLATE NOCASE ASC",
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
            or (not it.get("has_poster") and not int(it.get("poster_is_manual") or 0))
            or int(it.get("runtime") or 0) <= 0
        ]
    return items


def update_media_manual(media_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat(timespec="seconds")
    cat = payload.get("content_category") or "general_movie"
    if cat not in VALID_CATEGORIES:
        cat = "general_movie"
    is_adult = 1 if cat in ("jav", "english_adult", "indo") else 0
    year_val = int(payload["year"]) if str(payload.get("year") or "").strip().isdigit() else None
    runtime_raw = str(payload.get("runtime") or "").strip()
    runtime_val = int(runtime_raw) if runtime_raw.isdigit() else 0
    title_val = (payload.get("title") or "").strip() or "Untitled"
    series_title_val = (payload.get("series_title") or "").strip() or derive_series_title(title_val)

    # Use the series title for episodic catalogues and the media title for films/adult titles.
    metadata_search_title = (
        series_title_val if cat in ("tv_show", "anime", "donghua") else title_val
    )

    season_raw = str(payload.get("season_num") if payload.get("season_num") is not None else "").strip()
    season_val = int(season_raw) if season_raw.isdigit() else None
    ep_raw = str(payload.get("episode_num") if payload.get("episode_num") is not None else "").strip()
    ep_val = int(ep_raw) if ep_raw.isdigit() else None

    with get_connection() as conn:
        conn.execute(
            """UPDATE media_items SET
                title = ?,
                series_title = ?,
                normalized_title = ?,
                title_is_manual = 1,
                code_id = ?,
                content_category = ?,
                season_num = COALESCE(?, season_num),
                episode_num = COALESCE(?, episode_num),
                is_adult = ?,
                year = ?,
                runtime = ?,
                studio = ?,
                plot = ?,
                has_metadata = 1,
                metadata_source = 'manual',
                updated_at = ?
            WHERE id = ?
            """,
            (
                title_val,
                series_title_val,
                metadata_search_title,
                (payload.get("code_id") or "").strip() or None,
                cat,
                season_val,
                ep_val,
                is_adult,
                year_val,
                runtime_val,
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



def migrate_torbox_import_hint_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Add the metadata JSON column used by Roxxy-to-TorBox import hints."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    savepoint = "tbh_torbox_hint_metadata_migration"
    try:
        conn.execute(f"SAVEPOINT {savepoint}")
        columns = {
            r["name"] for r in conn.execute("PRAGMA table_info(torbox_import_hints)").fetchall()
        }
        if "metadata_json" not in columns:
            conn.execute(
                "ALTER TABLE torbox_import_hints ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
            )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(migration_id,applied_at) VALUES(?,?)",
            (TORBOX_HINT_MIGRATION_ID, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_conn:
            conn.commit()
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    finally:
        if own_conn:
            conn.close()


def migrate_dual_subtitle_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Add non-destructive per-language subtitle storage and migrate legacy slots."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    savepoint = "tbh_phase6b_dual_subtitle_migration"
    try:
        conn.execute(f"SAVEPOINT {savepoint}")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS media_subtitles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                language TEXT NOT NULL CHECK(language IN ('en','id')),
                subtitle_path TEXT NOT NULL,
                subtitle_source TEXT NOT NULL DEFAULT 'unknown',
                is_embedded INTEGER NOT NULL DEFAULT 0,
                stream_index INTEGER,
                verified INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(media_id, language),
                FOREIGN KEY(media_id) REFERENCES media_items(id) ON DELETE CASCADE
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_subtitles_media ON media_subtitles(media_id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS subtitle_preferences (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        conn.execute("INSERT OR IGNORE INTO subtitle_preferences(key,value) VALUES('english_required','1')")
        conn.execute("INSERT OR IGNORE INTO subtitle_preferences(key,value) VALUES('indonesian_enabled','1')")
        conn.execute(
            """INSERT OR IGNORE INTO media_subtitles
               (media_id,language,subtitle_path,subtitle_source,is_embedded,verified,created_at,updated_at)
               SELECT id, CASE WHEN lower(COALESCE(subtitle_lang,'')) IN ('id','ind','indonesian') THEN 'id' ELSE 'en' END,
                      subtitle_path, COALESCE(NULLIF(subtitle_source,''),'legacy'),
                      CASE WHEN subtitle_path LIKE 'embedded://%' THEN 1 ELSE 0 END, 1,
                      COALESCE(created_at,?), COALESCE(updated_at,?)
               FROM media_items WHERE has_subtitle=1 AND subtitle_path IS NOT NULL AND TRIM(subtitle_path)<>''""",
            (datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(migration_id,applied_at) VALUES(?,?)",
            (DUAL_SUBTITLE_MIGRATION_ID, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_conn:
            conn.commit()
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    finally:
        if own_conn:
            conn.close()


def upsert_media_subtitle(media_id: int, language: str, subtitle_path: str, source: str,
                          is_embedded: bool = False, stream_index: Optional[int] = None,
                          verified: bool = True) -> None:
    lang = (language or '').lower()
    if lang not in ('en','id'):
        raise ValueError("Only English ('en') and Indonesian ('id') subtitles are supported.")
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO media_subtitles(media_id,language,subtitle_path,subtitle_source,is_embedded,stream_index,verified,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(media_id,language) DO UPDATE SET subtitle_path=excluded.subtitle_path,
               subtitle_source=excluded.subtitle_source,is_embedded=excluded.is_embedded,
               stream_index=excluded.stream_index,verified=excluded.verified,updated_at=excluded.updated_at""",
            (media_id,lang,subtitle_path,source,int(is_embedded),stream_index,int(verified),now,now),
        )
        rows = conn.execute("SELECT language FROM media_subtitles WHERE media_id=? AND verified=1", (media_id,)).fetchall()
        langs = {r['language'] for r in rows}
        legacy = conn.execute("SELECT language,subtitle_path,subtitle_source FROM media_subtitles WHERE media_id=? AND verified=1 ORDER BY CASE language WHEN 'en' THEN 0 ELSE 1 END LIMIT 1", (media_id,)).fetchone()
        conn.execute(
            "UPDATE media_items SET has_subtitle=?,subtitle_path=?,subtitle_lang=?,subtitle_source=?,updated_at=? WHERE id=?",
            (int(bool(langs)), legacy['subtitle_path'] if legacy else None, legacy['language'] if legacy else None, legacy['subtitle_source'] if legacy else 'none', now, media_id),
        )
        conn.commit()


def get_media_subtitles(media_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM media_subtitles WHERE media_id=? AND verified=1 ORDER BY CASE language WHEN 'en' THEN 0 ELSE 1 END", (media_id,)).fetchall()
        return [dict(r) for r in rows]



def get_subtitle_preferences() -> Dict[str, bool]:
    """Read Phase 6B language preferences; English is always required."""
    with get_connection() as conn:
        rows = conn.execute("SELECT key,value FROM subtitle_preferences").fetchall()
    values = {r["key"]: r["value"] for r in rows}
    return {"english_required": True, "indonesian_enabled": values.get("indonesian_enabled", "1") == "1"}


def save_subtitle_preferences(indonesian_enabled: bool) -> Dict[str, bool]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("INSERT INTO subtitle_preferences(key,value) VALUES('english_required','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        conn.execute("INSERT INTO subtitle_preferences(key,value) VALUES('indonesian_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if indonesian_enabled else "0",))
        conn.commit()
    return get_subtitle_preferences()


def get_subtitle_language_stats(indonesian_enabled: Optional[bool] = None) -> Dict[str, int]:
    prefs = get_subtitle_preferences()
    if indonesian_enabled is None:
        indonesian_enabled = prefs["indonesian_enabled"]
    with get_connection() as conn:
        rows = conn.execute("""SELECT m.id,
            MAX(CASE WHEN s.language='en' AND s.verified=1 THEN 1 ELSE 0 END) AS has_en,
            MAX(CASE WHEN s.language='id' AND s.verified=1 THEN 1 ELSE 0 END) AS has_id
            FROM media_items m LEFT JOIN media_subtitles s ON s.media_id=m.id GROUP BY m.id""").fetchall()
    stats = {"complete":0,"ready":0,"partial":0,"not_ready":0}
    for row in rows:
        en, ident = bool(row["has_en"]), bool(row["has_id"])
        if not en:
            stats["partial" if ident else "not_ready"] += 1
        elif indonesian_enabled and not ident:
            stats["ready"] += 1
        elif indonesian_enabled and ident:
            stats["complete"] += 1
        else:
            stats["ready"] += 1
    return stats

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
                SUM(CASE WHEN content_category = 'indo_movie' THEN 1 ELSE 0 END) AS indo_movie_count,
                SUM(CASE WHEN is_watched = 1 THEN 1 ELSE 0 END) AS watched_count,
                SUM(CASE WHEN classification_status = 'pending' THEN 1 ELSE 0 END) AS classification_pending_count,
                SUM(CASE WHEN classification_status = 'classified' THEN 1 ELSE 0 END) AS classification_classified_count,
                SUM(CASE WHEN classification_status = 'needs_review' THEN 1 ELSE 0 END) AS classification_needs_review_count,
                SUM(CASE WHEN classification_status = 'manually_classified' THEN 1 ELSE 0 END) AS classification_manual_count
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
    safe_name = os.path.basename(file_name or "").strip()
    if not safe_name or not safe_name.endswith(".db"):
        return False
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
        local_rows = conn.execute(
            """
            SELECT id, title, file_name, file_path, code_id, series_title, season_num,
                   episode_num, year, tmdb_id, anilist_id, content_category, torbox_kind,
                   torbox_item_id, unique_key
            FROM media_items WHERE source_type = 'local'
            """
        ).fetchall()
        for r in local_rows:
            fp = r["file_path"]
            if fp and not os.path.exists(fp):
                # A DB row whose file vanished is treated as a removal so the
                # catalog badge can say "WAS IN LIBRARY" instead of forgetting it.
                _insert_media_tombstone(conn, dict(r), reason="missing_local_file")
                conn.execute("DELETE FROM media_items WHERE id = ?", (r["id"],))
                removed_local += 1
        pruned_tombstones = _prune_media_tombstones(conn)
        conn.execute("DELETE FROM playlist_items WHERE media_id NOT IN (SELECT id FROM media_items)")
        consolidated = consolidate_series_groups(conn)
        conn.commit()

    _run_vacuum_standalone()
    return {
        "removed_missing_local_files": removed_local,
        "cleared_missing_subtitles": cleared_subs,
        "consolidated_series_items": consolidated,
        "pruned_tombstones": pruned_tombstones,
    }


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
                series_title = NULL,
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
                tmdb_id = NULL,
                anilist_id = NULL,
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
        conn.execute("DELETE FROM series_season_cache")
        conn.commit()
    cleared_cache = _clear_cache_folders(clear_subtitles=False)
    verify_subtitle_files()
    return {"reset_items": affected, "cleared_cache_files": cleared_cache}


def erase_all_media_data() -> Dict[str, Any]:
    with get_connection() as conn:
        deleted_media = conn.execute("DELETE FROM media_items").rowcount
        deleted_playlists = conn.execute("DELETE FROM playlists").rowcount
        # A full erase is a deliberate reset, not a removal of individual
        # titles, so the "WAS IN LIBRARY" history is cleared as well.
        deleted_tombstones = conn.execute("DELETE FROM media_tombstones").rowcount
        conn.execute("DELETE FROM series_season_cache")
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN "
            "('media_items', 'playlists', 'playlist_items', 'media_tombstones')"
        )
        conn.commit()

    _run_vacuum_standalone()

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
        "deleted_tombstones": deleted_tombstones,
        "cleared_cache_files": cleared_cache,
    }

def delete_media_record_and_cache(media_id: int) -> Optional[Dict[str, Any]]:
    """
    Menghapus record media dari database (beserta relasi playlist_items via ON DELETE CASCADE)
    serta membersihkan file cache poster dan subtitle terkait.
    Mengembalikan dict data item sebelum dihapus agar caller (app.py) bisa menghapus file fisiknya.
    """
    item = get_media_item(media_id)
    if not item:
        return None

    # 1. Hapus cache poster lokal jika ada
    poster_path = item.get("local_poster_path")
    if poster_path and os.path.isfile(poster_path):
        try:
            os.remove(poster_path)
        except OSError:
            pass

    # 2. Hapus cache subtitle lokal (hanya jika berada di dalam folder CACHE_DIR/subtitles)
    sub_path = item.get("subtitle_path") or ""
    sub_cache_dir = os.path.abspath(os.path.join(CACHE_DIR, "subtitles"))
    if sub_path and not sub_path.startswith("embedded://"):
        abs_sub = os.path.abspath(sub_path)
        if abs_sub.startswith(sub_cache_dir) and os.path.isfile(abs_sub):
            try:
                os.remove(abs_sub)
            except OSError:
                pass

    # 3. Hapus dari SQLite (playlist_items otomatis terhapus karena ON DELETE CASCADE)
    with get_connection() as conn:
        _insert_media_tombstone(conn, item, reason="record_delete")
        _prune_media_tombstones(conn)
        conn.execute("DELETE FROM media_items WHERE id = ?", (media_id,))
        conn.commit()

    return item


# --- Media tombstones -------------------------------------------------------
# Deleting a title used to erase every trace of it, so the catalog badge fell
# straight back to "NOT IN LIBRARY". A tombstone keeps just enough identity
# (title, file name, code, series/season/episode) to show "WAS IN LIBRARY"
# without retaining the stream URL or the media row itself.
TOMBSTONE_RETENTION_DAYS = 365
TOMBSTONE_MAX_ROWS = 5000

_TOMBSTONE_VIDEO_SUFFIXES = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".wmv",
    ".mpg", ".mpeg", ".flv",
}


def _tombstone_match_key(value: Any) -> str:
    """Fold a title or file name into a comparison key, mirroring Roxxy."""
    text = str(value or "").strip()
    stem, extension = os.path.splitext(text)
    if extension.casefold() in _TOMBSTONE_VIDEO_SUFFIXES:
        text = stem
    text = re.sub(r"[^0-9a-z]+", " ", text.casefold())
    key = " ".join(text.split())
    if len(key) < 6 or not any(ch.isalpha() for ch in key):
        return ""
    return key


def _tombstone_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _media_tombstone_identity(item: Dict[str, Any]) -> str:
    """Stable key so re-deleting the same title refreshes one row, not many."""
    code = str(item.get("code_id") or "").strip().upper()
    if code:
        return f"code:{code}"
    title_key = _tombstone_match_key(item.get("title"))
    file_key = _tombstone_match_key(item.get("file_name"))
    series_key = _tombstone_match_key(item.get("series_title"))
    season = _tombstone_int(item.get("season_num"))
    episode = _tombstone_int(item.get("episode_num"))
    if season is not None and episode is not None:
        base = series_key or title_key or file_key
        if base:
            return f"episode:{base}|s{season}|e{episode}"
    key = title_key or file_key
    return f"title:{key}" if key else ""


def _insert_media_tombstone(
    conn: sqlite3.Connection, item: Dict[str, Any], reason: str = "deleted"
) -> int:
    if not isinstance(item, dict):
        return 0
    identity = _media_tombstone_identity(item)
    if not identity:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO media_tombstones (
            identity_key, title, file_name, code_id, series_title, season_num,
            episode_num, year, tmdb_id, anilist_id, content_category,
            torbox_kind, torbox_item_id, unique_key, delete_reason, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(identity_key) DO UPDATE SET
            title = excluded.title,
            file_name = excluded.file_name,
            code_id = excluded.code_id,
            series_title = excluded.series_title,
            season_num = excluded.season_num,
            episode_num = excluded.episode_num,
            year = excluded.year,
            tmdb_id = excluded.tmdb_id,
            anilist_id = excluded.anilist_id,
            content_category = excluded.content_category,
            torbox_kind = excluded.torbox_kind,
            torbox_item_id = excluded.torbox_item_id,
            unique_key = excluded.unique_key,
            delete_reason = excluded.delete_reason,
            deleted_at = excluded.deleted_at
        """,
        (
            identity,
            item.get("title"),
            item.get("file_name"),
            str(item.get("code_id") or "").strip().upper() or None,
            item.get("series_title"),
            _tombstone_int(item.get("season_num")),
            _tombstone_int(item.get("episode_num")),
            _tombstone_int(item.get("year")),
            _tombstone_int(item.get("tmdb_id")),
            _tombstone_int(item.get("anilist_id")),
            item.get("content_category"),
            item.get("torbox_kind"),
            str(item.get("torbox_item_id") or "").strip() or None,
            item.get("unique_key"),
            str(reason or "deleted"),
            now,
        ),
    )
    return 1


def _prune_media_tombstones(
    conn: sqlite3.Connection,
    max_rows: int = TOMBSTONE_MAX_ROWS,
    max_age_days: int = TOMBSTONE_RETENTION_DAYS,
) -> int:
    """Bound the history table by age, then by newest rows."""
    cutoff = (datetime.now() - timedelta(days=int(max_age_days))).isoformat(timespec="seconds")
    removed = conn.execute("DELETE FROM media_tombstones WHERE deleted_at < ?", (cutoff,)).rowcount
    total = conn.execute("SELECT COUNT(*) FROM media_tombstones").fetchone()[0]
    overflow = int(total or 0) - max(0, int(max_rows))
    if overflow > 0:
        removed += conn.execute(
            """
            DELETE FROM media_tombstones WHERE id IN (
                SELECT id FROM media_tombstones ORDER BY id ASC LIMIT ?
            )
            """,
            (overflow,),
        ).rowcount
    return removed


def add_media_tombstone(item: Dict[str, Any], reason: str = "deleted") -> int:
    """Remember a removed title so catalog badges can show "WAS IN LIBRARY"."""
    try:
        with get_connection() as conn:
            inserted = _insert_media_tombstone(conn, item, reason)
            _prune_media_tombstones(conn)
            conn.commit()
    except sqlite3.OperationalError:
        # A caller may run against a pre-tombstone snapshot; deletion must not fail.
        return 0
    return inserted


_TOMBSTONE_PUBLIC_FIELDS = (
    "title, file_name, code_id, series_title, season_num, episode_num, year, "
    "tmdb_id, anilist_id, content_category, delete_reason, deleted_at"
)


def get_media_tombstone_marks(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Removed-title marks for the browser catalog matcher (no URLs/secrets)."""
    cap = TOMBSTONE_MAX_ROWS if limit is None else max(1, min(int(limit), TOMBSTONE_MAX_ROWS))
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_TOMBSTONE_PUBLIC_FIELDS}
                FROM media_tombstones
                ORDER BY id DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def query_media_tombstones(
    search: str = "", category: str = "", limit: int = 20
) -> List[Dict[str, Any]]:
    """Title search over removed items, used for the adult detail hint."""
    clauses = ["1=1"]
    params: List[Any] = []
    term = str(search or "").strip()
    if term:
        like = f"%{term}%"
        clauses.append("(title LIKE ? OR file_name LIKE ? OR series_title LIKE ?)")
        params.extend([like, like, like])
    if category and category != "all":
        clauses.append("content_category = ?")
        params.append(category)
    cap = max(1, min(int(limit), 200))
    params.append(cap)
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_TOMBSTONE_PUBLIC_FIELDS}
                FROM media_tombstones
                WHERE {' AND '.join(clauses)}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]