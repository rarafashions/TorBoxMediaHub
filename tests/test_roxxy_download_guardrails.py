"""Guardrails for Roxxy download destinations and the stream-inspection cache."""
import hashlib
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import app as app_module
import database_manager as db
import roxxy_download_engine as rde
import roxxy_module as rm
import subtitle_engine as se


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.realpath(path)).rstrip("\\/")


class SafeDownloadDirTests(unittest.TestCase):
    def test_accepts_existing_absolute_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = rde._safe_download_dir(tmp)
            self.assertEqual(_normalized(resolved), _normalized(tmp))

    def test_rejects_empty_and_relative_paths(self):
        for value in ("", "   ", "Movies", os.path.join("relative", "dir")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rde._safe_download_dir(value)

    def test_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "does", "not", "exist")
            with self.assertRaises(ValueError):
                rde._safe_download_dir(missing)

    def test_rejects_path_inside_denied_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            protected = os.path.join(tmp, "protected")
            nested = os.path.join(protected, "nested")
            os.makedirs(nested)
            allowed = os.path.join(tmp, "allowed")
            os.makedirs(allowed)
            with patch.object(rde, "_download_denied_roots", return_value=[_normalized(protected)]):
                with self.assertRaises(ValueError):
                    rde._safe_download_dir(nested)
                self.assertEqual(_normalized(rde._safe_download_dir(allowed)), _normalized(allowed))

    def test_denied_roots_always_include_app_directory(self):
        app_dir = _normalized(os.path.dirname(os.path.abspath(rde.__file__)))
        self.assertIn(app_dir, rde._download_denied_roots())
        with self.assertRaises(ValueError):
            rde._safe_download_dir(os.path.join(app_dir, "templates"))

    @unittest.skipUnless(os.name == "nt", "Windows device namespace")
    def test_rejects_windows_device_namespace_prefix(self):
        with self.assertRaises(ValueError):
            rde._safe_download_dir("\\\\?\\C:\\Temp\\downloads")


class InspectCacheTests(unittest.TestCase):
    def setUp(self):
        rm._INSPECT_CACHE.clear()

    def tearDown(self):
        rm._INSPECT_CACHE.clear()

    @staticmethod
    def _fake_inspect(url: str) -> dict:
        return {"engine": "aria2c", "kind": "file", "content_type": "", "filename": "",
                "extension": ".mkv", "content_length": None, "http_status": 200, "url": url}

    def test_overflow_evicts_oldest_instead_of_flushing_cache(self):
        total = rm._INSPECT_CACHE_MAX + 5
        with patch("roxxy_download_engine._inspect", side_effect=self._fake_inspect) as inspect:
            for index in range(total):
                rm._resolve_remote_stream(f"https://cdn.example/v/{index}")

            self.assertEqual(inspect.call_count, total)
            self.assertEqual(len(rm._INSPECT_CACHE), rm._INSPECT_CACHE_MAX)

            oldest = hashlib.sha1(b"https://cdn.example/v/0").hexdigest()
            newest = hashlib.sha1(f"https://cdn.example/v/{total - 1}".encode()).hexdigest()
            self.assertNotIn(oldest, rm._INSPECT_CACHE)
            self.assertIn(newest, rm._INSPECT_CACHE)

            # A surviving entry is served from cache, so no extra inspection happens.
            rm._resolve_remote_stream(f"https://cdn.example/v/{total - 1}")
            self.assertEqual(inspect.call_count, total)

    def test_expired_entry_is_reinspected(self):
        url = "https://cdn.example/expired"
        key = hashlib.sha1(url.encode()).hexdigest()
        rm._INSPECT_CACHE[key] = (0.0, {"engine": "manual"})
        with patch("roxxy_download_engine._inspect", side_effect=self._fake_inspect) as inspect:
            result = rm._resolve_remote_stream(url)
        inspect.assert_called_once()
        self.assertEqual(result["engine"], "aria2c")

    def test_cache_hit_returns_a_copy(self):
        url = "https://cdn.example/copy"
        with patch("roxxy_download_engine._inspect", side_effect=self._fake_inspect):
            first = rm._resolve_remote_stream(url)
            first["engine"] = "tampered"
            second = rm._resolve_remote_stream(url)
        self.assertEqual(second["engine"], "aria2c")


class DownloaderAccelerationTests(unittest.TestCase):
    """The conservative aria2c/yt-dlp acceleration plan must stay explicit."""

    def test_aria2c_engine_keeps_ytdlp_as_fallback(self):
        self.assertEqual(rde._engine_sequence("aria2c"), ["aria2c", "yt-dlp"])
        self.assertEqual(rde._engine_sequence("yt-dlp"), ["yt-dlp"])

    def test_aria2c_command_enables_bounded_parallel_ranges(self):
        cmd = rde._aria2c_command("aria2c.exe", "C:/downloads", "Title (1).mp4", "https://cdn.example/v")
        self.assertIn("--continue=true", cmd)
        self.assertIn("--max-connection-per-server=4", cmd)
        self.assertIn("--split=4", cmd)
        self.assertIn("--min-split-size=1M", cmd)
        self.assertIn("--file-allocation=none", cmd)
        self.assertIn("--max-tries=3", cmd)
        self.assertIn("--retry-wait=2", cmd)
        self.assertEqual(cmd[cmd.index("--out") + 1], "Title (1).mp4")
        self.assertEqual(cmd[-1], "https://cdn.example/v")

    def test_engine_command_uses_collision_resolved_aria2c_basename(self):
        cmd = rde._engine_command("aria2c", "aria2c.exe", "C:/downloads/Title (1).mp4", "C:/downloads", "https://cdn.example/v")
        self.assertEqual(cmd[cmd.index("--out") + 1], "Title (1).mp4")

    def test_ytdlp_command_accelerates_fragments(self):
        cmd = rde._ytdlp_command("yt-dlp.exe", "C:/downloads/Title.mp4", "https://cdn.example/live.m3u8")
        self.assertIn("--concurrent-fragments", cmd)
        self.assertEqual(cmd[cmd.index("--concurrent-fragments") + 1], "4")
        self.assertIn("--fragment-retries", cmd)
        self.assertIn("--continue", cmd)
        self.assertIn("--part", cmd)
        self.assertEqual(cmd[cmd.index("-o") + 1], "C:/downloads/Title.%(ext)s")

    def test_ytdlp_command_hardens_network_retries(self):
        cmd = rde._ytdlp_command("yt-dlp.exe", "C:/downloads/Title.mp4", "https://cdn.example/live.m3u8")
        self.assertEqual(cmd[cmd.index("--retries") + 1], "10")
        self.assertEqual(cmd[cmd.index("--retry-sleep") + 1], "2")
        self.assertEqual(cmd[cmd.index("--socket-timeout") + 1], "20")
        self.assertEqual(cmd[cmd.index("--throttled-rate") + 1], "100K")

    def test_process_output_sanitizer_redacts_urls_and_tokens(self):
        safe = rde._sanitize_process_line(
            "ERROR: unable to download https://cdn.example/a?token=SECRET&expires=1"
        )
        self.assertNotIn("SECRET", safe)
        self.assertNotIn("https://", safe)
        self.assertIn("<url>", safe)
        self.assertNotIn("SECRET", rde._sanitize_process_line("retry &token=SECRET now"))

    def test_expiry_error_detector_matches_signed_link_failures(self):
        for text in (
            "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            "HTTP Error 410: Gone",
            "token expired",
            "ERROR: unable to download fragment 1304",
            "The read operation timed out",
        ):
            with self.subTest(text=text):
                self.assertTrue(rde._looks_like_expiry_error(text))
        self.assertFalse(rde._looks_like_expiry_error("HTTP Error 404: Not Found"))

    def test_refresh_stream_url_matches_the_same_cdn_path(self):
        payload = {"streams": [
            {"url": "https://cdn.example/other/file.mp4?sig=old"},
            {"url": "https://cdn.example/media/abc/video.m3u8?sig=new"},
        ]}
        with patch.object(rm, "_roxxy_get", return_value=payload) as fetch:
            url = rde._refresh_stream_url(
                {"id": "pornhub:1", "_roxxyType": "Pornhub"},
                {"url": "https://cdn.example/media/abc/video.m3u8?sig=old"},
            )
        self.assertEqual(url, "https://cdn.example/media/abc/video.m3u8?sig=new")
        self.assertIn("stream/Pornhub/", fetch.call_args.args[0])

    def test_refresh_stream_url_falls_back_to_the_expected_filename(self):
        payload = {"streams": [
            {"url": "https://cdn.example/media/new/video.m3u8?sig=new",
             "behaviorHints": {"filename": "Title.mp4"}},
        ]}
        with patch.object(rm, "_roxxy_get", return_value=payload):
            url = rde._refresh_stream_url(
                {"id": "pornhub:2", "type": "Pornhub"},
                {"url": "https://cdn.example/media/old/video.m3u8?sig=old",
                 "behaviorHints": {"filename": "Title.mp4"}},
            )
        self.assertEqual(url, "https://cdn.example/media/new/video.m3u8?sig=new")

    def test_remove_incomplete_output_drops_partial_and_control_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "Title.mp4")
            for suffix in ("", ".aria2", ".part"):
                with open(target + suffix, "wb") as fh:
                    fh.write(b"partial")
            keep = os.path.join(tmp, "other.mp4")
            with open(keep, "wb") as fh:
                fh.write(b"keep")
            rde._remove_incomplete_output(target)
            self.assertFalse(os.path.exists(target))
            self.assertFalse(os.path.exists(target + ".aria2"))
            self.assertFalse(os.path.exists(target + ".part"))
            self.assertTrue(os.path.exists(keep))

    def test_remove_incomplete_output_drops_stale_fragment_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "Title.mp4")
            orphan = target + ".part-Frag1304.part"
            with open(orphan, "wb") as fh:
                fh.write(b"")
            other = os.path.join(tmp, "Other.mp4.part-Frag1.part")
            with open(other, "wb") as fh:
                fh.write(b"x")
            rde._remove_incomplete_output(target)
            self.assertFalse(os.path.exists(orphan))
            self.assertTrue(os.path.exists(other))

    def test_mp4_stream_is_routed_to_aria2c(self):
        class _Response:
            status_code = 200
            headers = {"Content-Type": "video/mp4", "Content-Length": "1024"}
            def close(self):
                pass

        with patch.object(rde.HTTP, "head", return_value=_Response()):
            info = rde._inspect("https://cdn.example/video.mp4")
        self.assertEqual(info["engine"], "aria2c")
        self.assertEqual(info["kind"], "file")

    def test_hls_stream_stays_on_ytdlp(self):
        class _Response:
            status_code = 200
            headers = {"Content-Type": "application/vnd.apple.mpegurl"}
            def close(self):
                pass

        with patch.object(rde.HTTP, "head", return_value=_Response()):
            info = rde._inspect("https://cdn.example/live.m3u8")
        self.assertEqual(info["engine"], "yt-dlp")
        self.assertEqual(info["kind"], "manifest")


class _ThreadRecorder:
    created = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        _ThreadRecorder.created.append(self)

    def start(self):
        pass


class LocalRetryEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self._tmp = tempfile.TemporaryDirectory()
        self.outdir = self._tmp.name
        _ThreadRecorder.created = []
        with rde._lock:
            rde._jobs.clear()
        self.job_id = "job123"
        self.job = {
            "id": self.job_id, "status": "failed", "engine": "aria2c",
            "filename": "Title.mp4", "output_dir": rde._safe_download_dir(self.outdir),
            "url": "https://cdn.example/video.mp4", "attempts": 0,
            "created_at": "2026-01-01T00:00:00", "progress": 0.0,
            "target_path": os.path.join(rde._safe_download_dir(self.outdir), "Title (1).mp4"),
        }
        with rde._lock:
            rde._jobs[self.job_id] = dict(self.job)

    def tearDown(self):
        with rde._lock:
            rde._jobs.clear()
        self._tmp.cleanup()
        _ThreadRecorder.created = []

    def _retry(self):
        with patch.object(rde.threading, "Thread", _ThreadRecorder):
            return self.client.post(f"/api/roxxy/local/retry/{self.job_id}")

    def test_retry_requeues_failed_job_and_keeps_target(self):
        response = self._retry()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["job"]["status"], "queued")
        self.assertEqual(body["job"]["attempts"], 1)
        self.assertNotIn("url", body["job"])
        self.assertNotIn("target_path", body["job"])
        self.assertEqual(len(_ThreadRecorder.created), 1)
        args = _ThreadRecorder.created[0].args
        self.assertEqual(args[0], self.job_id)
        self.assertEqual(args[1], "aria2c")
        self.assertEqual(args[2], self.job["url"])
        self.assertEqual(args[5], True)
        self.assertEqual(args[6], self.job["target_path"])

    def test_unknown_job_returns_404(self):
        with patch.object(rde.threading, "Thread", _ThreadRecorder):
            response = self.client.post("/api/roxxy/local/retry/missing")
        self.assertEqual(response.status_code, 404)

    def test_non_failed_job_is_rejected(self):
        with rde._lock:
            rde._jobs[self.job_id]["status"] = "downloading"
        response = self._retry()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_ThreadRecorder.created, [])

    def test_retry_without_stored_url_is_rejected(self):
        with rde._lock:
            rde._jobs[self.job_id]["url"] = ""
        response = self._retry()
        self.assertEqual(response.status_code, 409)

    def test_retry_cap_is_enforced(self):
        with rde._lock:
            rde._jobs[self.job_id]["attempts"] = rde.RETRY_LIMIT
        response = self._retry()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(_ThreadRecorder.created, [])

    def test_retry_revalidates_download_directory(self):
        with rde._lock:
            rde._jobs[self.job_id]["output_dir"] = "relative/path"
        response = self._retry()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_ThreadRecorder.created, [])

    def test_queue_hides_private_retry_fields(self):
        with patch.object(rde.threading, "Thread", _ThreadRecorder):
            body = self.client.get("/api/roxxy/local/queue").get_json()
        job = body["jobs"][0]
        for key in ("url", "output_dir", "target_path", "pid", "started_ts"):
            self.assertNotIn(key, job)


class JobFallbackTests(unittest.TestCase):
    def setUp(self):
        with rde._lock:
            rde._jobs.clear()
        self.job_id = "fallback"
        with rde._lock:
            rde._jobs[self.job_id] = {
                "id": self.job_id, "created_at": "2026-01-01T00:00:00",
                "started_ts": time.time(), "meta_snapshot": {}, "stream_snapshot": {},
                "attempts": 0, "engine": "aria2c",
            }

    def tearDown(self):
        with rde._lock:
            rde._jobs.clear()

    def test_aria2c_failure_falls_back_to_ytdlp(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def fake_run(job_id, engine, cmd):
                calls.append(engine)
                target = os.path.join(tmp, "Title.mp4")
                if engine == "aria2c":
                    with open(target, "wb") as fh:
                        fh.write(b"partial")
                    with open(target + ".aria2", "wb") as fh:
                        fh.write(b"control")
                    return 1, ""
                with open(target, "wb") as fh:
                    fh.write(b"complete")
                return 0, ""

            with patch.object(rde.shutil, "which", return_value="exe"), \
                 patch.object(rde, "_run_download_process", side_effect=fake_run), \
                 patch.object(rde.db, "enqueue_roxxy_library_task", return_value=42):
                rde._run_job(self.job_id, "aria2c", "https://cdn.example/x", tmp, "Title.mp4")

            job = rde._jobs[self.job_id]
            self.assertEqual(calls, ["aria2c", "yt-dlp"])
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["engine_active"], "yt-dlp")
            self.assertEqual(job["library_task_id"], 42)
            self.assertFalse(os.path.exists(os.path.join(tmp, "Title.mp4.aria2")))

    def test_collision_rename_reports_the_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "Title.mp4")
            with open(old, "wb") as fh:
                fh.write(b"old")
            stamp = time.time() - 3600
            os.utime(old, (stamp, stamp))

            def fake_run(job_id, engine, cmd):
                template = cmd[cmd.index("-o") + 1]
                with open(template.replace("%(ext)s", "mp4"), "wb") as fh:
                    fh.write(b"new")
                return 0, ""

            with patch.object(rde.shutil, "which", return_value="exe"), \
                 patch.object(rde, "_run_download_process", side_effect=fake_run), \
                 patch.object(rde.db, "enqueue_roxxy_library_task", return_value=1) as enqueue:
                rde._run_job(self.job_id, "yt-dlp", "https://cdn.example/x", tmp, "Title.mp4")

            final_path = enqueue.call_args.args[1]
            self.assertEqual(os.path.basename(final_path), "Title (1).mp4")
            self.assertTrue(os.path.isfile(final_path))

    def test_both_engines_failing_marks_job_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rde.shutil, "which", return_value="exe"), \
                 patch.object(rde, "_run_download_process", return_value=(1, "")):
                rde._run_job(self.job_id, "aria2c", "https://cdn.example/x", tmp, "Title.mp4")

            job = rde._jobs[self.job_id]
            self.assertEqual(job["status"], "failed")
            self.assertIn("aria2c: exit 1", job["message"])
            self.assertIn("yt-dlp: exit 1", job["message"])

    def test_expired_link_is_refreshed_and_retried_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with rde._lock:
                rde._jobs[self.job_id].update(
                    meta_snapshot={"id": "pornhub:1", "_roxxyType": "Pornhub"},
                    stream_snapshot={"url": "https://cdn.example/v?sig=old"},
                )
            calls = []

            def fake_run(job_id, engine, cmd):
                calls.append(cmd[-1])
                if len(calls) == 1:
                    return 1, "ERROR: HTTP Error 403: Forbidden"
                with open(os.path.join(tmp, "Title.mp4"), "wb") as fh:
                    fh.write(b"complete")
                return 0, ""

            with patch.object(rde.shutil, "which", return_value="exe"), \
                 patch.object(rde, "_run_download_process", side_effect=fake_run), \
                 patch.object(rde, "_refresh_stream_url", return_value="https://cdn.example/v?sig=new") as refresh, \
                 patch.object(rde.db, "enqueue_roxxy_library_task", return_value=7):
                rde._run_job(self.job_id, "yt-dlp", "https://cdn.example/v?sig=old", tmp, "Title.mp4")

            job = rde._jobs[self.job_id]
            self.assertEqual(job["status"], "completed")
            self.assertEqual(calls, ["https://cdn.example/v?sig=old", "https://cdn.example/v?sig=new"])
            refresh.assert_called_once()

    def test_failure_message_keeps_the_sanitized_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rde.shutil, "which", return_value="exe"), \
                 patch.object(rde, "_run_download_process",
                              return_value=(1, "ERROR: HTTP Error 404: Not Found <url>")):
                rde._run_job(self.job_id, "yt-dlp", "https://cdn.example/x", tmp, "Title.mp4")

            job = rde._jobs[self.job_id]
            self.assertEqual(job["status"], "failed")
            self.assertIn("HTTP Error 404", job["message"])
            self.assertNotIn("https://", job["message"])


class ProvidedSubtitleTests(unittest.TestCase):
    """Roxxy-supplied subtitle tracks must be filtered, deduped and validated."""

    def test_accepts_only_english_and_indonesian_once(self):
        entries = [
            {"lang": "English", "url": "https://93.184.216.34/a.srt"},
            {"language": "id", "url": "https://93.184.216.34/b.srt"},
            {"lang": "en", "url": "https://93.184.216.34/c.srt"},
            {"lang": "ja", "url": "https://93.184.216.34/d.srt"},
            {"lang": "en", "url": ""},
        ]
        with patch.object(se, "_download_provided_subtitle", return_value="C:/cache/a.srt") as dl:
            tracks = se.fetch_provided_subtitles(entries, {"unique_key": "local:X"})
        self.assertEqual([t["subtitle_lang"] for t in tracks], ["en", "id"])
        self.assertTrue(all(t["subtitle_source"] == "roxxy" for t in tracks))
        self.assertEqual(dl.call_count, 2)

    def test_ignores_missing_or_malformed_payloads(self):
        for payload in (None, {}, "https://93.184.216.34/a.srt", [None, "x", 5]):
            with self.subTest(payload=payload):
                self.assertEqual(se.fetch_provided_subtitles(payload, {"unique_key": "k"}), [])

    def test_language_normalisation_handles_regional_tags(self):
        self.assertEqual(se.normalize_subtitle_language("en-US"), "en")
        self.assertEqual(se.normalize_subtitle_language("id_ID"), "id")
        self.assertEqual(se.normalize_subtitle_language("English"), "en")
        self.assertIsNone(se.normalize_subtitle_language("jpn"))

    def test_private_and_loopback_urls_are_rejected(self):
        self.assertFalse(se._provided_subtitle_url_is_public("http://127.0.0.1/a.srt"))
        self.assertFalse(se._provided_subtitle_url_is_public("http://localhost/a.srt"))
        self.assertFalse(se._provided_subtitle_url_is_public("file:///C:/a.srt"))
        self.assertTrue(se._provided_subtitle_url_is_public("https://93.184.216.34/a.srt"))

    def test_untrusted_subtitle_url_never_triggers_a_request(self):
        with patch.object(se.HTTP_SESSION, "get") as get:
            self.assertIsNone(se._download_provided_subtitle("http://127.0.0.1/a.srt", "x", 1024))
        get.assert_not_called()

    def test_size_cap_rejects_oversized_responses(self):
        class _Response:
            headers = {}
            def iter_content(self, chunk_size=0):
                return iter([b"a" * 64, b"b" * 64])
        self.assertIsNone(se._read_subtitle_response(_Response(), max_bytes=100))
        self.assertEqual(len(se._read_subtitle_response(_Response(), max_bytes=1000)), 128)

    def test_attach_persists_every_supplied_track(self):
        tracks = [
            {"subtitle_path": "C:/cache/en.srt", "subtitle_lang": "en", "subtitle_source": "roxxy"},
            {"subtitle_path": "C:/cache/id.srt", "subtitle_lang": "id", "subtitle_source": "roxxy"},
        ]
        with patch.object(rde.subtitle_engine, "fetch_provided_subtitles", return_value=tracks), \
             patch.object(rde.db, "upsert_media_subtitle") as upsert:
            langs = rde.attach_provided_subtitles(7, {"unique_key": "local:X"}, [{"url": "x"}])
        self.assertEqual(langs, ["en", "id"])
        self.assertEqual(upsert.call_count, 2)
        self.assertEqual(upsert.call_args_list[0].args[:2], (7, "en"))
        self.assertFalse(upsert.call_args_list[0].kwargs["is_embedded"])


class ImportHintMetadataTests(unittest.TestCase):
    """Cloud imports must reuse the Roxxy metadata snapshot without trusting it."""

    def test_sanitizer_keeps_known_fields_and_drops_unknown(self):
        cleaned = app_module._sanitize_import_metadata({
            "name": "  Sample Title  ",
            "description": "Plot",
            "jav_code": "ABP-123",
            "year": "2021",
            "imdbRating": "8.1",
            "genres": ["Drama", "  ", 7],
            "cast": ["Actor"],
            "poster": "https://93.184.216.34/p.jpg",
            "signed_url": "https://secret.example/token",
            "runtime": "120 min",
        })
        self.assertEqual(cleaned["name"], "Sample Title")
        self.assertEqual(cleaned["year"], 2021)
        self.assertEqual(cleaned["imdbRating"], 8.1)
        self.assertEqual(cleaned["genres"], ["Drama", "7"])
        self.assertEqual(cleaned["cast"], ["Actor"])
        self.assertNotIn("signed_url", cleaned)

    def test_sanitizer_rejects_impossible_year_and_rating(self):
        cleaned = app_module._sanitize_import_metadata({"name": "X", "year": 1200, "imdbRating": 42})
        self.assertNotIn("year", cleaned)
        self.assertNotIn("imdbRating", cleaned)

    def test_subtitle_hint_rejects_non_http_urls_and_caps_entries(self):
        entries = [{"url": "ftp://93.184.216.34/a.srt", "lang": "en"}]
        entries += [{"url": f"https://93.184.216.34/{i}.srt", "lang": "en"} for i in range(12)]
        cleaned = app_module._sanitize_import_subtitles(entries)
        self.assertEqual(len(cleaned), 8)
        self.assertTrue(all(e["url"].startswith("https://") for e in cleaned))

    def test_media_payload_maps_metadata_and_marks_source(self):
        payload = app_module._import_hint_media_payload({
            "name": "Sample Title", "description": "Plot", "year": 2021,
            "runtime": "95 min", "genres": ["Drama"], "imdbRating": 7.5,
            "studio": "Studio A", "original_name": "Original",
        })
        self.assertEqual(payload["title"], "Sample Title")
        self.assertEqual(payload["runtime"], 95)
        self.assertEqual(payload["year"], 2021)
        self.assertEqual(payload["genres"], ["Drama"])
        self.assertEqual(payload["rating"], 7.5)
        self.assertEqual(payload["has_metadata"], 1)
        self.assertEqual(payload["metadata_source"], "roxxy")

    def test_empty_metadata_produces_no_fields(self):
        self.assertEqual(app_module._import_hint_media_payload({}), {})

    def test_classification_uses_jav_code_when_filename_is_missing(self):
        category = app_module._classify_import_hint(
            {"name": "Sample JAV Title", "jav_code": "ABP-123"}, ""
        )
        self.assertEqual(category, "jav")


class TorboxHintPersistenceTests(unittest.TestCase):
    """Import hints must survive restarts without losing earlier metadata."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = (db.DB_DIR, db.DB_PATH, db.CACHE_DIR, db.BACKUP_DIR, db.PLAYLIST_DIR, db.STATIC_DIR)
        db.DB_DIR = self._tmp.name
        db.DB_PATH = os.path.join(self._tmp.name, "hints.db")
        db.CACHE_DIR = os.path.join(self._tmp.name, "cache")
        db.BACKUP_DIR = os.path.join(self._tmp.name, "backup")
        db.PLAYLIST_DIR = os.path.join(self._tmp.name, "playlists")
        db.STATIC_DIR = os.path.join(self._tmp.name, "static")
        db.init_db()

    def tearDown(self):
        (db.DB_DIR, db.DB_PATH, db.CACHE_DIR,
         db.BACKUP_DIR, db.PLAYLIST_DIR, db.STATIC_DIR) = self._old
        self._tmp.cleanup()

    def test_hint_roundtrips_metadata_and_poster(self):
        meta = {"meta": {"name": "Sample", "jav_code": "ABP-123"}, "subtitles": [{"lang": "en", "url": "https://93.184.216.34/a.srt"}]}
        db.save_torbox_import_hint("torrent", "42", "jav", "https://93.184.216.34/p.jpg", meta)
        hint = db.get_torbox_import_hint("torrent", "42")
        self.assertEqual(hint["content_category"], "jav")
        self.assertEqual(hint["poster_url"], "https://93.184.216.34/p.jpg")
        self.assertEqual(hint["metadata"]["meta"]["jav_code"], "ABP-123")

    def test_later_hint_without_metadata_preserves_it(self):
        db.save_torbox_import_hint("torrent", "42", "jav", "", {"meta": {"name": "Sample"}})
        db.save_torbox_import_hint("torrent", "42", "jav", "https://93.184.216.34/p.jpg")
        hint = db.get_torbox_import_hint("torrent", "42")
        self.assertEqual(hint["metadata"]["meta"]["name"], "Sample")
        self.assertEqual(hint["poster_url"], "https://93.184.216.34/p.jpg")

    def test_invalid_category_is_normalised(self):
        db.save_torbox_import_hint("torrent", "7", "not-a-category", "")
        self.assertEqual(db.get_torbox_import_hint("torrent", "7")["content_category"], "uncategorized")

    def test_missing_hint_returns_empty_mapping(self):
        self.assertEqual(db.get_torbox_import_hint("torrent", "missing"), {})

    def test_migration_adds_column_to_legacy_hint_table(self):
        with db.get_connection() as conn:
            conn.execute("DROP TABLE torbox_import_hints")
            conn.execute(
                """CREATE TABLE torbox_import_hints (
                       torbox_kind TEXT NOT NULL,
                       torbox_item_id TEXT NOT NULL,
                       content_category TEXT NOT NULL DEFAULT 'uncategorized',
                       poster_url TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       PRIMARY KEY (torbox_kind, torbox_item_id)
                   )"""
            )
            conn.execute(
                "INSERT INTO torbox_import_hints (torbox_kind, torbox_item_id, content_category) "
                "VALUES ('torrent', '9', 'jav')"
            )
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ?",
                (db.TORBOX_HINT_MIGRATION_ID,),
            )
            conn.commit()
            db.migrate_torbox_import_hint_schema(conn)
            conn.commit()
            columns = {
                r["name"] for r in conn.execute("PRAGMA table_info(torbox_import_hints)").fetchall()
            }
        self.assertIn("metadata_json", columns)
        hint = db.get_torbox_import_hint("torrent", "9")
        self.assertEqual(hint["content_category"], "jav")
        self.assertEqual(hint["metadata"], {})


class LocalSubtitleSnapshotTests(unittest.TestCase):
    """Local downloads must carry Roxxy's subtitle list into the D3 task."""

    def setUp(self):
        self.client = app_module.app.test_client()
        self._tmp = tempfile.TemporaryDirectory()
        _ThreadRecorder.created = []
        with rde._lock:
            rde._jobs.clear()

    def tearDown(self):
        with rde._lock:
            rde._jobs.clear()
        self._tmp.cleanup()
        _ThreadRecorder.created = []

    def test_start_captures_supplied_subtitles_without_leaking_them(self):
        info = {
            "engine": "aria2c", "kind": "file", "content_type": "video/mp4",
            "filename": "Title.mp4", "extension": ".mp4", "content_length": 10,
            "http_status": 200, "url": "https://93.184.216.34/video.mp4",
        }
        subs = [{"lang": "en", "url": "https://93.184.216.34/a.srt"}]
        with patch.object(rde, "_inspect", return_value=info), \
             patch.object(rde.threading, "Thread", _ThreadRecorder):
            response = self.client.post(
                "/api/roxxy/local/start",
                json={
                    "stream": {"url": "https://93.184.216.34/video.mp4", "name": "Title"},
                    "meta": {"name": "Title"},
                    "subtitles": subs,
                    "download_path": self._tmp.name,
                },
            )
        body = response.get_json()
        self.assertTrue(body["ok"])
        job = rde._jobs[body["job"]["id"]]
        self.assertEqual(job["stream_snapshot"]["_subtitles"], subs)
        queue = self.client.get("/api/roxxy/local/queue").get_json()
        self.assertNotIn("stream_snapshot", queue["jobs"][0])


if __name__ == "__main__":
    unittest.main()