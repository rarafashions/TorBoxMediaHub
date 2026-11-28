# Catatan Versi dan Pengembangan — TorBox Media Hub

**Versi saat ini:** `v1.3DK`
**Tanggal status:** 28 November 2026
**Baseline tes:** `python -m pytest tests -q --no-header` → **528 passed, 129 subtests passed** (0 gagal)
**Harness UI:** jsdom `v2_app_check.js` → `ALL CHECKS PASSED`

Dokumen ini mencatat riwayat versi, keputusan teknis yang mengikat, status pengembangan, dan prosedur rilis. Untuk panduan pemakaian lihat [`DOKUMENTASI_APLIKASI.md`](DOKUMENTASI_APLIKASI.md).

---

## 1. Skema versi

- `APP_VERSION` di `app.py` adalah satu-satunya sumber versi; template (`index.html`, `home_v2.html`, `roxxy.html`) merender nilai itu.
- Format: `v1.3D<letter>` untuk increment pengembangan setelah `v1.1`.
- Versi dinaikkan juga saat perubahan `static/ui/*` agar cache JS/CSS lama tidak dipakai.
- Increment tidak selalu berarti rilis publik; beberapa build internal tidak punya catatan terpisah.
- Semua commit saat ini **lokal, belum di-push**.

---

## 2. Ringkasan status

| Aspek | Status |
|---|---|
| UI v2 (Beranda, Library, Serial, Playlist, Unduhan, Statistik, Pengaturan) | Fungsional, teruji harness |
| Pencarian episode berlapis | Selesai, terverifikasi live |
| Progres unduhan + auto-add + refresh status episode | Selesai, teruji unit + harness; verifikasi live setelah restart |
| Video Feeds lima manifest | Selesai |
| WebDL | Selesai, teruji unit |
| Auto-add lokal D1–D3 | Selesai, dipakai produksi |
| Metadata + klasifikasi | Selesai, audit DB 28 Nov |
| Subtitle 4 lapis + dual language + audit | Selesai |
| Backup/restore/maintenance | Selesai |
| Docker | Belum ada |
| Autentikasi | Belum ada (by design saat ini) |

---

## 3. Riwayat versi

Tanggal mengikuti commit Git. Build yang tidak punya catatan rilis terpisah digabung ke milestone terdekat.

| Versi | Tanggal | Cakupan utama |
|---|---|---|
| `v1.0` | 22 Nov 2026 | Katalog poster, streaming langsung TorBox, tanpa unduhan video |
| `v1.1` | 22 Nov 2026 | Discovery 6 kategori, cache checker multi-indexer, pelacakan episode serial, dukungan multi-OS/pemutar, playlist M3U |
| `v1.3D2` | — | Baseline klasifikasi D2, duplicate detection JAV, subtitle policy |
| `v1.3D3` | 27 Nov 2026 | Pipeline D3 direct-to-Library, guard keamanan, pembersihan resource, klasifikasi eksplisit |
| `v1.3D4` | 27 Nov 2026 | WebDL halaman provider, auto-add lokal + TorBox, hint metadata/subtitle persisten, akselerasi aria2c/yt-dlp, tombol coba ulang |
| `v1.3D5` | 27 Nov 2026 | Deteksi Indo pada auto-add, reklasifikasi aman, `classifier_version`, badge IN LIBRARY lintas kategori |
| `v1.3D8` | 27 Nov 2026 | Addon Donghua kedua, UI season/episode di Video Feeds, payload episodic D3 |
| `v1.3D9` | 27 Nov 2026 | Lima manifest (JAV/English Adult/OnlyFans/Indonesia Movies/Donghua), kategori `indo_movie`, auto-analisis stream, filter katalog vs genre |
| `v1.3DF` | 28 Nov 2026 | View Pengaturan v2, filter Needs Review, sorotan tanpa "Putar Episode", 6 panel settings |
| `v1.3DG` | 28 Nov 2026 | Pencarian episode berlapis (TVMaze, TPB/EZTV/Torrentio/Nyaa/NZBIndex/NZBGeek/Roxxy), seed/leech, cache batch |
| `v1.3DH` | 28 Nov 2026 | Progres unduhan di panel Serial, endpoint peek `download-status`, auto-add + refresh status episode, series hint |
| `v1.3DI` | 28 Nov 2026 | Modal kartu English Adult ala Roxxy: sumber stream langsung, analisis otomatis, unduhan lokal, monitoring unduhan + badge Library |
| `v1.3DJ` | 28 Nov 2026 | Koreksi A6: monitor unduhan di atas katalog, tata letak dua kolom, cover/sinopsis/durasi dari halaman provider |
| `v1.3DK` | 28 Nov 2026 | Header English Adult dipadatkan: sumber + mode satu baris, Tempel URL bisa dilipat, status menyatu dengan grid |

> Build `D6`, `D7`, dan `DA`–`DE` tidak memiliki entri rilis terpisah di repo; perubahan pada rentang itu tercakup dalam audit DB, perbaikan metadata/klasifikasi, dan pembangunan shell v2.

---

## 4. Catatan per increment penting

### 4.1 `v1.3D3` — fondasi keamanan dan pipeline

- Guard tulis lintas situs (`Origin`/`Referer`/`Sec-Fetch-Site`).
- Guard SSRF poster per hop, redirect manual, batas ukuran.
- Guard path unduhan lokal.
- Pembersihan resource/socket dan logging file.
- Pipeline D1 (validasi), D2 (klasifikasi), D3 (registrasi Library).

### 4.2 `v1.3D4` — WebDL dan auto-add

- WebDL hanya menerima URL halaman publik; provider dipetakan di `webdl_resolver.py`.
- Dedupe 15 menit, batas 60 submit/jam, terjemahan errorTorBox.
- Unduhan lokal dan TorBox otomatis masuk Library.
- Snapshot subtitle Roxxy disimpan dan dipasang saat registrasi.
- Hint `torbox_import_hints` bertahan setelah restart.
- aria2c 4 koneksi untuk file langsung; yt-dlp untuk HLS/DASH dan fallback.
- Retry dengan refresh tautan saat stream kedaluwarsa.

### 4.3 `v1.3D5` — klasifikasi Indo dan reklasifikasi aman

- `classify_indo` menerima bukti nama file/folder, sufiks (`-nya`, `-ku`, `-mu`), dan kamus diperluas.
- Ambang dua sinyal pendukung.
- Migrasi `tbh_v1_3d5_multicat5_indo`, `CLASSIFIER_VERSION = "d2-multicat-5"`.
- Reklasifikasi tidak menurunkan `indo`/adult/`indo_movie`/`donghua`; lock/manual dihormati.
- Badge IN LIBRARY mencocokkan kode lalu judul ternormalisasi; serial tidak menandai semua episode.

### 4.4 `v1.3D8` — Donghua dan episode

- Dua manifest (dewasa + Donghua) dengan token terpisah.
- `/api/roxxy/meta` membangun `meta.series`: season, urutan episode numerik, label `E<n>`/`S<nn>·E<n>`, `default_episode_id`.
- Badge per episode; payload unduhan berisi `series_title`, `season_num`, `episode_num`.
- UI episode bergaya Netflix/Prime.

### 4.5 `v1.3D9` — lima manifest dan Indonesia Movies

- Lima slot manifest dengan alias lama tetap dibaca.
- Kategori per sumber: OnlyFans/English Adult → `english_adult`, Donghua → `donghua`, Indonesia Movies → `indo_movie`.
- `indo_movie` tidak di-enrich otomatis dan tidak masuk pencarian subtitle otomatis.
- Analisis stream otomatis untuk semua sumber; opsi cached/uncached/NZB/lokal.
- Katalog yang tidak mendukung `search` tidak dipanggil saat "Cari semua katalog".

### 4.6 `v1.3DF` — UI v2 lengkap

- Tujuh tab v2 fungsional.
- Sorotan serial hanya **Lihat Episode** (tanpa Putar Episode).
- Filter **Status klasifikasi** (Needs Review/classified/manual).
- View **Pengaturan**: API key masked, pemutar, folder scan, subtitle, lingkungan, database.
- Aksi destruktif memakai konfirmasi; operasi DB menyegarkan bootstrap diam-diam.

### 4.7 `v1.3DG` — pencarian episode berlapis

- Endpoint `POST /api/series/episode-search`.
- Judul TV memakai TVMaze (cache 6 jam); anime/donghua memakai judul library.
- Sumber: TorBox cache, TPB, EZTV, Torrentio, Nyaa, NZBIndex, NZBGeek, Roxxy.
- Dedupe `(kind, hash, episode)`, urutan cached → seeders → kualitas → ukuran.
- Cache NZB `null` → UI `CACHE ?` (jujur, bukan klaim palsu).
- Batas 24 episode; setting `nzbgeek_api_key`.
- Verifikasi live: "Dark" S1E1 → 27 rilis/9 cached; "Dating Naked UK" S2E5-6 → 7 rilis/5 cached; "Battle Through The Heavens" → Nyaa 4K seed 22/24 + Roxxy 3 meta/16 stream.

### 4.8 `v1.3DH` — progres unduhan dan auto-refresh

- Tray **Unduhan episode** di panel Serial: progress bar, persen, state, pill `IMPORT`.
- Endpoint `GET /api/series/download-status?series_title=&season=` membaca monitor dengan `status(consume_imported=False)` (peek), TTL `recently_imported` 900 detik, frontend dedupe per `key`.
- Hanya field aman yang dikembalikan; `replace_media_id`/metadata tidak dikirim.
- `+ TorBox` mengirim `episode`; monitor menyimpan `episode` + hint `series` (`title`, `season`, `episode`).
- `_apply_series_hint()` memaku nomor hanya untuk unduhan **satu file video**; season pack tetap parsing per file.
- Setelah impor: toast, `loadAll({silent:true})`, `loadStatus(activeSeason)`, rilis episode dibuang, baris menjadi **Putar**, completeness naik.
- Poll 5 detik mulai saat add, berhenti saat panel ditutup/tab hidden.
- Tes baru 8: peek vs consume + TTL, filter serial/season case-insensitive, field aman, episode diteruskan, fallback input invalid, `_apply_series_hint`.

### 4.9 `v1.3DI` — modal English Adult + monitoring unduhan

- `adult_streams.py` mengekstrak URL media yang **sudah dipublikasikan halaman provider** (bukan menebak): Eporner embed `gvideo`, XVideos/XNXX `embedframe` (SD MP4 + HLS), YouJizz `dataEncodings`, XXXYMovies/SunPorno `flashvars.video_url`, Pornhub `mediaDefinitions`.
- XHamster/YouPorn/SpankBang tidak diambil: `streams_unsupported` dengan pesan jujur, tanpa request tambahan.
- `POST /api/adult/streams` menerima `{url}` atau `{provider,id}`; cache 10 menit; hanya `https://`, tanpa kredensial, host privat/localhost ditolak.
- Id URL tempel diambil dari path halaman karena `adult_discovery.link()` memakai id hash.
- Analisis memakai `/api/roxxy/analyze` (stateless, 3 worker); unduhan lokal memakai `/api/roxxy/local/start` dengan `meta._roxxySource = "english_adult"`; `_roxxyType` sengaja kosong.
- `GET /api/adult/download-status` (peek, `consume_imported=False`) dan `GET /api/adult/local-status` (hanya job adult, field tampilan) memberi modal + panel Download Center progres 4 detik.
- Modal menampilkan badge **IN LIBRARY**, chip metadata, WebDL + catatan dukungan, Status TorBox Cloud, dan Pilihan Sumber Stream; kartu bisa diklik/Enter, Esc menutup.
- Poster katalog ikut dikirim saat submit agar impor tidak tanpa poster.
- Tes baru 23: 5 parser + normalisasi URL/file + cache/breaker + rute streams + filter monitor + kontrak UI statis.

### 4.10 `v1.3DJ` — koreksi tata letak + metadata halaman provider

- **Monitor unduhan** dipindah ke atas seluruh panel adult: selalu tampil (empty state, bukan `hidden`), progres/impor terlihat saat modal ditutup, dan baris hasil impor bertahan 10 menit setelah toast.
- Katalog dipecah dua kolom (`xl:grid-cols-[minmax(0,1fr)_320px]`); cooldown/terblokir + tombol **Perbarui** pindah ke panel **Status Sumber** di kanan, bukan strip panjang di bawah katalog.
- **Cover, sinopsis, durasi diambil dari halaman provider** (OpenGraph + JSON-LD `VideoObject`); TPDB hanya mengisi kekosongan. XVideos/XNXX membaca halaman asli karena `embedframe` hanya memberi kartu generik; Eporner sebaliknya (embed memuat kartu, halaman asli berupa shell). Judul generik (age wall/embed/bot check) dibuang, thumbnail JSON-LD terbesar menang atas `og:image`, poster privat ditolak.
- Metadata provider yang sama ikut dikirim saat **WebDL** dan **unduhan lokal** (`description`, `poster`, `runtime`).
- Tes tambahan 11: page metadata (OG/JSON-LD, judul age-wall, guard poster privat, thumbnail terbesar), merge halaman asli XVideos + fallback saat gagal, kontrak UI statis. Suite **527 passed, 123 subtests**; verifikasi live ulang di port 5052 (Eporner kartu 360p + 770 s, XVideos kartu halaman asli 417 s).

### 4.11 `v1.3DK` — header English Adult dipadatkan

- Header panel adult sebelumnya memakai empat baris penuh (judul, sumber, mode, pencarian, tempel URL) sehingga katalog terdorong ke bawah.
- Sekarang **dua baris**: (1) badge 18+, judul **Katalog Adult**, dropdown sumber, dan tombol mode Terbaru/Populer/Rating; (2) kolom pencarian dengan tombol **Cari**, **Reset**, dan **Tempel URL**.
- Baris tempel URL **dilipat** di balik tombol `Tempel URL` (`toggleAdultPasteRow`, `aria-expanded`/`aria-controls`, fokus otomatis ke input); tombol kirimnya bernama **Buka**.
- Status pemuatan dan jumlah item menyatu di satu baris kecil di dalam kartu header, menggantikan baris terpisah di atas grid.
- Catatan 9 situs + SpankBang/SunPorno dipindah ke panel **Status Sumber** di kanan agar tidak menambah tinggi header.
- Tes UI +1 (kontrak elemen header ringkas), suite **528 passed, 129 subtests**.

---

## 5. Keputusan yang mengikat

Keputusan berikut sudah diambil dan tidak boleh diubah tanpa persetujuan eksplisit pengguna:

1. **Pencarian subtitle tetap dijalankan untuk `english_adult`.** Audio non-Inggris (mis. Czech) tetap membutuhkan subtitle; persentase rendah diterima.
2. **Duplikat serial tidak di-hide.** Tampilkan semua, urutkan salinan terbaik per episode berdasarkan resolusi lalu ukuran file.
3. **Film tanpa TMDb/non-JAV/tanpa keyword Indo → `english_adult`.** Bukan `general_movie`.
4. **Discovery hanya empat kategori** (Movies, TV Shows, Anime, Donghua). JAV/English Adult/Indo ada di Video Feeds.
5. **JAV tidak dikunci menurut manifest.** JAV baru masuk kategori `jav` bila kode produksi tervalidasi; tanpa kode mengikuti bukti adult/uncategorized.
6. **`indo_movie` non-adult** dan tidak di-enrich/subtitle otomatis.
7. **Unduhan dari panel Serial lewat TorBox** (`POST /api/torbox/add-cached`); unduhan lokal tetap jalur Roxxy + D3.
8. **Monitor progres memakai peek** agar view Unduhan dan panel Serial tidak saling merebut event.
9. **Series hint hanya untuk single-video.** Season pack tidak boleh dipaku ke satu episode.
10. **v2 tanpa framework/bundler/npm/Tailwind.** Hanya `static/ui/*` + `templates/home_v2.html`.
11. **Route lama tidak diubah.** `/`, `/v2`, `/roxxy`, `/api/*` dipertahankan; endpoint baru ditambahkan.
12. **Label UI berbahasa Indonesia** dan mengutamakan fungsi di atas estetika.
13. **Secret tidak pernah tampil mentah.** API key, token manifest, dan signed stream URL tidak dikirim ke browser.
14. **Restart hanya saat antrean 0 dan task idle.** Jangan mengganggu unduhan/antrean aktif.
15. **Commit lokal tanpa push** sampai pengguna meminta.
16. **Tes konservatif.** Jangan mengubah ekspektasi tes lama hanya agar suite hijau; baseline wajib tetap hijau.
17. **Stream lokal English Adult diambil dari halaman provider**, bukan ekstraksi generik yt-dlp (diuji gagal untuk Eporner/XVideos), dan tidak mengubah `roxxy_module.py` maupun perilaku `/roxxy`.
18. **Sumber adult memakai `_roxxySource = "english_adult"` dan tidak mengisi `_roxxyType`**, agar `_refresh_stream_url` tidak memanggil manifest Roxxy dengan id provider kita.
19. **Metadata kartu adult berasal dari halaman provider** (judul, cover, sinopsis, durasi). TPDB hanya mengisi kekosongan; jangan menimpa cover/sinopsis provider dengan tebakan TPDB.

---

## 6. Arsitektur saat ini

| Modul | Tanggung jawab |
|---|---|
| `app.py` | Flask, API, Discovery, cache checker, monitor TorBox, impor, task manager, playlist, settings, DB routes |
| `database_manager.py` | SQLite WAL, skema, migrasi, masking secret, backup, statistik, klasifikasi persist |
| `metadata_engine.py` | Metadata TMDb/TVMaze/AniList/JAVDatabase/TPDB, poster, runtime `ffprobe`, parser judul |
| `classification_engine.py` | Klasifikasi deterministik tanpa network, atribut, kode JAV, verdict |
| `indo_keyword_engine.py` | Kamus dan skor sinyal Indonesia |
| `subtitle_engine.py` | Empat lapis subtitle, dual language, track provider |
| `dual_subtitle_engine.py` | Resolusi dua track subtitle untuk pemutar |
| `subtitle_quality_audit.py` | Audit kualitas subtitle |
| `subtitle_match_validator.py` | Validasi kecocokan subtitle |
| `roxxy_module.py` | Lima manifest, katalog, meta, stream, analisis, cache check |
| `roxxy_download_engine.py` | Unduhan lokal, queue, D1–D3, registrasi Library |
| `webdl_resolver.py` | Pemetaan URL halaman WebDL dan validasi host |
| `adult_providers.py` | Registry 10 sumber English Adult, allowlist host, mode katalog/manual |
| `adult_discovery.py` | Katalog/pencarian adult, cache + breaker, tempel URL, detail TPDB |
| `adult_streams.py` | Ekstraksi URL stream langsung dari halaman provider |
| `platform_support.py` | Deteksi OS, executable, direktori data |
| `static/ui/*` | UI v2 tanpa framework |
| `templates/index.html` | UI lama |
| `templates/roxxy.html` | Video Feeds |
| `templates/home_v2.html` | Shell UI v2 |

---

## 7. Kualitas dan pengujian

### Baseline

```bash
python -m py_compile app.py roxxy_module.py database_manager.py
python -m pytest tests -q --no-header
```

Hasil acuan: **527 passed, 123 subtests passed**.

### Cakupan tes (folder `tests/`)

- Klasifikasi: engine, database, integrasi, persistensi, normalisasi, adult lexicon, fallback provider.
- Subtitle: dual engine, retrieval, language provider, orkestrasi, pemutar.
- Roxxy: sumber, serial, library match, guardrail unduhan, retensi queue.
- TorBox: WebDL, add-cached, series download status.
- Keamanan: request guard, poster proxy SSRF, poster reliability.
- Serial: urutan episode, pencarian rilis, download status.
- English Adult: discovery provider/katalog, tempel URL, detail+WebDL, ekstraksi stream modal, monitor unduhan.
- Platform: `platform_support`.

### Aturan tes

- Tes offline; tidak memanggil jaringan nyata kecuali mock yang disengaja.
- Tidak menyentuh database aplikasi.
- Jangan mengubah ekspektasi lama untuk menutupi regresi.
- Tes baru harus menjelaskan perilaku yang dikunci, bukan sekadar menambah angka.

### Harness UI

`v2_app_check.js` (jsdom) memeriksa:

- Tujuh tab v2, filter Needs Review, panel Pengaturan.
- Sorotan tanpa "Putar Episode".
- Alur pencarian episode, `+ TorBox`, serta pemanggilan `GET /api/series/download-status`.
- Tiga/empat input API key kosong dengan badge tersamarkan.
- Backup DB dan auto-detect pemutar.
- Target: `ALL CHECKS PASSED`.

---

## 8. Status data dan audit DB (28 Nov 2026)

Audit DB live menemukan empat masalah utama; semuanya sudah ditangani:

| Masalah | Penanganan |
|---|---|
| Status klasifikasi tidak pernah ditulis | Langkah 3: persist verdict untuk batch/reclassify/D3 |
| `general_movie` menampung konten adult | Langkah 4: 50 baris pindah ke `english_adult`; `general_movie` tanpa cover = 0 |
| Subtitle English Adult | Keputusan: tetap dicari |
| Duplikat serial | Keputusan: jangan di-hide, urutkan kualitas |

Sisa yang belum dikerjakan (bukan blocker):

- Duplikat episode masih ikut di total Library/statistik dan **Putar season**; ada **21 grup** episode ganda (maksimal 3 salinan/episode).
- 15 baris adult dan 5 JAV berkode hasil parse nama file masih **Needs Review** sampai ada verdict baru.
- 1 baris impor baru masih `pending`, diproses batch berikutnya.

Catatan: angka baris berubah mengikuti pertumbuhan Library; jangan menganggapnya angka tetap.

---

## 9. Pending dan roadmap

### Pending teknis

- Verifikasi live fitur `v1.3DH` setelah restart saat antrean kosong.
- Uji manual checklist di `docs/PENDING_TASKS.md` (v2, Discovery, Roxxy, WebDL, reclassify, subtitle audit, backup).
- Rapikan hitungan duplikat di statistik dan **Putar season**.
- Selesaikan sisa Needs Review bila ada bukti baru.

### Roadmap yang belum dikerjakan

- **Docker/Docker Compose** untuk Unraid/Synology/NAS.
- **Endpoint redirect M3U permanen** (`/stream/<unique_key>`) yang membuat tautan baru saat diputar.
- Opsional: autentikasi ringan untuk deployment LAN.
- Opsional: editor manual/klasifikasi/hapus fisik di UI v2.

---

## 10. Alur kerja pengembangan

### Sebelum perubahan berisiko

1. Pastikan antrean TorBox, unduhan lokal, dan task latar kosong.
2. Backup database (`POST /api/db/backup` atau salin `database/torbox_media_hub.db`).
3. Catat baseline tes.

### Setelah perubahan

1. `python -m py_compile app.py roxxy_module.py database_manager.py`
2. `node --check static/ui/home.js` bila UI v2 berubah.
3. `python -m pytest tests -q --no-header`
4. Harness jsdom bila UI v2 berubah.
5. Uji live endpoint baru dengan `curl.exe` (PowerShell NonInteractive).
6. Bump `APP_VERSION` bila perlu.
7. Perbarui `docs/PENDING_TASKS.md`.
8. Commit lokal; jangan push tanpa persetujuan.

### Restart aplikasi

- Hanya saat antrean 0 dan task idle.
- Windows: hentikan proses pemilik port 5050 (`taskkill /PID <pid> /T /F`), lalu jalankan launcher/`python app.py`.
- Verifikasi setelah restart: `/api/bootstrap` mengembalikan versi benar, `/v2` 200, log tanpa traceback.

### Konvensi commit

- Pesan ringkas berbahasa Inggris untuk tipe perubahan (`feat`, `fix`, `docs`, `refactor`, `perf`, `test`).
- Body menjelaskan alasan dan batasan, bukan hanya daftar file.
- Sebut hasil tes di commit untuk perubahan besar.

---

## 11. Checklist rilis

- [ ] `APP_VERSION` dinaikkan.
- [ ] `docs/PENDING_TASKS.md` diperbarui (item selesai + verifikasi manual).
- [ ] `docs/DOKUMENTASI_APLIKASI.md` diperbarui bila fitur/konfigurasi berubah.
- [ ] `README_GITHUB_DRAFT.md` diselaraskan.
- [ ] `python -m py_compile` bersih.
- [ ] Suite tes hijau (425+ passed, 63 subtests).
- [ ] Harness jsdom `ALL CHECKS PASSED`.
- [ ] Uji live `/`, `/v2`, `/roxxy`, endpoint baru.
- [ ] Tidak ada secret/token/signed URL di log, respons, atau dokumen.
- [ ] Backup DB terbaru tersedia.
- [ ] Commit lokal dibuat; push menunggu persetujuan.

---

## 12. Catatan kompatibilitas

- Python 3.10+; hindari sintaks 3.12-only.
- Windows path memakai `ntpath`; jangan asumsikan `/`.
- Android/Termux tidak punya `tkinter`; gunakan fallback browser filesystem.
- macOS memakai path aplikasi (`/Applications/...`), bukan `PATH` saja.
- SQLite WAL: satu penulis; jangan menjalankan dua instance terhadap DB yang sama.
- Jangan mengubah nama route, nama modul, nama tabel, atau key setting lama; gunakan alias/migrasi.
- `roxxy_*` adalah nama internal yang dipertahankan meski UI menampilkan "Video Feeds".