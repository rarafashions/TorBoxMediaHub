# 🎬 TorBox Media Hub `v1.0`
### Zero-Download Rich Movie Catalog, Auto-Subtitle & Direct Cloud `.m3u` Streaming for TorBox & Local Media

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Backend-Flask%20%2B%20SQLite%20WAL-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Player](https://img.shields.io/badge/Players-VLC%20%26%20MPV%20M3U-FF8800?logo=vlcmediaplayer&logoColor=white)](https://www.videolan.org/)
[![Platform](https://img.shields.io/badge/OS-Windows%20%7C%20macOS%20%7C%20Linux-6366f1)](#installation--quick-start)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#license)

**TorBox Media Hub** is a fast, lightweight, self-hosted desktop web application that transforms your raw **TorBox Cloud** library (`torrents` & `usenet`) and **local hard drive folders** into a **rich visual movie catalog**—**without downloading a single video file from TorBox to your local disk**.

Instead of consuming terabytes of local storage, TorBox Media Hub indexes your cloud library via the TorBox API, enriches it with high-resolution posters, cast, real video runtimes (`ffprobe`), and 4-tier subtitles, builds curated `.m3u` playlists, and **streams directly from TorBox servers** into **VLC** or **MPV Media Player** on demand.

> 💡 **New to TorBox?** Sign up using our **[TorBox Referral Link](https://torbox.app/subscription?referral=d8962fcc-d39e-43f6-9a8a-f565bab5f809)** to get **7 bonus days free** on your subscription while supporting the continued development of this open-source project!

---

## 🖼️ Preview & Screenshots

| Rich Visual Movie Catalog | Multi-Tier Subtitle Finder | Media Details & Manual Editor |
| :---: | :---: | :---: |
| ![Catalog View](docs/assets/TMH-General-Movies.png) | ![Subtitle Engine](docs/assets/TMH-Subtitle.png) | ![Media Details Card](docs/assets/TMH-Details-Media-Card.png) |

---

## ✨ Why TorBox Media Hub?

Managing dozens of cached torrents, Usenet downloads, and local video files from plain text lists is tedious. **TorBox Media Hub** solves the biggest daily pain points for TorBox desktop users without requiring heavy media server stacks like Plex, Jellyfin, Rclone, or FUSE mounts:

* **Zero Local Disk Downloads for Cloud Media:** TorBox Media Hub **never downloads video files from TorBox to your hard drive**. Your cloud files stay 100% in the TorBox Cloud while appearing locally as a rich, Netflix-style poster catalog—saving massive amounts of disk space.
* **Direct API Cloud Streaming (`requestdl`):** Streams videos directly from TorBox's high-speed CDN to **VLC** or **MPV** via dynamic API stream links generated right when you click Play or build a playlist—eliminating dead links from expired URLs.
* **No More Messy Raw Filenames:** Automatically parses cryptic release tags, classifies content into dedicated categories, detects exact video runtimes, and fetches high-resolution posters, cast, studios, and synopses.
* **Zero-Hassle Subtitles for Direct Streams:** Automatically inspects embedded tracks (`ffprobe`), extracts sidecar `.srt`/`.ass` files bundled inside your TorBox torrents/Usenet archives, or downloads matching subtitles from OpenSubtitles & SubtitleCat—then injects them directly into **VLC** or **MPV**.
* **Dual-Player Native Support (VLC & MPV):** Switch seamlessly between **VLC Media Player** and **MPV** with OS-aware default paths (`Windows`, `macOS`, `Linux`) and automatic external subtitle flags (`--sub-file` / `--sub-files`).
* **Cloud + Local Hybrid Library:** Combine direct-stream TorBox Cloud media and existing local disk folders in one searchable interface, with dedicated workspace tabs (**Unassigned**, **In Playlists**, **All Media**) to track what you haven't curated or watched yet.
* **100% Local & Privacy-First:** Runs entirely on your own machine using SQLite (`WAL` mode). Only lightweight posters, `.srt` subtitles, and `.m3u` playlist text files are cached locally. Your API keys are write-only masked (`••••••••xxxx`) on the server and never exposed in plain text to the browser.

---

## 🚀 Core Features

### 1. Zero-Download Cloud Catalog & Local Disk Scanner
* **Instant TorBox Cloud Metadata Sync (`Sync Cloud`):** Queries `torrents/mylist` and `usenet/mylist` via the TorBox API to catalog all your cloud videos in a single atomic SQLite transaction—**zero video files are downloaded to your disk**, and `requestdl` rate limits are completely avoided during sync.
* **Recursive Local Disk Scanner (`Scan Local`):** Optionally indexes existing local directories for all major video formats (`.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.webm`, `.m4v`, `.ts`, `.m2ts`), probes actual video duration via `ffprobe`, and links local sidecar subtitles automatically.
* **Smart Metadata Protection:** Re-syncing your library safely preserves any metadata, posters, custom runtimes, or categories you have already fetched or manually edited.

### 2. Multi-Source Metadata, Poster & Runtime Engine (`metadata_engine.py`)
Clicking **Enrich Meta** classifies your library using a strict priority pipeline so niche libraries never pollute your mainstream catalog:
1. **JAV (`jav`):** Detects official studio production codes (including `FC2-PPV`, `HEYZO`, `CARIB`, and hundreds of studio prefixes) with anti-false-positive filtering against technical codec/audio tags (`DDP5.1`, `AAC2.0`, `x264`, `AMZN`, etc.), fetching posters, actresses, studios, runtimes, and release dates from **JAVDatabase**.
2. **English Adult (`english_adult`):** Studio and scene-date detection completely isolated from TMDb/AniList, powered by **ThePornDB (TPDB) API** (`/scenes` & `/movies`).
3. **Anime & Donghua (`anime` / `donghua`):** Identifies fansub groups (`[SubsPlease]`, `[Erai-raws]`, etc.), CJK characters, and origin country (`JP` vs `CN`/`TW`) via **AniList GraphQL API** and **TMDb**.
4. **Movies & TV Shows (`general_movie` / `tv_show`):** Accurate title, year, and season/episode (`S01E01`) matching via **TMDb API** (with **TVMaze API** fallback for TV series).
5. **Automatic `ffprobe` Runtime Detection:** Whenever an API doesn't provide a runtime (or for local files & TorBox streams), `ffprobe` automatically probes the video container header to display accurate duration badges (e.g., `1h 58m`) on cards, filters (`Longest Duration`), and detail modals without downloading the file.
6. **Lightweight Local Poster Caching:** Only small cover posters are cached locally in `cache/posters/` using SHA-256 hashes for instant UI rendering.

### 3. 4-Tier Automated Subtitle Engine (`subtitle_engine.py`)
Subtitle discovery runs sequentially from fastest local sources to external providers:
1. **Local Sidecar Detection:** Finds matching `.srt`, `.ass`, `.ssa`, `.vtt`, or `.sub` files in the same directory as local videos.
2. **TorBox Cloud Archive Indexing:** Scans inside your TorBox torrent/Usenet file trees for bundled subtitle files and downloads only the tiny subtitle text file to `cache/subtitles/`.
3. **Embedded Track Inspection (`ffprobe`):** Inspects `.mkv`/`.mp4` container headers on both **local files** and **live TorBox Cloud streams** to detect built-in subtitle tracks.
4. **Public Subtitle Providers:**
   * **JAV:** Searches English/Indonesian subtitles via **SubtitleCat**, with fallback to **OpenSubtitles REST API**.
   * **Movies / TV Shows / Anime / Donghua:** Queries **OpenSubtitles REST API** with `season` and `episode` matching *(automatically skipped for `english_adult`)*.

### 4. Curated `.m3u` Playlists & 1-Click Direct API Streaming
* **Dedicated Workspace Scope Tabs:** Instantly switch between **Unassigned** (media not yet in any playlist), **In Playlists** (grouped cleanly by playlist with 1-click Play & Delete controls), and **All Media**.
* **Dynamic `.m3u` Playlist Builder:** Outputs lightweight `.m3u` playlists in `playlists/` complete with duration (`#EXTINF`), external subtitle injection (`#EXTVLCOPT:sub-file=...`), and fresh `requestdl` direct streaming URLs generated via the TorBox API.
* **1-Click Direct Streaming in VLC & MPV:** Launch individual cloud videos or entire `.m3u` playlists directly into **VLC Media Player** or **MPV** (complete with `--sub-file` / `--sub-files` & `--sub-auto=fuzzy` arguments) right from your browser—streaming straight from TorBox with zero local video downloads.

### 5. Rich Filtering, Inline Detail Modal & Database Maintenance
* **Granular Library Filters:** Filter simultaneously by **Search** (title, JAV code, actress, studio, filename), **Source** (`TorBox Cloud` / `Local Disk`), **Category**, **Metadata Status**, **Subtitle Status**, **Watch Status**, and **Sort Order** (`Newest`, `Title A-Z`, `Release Year`, `Highest Rating`, `Favorite Stars`, `Largest Size`, `Longest Duration`).
* **Interactive Detail & Manual Override Modal:** View full metadata, duration badges, subtitle paths, and playlist assignments—or manually edit the **Title**, **Code ID**, **Category**, **Year**, **Duration (minutes)**, **Studio**, and **Plot**, re-enrich metadata, or fetch subtitles on demand.
* **Parallel Worker Pool & DB Tools:** Powered by `ThreadPoolExecutor(max_workers=4)` with live progress tracking and built-in **SQLite Backup**, **Restore**, **Maintenance (`VACUUM`)**, **Metadata Reset**, and **Full Erase**.

---

## 📂 Project Architecture

```text
TorBoxMediaHub/
├── app.py                  # Flask server (127.0.0.1:5050), REST API, Task Manager, Direct Stream Launcher & M3U Builder
├── database_manager.py     # SQLite WAL management, schema, secret masking, OS player defaults & DB maintenance
├── metadata_engine.py      # Content classifier, title/code parser, API clients, ffprobe duration & poster cache
├── subtitle_engine.py      # 4-tier subtitle engine (Sidecar, TorBox Cloud, ffprobe, SubtitleCat, OpenSubtitles)
├── requirements.txt        # Python dependencies
├── .gitignore              # Excludes local DB, cache, playlists, backups & secrets
├── templates/
│   └── index.html          # Single-Page Web UI (Tailwind CSS + Vanilla JS)
├── static/                 # App icon & branding assets (logo.png, favicon.ico)
├── docs/
│   └── assets/             # Screenshots & demo previews for documentation
├── database/               # Local SQLite catalog database (torbox_media_hub.db) [git-ignored]
├── cache/                  # Cached cover posters & .srt/.ass subtitles only (no video downloads) [git-ignored]
├── playlists/              # Generated .m3u direct-stream playlist files [git-ignored]
└── backups/                # Database backup archives [git-ignored]
```

---

## 🛠️ Installation & Quick Start

### Prerequisites
* **Python 3.10+**
* **VLC Media Player** and/or **MPV Media Player** (for 1-click direct video & `.m3u` playlist streaming)
* **FFmpeg / `ffprobe`** in your system `PATH` *(recommended for automatic video duration detection and embedded subtitle inspection on cloud & local files)*

### 1. Clone the Repository
```bash
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
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
Open your browser and navigate to:
```text
http://127.0.0.1:5050
```

### 4. Configure Your API Keys & Player
1. Open the **Settings & DB** tab in the web interface.
2. Enter your **TorBox API Key** from [TorBox Settings](https://torbox.app/settings) *(Don't have an account yet? [Sign up via our referral link](https://torbox.app/subscription?referral=d8962fcc-d39e-43f6-9a8a-f565bab5f809) to get **7 free bonus days**!)*.
3. Enter your free **TMDb API Key** (and optional **ThePornDB API Key** for English Adult metadata).
4. Choose your **Primary Media Player** (**VLC** or **MPV**), verify or customize its executable path, and optionally add **Local Scan Folders**.

---

## 🔄 Typical Workflow

1. **Sync Cloud Catalog (Zero Video Downloads):** Click **Sync Cloud** to index your TorBox torrents/Usenet library into a visual catalog without downloading any video files to your hard drive (and optionally click **Scan Local** for existing local folders).
2. **Enrich Metadata & Runtimes:** Click **Enrich Meta** to automatically classify titles, probe video durations, and download cover posters in parallel.
3. **Fetch Subtitles:** Click **Find Subtitles** to detect embedded tracks, extract TorBox archive subtitles, or download matching `.srt` files.
4. **Curate & Direct Stream:** Select items in **Unassigned**, add them to a new or existing `.m3u` playlist, and hit **Play** to stream directly from TorBox's API into **VLC** or **MPV** with subtitles loaded.

---

## 🗺️ Roadmap & Planned Enhancements

* [x] **Zero-Download Rich Catalog & Direct API Streaming:** Visual poster catalog with on-demand `requestdl` stream URLs—zero local disk space used for TorBox videos.
* [x] **Cross-Platform VLC & MPV Support:** Built-in player selector with OS default paths (`Windows`, `macOS`, `Linux`) and automatic external subtitle arguments.
* [x] **Automatic `ffprobe` Video Duration Detection:** Real runtime probing for local files and TorBox Cloud streams, plus manual runtime editing in the Detail Modal.
* [ ] **Live HTTP M3U Redirect Endpoint (`/stream/<unique_key>`):** Serve permanent local M3U URLs that dynamically `302 Redirect` to fresh `requestdl` links at playback time.
* [ ] **Docker & Docker Compose Support:** Ready-to-use `Dockerfile` for Unraid, Synology NAS, and home-server deployments.

---

## 🤝 Contributing & Community

Contributions, bug reports, and feature requests are always welcome!
* **Issues & Pull Requests:** Feel free to open an issue or submit a PR on GitHub.
* **Support Development:** If this project improves your TorBox workflow, consider starring ⭐ the repository or using our **[TorBox Referral Link](https://torbox.app/subscription?referral=d8962fcc-d39e-43f6-9a8a-f565bab5f809)** when subscribing or renewing your TorBox plan!

---

## 📄 License

This project is open-source and available under the **MIT License**.