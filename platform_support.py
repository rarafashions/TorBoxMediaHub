"""Cross-platform runtime helpers for TorBox Media Hub.

The application uses an independent data directory per installation. This module
only resolves executables and reports platform capabilities; it never migrates
media/database paths between operating systems.
"""
from __future__ import annotations

import os
import ntpath
import platform
import shutil
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional


def detect_runtime_os(system: Optional[str] = None, environ: Optional[dict] = None) -> str:
    env = environ if environ is not None else os.environ
    if env.get("ANDROID_ROOT") or "com.termux" in env.get("PREFIX", ""):
        return "android"
    value = (system or platform.system()).lower()
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "darwin"
    if value == "linux":
        return "linux"
    return value or "unknown"


def _windows_candidates(name: str, env: dict) -> List[str]:
    suffix = ".exe" if not name.lower().endswith(".exe") else ""
    exe = name + suffix
    join = ntpath.join
    roots = [
        env.get("ProgramFiles", r"C:\Program Files"),
        env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        env.get("LOCALAPPDATA", ""),
        join(env.get("USERPROFILE", ""), "scoop", "apps"),
        r"C:\ffmpeg\bin",
    ]
    rels = {
        "mpv": [join("mpv", "mpv.exe"), join("mpv", "current", "mpv.exe"), join("Programs", "mpv", "mpv.exe")],
        "vlc": [join("VideoLAN", "VLC", "vlc.exe"), join("Programs", "VideoLAN", "VLC", "vlc.exe")],
        "ffprobe": [join("ffmpeg", "bin", "ffprobe.exe"), "ffprobe.exe"],
        "ffmpeg": [join("ffmpeg", "bin", "ffmpeg.exe"), "ffmpeg.exe"],
    }.get(name.lower(), [exe])
    result: List[str] = []
    for root in roots:
        if root:
            for rel in rels:
                result.append(join(root, rel) if rel != exe else join(root, exe))
    # Scoop shims are normally on PATH, but common install roots are useful fallbacks.
    scoop = join(env.get("USERPROFILE", ""), "scoop", "apps")
    if scoop and name.lower() in {"mpv", "vlc"}:
        result.extend([join(scoop, name.lower(), "current", exe)])
    return result


def executable_candidates(name: str, system: Optional[str] = None,
                          environ: Optional[dict] = None) -> List[str]:
    env = environ if environ is not None else os.environ
    os_name = detect_runtime_os(system, env)
    key = name.lower().removesuffix(".exe")
    if os_name == "windows":
        return _windows_candidates(key, env)
    if os_name == "darwin":
        mapping = {
            "mpv": ["/opt/homebrew/bin/mpv", "/usr/local/bin/mpv", "/Applications/mpv.app/Contents/MacOS/mpv"],
            "vlc": ["/Applications/VLC.app/Contents/MacOS/VLC", os.path.expanduser("~/Applications/VLC.app/Contents/MacOS/VLC"), "/opt/homebrew/bin/vlc", "/usr/local/bin/vlc"],
            "ffprobe": ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"],
            "ffmpeg": ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"],
        }
        return mapping.get(key, [])
    if os_name == "linux":
        mapping = {
            "mpv": ["/usr/bin/mpv", "/usr/local/bin/mpv", "/snap/bin/mpv", "/var/lib/flatpak/exports/bin/io.mpv.Mpv"],
            "vlc": ["/usr/bin/vlc", "/usr/local/bin/vlc", "/snap/bin/vlc", "/var/lib/flatpak/exports/bin/org.videolan.VLC"],
            "ffprobe": ["/usr/bin/ffprobe", "/usr/local/bin/ffprobe", "/snap/bin/ffprobe"],
            "ffmpeg": ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/snap/bin/ffmpeg"],
        }
        return mapping.get(key, [])
    return []


def _expand_configured_path(value: str, env: dict, system: str) -> str:
    raw = (value or "").strip().strip('"')
    if not raw:
        return ""
    if detect_runtime_os(system, env) == "windows":
        for key, val in env.items():
            raw = raw.replace(f"%{key}%", str(val))
    return os.path.expanduser(os.path.expandvars(raw))


def resolve_executable(name: str, configured_path: Optional[str] = None,
                       system: Optional[str] = None, environ: Optional[dict] = None,
                       which_func: Callable = shutil.which,
                       isfile_func: Callable = os.path.isfile,
                       access_func: Callable = os.access) -> Optional[str]:
    """Resolve a configured executable, PATH entry, or known OS installation path."""
    env = environ if environ is not None else os.environ
    os_name = detect_runtime_os(system, env)
    candidates: List[str] = []
    configured = _expand_configured_path(configured_path or "", env, system or platform.system())
    if configured:
        candidates.append(configured)
        # A configured bare command may still be discoverable via PATH.
        found = which_func(configured)
        if found:
            candidates.append(found)
    names = [name]
    if os_name == "windows" and not name.lower().endswith(".exe"):
        names.insert(0, name + ".exe")
    for binary_name in names:
        found = which_func(binary_name)
        if found:
            candidates.append(found)
    candidates.extend(executable_candidates(name, os_name, env))

    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate = ntpath.normpath(candidate) if os_name == "windows" else os.path.normpath(candidate)
        marker = candidate.casefold() if os_name == "windows" else candidate
        if marker in seen:
            continue
        seen.add(marker)
        if isfile_func(candidate):
            if os_name == "windows" or access_func(candidate, os.X_OK):
                return candidate
    return None


def runtime_capabilities() -> Dict[str, object]:
    os_name = detect_runtime_os()
    return {
        "os_platform": os_name,
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "mpv_available": bool(resolve_executable("mpv")),
        "ffmpeg_available": bool(resolve_executable("ffmpeg")),
        "ffprobe_available": bool(resolve_executable("ffprobe")),
    }


def ensure_data_directories(data_dir: str) -> Dict[str, str]:
    """Create and return per-installation data directories, without touching media."""
    root = Path(data_dir).expanduser().resolve()
    paths = {
        "database": root / "database",
        "backups": root / "backups",
        "cache": root / "cache",
        "playlists": root / "playlists",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return {key: str(path) for key, path in paths.items()}
