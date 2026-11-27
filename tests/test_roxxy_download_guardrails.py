"""Guardrails for Roxxy download destinations and the stream-inspection cache."""
import hashlib
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import app as app_module
import roxxy_download_engine as rde
import roxxy_module as rm


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
                    return 1
                with open(target, "wb") as fh:
                    fh.write(b"complete")
                return 0

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
                return 0

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
                 patch.object(rde, "_run_download_process", return_value=1):
                rde._run_job(self.job_id, "aria2c", "https://cdn.example/x", tmp, "Title.mp4")

            job = rde._jobs[self.job_id]
            self.assertEqual(job["status"], "failed")
            self.assertIn("aria2c: exit 1", job["message"])
            self.assertIn("yt-dlp: exit 1", job["message"])


if __name__ == "__main__":
    unittest.main()