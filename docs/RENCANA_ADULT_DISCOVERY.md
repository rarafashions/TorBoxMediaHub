# Rencana Fase A — English Adult Discovery di Discovery Hub

**Status:** Fase **A1-A2 selesai** (adapter + tes + verifikasi live); A3-A5 masih rencana
**Tanggal:** 28 November 2026
**Versi acuan:** `v1.3DH`
**Baseline tes:** `466 passed, 63 subtests passed`

---

## 0a. Status implementasi (diperbarui setelah A1-A2)

| Fase | Isi | Status |
|---|---|---|
| A1 | Registry provider + mesin (cache, breaker, normalisasi) + Eporner & Pornhub (JSON) + `/api/adult/providers,feed,search` | **selesai**, 22 tes |
| A2 | Adapter HTML: XHamster, XVideos, XNXX, YouPorn, YouJizz, XXXYMovies, SunPorno | **selesai**, 18 tes |
| A3 | SpankBang mode tempel URL + status `blocked` di UI | belum |
| A4 | `/api/adult/detail` (TPDB) + `/api/adult/webdl` + pola halaman WebDL baru | belum |
| A5 | Tab English Adult di `/` lalu `/v2` | belum |

Hasil uji live (instance sementara port 5052, data dir terpisah, tidak mengganggu app di port 5000):

| Sumber | Browse | Pencarian | Catatan |
|---|---|---|---|
| Eporner | 24 | 24 | API JSON resmi |
| Pornhub | 30 | 30 | JSON webmasters |
| XHamster | 7 | 7 | hanya 7 kartu dirender server; sisanya lazy-load |
| XVideos | 27 | 25 | dua tata letak (browse vs search) |
| XNXX | 36 | 36 | dua tata letak; durasi ditulis `14min` |
| YouPorn | 28 | 32 | views `306M` terbaca benar |
| YouJizz | 24 | 23 | thumbnail protocol-relative di-upgrade ke HTTPS |
| XXXYMovies | 48 | 48 | pencarian hanya halaman pertama |
| SunPorno | — | 8 | hanya pencarian, tanpa paginasi |
| SpankBang | — | — | `blocked` (Cloudflare) |

Perbaikan yang lahir dari uji live (bukan dari asumsi):

1. **XVideos/XNXX punya dua markup**: browse memakai `a.thumb-link`, sedangkan halaman search/new memakai bingkai `div.thumb-inside` tanpa kelas itu. Parser kini mencoba keduanya.
2. **`14.2M` tidak boleh dibaca sebagai 14 menit**: parser durasi hanya menerima kata eksplisit (`min`, `mins`, `minute`, `hr`), bukan huruf `m` tunggal.
3. **Thumbnail `//cdn...`** (YouJizz) di-upgrade ke `https:` sebelum validasi.
4. **Tanggal relatif** (`15 hours ago`) tidak diubah menjadi tanggal palsu; field `published` dibiarkan kosong.
5. **Sumber tanpa paginasi** tidak lagi mengulang halaman 1 saat UI meminta halaman 2; hasilnya kosong dan `has_more=false`.

---

## 0. Ringkasan eksekutif

Permintaan: mengembalikan **English Adult Discovery** ke Discovery Hub dengan pola yang meniru Roxxy (katalog per sumber, browse, global search), lalu mengirim **URL halaman asli** ke TorBox WebDL sebagai jalur utama.

Kesimpulan: **layak dan sebagian besar infrastrukturnya sudah ada.**

1. Backend WebDL **masih hidup** di `app.py`: `GET /api/torbox/webdl/info` (baris 4321) dan `POST /api/torbox/webdl/add` (baris 4341), lengkap dengan guard 60 submit/jam, dedupe 900 detik, pemetaan error TorBox, dan auto-monitor. Yang dihapus November lalu hanya UI dan feed-nya.
2. TorBox saat ini melaporkan **10 dari 10 situs** yang Anda sebut aktif untuk WebDL, masing-masing `status: true`, batas **100 GB per link** dan **100 link per hari**.
3. Riset langsung menunjukkan **8 situs bisa diambil otomatis** (2 lewat API JSON resmi/publik, 6 lewat HTML listing), **1 situs butuh strategi khusus** (SunPorno: pencarian jalan, browse tidak), dan **1 situs diblokir Cloudflare** (SpankBang).
4. Tidak perlu mengubah `roxxy_module.py`. Fitur baru berdiri sendiri sebagai modul katalog + endpoint + tab UI.
5. Kategori `english_adult` sudah termasuk `_MANIFEST_LOCKED_CATEGORIES` (app.py:856), jadi hint impor akan mengunci kategori dengan benar tanpa menyentuh classifier.

---

## 1. Visi produk dan alasan fitur ini

Perjalanan aplikasi:

| Fase | Tujuan | Isi |
|---|---|---|
| v0 | Melihat koleksi sendiri | Library lokal + TorBox cloud, metadata, cover, subtitle EN/ID, playlist untuk MPV/VLC |
| v1 | Menambah yang belum dimiliki | Discovery & Search (Movies, TV, Anime, Donghua), cache checker, unduhan ke TorBox |
| v1.1+ | Menutup celah kategori | Roxxy untuk JAV, anime, donghua; auto-add ke Library; klasifikasi; subtitle |
| sekarang | Menutup celah yang tersisa | **English Adult kembali ke Discovery**, karena Discovery lebih tepat untuk katalog besar + pencarian, sementara Roxxy tetap untuk JAV/anime/donghua |

Prinsip yang dipegang:

- **Discovery adalah katalog**, bukan mesin unduh. Tugasnya menemukan judul, menampilkan metadata, dan menyerahkan URL ke TorBox.
- **WebDL adalah jalur utama** untuk adult: satu URL halaman → TorBox mengunduh → monitor mengimpor → Library.
- **Torrent/NZB adalah jalur cadangan**, dipakai hanya jika WebDL tidak tersedia atau pengguna memilihnya.
- **Roxxy tidak diubah.** JAV, anime, donghua, OnlyFans, Indonesia Movies tetap seperti sekarang.
- Pengguna harus selalu tahu apa yang sedang terjadi: loading, sebagian sumber gagal, sumber diblokir, atau TorBox menolak.

---

## 2. Peta alur aplikasi saat ini dan titik integrasi

### 2.1 Alur yang sudah ada

```text
Startup
  platform_support (OS, path)
  database_manager (WAL + migrasi + recovery D3)
  TorboxDownloadMonitor (poll 10 s, TTL impor 900 s)
        │
        ▼
Bootstrap  GET /api/bootstrap
  media_items + playlists + stats + task + env + settings (secret dimask)
        │
        ├── UI lama  /        (Discovery, Library, Settings & DB)
        ├── UI v2    /v2      (Beranda, Library, Serial, Playlist, Unduhan, Statistik, Pengaturan)
        └── Roxxy    /roxxy   (5 manifest, katalog, episode, stream, unduhan lokal)
```

Alur Discovery mainstream (yang dipertahankan):

```text
GET /api/discover?category=...&feed=trending|popular|top_rated|search
  guard 4 kategori (app.py:3391)
  TMDb / AniList  ->  normalisasi kandidat
  cache multi-indexer (Torrentio, YTS, TPB, Nyaa, EZTV, NZBIndex)
  POST /api/discover/search-cache  ->  status CACHED/UNCACHED/CACHE ?
  POST /api/torbox/add-cached      ->  monitor  ->  impor  ->  enrich  ->  subtitle
```

Alur Roxxy (yang ditiru bentuknya, bukan kodenya):

```text
manifest -> catalog -> meta -> streams -> analyze
   ├── magnet  -> /api/torbox/add-cached
   ├── lokal   -> roxxy_download_engine -> D1 -> D2 -> D3 -> media_items
   └── WebDL   -> /api/torbox/webdl/add -> monitor -> impor
```

### 2.2 Titik integrasi fitur baru

| Titik | Perubahan | Tidak menyentuh |
|---|---|---|
| Tab Discovery | tambah tab **English Adult** di `/` dan (nanti) di `/v2` | 4 tab lama, `/api/discover` |
| API baru | `/api/adult/*` | `/api/discover`, `/api/roxxy/*` |
| Modul baru | `adult_discovery.py` + `adult_providers.py` | `roxxy_module.py`, `roxxy_download_engine.py` |
| Submit | memakai `/api/torbox/webdl/add` yang sudah ada | alur add-cached dan monitor |
| Impor | hint `category=english_adult` + metadata TPDB | classifier, D1–D3 Roxxy |
| Pengaturan | tidak ada key baru wajib; TPDB opsional (sudah ada) | settings lain |

---

## 3. Keputusan yang berubah dan yang tetap

### Berubah (keputusan Anda, 28 November 2026)

1. Discovery kembali memiliki kategori **English Adult** sebagai tab kelima, tetapi memakai jalur provider terpisah (bukan TMDb/AniList).
2. **WebDL tube page** kembali menjadi fitur resmi untuk adult, setelah sebelumnya dihapus.
3. Adapter adult yang dulu dihapus (Eporner, XHamster, TPDB dan sejenisnya) dibangun ulang dalam modul baru yang tidak bergantung pada Roxxy.

### Tetap

1. Empat kategori mainstream tetap: `general_movie`, `tv_show`, `anime`, `donghua`.
2. `GET /api/discover` **tetap** mengembalikan 400 untuk kategori lain, termasuk `english_adult`. Jalur adult memakai endpoint sendiri agar kontrak lama dan tes lama tidak berubah.
3. JAV tetap di Roxxy dan **tidak** mendapat tab Discovery.
4. `indo` dan `indo_movie` tidak masuk Discovery adult.
5. Roxxy tidak diubah: manifest, katalog, episode, stream, unduhan lokal, dan D1–D3.
6. Tidak ada unduhan otomatis. Setiap submit adalah aksi eksplisit pengguna.
7. Secret (API key, token manifest, signed URL) tidak pernah tampil atau masuk log.

---

## 4. Hasil riset sumber (diverifikasi 28 November 2026)

### 4.1 TorBox WebDL — kesiapan hoster

Diambil langsung dari `GET https://api.torbox.app/v1/api/webdl/hosters` dengan key yang tersimpan di DB (key tidak dicetak):

| Situs | Domain | Status TorBox | Tipe | Batas per link | Batas harian |
|---|---|---|---|---|---|
| Pornhub | pornhub.com | aktif | stream | 100 GB | 100 |
| Eporner | eporner.com | aktif | hoster | 100 GB | 100 |
| XHamster | xhamster.com | aktif | stream | 100 GB | 100 |
| SpankBang | spankbang.com | aktif | stream | 100 GB | 100 |
| SunPorno | sunporno.com | aktif | stream | 100 GB | 100 |
| XNXX | xnxx.com | aktif | stream | 100 GB | 100 |
| XXXYMovies | xxxymovies.com | aktif | stream | 100 GB | 100 |
| XVideos | xvideos.com | aktif | stream | 100 GB | 100 |
| YouPorn | youporn.com | aktif | stream | 100 GB | 100 |
| YouJizz | youjizz.com | aktif | stream | 100 GB | 100 |

Artinya: **TorBox adalah bagian yang sudah selesai.** Yang perlu dibangun adalah lapisan katalog di sisi kita.

### 4.2 Sumber data per situs

| Situs | Transport | Endpoint / pola | Yang didapat | Katalog | Catatan |
|---|---|---|---|---|---|
| **Eporner** | JSON resmi | `/api/v2/video/search/?query=&order=&per_page=&page=&format=json` | id, title, url halaman, thumb, `length_sec`, views, rate, added | `latest`, `top-weekly`, `top-monthly`, `top-rated`, `longest`, `most-popular`; query kosong = browse (total 100000) | **Paling stabil.** Tanpa key. `url` langsung bisa dikirim ke TorBox |
| **Pornhub** | JSON publik | `/webmasters/search?search=&page=&ordering=` | `video_id`, `url`, title, `default_thumb`, `duration` (`MM:SS`), views, rating, publish_date, tags | query kosong = browse (30 item/halaman) | **Stabil.** `ordering` diterima (efek belum diverifikasi), pagination jelas |
| **XHamster** | HTML | `/search/{q}`, `/newest`, `/best`, `/best/weekly` | kartu dengan `data-video-id`, judul, durasi, thumb CDN | `new`, `popular`, `top` | **Hanya ±7 kartu dirender server**; sisanya lazy-load lewat XHR (terverifikasi A2) |
| **XVideos** | HTML | `/?k={q}`, `/best`, `/new/{n}` | `data-videoid` / `data-id`, href detail, durasi, thumb | `popular`, `new` | Satu parser bisa dipakai XNXX (markup mirip) |
| **XNXX** | HTML | `/search/{q}`, `/best` | `data-eid`, `data-id`, durasi, thumb | `popular` | ID alfanumerik; URL halaman dipakai apa adanya |
| **YouPorn** | HTML | `/search/?query={q}`, `/top_rated/`, `/most_viewed/`, `/most_favorited/` | `data-video-id` (±32), `/watch/<id>/<slug>` | `top_rated`, `most_viewed`, `most_favorited` | Halaman besar (±500 KB); perlu batas ukuran |
| **YouJizz** | HTML | `/search/{q}-{page}.html`, `/most-popular/{n}.html`, `/newest-clips/`, `/top-rated-month/`, `/top-rated-week/`, `/trending/` | `data-video-id` (24), JSON-LD, durasi, thumb | 5 mode | **Paling kaya**; punya JSON-LD sebagai fallback |
| **SunPorno** | HTML | `/search/{q}/` | href `/v/<id>/...`, durasi | **pencarian saja** | Browse (`/top/`, `/latest/`) 404 atau dimuat lewat JS |
| **XXXYMovies** | HTML | `/search/{q}/`, `/latest-updates/{n}/` | href `/<id>/<slug>/`, durasi, thumb | `latest` | Markup sederhana |
| **SpankBang** | — | semua jalur | **403 Cloudflare** (`Just a moment...`) | — | Tidak bisa diambil dengan HTTP biasa |

### 4.3 Metadata pihak ketiga

| Sumber | Fungsi | Biaya | Catatan |
|---|---|---|---|
| **TPDB** (`api.theporndb.net`) | poster/background, studio, performer, tag, tanggal, durasi, rating | paket gratis dengan key | Sudah dipakai `metadata_engine._fetch_tpdb_metadata`; tanpa key → Cloudflare 403. Cache 24 jam wajib |
| **JAVDatabase** | kode JAV | gratis | Tidak dipakai di fase ini (JAV tetap Roxxy) |
| API pihak ketiga lain (RapidAPI dan sejenis) | pencarian tube | berbayar/kuota kecil | Tidak dipakai kecuali ada situs yang benar-benar tertutup |

Kesimpulan riset: **tidak perlu API berbayar** untuk 8 situs. SpankBang ditangani dengan jalur manual.

---

## 5. Arsitektur yang diusulkan

### 5.1 Modul baru

```text
adult_providers.py     spesifikasi deklaratif per situs (host, path, selector, field mapping)
adult_discovery.py     HTTP client, cache, circuit breaker, parser generik, normalisasi, orkestrasi
```

Alasan dipisah: spesifikasi situs berubah paling sering (markup/path), sedangkan mesin (cache, guard, parsing) stabil. Tes parser bisa memakai fixture tanpa menyentuh jaringan.

Tidak ada perubahan pada `roxxy_module.py` dan `roxxy_download_engine.py`.

### 5.2 Alur permintaan

```text
GET /api/adult/providers
  -> daftar 10 situs + status (ok / blocked / search_only / disabled)
  -> digabung dengan status hoster TorBox (cache 24 jam)

GET /api/adult/feed?provider=eporner&mode=popular&page=1
  -> satu provider
GET /api/adult/feed?provider=all&mode=popular&page=1
  -> fan-out paralel (maks 4), hasil digabung, kegagalan per sumber dilaporkan

GET /api/adult/search?q=...&providers=eporner,pornhub,xhamster&page=1
  -> pencarian lintas situs, dedupe, urutkan

GET /api/adult/detail?provider=...&id=...
  -> metadata kartu + TPDB (opsional) + status IN LIBRARY

POST /api/adult/webdl
  -> validasi URL halaman -> /api/torbox/webdl/add (internal)
  -> monitor -> impor -> enrich -> Library (english_adult)
```

### 5.3 Normalisasi item

Satu bentuk untuk semua sumber:

```json
{
  "provider": "xhamster",
  "provider_id": "xham123",
  "page_url": "https://xhamster.com/videos/...",
  "title": "...",
  "thumb": "https://...",
  "duration_sec": 754,
  "views": 120000,
  "rating": 87.5,
  "published": "2026-11-20",
  "site_label": "XHamster",
  "source_kind": "feed",
  "sources": ["xhamster"]
}
```

Aturan:

- `page_url` wajib HTTPS dan host harus cocok dengan allowlist provider.
- Item tanpa `page_url` valid **dibuang**, bukan ditampilkan sebagai kartu mati.
- `provider_id` diambil dari atribut data atau segmen URL; hanya untuk dedupe dan cache.
- Dedupe kunci: `(provider, provider_id)`; lintas situs dedupe memakai `page_url` ternormalisasi.

---

## 6. Kontrak API baru

| Method | Route | Fungsi | Kode error |
|---|---|---|---|
| GET | `/api/adult/providers` | Daftar situs, mode katalog, status, batas TorBox | 200 |
| GET | `/api/adult/feed` | Browse per situs atau gabungan | 400 provider/mode tidak dikenal |
| GET | `/api/adult/search` | Pencarian lintas situs | 400 query kosong |
| GET | `/api/adult/detail` | Metadata + TPDB + IN LIBRARY | 404 item tidak ditemukan |
| POST | `/api/adult/webdl` | Kirim halaman ke TorBox WebDL | 400 URL/ provider, 409 duplikat, 429 batas, 502 TorBox |
| GET | `/api/adult/thumb` | Proxy thumbnail (opsional) | 400 host tidak diizinkan |

Bentuk respons selalu `{"ok": bool, ...}` seperti API lain, ditambah:

```json
{
  "ok": true,
  "items": [],
  "page": 1,
  "has_more": true,
  "providers": [
    {"id": "eporner", "label": "Eporner", "status": "ok", "count": 30, "ms": 240}
  ],
  "warnings": [
    {"provider": "spankbang", "code": "blocked", "message": "Cloudflare menolak permintaan otomatis."}
  ]
}
```

`warnings` adalah kunci desain: kegagalan satu situs tidak boleh menggagalkan seluruh permintaan.

---

## 7. Mode katalog per situs

| Situs | Populer | Terbaru | Rating tertinggi | Pencarian | Halaman |
|---|---|---|---|---|---|
| Eporner | `top-weekly` | `latest` | `top-rated` | ya | ya |
| Pornhub | query kosong | `ordering=newest` (best-effort) | `ordering=mostviewed` | ya | ya |
| XHamster | `/best` | `/newest` | `/best/weekly` | ya | ya |
| XVideos | `/best` | `/new/{n}` | — | ya | ya |
| XNXX | `/best` | — | — | ya | ya |
| YouPorn | `/most_viewed/` | — | `/top_rated/` | ya | ya |
| YouJizz | `/most-popular/{n}.html` | `/newest-clips/` | `/top-rated-month/` | ya | ya |
| SunPorno | — | — | — | ya | ya |
| XXXYMovies | — | `/latest-updates/{n}/` | — | ya | ya |
| SpankBang | — | — | — | manual | — |

Definisi "favorit" pada rencana ini diartikan sebagai **Top Rated / Best of site**. Bila yang Anda maksud adalah daftar favorit pribadi (disimpan per item), itu fitur terpisah dan butuh tabel baru; lihat bagian 12.

---

## 8. Cache, rate limit, dan ketahanan

| Mekanisme | Nilai | Alasan |
|---|---|---|
| Cache list | 10 menit per `(provider, mode, query, page)` | Sama seperti Roxxy; menghindari hit berulang saat UI scroll |
| Cache detail/TPDB | 24 jam per `(provider, id)` | Kuota TPDB terbatas |
| Cache status hoster TorBox | 24 jam | Endpoint TorBox sudah mahal |
| Konkurensi global | 4 permintaan | Cukup cepat, tidak menyerang situs |
| Jarak antar-request per situs | minimal 1,5 detik | Sopan terhadap sumber |
| Timeout | connect 4 s, read 8 s | UI tetap responsif |
| Retry | 1× hanya untuk 502/503/timeout, dengan jitter | Menghindari badai |
| Ukuran respons | maks 3 MB | YouPorn ±500 KB; sisanya jauh di bawah |
| Circuit breaker | 1× Cloudflare atau 3 gagal berturut → cooldown 15 menit | Mencegah spam ke situs yang memblokir |
| Dedupe submit | 900 detik per URL (sudah ada) | Mencegah dobel kirim |
| Batas submit | 60/jam (sudah ada) | Sesuai kapasitas TorBox |

Status breaker ditampilkan di UI, bukan hanya di log.

---

## 9. Error handling dan notifikasi pengguna

### 9.1 Taksonomi

| Kode | Arti | Pesan UI (Indonesia) | Aksi |
|---|---|---|---|
| `ok` | normal | — | — |
| `blocked` | Cloudflare/403 | "SpankBang memblokir permintaan otomatis. Buka di browser lalu tempel URL-nya." | tombol Tempel URL |
| `rate_limited` | 429 | "XHamster membatasi permintaan. Coba lagi dalam beberapa menit." | tombol Coba lagi setelah cooldown |
| `timeout` | tidak merespons | "YouPorn tidak merespons dalam 8 detik." | Coba lagi |
| `parse_error` | markup berubah / 0 kartu | "Struktur halaman XVideos berubah; hasil dilewati." | log + laporan |
| `disabled` | sengaja dimatikan | "Sumber ini belum diaktifkan." | — |
| `torbox_unsupported` | TorBox menolak | "TorBox tidak mendukung link ini sebagai WebDL." | fallback torrent/NZB |
| `torbox_limit` | kuota/plan | "Batas WebDL TorBox tercapai atau plan tidak mendukung." | hapus item lama |

### 9.2 Aturan notifikasi

- **Partial success bukan error.** Tampilkan "24 hasil dari 4 situs; 1 situs dilewati" dengan daftar sumber di bawah grid.
- **Satu toast per aksi pengguna**, bukan per sumber. Kegagalan per sumber tampil inline di baris status.
- **Jangan tampilkan HTML mentah, stack trace, API key, atau URL dengan token.** Semua pesan melewati sanitizer yang sudah ada (`_safe_error` pola Roxxy).
- **Sukses WebDL**: "Dikirim ke TorBox WebDL — auto-monitoring aktif." Setelah impor: "Episode/film masuk Library" lewat mekanisme monitor yang sudah ada.
- **Loading**: skeleton kartu + label "Menghubungi 3 situs...", bukan spinner tanpa konteks.
- **Empty state** membedakan: query tidak ada hasil vs semua sumber gagal vs semua sumber diblokir.

---

## 10. Keamanan

| Risiko | Mitigasi |
|---|---|
| SSRF lewat parameter provider/URL | Tidak ada URL yang di-fetch dari input pengguna. URL dibangun dari spesifikasi internal dan divalidasi terhadap allowlist host |
| Redirect ke host jahat | Redirect manual, maksimal 2 hop, setiap hop divalidasi |
| Thumbnail jahat | Proxy hanya untuk allowlist CDN; tanpa cookie; batas 5 MB; `Content-Type` harus gambar |
| Endpoint dipakai sebagai proxy umum | `/api/adult/thumb` menolak host di luar allowlist; tanpa parameter bebas |
| WebDL ke hoster tak didukung | Preflight memakai daftar hoster TorBox; hanya 10 domain NSFW yang diizinkan |
| Kebocoran API key | Key hanya dibaca server; tidak pernah dikirim ke browser/log |
| Beban berlebih | Cache, breaker, batas konkurensi, batas ukuran |
| Konten 18+ tanpa konfirmasi | Tab adult memakai gerbang "Saya 18+" per sesi (localStorage), tanpa mengubah Library |

Catatan hukum: aplikasi tidak menyimpan atau mendistribusikan video; hanya URL halaman yang dikirim ke akun TorBox pengguna. Tanggung jawab kepatuhan tetap pada pengguna.

---

## 11. UI dan UX

### 11.1 Discovery lama (`/`)

Tab baru **English Adult**:

```text
[ Movies ] [ TV Shows ] [ Anime ] [ Donghua ] [ English Adult ]

Sumber:  (•) Semua   ( ) Eporner   ( ) Pornhub   ...
Katalog: [ Populer v ]   Cari: [___________]  [ Cari ]

┌─────────────┬─────────────┬─────────────┐
│  thumb 16:9 │  thumb      │  thumb      │
│  Judul      │  Judul      │  Judul      │
│  12:34 · 1.2M views      │  [IN LIBRARY]│
│  [Kirim WebDL] [Detail]  │
└─────────────┴─────────────┴─────────────┘

24 hasil dari 4 situs · 1 situs dilewati (SpankBang diblokir)
[Muat berikutnya]
```

Detail (drawer):

- Poster/background TPDB bila ada.
- Studio, performer, tag, tanggal, durasi, rating.
- Tombol utama **Kirim ke TorBox WebDL**.
- Tombol sekunder **Cari torrent/NZB** (fase berikutnya).
- **Tempel URL** untuk sumber yang diblokir; URL divalidasi sebelum dikirim.

### 11.2 UI v2

Tahap 1 memindahkan Discovery ke v2 dengan segmented control lima kategori, memakai komponen grid yang sudah ada (`static/ui/*`). Tidak menambah framework.

### 11.3 Aksesibilitas

- Setiap kartu adalah `button`/`a` dengan label jelas.
- Status memakai teks + ikon, bukan warna saja.
- Fokus keyboard terlihat; Escape menutup drawer.
- Gambar `loading="lazy"` dan `referrerpolicy="no-referrer"` untuk mengurangi hotlink block.

---

## 12. Database

Fase A **tidak memerlukan migrasi**. Semua cache bersifat in-memory seperti Roxxy.

Opsional fase berikutnya (perlu persetujuan):

| Tabel | Isi | Alasan |
|---|---|---|
| `adult_favorites` | provider, provider_id, page_url, judul, thumb, dibuat | Bila "favorit" berarti daftar pribadi |
| `adult_search_history` | query, waktu | Riwayat pencarian, bisa dimatikan |

Rekomendasi: tunda sampai fitur inti stabil.

---

## 13. Pengujian

| Jenis | Isi |
|---|---|
| Unit parser | Fixture HTML/JSON **sintetis** (judul diganti "Sample 1", struktur asli dipertahankan) untuk 8 situs |
| Unit cache | TTL, eviction, hit/miss, key per halaman |
| Unit breaker | 1× Cloudflare → blocked; 3 gagal → cooldown; pulih setelah waktu |
| Unit validasi URL | Host allowlist, scheme, port, kredensial, redirect lintas host |
| Unit normalisasi | Dedupe `(provider, id)`, durasi `HH:MM:SS`/detik, rating 0–100 → 0–10 |
| Integrasi | `/api/adult/*` dengan `requests` di-mock; tidak ada jaringan nyata |
| WebDL | Payload ke `/api/torbox/webdl/add` benar (category, metadata, poster), dedupe, 429 |
| Regresi | `/api/discover` tetap 400 untuk `english_adult`; suite lama tetap hijau |
| Harness | jsdom: tab adult, provider chip, partial warning, tombol Kirim WebDL |

Aturan: fixture tidak boleh memuat judul/konten eksplisit asli, dan tidak ada tes yang memanggil situs dewasa sungguhan.

---

## 14. Urutan implementasi

### A1 — Fondasi + dua sumber JSON (commit pertama)

- `adult_providers.py`: spesifikasi Eporner + Pornhub.
- `adult_discovery.py`: HTTP client, cache, breaker, normalisasi, dedupe, orkestrasi.
- Endpoint: `/api/adult/providers`, `/api/adult/feed`, `/api/adult/search`.
- Tes unit + integrasi (mock).
- **Kriteria terima:** browse dan search Eporner/Pornhub mengembalikan item valid; provider lain berstatus `disabled`; suite tetap 425+ hijau.

### A2 — Enam sumber HTML

- XHamster, XVideos, XNXX, YouPorn, YouJizz, XXXYMovies.
- Parser generik berbasis atribut data + fallback JSON-LD.
- Laporan `parse_yield` per sumber; breaker aktif.
- **Kriteria terima:** minimal 6 situs mengembalikan ≥ 20 kartu per halaman pada tes manual; fixture hijau.

### A3 — SunPorno + SpankBang

- SunPorno: pencarian saja, `search_only` di UI.
- SpankBang: status `blocked`, mode manual (tempel URL) yang tervalidasi.
- **Kriteria terima:** UI jujur soal keterbatasan; tidak ada error berulang ke Cloudflare.

### A4 — WebDL + metadata + notifikasi

- `/api/adult/detail` dengan TPDB (cache 24 jam).
- `/api/adult/webdl`: preflight hoster, kategori `english_adult`, hint metadata/poster, dedupe, 429.
- Tambah pola halaman di `webdl_resolver.py` untuk 5 situs yang belum ada (spankbang, sunporno, xnxx, youporn, xxxymovies) sebagai fallback.
- **Kriteria terima:** submit nyata berhasil, monitor mengimpor, item muncul di Library dengan kategori `english_adult`.

### A5 — UI

- Tab di `/` (legacy) lebih dulu, karena Discovery hidup di sana.
- Lalu port ke `/v2` bersama Tahap 1.

---

## 15. Hubungan dengan Tahap 1–3 (permintaan sebelumnya)

| Tahap | Isi | Ketergantungan |
|---|---|---|
| **1** | Discovery di v2 (mainstream + adult), hapus fisik, edit manual + klasifikasi, poster manual | A1–A4 menyuplai API adult |
| **2** | Audit subtitle + filter kualitas, remote M3U per item, scope playlist | Tidak bergantung adult |
| **3** | `/` diarahkan ke v2, UI lama di `/legacy` | Semua di atas |

Urutan yang disarankan: **A1 → A2 → A3 → A4 → A5(legacy) → Tahap 1 (v2 Discovery + kurasi) → Tahap 2 → Tahap 3.** Dengan begitu Discovery adult sudah bisa dipakai sebelum wajah v2 diubah.

---

## 16. Risiko dan mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Markup situs berubah | Parser 0 hasil | Parser berbasis atribut + `parse_yield` + fixture + mudah diperbaiki di satu file spesifikasi |
| Cloudflare makin agresif | Situs tak bisa diakses | Status `blocked`, mode manual, breaker |
| Situs memblokir IP pengguna | Semua sumber gagal | Pesan jelas + jalur manual + fallback torrent/NZB |
| Kuota TPDB habis | Metadata hilang | Cache 24 jam; metadata kartu tetap dipakai |
| TorBox mengubah dukungan hoster | Submit gagal | Preflight `/webdl/hosters`; pesan `torbox_unsupported` |
| Beban berlebih ke situs | Blokir permanen | Rate limit + cache + konkurensi 4 |
| Konten eksplisit masuk repo | Masalah distribusi | Fixture sintetis; tidak ada gambar/titel asli di tes |

---

## 17. Temuan audit pendukung

| Temuan | Bukti |
|---|---|
| Backend WebDL masih lengkap | `app.py:4321`, `app.py:4341` |
| UI WebDL dan feed adult sudah dihapus | `templates/index.html` tidak memuat `webdl` |
| Guard submit & dedupe aktif | `app.py:4220`–`4295` (60/jam, 900 s) |
| Kategori adult terkunci saat impor | `app.py:856`, `roxxy_module.py:80` |
| Discovery guard 4 kategori | `app.py:3391` |
| Monitor in-memory, TTL impor 900 s | `app.py:185`, `app.py:188` |
| Pola halaman WebDL baru mencakup 8 provider | `webdl_resolver.py:21`–`30` |
| TPDB dipanggil dengan Bearer key | `metadata_engine.py:974` |
| Klasifikasi adult tetap hati-hati | `classification_engine.py` (`d2-multicat-5`) |

---

## 18. Pertanyaan terbuka

1. **"Favorit"**: apakah yang dimaksud mode *Top Rated/Best of site*, atau daftar favorit pribadi yang disimpan?
2. **Cakupan situs pertama**: setuju mulai dari Eporner + Pornhub, lalu 6 situs HTML, atau ingin XHamster masuk sejak commit pertama?
3. **Gerbang 18+**: cukup konfirmasi per sesi di tab adult, atau perlu konfirmasi setiap kali membuka tab?
4. **Torrent/NZB fallback**: dibangun bersamaan dengan A4, atau ditunda setelah WebDL stabil?

---

## 19. Definisi selesai Fase A

- Semua situs yang bisa diakses otomatis tampil sebagai katalog dengan browse + search.
- Setiap kartu bisa dikirim ke TorBox WebDL, dan item muncul di Library dengan kategori `english_adult`, poster, dan metadata.
- Sumber yang diblokir atau berubah struktur tidak membuat UI rusak; statusnya jelas.
- Tidak ada perubahan pada Roxxy, tidak ada regresi pada 4 kategori Discovery, dan baseline tes tetap hijau.
- Dokumentasi (`DOKUMENTASI_APLIKASI.md`, `CATATAN_VERSI_PENGEMBANGAN.md`, referensi skill) diperbarui untuk mencatat perubahan keputusan.