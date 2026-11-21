import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request, send_file

import database_manager as db
import metadata_engine as meta_eng
import subtitle_engine as sub_eng

APP_VERSION = "v1.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".m2ts")
TORBOX_API_BASE = "https://api.torbox.app/v1/api"


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


def _is_video_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS


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
    """Fetches a temporary TorBox stream URL when runtime is missing so ffprobe can read duration."""
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

    settings = db.get_settings()
    torbox_key = (settings.get("torbox_api_key") or "").strip()

    lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
    total_items = len(items)
    skipped_count = 0

    for idx, it in enumerate(items, start=1):
        dur = int((it.get("runtime") or 0) * 60) if it.get("runtime") else -1
        disp_title = it.get("title") or it.get("file_name") or "Media"
        if it.get("code_id"):
            disp_title = f"[{it['code_id']}] {disp_title}"
        elif it.get("season_num") is not None and it.get("episode_num") is not None:
            disp_title = f"{disp_title} - S{int(it['season_num']):02d}E{int(it['episode_num']):02d}"

        if progress_cb:
            progress_cb(idx, total_items, f"Preparing stream ({idx}/{total_items}): {disp_title[:55]}...")

        target_uri = ""
        if it.get("source_type") == "local":
            target_uri = it.get("file_path") or ""
        else:
            if torbox_key and it.get("torbox_item_id"):
                fid_val = str(it.get("torbox_file_id")) if it.get("torbox_file_id") not in (None, "") else "0"
                fresh_url = _request_torbox_stream_url(
                    torbox_key,
                    it.get("torbox_kind") or "torrent",
                    str(it["torbox_item_id"]),
                    fid_val,
                )
                if fresh_url:
                    target_uri = fresh_url
                    db.upsert_media_item({"unique_key": it["unique_key"], "file_name": it["file_name"], "stream_url": fresh_url})
                # Short delay to avoid TorBox API HTTP 429 rate limits on large playlists
                if idx < total_items:
                    time.sleep(0.15)
            if not target_uri:
                target_uri = it.get("stream_url") or ""

        if not target_uri:
            skipped_count += 1
            TASK_MGR.update(log_line=f"Warning: Stream URL unavailable, skipped '{disp_title}'")
            continue

        lines.append(f"#EXTINF:{dur},{disp_title}")
        sp = it.get("subtitle_path") or ""
        if sp and not sp.startswith("embedded://") and os.path.isfile(sp):
            lines.append(f"#EXTVLCOPT:sub-file={sp}")
        lines.append(target_uri)

    if skipped_count > 0:
        TASK_MGR.update(log_line=f"Playlist '{name}': {skipped_count} item(s) skipped due to unavailable stream URL.")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def _resolve_player_binary(settings: Dict[str, str]) -> Tuple[str, str, Optional[str]]:
    """Returns (player_type, display_name, executable_path)."""
    player_type = (settings.get("media_player") or "vlc").strip().lower()
    if player_type not in ("vlc", "mpv"):
        player_type = "vlc"

    defaults = db.get_default_player_paths()
    raw_path = (
        settings.get("mpv_path") if player_type == "mpv" else settings.get("vlc_path")
    ) or defaults[player_type]
    raw_path = raw_path.strip()

    display_name = "MPV" if player_type == "mpv" else "VLC"

    if os.path.isfile(raw_path):
        return player_type, display_name, raw_path
    which_bin = shutil.which(raw_path) or shutil.which(player_type)
    if which_bin:
        return player_type, display_name, which_bin
    return player_type, display_name, None


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
            "stats": db.get_telemetry_stats(),
            "items": items,
            "playlists": db.get_all_playlists(),
            "task": TASK_MGR.status(),
        }
    )


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


@app.route("/api/media/<int:media_id>/play", methods=["POST"])
def api_media_play(media_id: int):
    item = db.get_media_item(media_id)
    if not item:
        return jsonify({"ok": False, "error": "Media not found."}), 404

    settings = db.get_settings()
    player_type, player_label, bin_path = _resolve_player_binary(settings)
    if not bin_path:
        cfg_path = settings.get("mpv_path") if player_type == "mpv" else settings.get("vlc_path")
        return jsonify({"ok": False, "error": f"{player_label} not found at: {cfg_path}"}), 400

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
        return jsonify({"ok": False, "error": "Media stream URL/path is unavailable."}), 400

    cmd = [bin_path, target]
    sp = item.get("subtitle_path") or ""
    if sp and not sp.startswith("embedded://") and os.path.isfile(sp):
        if player_type == "mpv":
            mpv_sub = sp.replace("\\", "/")
            cmd.extend([f"--sub-file={mpv_sub}", "--sid=1", "--sub-visibility=yes"])
        else:
            cmd.append(f"--sub-file={sp}")
    elif player_type == "mpv":
        cmd.append("--sub-auto=fuzzy")

    try:
        subprocess.Popen(cmd, close_fds=True)
        return jsonify({"ok": True, "message": f"Playing {item['title']} in {player_label}."})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to launch {player_label}: {exc}"}), 500


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


@app.route("/api/playlists/<int:playlist_id>/play", methods=["POST"])
def api_playlist_play(playlist_id: int):
    pl = db.get_playlist_by_id(playlist_id)
    if not pl:
        return jsonify({"ok": False, "error": "Playlist not found."}), 404
    _generate_m3u_file(pl["name"], pl["items"], existing_file_path=pl["file_path"])
    settings = db.get_settings()
    player_type, player_label, bin_path = _resolve_player_binary(settings)
    if not bin_path:
        cfg_path = settings.get("mpv_path") if player_type == "mpv" else settings.get("vlc_path")
        return jsonify({"ok": False, "error": f"{player_label} not found at: {cfg_path}"}), 400

    cmd = [bin_path]
    if player_type == "mpv":
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
        subprocess.Popen(cmd, close_fds=True)
        return jsonify({"ok": True, "message": f"Playing playlist '{pl['name']}' in {player_label}."})
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
            "os_platform": platform.system().lower(),
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
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)