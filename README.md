# 🎬 TorBox Media Hub
### Metadata Catalog, Auto-Subtitle & `.m3u` Playlist Builder for TorBox Cloud & Local Media

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Backend-Flask%20%2B%20SQLite-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![VLC](https://img.shields.io/badge/Player-VLC%20%2F%20MPV%20M3U-FF8800?logo=vlcmediaplayer&logoColor=white)](https://www.videolan.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#license)

**TorBox Media Hub** (formerly *TorBox Playlist Builder*) is a lightweight, self-hosted desktop web application that turns your raw **TorBox Cloud** library (`torrents` & `usenet`) and **local hard drive folders** into a unified, visual streaming catalog—complete with automated high-resolution posters, 4-tier subtitle injection, and one-click `.m3u` playback in **VLC Media Player**.

> 💡 **New to TorBox?** Sign up using our referral link ** (https://torbox.app/subscription?referral=d8962fcc-d39e-43f6-9a8a-f565bab5f809) ** to get **7 bonus days free** on your subscription while supporting the continued development of this open-source project!


## 🖼️ Preview & Screenshots

> *(Tip: Replace these placeholders with actual screenshots/GIFs saved in `docs/assets/` before announcing on `r/TorBoxApp` and Discord)*

| Visual Poster Catalog | Multi-Tier Subtitle Finder | 1-Click VLC M3U Streaming |
| :---: | :---: | :---: |
| ![Catalog View](docs/assets/TMH-General-Movies.png) | ![Subtitle Engine](docs/assets/TMH-Subtitle.png) | ![Media Details Card](docs/assets/TMH - Details - Media - Card.png) |

---

## ✨ Why TorBox Media Hub?

Managing dozens of cached torrents, Usenet downloads, and local video files from raw file lists can be frustrating. **TorBox Media Hub** solves the biggest daily pain points for TorBox desktop users without requiring heavy media server stacks like Plex, Rclone, or FUSE mounts:

* **No More Messy Raw File Lists:** Automatically parses cryptic release filenames, classifies content types, and fetches high-resolution posters, cast, genres, and descriptions so your TorBox Cloud looks like a real streaming app.
* **Zero-Hassle Subtitles for Direct Streams:** Automatically inspects embedded tracks (`ffprobe`), extracts sidecar `.srt`/`.ass` files bundled inside your TorBox torrents/Usenet archives, or searches public providers (OpenSubtitles & SubtitleCat) and injects them directly into VLC.
* **Fresh Stream Links on Demand (No Expired URLs):** Generates dynamic `.m3u` playlists with fresh `requestdl` stream links right when you build or play a playlist—eliminating dead links from stale playlists.
* **Cloud + Local Hybrid Library:** Combine your TorBox Cloud media and local disk folders in one searchable interface, with dedicated workspace tabs to track what you haven't watched yet.
* **100% Local & Privacy-First:** Runs entirely on your local machine using SQLite (`WAL` mode). Your API keys are write-only masked (`••••••••xxxx`) and never leave your device.

---

## 🚀 Core Features

### 1. Hybrid Media Ingestion (TorBox Cloud & Local Disk)
* **Instant TorBox Cloud Sync:** Fetches all video files from `torrents/mylist` and `usenet/mylist` in a single atomic SQLite transaction without triggering `requestdl` rate limits during sync.
* **Recursive Local Disk Scanner:** Scans configured local directories for all major video formats (`.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.webm`, `.m4v`, `.ts`, `.m2ts`) and automatically links local sidecar subtitles.
* **Metadata Protection:** Re-syncing your library safely preserves any metadata, posters, or categories you have already fetched or manually edited.

### 2. Multi-Source Metadata & Poster Engine (`metadata_engine.py`)
Media files are automatically classified using a strict priority pipeline so niche libraries never pollute your mainstream catalog:
1. **JAV (`jav`):** Detects official studio production codes (including `FC2-PPV`, `HEYZO`, `CARIB`, and hundreds of studio prefixes) with anti-false-positive filtering against technical tags (`DDP5.1`, `ITA`, `x264`, `AMZN`, etc.), fetching posters, actresses, studios, and release dates from **JAVDatabase**.
2. **Adult Content (`english_adult`):** Studio and keyword-based detection completely isolated from TMDb/AniList, powered by **ThePornDB (TPDB) API** (`/scenes` & `/movies`).
3. **Anime & Donghua (`anime` / `donghua`):** Identifies fansub groups (`[SubsPlease]`, `[Erai-raws]`, etc.), CJK characters, and origin country (`JP` vs `CN`/`TW`) via **AniList GraphQL API** and **TMDb**.
4. **Movies & TV Shows (`general_movie` / `tv_show`):** Accurate title, year, and season/episode matching via **TMDb API** (with colon-subtitle fallback and **TVMaze API** support for TV series).
5. **Local Poster Caching & Cache-Busting:** Posters are downloaded and stored locally in `cache/posters/` using SHA-256 hashes with `?v=updated_at` cache-busting for instant UI updates.

### 3. 4-Tier Automated Subtitle Engine (`subtitle_engine.py`)
Subtitle discovery runs sequentially from fastest local sources to external providers:
1. **Local Sidecar Detection:** Finds matching `.srt`, `.ass`, `.ssa`, `.vtt`, or `.sub` files in the same directory as local videos.
2. **TorBox Cloud Archive Indexing:** Scans inside your TorBox torrent/Usenet file trees for bundled subtitle files and downloads them automatically to `cache/subtitles/`.
3. **Embedded Track Inspection (`ffprobe`):** Inspects `.mkv`/`.mp4` container headers on both **local files** and **live TorBox Cloud streams** to detect built-in subtitle tracks.
4. **Public Subtitle Providers:**
   * **JAV:** Searches English/Indonesian subtitles via **SubtitleCat**, with fallback to **OpenSubtitles REST API**.
   * **Movies / TV Shows / Anime / Donghua:** Queries **OpenSubtitles REST API** with `season` and `episode` matching *(automatically skipped for `english_adult`)*.

### 4. `.m3u` Playlist Curation & Direct VLC Integration
* **Dedicated Workspace Tabs:** Easily filter between **Unassigned Media** (*Belum Masuk Playlist*), **In Playlist** (*Sudah Masuk Playlist*), and **All Media**.
* **Dynamic `.m3u` Generation:** Outputs standard `.m3u` playlists in `playlists/` complete with duration (`#EXTINF`), external subtitle injection (`#EXTVLCOPT:sub-file=...`), and fresh `requestdl` stream URLs.
* **1-Click VLC Playback:** Launch a single video or an entire curated playlist directly into **VLC Media Player** from your browser.

### 5. Background Task Control & Database Maintenance
* **Parallel Worker Pool:** Uses `ThreadPoolExecutor(max_workers=4)` with live percentage progress tracking (`..% completed`) and UI action locking to prevent database conflicts.
* **Write-Only Secret Masking:** API keys (TorBox, TMDb, ThePornDB) are masked on the server side and never exposed in plain text to the browser.
* **Built-in DB Tools:** One-click SQLite `.db` Backup, Restore, Maintenance (`VACUUM` & orphaned local file cleanup), Metadata Reset, and Full Erase.

---

## 📂 Project Architecture

```text
TorBoxMediaHub/
├── app.py                  # Flask server (127.0.0.1:5050), REST API, Task Manager & M3U Generator
├── database_manager.py     # SQLite WAL management, schema, secret masking, backup & maintenance
├── metadata_engine.py      # Content classifier, title/code parser, API clients & poster downloader
├── subtitle_engine.py      # 4-tier subtitle engine (Sidecar, TorBox Cloud, ffprobe, SubtitleCat, OpenSubtitles)
├── requirements.txt        # Python dependencies
├── .gitignore              # Excludes local DB, cache, playlists, backups & secrets
├── templates/
│   └── index.html          # Single-Page Web UI (Tailwind CSS + Vanilla JS)
├── docs/
│   └── assets/             # Screenshots & demo GIFs for documentation
├── database/               # Local SQLite database (torbox_media_hub.db) [git-ignored]
├── cache/                  # Cached posters & downloaded subtitles [git-ignored]
├── playlists/              # Generated .m3u playlist files [git-ignored]
└── backups/                # Database backup archives [git-ignored]
```

---

## 🛠️ Installation & Quick Start

### Prerequisites
* **Python 3.10+**
* **VLC Media Player** (for 1-click video & `.m3u` playlist playback)
* **FFmpeg / `ffprobe`** in your system `PATH` *(optional, recommended for embedded subtitle inspection on cloud & local files)*

### 1. Clone the Repository
```bash
git clone https://github.com/rarafashions/TorBox-Media-Hub.git
cd TorBox-Media-Hub
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```
*(Or manually: `pip install flask requests beautifulsoup4`)*

### 3. Start the Local Server
```bash
python app.py
```
Open your browser and go to:
```text
http://127.0.0.1:5050
```

### 4. Configure Your API Keys
1. Navigate to the **Settings & DB** tab in the web interface.
2. Enter your **TorBox API Key** from [TorBox Settings](https://torbox.app/settings) *(Don't have an account yet? [Sign up via our referral link](https://bit.ly/TorBox7DaysAddistions) to get **7 free bonus days**!)*.
3. Enter your free **TMDb API Key** (and optional **ThePornDB API Key**).
4. Set the local executable path for **VLC Media Player** and add any **Local Scan Folders** you want to include.

---

## 🔄 Typical Workflow

1. **Sync & Scan:** Click **Sync Cloud** to import your TorBox torrents/Usenet files, and **Scan Local** to index local video folders.
2. **Enrich Metadata:** Run **Batch Enrich** to automatically classify titles and download posters/metadata in parallel.
3. **Fetch Subtitles:** Click **Find Subtitles** to detect embedded tracks, extract TorBox archive subtitles, or download matching `.srt` files.
4. **Curate & Watch:** Select items in **Unassigned Media**, add them to a `.m3u` playlist, and hit **Play** to stream immediately in VLC with subtitles loaded.

---

## 🗺️ Roadmap & Planned Enhancements

* [ ] **Live HTTP M3U Redirect Endpoint (`/stream/<unique_key>`):** Serve permanent local M3U URLs that dynamically `302 Redirect` to fresh `requestdl` links at playback time.
* [ ] **Cross-Platform Auto-Detection & MPV/IINA Support:** Automatic binary detection via `shutil.which()` for Windows, macOS, and Linux players.
* [ ] **Docker & Docker Compose Support:** Ready-to-use `Dockerfile` for Unraid, Synology NAS, and home-server deployments.

---

## 🤝 Contributing & Community

Contributions, bug reports, and feature requests are welcome!
* **Issues & Pull Requests:** Feel free to open an issue or submit a PR on GitHub.
* **Support Development:** If this project improves your TorBox workflow, consider starring ⭐ the repository or using our **[TorBox Referral Link](https://torbox.app/subscription?referral=YOUR_REFERRAL_CODE)** when subscribing or renewing your TorBox plan!

---

## 📄 License

This project is open-source and available under the **MIT License**.