"""Host-independent tests for Phase 6B.6 platform discovery helpers."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

import platform_support as ps


class PlatformDetectionTests(unittest.TestCase):
    def test_detects_windows_linux_and_macos(self):
        self.assertEqual(ps.detect_runtime_os("Windows", {}), "windows")
        self.assertEqual(ps.detect_runtime_os("Linux", {}), "linux")
        self.assertEqual(ps.detect_runtime_os("Darwin", {}), "darwin")

    def test_detects_android_before_linux(self):
        self.assertEqual(ps.detect_runtime_os("Linux", {"ANDROID_ROOT": "/system"}), "android")

    def test_windows_candidates_include_scoop_and_ffmpeg(self):
        env = {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)",
               "LOCALAPPDATA": r"C:\Users\Test\AppData\Local", "USERPROFILE": r"C:\Users\Test"}
        mpv = ps.executable_candidates("mpv", "windows", env)
        ffprobe = ps.executable_candidates("ffprobe", "windows", env)
        self.assertTrue(any("scoop\\apps\\mpv\\current\\mpv.exe" in x.lower() for x in mpv))
        self.assertTrue(any(x.lower().endswith(r"ffmpeg\bin\ffprobe.exe") for x in ffprobe))

    def test_macos_candidates_include_both_homebrew_prefixes(self):
        candidates = ps.executable_candidates("mpv", "darwin", {})
        self.assertIn("/opt/homebrew/bin/mpv", candidates)
        self.assertIn("/usr/local/bin/mpv", candidates)

    def test_linux_candidates_include_flatpak_and_snap(self):
        candidates = ps.executable_candidates("mpv", "linux", {})
        self.assertIn("/snap/bin/mpv", candidates)
        self.assertIn("/var/lib/flatpak/exports/bin/io.mpv.Mpv", candidates)

    def test_resolves_configured_executable_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "mpv-test"
            binary.write_text("binary", encoding="utf-8")
            binary.chmod(0o755)
            resolved = ps.resolve_executable(
                "mpv", configured_path=str(binary), system="Linux", environ={},
                which_func=lambda _name: None,
            )
            self.assertEqual(resolved, str(binary))

    def test_rejects_non_executable_posix_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "mpv-test"
            binary.write_text("binary", encoding="utf-8")
            binary.chmod(0o644)
            # Simulate POSIX execute-bit semantics explicitly. os.access(..., X_OK)
            # on Windows does not reliably model POSIX permission bits, even when
            # the resolver is asked to simulate Linux.
            def posix_executable_access(path, _mode):
                mode = Path(path).stat().st_mode
                return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))

            resolved = ps.resolve_executable(
                "mpv", configured_path=str(binary), system="Linux", environ={},
                which_func=lambda _name: None,
                access_func=posix_executable_access,
            )
            self.assertIsNone(resolved)

    def test_ensures_independent_data_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = ps.ensure_data_directories(tmp)
            self.assertEqual(set(dirs), {"database", "backups", "cache", "playlists"})
            self.assertTrue(all(Path(p).is_dir() for p in dirs.values()))


if __name__ == "__main__":
    unittest.main()
