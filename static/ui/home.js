/* ==========================================================================
   TorBox Media Hub — App v2 (vanilla JS, tanpa dependensi)
   Data: GET /api/bootstrap?playlist_scope=all
   Aksi: media, playlist, task, unduhan lokal, TorBox monitor.

   Prinsip:
   - Semua teks dari server dipasang lewat textContent (bukan innerHTML).
   - Satu sumber state; tampilan dirender ulang dari state.
   - Tidak ada URL bertanda tangan / API key yang ditampilkan.
   ========================================================================== */

(function () {
  "use strict";

  /* ------------------------------ State --------------------------------- */

  const state = {
    items: [],
    playlists: [],
    stats: null,
    env: null,
    version: "",
    task: null,
    loading: true,
    error: null,
    view: "home",
    q: "",
    searchIndex: new Map(),
    filters: {
      cat: "all",
      source: "all",
      watched: "all",
      sub: "all",
      meta: "all",
      review: "all",
      sort: "newest",
      grouped: true,
    },
    seriesCat: "all",
    seriesSort: "newest",
    limit: 60,
    selectMode: false,
    selection: new Set(),
    detailId: null,
    downloads: {
      watch: null,
      queue: null,
      library: null,
      error: "",
      loading: false,
      lastLoaded: 0,
    },
    settings: {
      data: null,
      loading: false,
      error: "",
      saving: false,
      busy: "",
      player: "vlc",
      playerPaths: { vlc: "", mpv: "", custom: "" },
      customArgs: "",
      localPaths: "",
      indonesian: true,
      keyDrafts: { torbox: "", tmdb: "", tpdb: "", nzbgeek: "" },
    },
    discover: {
      tab: "mainstream",
      category: "general_movie",
      feed: "trending",
      q: "",
      items: [],
      loading: false,
      error: "",
      loadedKey: "",
      adultApproved: false,
      adultProviders: [],
      adultModes: [],
      adultProvider: "all",
      adultMode: "popular",
      adultQ: "",
      adultItems: [],
      adultWarnings: [],
      adultLoading: false,
      adultError: "",
      adultPage: 1,
      adultHasMore: false,
      adultLoadedKey: "",
    },
  };

  const CATEGORY_LABEL = {
    general_movie: "Film",
    tv_show: "Serial TV",
    anime: "Anime",
    donghua: "Donghua",
    jav: "JAV",
    english_adult: "Adult",
    indo: "Indonesia",
    indo_movie: "Indonesia Movies",
    uncategorized: "Belum Dikategorikan",
  };

  const CATEGORY_ORDER = [
    "general_movie",
    "tv_show",
    "anime",
    "donghua",
    "jav",
    "english_adult",
    "indo",
    "indo_movie",
    "uncategorized",
  ];

  const SERIES_CATEGORIES = new Set(["tv_show", "anime", "donghua"]);

  const SORT_LABEL = {
    newest: "Terbaru",
    oldest: "Terlama",
    title: "Judul A-Z",
    rating: "Rating tertinggi",
    year: "Tahun terbaru",
    size: "Ukuran terbesar",
    episodes: "Episode terbanyak",
    progress: "Progres terendah",
  };

  const VIEW_TITLE = {
    home: "Beranda",
    discover: "Discovery",
    library: "Library",
    series: "Serial",
    playlists: "Playlist",
    downloads: "Unduhan",
    stats: "Statistik",
    settings: "Pengaturan",
  };

  /* --------------------------- DOM helpers ------------------------------ */

  const $ = (sel, root) => (root || document).querySelector(sel);

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) {
      for (const key in props) {
        const val = props[key];
        if (val == null || val === false) continue;
        if (key === "class") node.className = val;
        else if (key === "text") node.textContent = val;
        else if (key === "html") node.innerHTML = val;
        else if (key.startsWith("on") && typeof val === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), val);
        } else if (key === "dataset") {
          Object.assign(node.dataset, val);
        } else {
          node.setAttribute(key, val === true ? "" : val);
        }
      }
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const svg = (path, extra) =>
    `<svg class="btn__svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
      ${extra || ""}>${path}</svg>`;

  const ICON = {
    play: svg('<path d="M8 5v14l11-7z" fill="currentColor" stroke="none"/>'),
    check: svg('<path d="M20 6 9 17l-5-5"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    back: svg('<path d="m15 18-6-6 6-6"/>'),
    chevL: svg('<path d="m15 18-6-6 6-6"/>'),
    chevR: svg('<path d="m9 18 6-6-6-6"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'),
    refresh: svg('<path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/>'),
    trash: svg('<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>'),
    download: svg('<path d="M12 3v12m0 0 4-4m-4 4-4-4M4 19h16"/>'),
    x: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
    list: svg('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'),
    film: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>'),
    tv: svg('<rect x="3" y="7" width="18" height="13" rx="2"/><path d="m8 3 4 4 4-4"/>'),
    activity: svg('<path d="M3 12h4l3 8 4-16 3 8h4"/>'),
    folder: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    star: svg('<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>'),
    external: svg('<path d="M14 3h7v7M21 3l-9 9M10 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>'),
    spark: svg('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>'),
  };

  /* --------------------------- Format helpers --------------------------- */

  function initials(title) {
    const words = String(title || "?")
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2);
    return words.map((w) => w[0].toUpperCase()).join("") || "?";
  }

  function runtimeLabel(minutes) {
    const m = Number(minutes) || 0;
    if (m <= 0) return "";
    const h = Math.floor(m / 60);
    const rest = m % 60;
    return h > 0 ? `${h}j ${rest}m` : `${rest}m`;
  }

  function bytesLabel(bytes) {
    const b = Number(bytes) || 0;
    if (b <= 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = b;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function numberLabel(n) {
    const v = Number(n) || 0;
    try {
      return new Intl.NumberFormat("id-ID").format(v);
    } catch (_) {
      return String(v);
    }
  }

  function dateLabel(value) {
    if (!value) return "";
    const d = new Date(String(value));
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
    try {
      return d.toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric" });
    } catch (_) {
      return String(value).slice(0, 10);
    }
  }

  function catLabel(cat) {
    return CATEGORY_LABEL[cat] || "Lainnya";
  }

  function truthyFlag(value) {
    if (value === true) return true;
    return Number(value) === 1;
  }

  function clampPercent(value) {
    const n = Number(value) || 0;
    return Math.max(0, Math.min(100, n));
  }

  function normalizeProgress(value) {
    let n = Number(value) || 0;
    if (n > 0 && n <= 1) n *= 100;
    return clampPercent(n);
  }

  function pct(part, total) {
    const t = Number(total) || 0;
    if (t <= 0) return 0;
    return clampPercent((Number(part) / t) * 100);
  }

  /* ------------------------------ Toast --------------------------------- */

  let toastHost = null;

  function toast(message, kind) {
    if (!toastHost) {
      toastHost = el("div", { class: "toast-host", role: "status", "aria-live": "polite" });
      document.body.append(toastHost);
    }
    const node = el("div", { class: `toast toast--${kind || "ok"}`, text: message });
    toastHost.append(node);
    setTimeout(() => {
      node.style.transition = "opacity .3s, transform .3s";
      node.style.opacity = "0";
      node.style.transform = "translateY(8px)";
      setTimeout(() => node.remove(), 320);
    }, 3400);
  }

  /* ------------------------------ API ----------------------------------- */

  function logError(err) {
    if (window.console && console.warn) console.warn("[TMH/v2]", err);
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      /* respons non-JSON (mis. 404 teks kosong) */
    }
    if (!res.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || `HTTP ${res.status}`);
    }
    return data;
  }

  function jsonBody(payload) {
    return {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    };
  }

  let loadController = null;

  async function loadAll(options) {
    const opts = options || {};
    if (loadController) loadController.abort();
    const controller = new AbortController();
    loadController = controller;
    if (!opts.silent) {
      state.loading = true;
      state.error = null;
      render();
    }
    try {
      const data = await api("/api/bootstrap?playlist_scope=all", { signal: controller.signal });
      if (controller.signal.aborted) return;
      state.items = Array.isArray(data.items) ? data.items : [];
      state.playlists = Array.isArray(data.playlists) ? data.playlists : [];
      state.stats = data.stats || null;
      state.env = data.env || null;
      state.version = data.version || "";
      state.task = data.task || state.task;
      state.error = null;
      buildSearchIndex();
    } catch (err) {
      if (err.name === "AbortError" || controller.signal.aborted) return;
      logError(err);
      if (!opts.silent) {
        state.error = err.message;
        if (state.items.length) toast(`Gagal menyegarkan katalog: ${err.message}`, "error");
      }
    } finally {
      if (loadController === controller) loadController = null;
      state.loading = false;
      render();
      openFromHash();
      ensureDiscoveryView();
    }
  }

  function buildSearchIndex() {
    state.searchIndex = new Map();
    for (const it of state.items) {
      const parts = [
        it.title,
        it.original_title,
        it.file_name,
        it.series_title,
        it.code_id,
        it.studio,
        it.director,
        it.year,
      ];
      if (Array.isArray(it.genres)) parts.push(...it.genres);
      if (Array.isArray(it.actresses)) parts.push(...it.actresses);
      state.searchIndex.set(
        Number(it.id),
        parts.filter(Boolean).join(" ").toLowerCase()
      );
    }
  }

  async function refreshPlaylists() {
    const data = await api("/api/playlists");
    state.playlists = Array.isArray(data.playlists) ? data.playlists : state.playlists;
    if (data.stats) state.stats = data.stats;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function waitForTask(timeoutMs) {
    const limit = Number(timeoutMs) || 45000;
    const started = Date.now();
    while (Date.now() - started < limit) {
      await sleep(800);
      try {
        const data = await api("/api/task/status");
        if (data && data.task) state.task = data.task;
        if (!data || !data.task || !data.task.running) return data ? data.task : null;
      } catch (err) {
        logError(err);
      }
    }
    return null;
  }

  /* --------------------------- Series helpers ---------------------------- */

  function isSeriesCategory(cat) {
    return SERIES_CATEGORIES.has(cat);
  }

  function isEpisodeItem(item) {
    return (
      isSeriesCategory(item.content_category) &&
      (Boolean(String(item.series_title || "").trim()) || item.episode_num != null)
    );
  }

  function seriesKey(item) {
    return String(item.series_title || item.title || item.file_name || "").trim().toLowerCase();
  }

  // Duplicate episodes from different sources are never hidden; the best copy
  // of the same episode is simply listed first. Resolution comes from the
  // release name, with file size as the tie-breaker.
  const QUALITY_TOKENS = [
    [/\b(2160p|4k|uhd)\b/i, 2160],
    [/\b(1440p|qhd)\b/i, 1440],
    [/\b(1080p|fhd|fullhd)\b/i, 1080],
    [/\b(720p)\b/i, 720],
    [/\b(576p|540p)\b/i, 576],
    [/\b(480p|sd)\b/i, 480],
    [/\b(360p)\b/i, 360],
  ];

  function videoQuality(item) {
    const name = `${item.file_name || ""} ${item.title || ""}`;
    for (const [re, value] of QUALITY_TOKENS) if (re.test(name)) return value;
    return 0;
  }

  function episodeOrder(a, b) {
    return (
      Number(a.season_num || 1) - Number(b.season_num || 1) ||
      Number(a.episode_num || 0) - Number(b.episode_num || 0) ||
      videoQuality(b) - videoQuality(a) ||
      (Number(b.file_size) || 0) - (Number(a.file_size) || 0)
    );
  }

  function groupSeries(items) {
    const map = new Map();
    for (const item of items) {
      if (!isEpisodeItem(item)) continue;
      const key = seriesKey(item);
      if (!key) continue;

      let group = map.get(key);
      if (!group) {
        group = {
          kind: "series",
          key,
          title: String(item.series_title || item.title || item.file_name || "Tanpa Judul").trim(),
          category: item.content_category,
          items: [],
          seasons: new Set(),
          watched: 0,
          watchedCount: 0,
          total: 0,
          posterItem: null,
          year: null,
          rating: 0,
          rating_star: 0,
          plot: "",
          studio: "",
          updated_at: "",
          fileSize: 0,
        };
        map.set(key, group);
      }

      group.items.push(item);
      group.seasons.add(parseInt(item.season_num || 1, 10) || 1);
      if (Number(item.is_watched)) group.watchedCount += 1;
      if (!group.posterItem && item.has_poster) group.posterItem = item;
      if (!group.year && item.year) group.year = item.year;
      if (Number(item.rating) > Number(group.rating)) group.rating = Number(item.rating);
      if (Number(item.rating_star) > Number(group.rating_star)) group.rating_star = Number(item.rating_star);
      if (!group.plot && item.plot) group.plot = item.plot;
      if (!group.studio && item.studio) group.studio = item.studio;
      if (String(item.updated_at || "") > group.updated_at) group.updated_at = String(item.updated_at || "");
      group.fileSize += Number(item.file_size) || 0;
    }

    const groups = Array.from(map.values());
    for (const group of groups) {
      group.items.sort(episodeOrder);
      group.total = group.items.length;
      group.watched = group.watchedCount;
      group.posterItem = group.posterItem || group.items.find((it) => it.has_poster) || group.items[0] || null;
      group.nextEpisode = group.items.find((it) => !Number(it.is_watched)) || null;
    }
    return groups;
  }

  function mixedEntries(items) {
    const groups = groupSeries(items);
    const grouped = new Set();
    for (const g of groups) for (const it of g.items) grouped.add(String(it.id));
    const singles = items.filter((it) => !grouped.has(String(it.id)));
    return [...groups, ...singles];
  }

  function entryTitle(entry) {
    return entry.kind === "series" ? entry.title : entry.title || entry.file_name || "Tanpa Judul";
  }

  function entryCategory(entry) {
    return entry.kind === "series" ? entry.category : entry.content_category;
  }

  function entryStar(entry) {
    return Number(entry.rating_star || 0);
  }

  function entryYear(entry) {
    if (entry.kind === "series") return entry.year || (entry.posterItem && entry.posterItem.year) || null;
    return entry.year || null;
  }

  function entryUpdated(entry) {
    return String(entry.updated_at || "");
  }

  function entrySize(entry) {
    return entry.kind === "series" ? Number(entry.fileSize) || 0 : Number(entry.file_size) || 0;
  }

  function entryWatched(entry) {
    if (entry.kind !== "series") return truthyFlag(entry.is_watched);
    return entry.items.length > 0 && entry.items.every((it) => truthyFlag(it.is_watched));
  }

  function entryIds(entry) {
    return entry.kind === "series" ? entry.items.map((it) => Number(it.id)) : [Number(entry.id)];
  }

  function entrySeasonCount(entry) {
    if (entry.kind !== "series") return 0;
    return entry.seasons.size;
  }

  function entryProgress(entry) {
    if (entry.kind !== "series") return truthyFlag(entry.is_watched) ? 100 : 0;
    return pct(entry.watchedCount, entry.total);
  }

  function seasonNumbers(group) {
    return Array.from(group.seasons).sort((a, b) => a - b);
  }

  function episodeLabel(num) {
    return num == null ? "EP" : `E${String(num).padStart(2, "0")}`;
  }

  function posterUrl(item) {
    const id = item && typeof item === "object" ? item.id : item;
    const version = item && typeof item === "object" ? item.updated_at || "" : "";
    return version
      ? `/api/media/${id}/poster?v=${encodeURIComponent(version)}`
      : `/api/media/${id}/poster`;
  }

  function readItem(id) {
    return state.items.find((it) => Number(it.id) === Number(id)) || null;
  }

  /* --------------------------- Filtering/sort --------------------------- */

  function itemMatchesSearch(item) {
    if (!state.q) return true;
    const idx = state.searchIndex.get(Number(item.id)) || "";
    return idx.includes(state.q);
  }

  function passesFilters(item, skip) {
    if (!itemMatchesSearch(item)) return false;
    const f = state.filters;
    if (skip !== "cat" && f.cat !== "all" && item.content_category !== f.cat) return false;
    if (f.source !== "all" && item.source_type !== f.source) return false;
    if (f.watched === "watched" && !truthyFlag(item.is_watched)) return false;
    if (f.watched === "unwatched" && truthyFlag(item.is_watched)) return false;
    if (f.sub === "has" && !truthyFlag(item.has_subtitle)) return false;
    if (f.sub === "none" && truthyFlag(item.has_subtitle)) return false;
    if (f.meta === "has" && !truthyFlag(item.has_metadata)) return false;
    if (f.meta === "none" && truthyFlag(item.has_metadata)) return false;
    const classification = String(item.classification_status || "");
    if (f.review === "needs_review" && classification !== "needs_review") return false;
    if (f.review === "classified" && classification !== "classified") return false;
    if (f.review === "manual" && classification !== "manually_classified") return false;
    if (f.review === "pending" && classification && classification !== "pending") return false;
    return true;
  }

  function filteredItems() {
    return state.items.filter((it) => passesFilters(it));
  }

  function sortEntries(entries, mode) {
    const list = entries.slice();
    const titleOf = (e) => entryTitle(e).toLowerCase();
    switch (mode) {
      case "oldest":
        return list.sort((a, b) => entryUpdated(a).localeCompare(entryUpdated(b)));
      case "title":
        return list.sort((a, b) => titleOf(a).localeCompare(titleOf(b), "id"));
      case "rating":
        return list.sort((a, b) => entryStar(b) - entryStar(a) || titleOf(a).localeCompare(titleOf(b), "id"));
      case "year":
        return list.sort((a, b) => (Number(entryYear(b)) || 0) - (Number(entryYear(a)) || 0));
      case "size":
        return list.sort((a, b) => entrySize(b) - entrySize(a));
      case "episodes":
        return list.sort((a, b) => {
          const ea = a.kind === "series" ? a.total : 1;
          const eb = b.kind === "series" ? b.total : 1;
          return eb - ea;
        });
      case "progress":
        return list.sort((a, b) => entryProgress(a) - entryProgress(b));
      case "newest":
      default:
        return list.sort((a, b) => entryUpdated(b).localeCompare(entryUpdated(a)));
    }
  }

  function libraryEntries() {
    const entries = state.filters.grouped ? mixedEntries(filteredItems()) : filteredItems();
    return sortEntries(entries, state.filters.sort);
  }

  function categoryCounts() {
    const counts = {};
    for (const item of state.items) {
      if (!passesFilters(item, "cat")) continue;
      const cat = item.content_category || "uncategorized";
      counts[cat] = (counts[cat] || 0) + 1;
    }
    return counts;
  }

  function seriesEntries() {
    const items = state.items.filter(
      (it) =>
        isSeriesCategory(it.content_category) &&
        isEpisodeItem(it) &&
        itemMatchesSearch(it) &&
        (state.seriesCat === "all" || it.content_category === state.seriesCat)
    );
    return sortEntries(groupSeries(items), state.seriesSort);
  }

  function isDefaultFilters() {
    const f = state.filters;
    return (
      f.cat === "all" &&
      f.source === "all" &&
      f.watched === "all" &&
      f.sub === "all" &&
      f.meta === "all" &&
      f.review === "all" &&
      f.sort === "newest" &&
      f.grouped === true
    );
  }

  function resetFilters() {
    state.filters = {
      cat: "all",
      source: "all",
      watched: "all",
      sub: "all",
      meta: "all",
      review: "all",
      sort: "newest",
      grouped: true,
    };
    state.limit = 60;
    clearSelection();
    render();
  }

  function goLibrary(patch) {
    const next = {
      cat: "all",
      source: "all",
      watched: "all",
      sub: "all",
      meta: "all",
      review: "all",
      sort: "newest",
      grouped: true,
    };
    Object.assign(next, patch || {});
    state.filters = next;
    state.selectMode = false;
    clearSelection();
    setView("library");
  }

  /* ------------------------------ Modal --------------------------------- */

  const modalStack = [];

  function openModal(options) {
    const opts = options || {};
    const overlay = el("div", {
      class: "modal",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": opts.title || "Dialog",
    });
    const sheet = el("div", { class: "sheet" + (opts.wide ? " sheet--wide" : "") });
    const closeBtn = el("button", {
      class: "sheet__close",
      type: "button",
      "aria-label": "Tutup",
      html: ICON.x,
      onclick: () => closeModal(overlay),
    });

    const head = el(
      "div",
      { class: "sheet__head" },
      el(
        "div",
        { class: "sheet__heading" },
        el("h2", { class: "sheet__title", text: opts.title || "" }),
        opts.subtitle ? el("p", { class: "sheet__sub", text: opts.subtitle }) : null
      ),
      closeBtn
    );

    sheet.append(head);
    if (opts.body) sheet.append(el("div", { class: "sheet__body" }, opts.body));
    if (opts.footer) sheet.append(el("div", { class: "sheet__foot" }, opts.footer));
    overlay.append(sheet);

    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) closeModal(overlay);
    });
    overlay.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeModal(overlay);
      }
      if (ev.key === "Tab") {
        const focusable = sheet.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (ev.shiftKey && document.activeElement === first) {
          ev.preventDefault();
          last.focus();
        } else if (!ev.shiftKey && document.activeElement === last) {
          ev.preventDefault();
          first.focus();
        }
      }
    });

    document.body.append(overlay);
    document.body.style.overflow = "hidden";
    modalStack.push({ overlay, sheet });
    requestAnimationFrame(() => overlay.classList.add("is-open"));
    const focusTarget = sheet.querySelector(".sheet__body input, .sheet__body button, .sheet__close");
    if (focusTarget) focusTarget.focus();

    if (opts.onClose) {
      overlay.addEventListener("tmh:closed", opts.onClose);
    }
    return { overlay, sheet, close: () => closeModal(overlay) };
  }

  function closeModal(target) {
    let index = modalStack.length - 1;
    if (target) index = modalStack.findIndex((m) => m.overlay === target);
    if (index < 0) return;
    const entry = modalStack[index];
    modalStack.splice(index, 1);
    entry.overlay.dispatchEvent(new Event("tmh:closed"));
    entry.overlay.remove();
    if (!modalStack.length && !document.querySelector(".detail")) {
      document.body.style.overflow = "";
    }
  }

  function closeAllModals() {
    while (modalStack.length) closeModal(modalStack[modalStack.length - 1].overlay);
  }

  function confirmDialog(options) {
    const opts = options || {};
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        modal.close();
        resolve(value);
      };
      const footer = el(
        "div",
        { style: "display:flex;gap:8px;justify-content:flex-end;width:100%" },
        el("button", { class: "btn btn--ghost", type: "button", text: "Batal", onclick: () => finish(false) }),
        el("button", {
          class: "btn " + (opts.danger === false ? "btn--primary" : "btn--danger"),
          type: "button",
          text: opts.confirmLabel || "Lanjutkan",
          onclick: () => finish(true),
        })
      );
      const modal = openModal({
        title: opts.title || "Konfirmasi",
        body: el("p", { style: "margin:0;color:var(--text-2);line-height:1.6", text: opts.message || "" }),
        footer,
        onClose: () => {
          if (!settled) {
            settled = true;
            resolve(false);
          }
        },
      });
    });
  }

  /* ------------------------------ Cards --------------------------------- */

  function cardButton(entry, opts) {
    const options = opts || {};
    const isSeries = entry.kind === "series";
    const item = isSeries ? entry.posterItem : entry;
    if (!item) return null;

    const title = entryTitle(entry);
    const category = entryCategory(entry);
    const seasonCount = entrySeasonCount(entry);
    const selectable = Boolean(options.selectable);
    const ids = entryIds(entry);
    const selected = ids.length > 0 && ids.every((id) => state.selection.has(id));

    const frame = el("div", { class: "card__frame" });
    frame.append(el("div", { class: "card__fallback", text: initials(title), "aria-hidden": "true" }));

    if (item.has_poster) {
      const img = el("img", {
        class: "card__img",
        alt: "",
        loading: "lazy",
        decoding: "async",
        src: posterUrl(item),
      });
      img.addEventListener("load", () => img.classList.add("is-loaded"));
      img.addEventListener("error", () => img.remove());
      frame.append(img);
    }

    const badges = el("div", { class: "card__badges" });
    if (category && category !== "uncategorized") {
      badges.append(el("span", { class: "card__badge card__badge--cat", text: catLabel(category) }));
    }
    if (isSeries) {
      badges.append(el("span", { class: "card__badge card__badge--series", text: `${seasonCount}S` }));
    }
    if (item.source_type === "local") {
      badges.append(el("span", { class: "card__badge card__badge--local", text: "Lokal" }));
    } else if (item.source_type === "cloud") {
      badges.append(el("span", { class: "card__badge card__badge--cloud", text: "Cloud" }));
    }
    if (badges.childElementCount) frame.append(badges);

    if (selectable && state.selectMode) {
      frame.append(el("span", { class: "card__select", text: selected ? "✓" : "", "aria-hidden": "true" }));
    }

    if (entryWatched(entry)) {
      frame.append(el("span", { class: "card__watched", html: ICON.check, "aria-hidden": "true" }));
    }

    if (isSeries) {
      const progress = entryProgress(entry);
      frame.append(
        el(
          "span",
          { class: "card__progress-label", text: `${entry.watchedCount}/${entry.total} EP` }
        ),
        el(
          "span",
          { class: "card__progress", "aria-hidden": "true" },
          el("span", { class: "card__progress-fill", style: `width:${progress}%` })
        )
      );
    }

    const hoverFacts = isSeries
      ? `${seasonCount} season · ${entry.total} episode`
      : [entryYear(entry) || null, runtimeLabel(item.runtime) || null].filter(Boolean).join(" · ");

    frame.append(
      el(
        "div",
        { class: "card__hoverinfo" },
        el("div", { class: "truncate", style: "font-weight:700;font-size:12.5px", text: title }),
        el("div", { style: "margin-top:3px;font-size:11px;color:var(--text-3)", text: hoverFacts || "—" }),
        item.genres && item.genres.length
          ? el("div", {
              class: "truncate",
              style: "margin-top:5px;font-size:11px;color:var(--text-2)",
              text: item.genres.slice(0, 3).join(" · "),
            })
          : null
      )
    );

    let subText = "";
    if (isSeries) subText = `${entry.total} episode`;
    else if (truthyFlag(item.is_watched)) subText = "Sudah ditonton";
    else if (entryYear(entry)) subText = String(entryYear(entry));

    const body = el(
      "div",
      { class: "card__body" },
      el("p", { class: "card__title clamp-2", text: title }),
      subText ? el("p", { class: "card__sub", text: subText }) : null
    );

    const card = el(
      "button",
      {
        class: "card" + (options.rank ? " card--ranked" : "") + (selected && state.selectMode ? " is-selected" : ""),
        type: "button",
        "aria-label":
          (state.selectMode && selectable ? "Pilih " : isSeries ? "Buka serial " : "Buka detail ") + title,
        "aria-pressed": selectable && state.selectMode ? (selected ? "true" : "false") : null,
        dataset: { ids: ids.join(","), key: isSeries ? `s:${entry.key}` : `i:${entry.id}` },
        onclick: () => {
          if (selectable && state.selectMode) toggleEntrySelection(entry, card);
          else if (isSeries) openSeries(entry);
          else openDetail(item.id);
        },
      },
      options.rank
        ? el("span", { class: "card__rank", text: String(options.rank), "aria-hidden": "true" })
        : null,
      frame,
      body
    );

    return card;
  }

  function railTitle(title, count, onMore) {
    return el(
      "div",
      { class: "rail__head" },
      el("h2", { class: "rail__title", text: title }),
      count ? el("span", { class: "rail__count", text: `${count} judul` }) : null,
      onMore
        ? el("button", {
            class: "rail__more",
            type: "button",
            text: "Lihat semua →",
            onclick: onMore,
          })
        : null
    );
  }

  function buildRail(title, entries, opts) {
    const options = opts || {};
    if (!entries.length) return null;

    const track = el("div", { class: "rail__track", role: "list" });
    entries.forEach((entry, idx) => {
      const card = cardButton(entry, { rank: options.ranked ? idx + 1 : 0 });
      if (!card) return;
      card.setAttribute("role", "listitem");
      track.append(card);
    });

    const prev = el("button", {
      class: "rail__arrow rail__arrow--prev",
      type: "button",
      "aria-label": "Geser ke kiri",
      html: ICON.chevL,
      onclick: () => track.scrollBy({ left: -track.clientWidth * 0.8, behavior: "smooth" }),
    });
    const next = el("button", {
      class: "rail__arrow rail__arrow--next",
      type: "button",
      "aria-label": "Geser ke kanan",
      html: ICON.chevR,
      onclick: () => track.scrollBy({ left: track.clientWidth * 0.8, behavior: "smooth" }),
    });

    const syncArrows = () => {
      const max = track.scrollWidth - track.clientWidth - 2;
      prev.disabled = track.scrollLeft <= 2;
      next.disabled = track.scrollLeft >= max;
    };
    track.addEventListener("scroll", syncArrows, { passive: true });
    requestAnimationFrame(syncArrows);

    return el(
      "section",
      { class: "rail", "aria-label": title },
      railTitle(title, entries.length, options.onMore),
      el("div", { class: "rail__viewport" }, prev, track, next)
    );
  }

  /* --------------------------- Stat fragments --------------------------- */

  function statTile(value, label, hint, onclick) {
    const props = {
      class: "stat-tile",
      type: onclick ? "button" : null,
      onclick: onclick || null,
    };
    return el(
      onclick ? "button" : "div",
      props,
      el("span", { class: "stat-tile__value", text: String(value) }),
      el("span", { class: "stat-tile__label", text: label }),
      hint ? el("span", { class: "stat-tile__hint", text: hint }) : null
    );
  }

  function quickTile(icon, title, sub, onclick, href) {
    const props = { class: "quick" };
    if (href) {
      props.href = href;
      props.target = "_blank";
      props.rel = "noopener";
      return el(
        "a",
        props,
        el("span", { class: "quick__icon", html: icon }),
        el("span", { class: "quick__text" }, el("span", { class: "quick__title", text: title }), el("span", { class: "quick__sub", text: sub }))
      );
    }
    props.type = "button";
    props.onclick = onclick;
    return el(
      "button",
      props,
      el("span", { class: "quick__icon", html: icon }),
      el("span", { class: "quick__text" }, el("span", { class: "quick__title", text: title }), el("span", { class: "quick__sub", text: sub }))
    );
  }

  function progressBar(value, extraClass) {
    return el(
      "div",
      { class: "progress" + (extraClass ? " " + extraClass : ""), role: "progressbar", "aria-valuenow": String(Math.round(clampPercent(value))), "aria-valuemin": "0", "aria-valuemax": "100" },
      el("div", { class: "progress__fill", style: `width:${clampPercent(value)}%` })
    );
  }

  // Subtitle retrieval intentionally skips these categories (adult Indo content
  // and English adult scenes), so they must not lower the coverage percentage.
  const SUBTITLE_EXEMPT_CATEGORIES = new Set(["indo", "indo_movie", "english_adult"]);

  function computedCounts() {
    let cloud = 0;
    let local = 0;
    let watched = 0;
    let sub = 0;
    let meta = 0;
    let poster = 0;
    let subEligible = 0;
    let subEligibleTotal = 0;
    for (const it of state.items) {
      if (it.source_type === "local") local += 1;
      else if (it.source_type === "cloud") cloud += 1;
      if (truthyFlag(it.is_watched)) watched += 1;
      if (truthyFlag(it.has_subtitle)) sub += 1;
      if (truthyFlag(it.has_metadata)) meta += 1;
      if (truthyFlag(it.has_poster)) poster += 1;
      if (!SUBTITLE_EXEMPT_CATEGORIES.has(it.content_category || "uncategorized")) {
        subEligibleTotal += 1;
        if (truthyFlag(it.has_subtitle)) subEligible += 1;
      }
    }
    return {
      total: state.items.length,
      cloud,
      local,
      watched,
      sub,
      meta,
      poster,
      subEligible,
      subEligibleTotal,
    };
  }

  /* ------------------------------- Views -------------------------------- */

  const main = () => $("#v2-main");

  function renderSkeleton() {
    const root = main();
    if (!root) return;
    root.replaceChildren(
      el("div", { class: "billboard" }, el("div", { class: "billboard__scrim" })),
      ...[0, 1].map(() =>
        el(
          "section",
          { class: "rail" },
          el("div", { class: "rail__head" }, el("div", { class: "skeleton", style: "width:180px;height:18px" })),
          el(
            "div",
            { class: "rail__track" },
            ...Array.from({ length: 7 }, () => el("div", { class: "skeleton skeleton--poster" }))
          )
        )
      )
    );
  }

  function renderError(message) {
    const root = main();
    if (!root) return;
    root.replaceChildren(
      el(
        "section",
        { class: "view" },
        el(
          "div",
          { class: "empty", style: "margin-top:6vh" },
          el("p", { class: "empty__title", text: "Katalog gagal dimuat" }),
          el("p", { text: message || "Terjadi kesalahan tak terduga." }),
          el("button", {
            class: "btn btn--ghost",
            style: "margin-top:18px",
            html: `${ICON.refresh}<span>Coba lagi</span>`,
            onclick: () => loadAll(),
          })
        )
      )
    );
  }

  function renderEmptyCatalog() {
    return el(
      "section",
      { class: "view" },
      el(
        "div",
        { class: "empty", style: "margin-top:6vh" },
        el("p", { class: "empty__title", text: "Library masih kosong" }),
        el("p", {
          text: "Tambahkan media dari Discovery, Video Feeds, atau sinkronkan TorBox Cloud untuk mulai membangun katalog.",
        }),
        el(
          "div",
          { style: "margin-top:20px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap" },
          el("a", { class: "btn btn--light", href: "#/discover", text: "Buka Discovery" }),
          el("a", { class: "btn btn--ghost", href: "/roxxy", text: "Buka Video Feeds" }),
          el("button", {
            class: "btn btn--ghost",
            type: "button",
            html: `${ICON.refresh}<span>Sinkronkan TorBox</span>`,
            onclick: () => runTask("sync", "Sinkronisasi TorBox"),
          })
        )
      )
    );
  }

  function renderWithHead(view, head, body) {
    return el("section", { class: "view" }, head, body);
  }

  function viewHead(title, sub, actions) {
    return el(
      "div",
      { class: "view__head" },
      el(
        "div",
        { class: "view__heading" },
        el("h1", { class: "view__title", text: title }),
        sub ? el("p", { class: "view__sub", text: sub }) : null
      ),
      el("div", { class: "view__actions" }, ...(actions || []))
    );
  }

  /* -------------------------------- Home -------------------------------- */

  function pickHero(entries) {
    const withPoster = entries.filter((entry) => {
      const item = entry.kind === "series" ? entry.posterItem : entry;
      return Boolean(item && item.has_poster);
    });
    const pool = withPoster.length ? withPoster : entries.slice();
    if (!pool.length) return null;
    return pool.slice().sort((a, b) => {
      const sa = entryStar(a) * 2 + Number(a.rating || 0);
      const sb = entryStar(b) * 2 + Number(b.rating || 0);
      if (sb !== sa) return sb - sa;
      return entryUpdated(b).localeCompare(entryUpdated(a));
    })[0];
  }

  function renderBillboard(entry) {
    const isSeries = entry.kind === "series";
    const item = isSeries ? entry.posterItem : entry;
    if (!item) return null;

    const metaTexts = isSeries
      ? [
          entry.year,
          entry.seasons.size ? `${entry.seasons.size} Season` : "",
          entry.items.length ? `${entry.items.length} Episode` : "",
          catLabel(entry.category),
        ]
      : [item.year, runtimeLabel(item.runtime), catLabel(item.content_category)];
    const meta = metaTexts.filter(Boolean).map((t) => el("span", { text: t }));

    const metaRow = el("div", { class: "billboard__meta" });
    meta.forEach((node, i) => {
      if (i > 0) metaRow.append(el("span", { class: "dot", "aria-hidden": "true" }));
      metaRow.append(node);
    });
    if (entryStar(entry)) {
      metaRow.append(el("span", { class: "dot", "aria-hidden": "true" }));
      metaRow.append(el("span", { class: "billboard__score", text: `★ ${entryStar(entry)}/5` }));
    }

    // A series hero does not get a "Putar Episode" action: autoplaying an
    // arbitrary episode adds no value. One compact action opens the episode
    // list, which keeps the page view wider and the next step explicit.
    const primary = isSeries
      ? el("button", {
          class: "btn btn--light",
          html: `${ICON.list}<span>Lihat Episode</span>`,
          onclick: () => openSeries(entry),
        })
      : el("button", {
          class: "btn btn--light",
          html: `${ICON.play}<span>Putar</span>`,
          onclick: () => playItem(item),
        });

    const secondary = isSeries
      ? null
      : el("button", {
          class: "btn btn--ghost",
          html: `${ICON.plus}<span>Detail</span>`,
          onclick: () => openDetail(item.id),
        });

    return el(
      "section",
      { class: "billboard", "aria-label": "Sorotan utama" },
      el("div", {
        class: "billboard__art",
        style: item.has_poster ? `background-image:url('${posterUrl(item)}')` : "",
      }),
      el("div", { class: "billboard__scrim" }),
      el(
        "div",
        { class: "billboard__content" },
        el("span", { class: "billboard__eyebrow", text: "Sorotan" }),
        el("h1", { class: "billboard__title", text: entryTitle(entry) }),
        metaRow,
        (isSeries ? entry.plot : item.plot)
          ? el("p", { class: "billboard__plot clamp-3", text: isSeries ? entry.plot : item.plot })
          : null,
        el("div", { class: "billboard__actions" }, ...[primary, secondary].filter(Boolean))
      )
    );
  }

  function playSeries(group) {
    const target = group.items.find((it) => !truthyFlag(it.is_watched)) || group.items[0];
    if (target) playItem(target);
  }

  function renderHome() {
    if (!state.items.length) return renderEmptyCatalog();

    const entries = mixedEntries(state.items);
    const hero = pickHero(entries);
    const counts = computedCounts();
    const sections = [];

    if (hero) sections.push(renderBillboard(hero));

    sections.push(
      el(
        "div",
        { class: "stat-strip" },
        statTile(numberLabel(counts.total), "Total item", `${numberLabel(counts.cloud)} cloud · ${numberLabel(counts.local)} lokal`, () => setView("stats")),
        statTile(numberLabel(counts.watched), "Sudah ditonton", `${numberLabel(counts.total - counts.watched)} belum`, () => goLibrary({ watched: "watched" })),
        statTile(numberLabel(counts.sub), "Punya subtitle", `${Math.round(pct(counts.subEligible, Math.max(1, counts.subEligibleTotal)))}% item non-adult`, () => goLibrary({ sub: "has" })),
        statTile(numberLabel(state.playlists.length), "Playlist", "Kelola dan putar berurutan", () => setView("playlists"))
      ),
      el(
        "div",
        { class: "quick-grid" },
        quickTile(ICON.folder, "Library lengkap", "Filter kategori, sumber, subtitle, dan status tonton", () => setView("library")),
        quickTile(ICON.tv, "Serial & episode", `${numberLabel(dfSeriesCount())} serial terdeteksi`, () => setView("series")),
        quickTile(ICON.list, "Playlist", "Buat, tambah, unduh M3U, atau putar", () => setView("playlists")),
        quickTile(ICON.download, "Pusat unduhan", "Antrean lokal, TorBox, dan tugas latar", () => setView("downloads")),
        quickTile(ICON.search, "Discovery", "Film, serial, anime, donghua + English Adult 18+", () => setView("discover")),
        quickTile(ICON.spark, "Video Feeds", "Katalog Roxxy dan unduhan lokal", null, "/roxxy")
      )
    );

    const recent = entries.slice().sort((a, b) => entryUpdated(b).localeCompare(entryUpdated(a)));
    sections.push(buildRail("Baru Ditambahkan", recent.slice(0, 20), { onMore: () => goLibrary({ sort: "newest" }) }));

    const unwatched = recent.filter((entry) => !entryWatched(entry));
    sections.push(
      buildRail("Belum Ditonton", unwatched.slice(0, 20), { onMore: () => goLibrary({ watched: "unwatched" }) })
    );

    const rated = entries
      .filter((entry) => entryStar(entry) > 0)
      .sort((a, b) => entryStar(b) - entryStar(a))
      .slice(0, 10);
    sections.push(buildRail("Rating Tertinggi", rated, { ranked: true, onMore: () => goLibrary({ sort: "rating" }) }));

    const byCat = {};
    for (const entry of entries) {
      const cat = entryCategory(entry) || "uncategorized";
      (byCat[cat] = byCat[cat] || []).push(entry);
    }
    const catKeys = [
      ...CATEGORY_ORDER.filter((c) => byCat[c] && byCat[c].length),
      ...Object.keys(byCat).filter((c) => !CATEGORY_ORDER.includes(c)),
    ];
    for (const cat of catKeys) {
      sections.push(
        buildRail(catLabel(cat), byCat[cat].slice(0, 20), { onMore: () => goLibrary({ cat }) })
      );
    }

    return el("div", { class: "view view--flush" }, ...sections.filter(Boolean));
  }

  function dfSeriesCount() {
    return groupSeries(state.items.filter(isEpisodeItem)).length;
  }

  /* ------------------------------ Library ------------------------------- */

  function chipRow(options) {
    const row = el("div", { class: "chips", role: "group", "aria-label": options.label || "Kategori" });
    for (const item of options.items) {
      const btn = el(
        "button",
        {
          class: "chip" + (item.active ? " is-active" : ""),
          type: "button",
          "aria-pressed": item.active ? "true" : "false",
          onclick: item.onclick,
        },
        el("span", { text: item.label }),
        item.count != null ? el("span", { class: "chip__count", text: numberLabel(item.count) }) : null
      );
      row.append(btn);
    }
    return row;
  }

  function filterField(labelText, selectNode) {
    return el(
      "label",
      { class: "field" },
      el("span", { class: "field__label", text: labelText }),
      selectNode
    );
  }

  function selectNode(options, current, onChange) {
    const node = el("select", { class: "select" });
    for (const opt of options) {
      node.append(el("option", { value: opt.value, text: opt.label }));
    }
    node.value = current;
    node.addEventListener("change", () => onChange(node.value));
    return node;
  }

  function sortOptionsFor(view) {
    const base = ["newest", "oldest", "title", "rating", "year", "size"];
    if (view === "series") base.push("episodes", "progress");
    return base.map((v) => ({ value: v, label: SORT_LABEL[v] }));
  }

  function updateFilters(patch) {
    Object.assign(state.filters, patch);
    state.limit = 60;
    render();
  }

  function renderLibrary() {
    const counts = categoryCounts();
    const entries = libraryEntries();
    const totalItems = state.items.length;
    const shownItems = entries.reduce((acc, e) => acc + (e.kind === "series" ? e.total : 1), 0);

    const actions = [
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.refresh}<span>Segarkan</span>`,
        onclick: () => loadAll(),
      }),
      el("button", {
        class: "toggle" + (state.selectMode ? " is-on" : ""),
        type: "button",
        "aria-pressed": state.selectMode ? "true" : "false",
        onclick: () => toggleSelectMode(),
      }, el("span", { class: "toggle__dot" }), el("span", { text: state.selectMode ? "Selesai pilih" : "Pilih" })),
    ];

    const chips = [{ value: "all", label: "Semua", count: Object.values(counts).reduce((a, b) => a + b, 0) }];
    for (const cat of CATEGORY_ORDER) {
      if (counts[cat]) chips.push({ value: cat, label: catLabel(cat), count: counts[cat] });
    }
    const chipRowNode = chipRow({
      label: "Filter kategori",
      items: chips.map((c) => ({
        label: c.label,
        count: c.count,
        active: state.filters.cat === c.value,
        onclick: () => updateFilters({ cat: c.value }),
      })),
    });

    const filters = el(
      "div",
      { class: "filters" },
      filterField(
        "Sumber",
        selectNode(
          [
            { value: "all", label: "Semua sumber" },
            { value: "cloud", label: "TorBox Cloud" },
            { value: "local", label: "Lokal" },
          ],
          state.filters.source,
          (v) => updateFilters({ source: v })
        )
      ),
      filterField(
        "Status tonton",
        selectNode(
          [
            { value: "all", label: "Semua" },
            { value: "unwatched", label: "Belum ditonton" },
            { value: "watched", label: "Sudah ditonton" },
          ],
          state.filters.watched,
          (v) => updateFilters({ watched: v })
        )
      ),
      filterField(
        "Subtitle",
        selectNode(
          [
            { value: "all", label: "Semua" },
            { value: "has", label: "Ada subtitle" },
            { value: "none", label: "Tanpa subtitle" },
          ],
          state.filters.sub,
          (v) => updateFilters({ sub: v })
        )
      ),
      filterField(
        "Cover",
        selectNode(
          [
            { value: "all", label: "Semua" },
            { value: "has", label: "Ada cover" },
            { value: "none", label: "Tanpa cover" },
          ],
          state.filters.meta,
          (v) => updateFilters({ meta: v })
        )
      ),
      filterField(
        "Status klasifikasi",
        selectNode(
          [
            { value: "all", label: "Semua status" },
            { value: "needs_review", label: "Needs Review" },
            { value: "classified", label: "Terklasifikasi" },
            { value: "manual", label: "Manual" },
          ],
          state.filters.review,
          (v) => updateFilters({ review: v })
        )
      ),
      filterField(
        "Urutkan",
        selectNode(sortOptionsFor("library"), state.filters.sort, (v) => updateFilters({ sort: v }))
      ),
      el(
        "button",
        {
          class: "toggle" + (state.filters.grouped ? " is-on" : ""),
          type: "button",
          "aria-pressed": state.filters.grouped ? "true" : "false",
          onclick: () => updateFilters({ grouped: !state.filters.grouped }),
        },
        el("span", { class: "toggle__dot" }),
        el("span", { text: state.filters.grouped ? "Serial digabung" : "Episode terpisah" })
      ),
      !isDefaultFilters()
        ? el("button", { class: "btn btn--ghost btn--sm", type: "button", text: "Reset filter", onclick: resetFilters })
        : null
    );

    const resultbar = el(
      "div",
      { class: "resultbar" },
      el("span", { class: "resultbar__count", text: `${numberLabel(entries.length)} judul` }),
      el("span", { text: `· ${numberLabel(shownItems)} item ditampilkan dari ${numberLabel(totalItems)}` }),
      state.q ? el("span", { text: `· pencarian "${state.q}"` }) : null,
      el("span", { class: "resultbar__spacer" }),
      entries.length > state.limit
        ? el("span", { text: `Menampilkan ${numberLabel(Math.min(state.limit, entries.length))} teratas` })
        : null
    );

    const grid = el("div", { class: "lib-grid" });
    if (!entries.length) {
      grid.append(
        el(
          "div",
          { class: "empty", style: "grid-column:1/-1" },
          el("p", { class: "empty__title", text: "Tidak ada hasil" }),
          el("p", { text: "Coba longgarkan filter atau hapus kata pencarian." }),
          el("button", { class: "btn btn--ghost", style: "margin-top:14px", type: "button", text: "Reset filter", onclick: resetFilters })
        )
      );
    } else {
      const visible = entries.slice(0, state.limit);
      for (const entry of visible) {
        const card = cardButton(entry, { selectable: true });
        if (card) grid.append(card);
      }
      if (entries.length > state.limit) {
        grid.append(
          el(
            "div",
            { style: "grid-column:1/-1;display:flex;justify-content:center;padding:18px 0" },
            el("button", {
              class: "btn btn--ghost",
              type: "button",
              text: `Muat lebih banyak (${numberLabel(entries.length - state.limit)} tersisa)`,
              onclick: () => {
                state.limit += 60;
                render();
              },
            })
          )
        );
      }
    }

    const wrap = el(
      "section",
      { class: "view" },
      viewHead(
        "Library",
        `${numberLabel(totalItems)} item · ${numberLabel(counts.general_movie || 0)} film · ${numberLabel(dfSeriesCount())} serial`,
        actions
      ),
      chipRowNode,
      filters,
      resultbar,
      grid
    );

    if (state.selectMode) {
      const bar = renderBulkbar(entries);
      if (bar) wrap.append(bar);
    }

    return wrap;
  }

  /* ------------------------------- Series ------------------------------- */

  function renderSeriesView() {
    const entries = seriesEntries();
    const chips = chipRow({
      label: "Kategori serial",
      items: [
        { value: "all", label: "Semua serial" },
        { value: "tv_show", label: "Serial TV" },
        { value: "anime", label: "Anime" },
        { value: "donghua", label: "Donghua" },
      ].map((c) => ({
        label: c.label,
        active: state.seriesCat === c.value,
        onclick: () => {
          state.seriesCat = c.value;
          render();
        },
      })),
    });

    const filters = el(
      "div",
      { class: "filters" },
      filterField(
        "Urutkan",
        selectNode(sortOptionsFor("series"), state.seriesSort, (v) => {
          state.seriesSort = v;
          render();
        })
      ),
      el("button", {
        class: "toggle" + (state.seriesSort === "progress" ? " is-on" : ""),
        type: "button",
        text: "Fokus progres terendah",
        onclick: () => {
          state.seriesSort = state.seriesSort === "progress" ? "newest" : "progress";
          render();
        },
      })
    );

    const grid = el("div", { class: "lib-grid" });
    if (!entries.length) {
      grid.append(
        el(
          "div",
          { class: "empty", style: "grid-column:1/-1" },
          el("p", { class: "empty__title", text: "Belum ada serial" }),
          el("p", { text: "Serial TV, anime, dan donghua yang punya nomor episode atau judul seri muncul di sini." })
        )
      );
    } else {
      for (const entry of entries) {
        const card = cardButton(entry);
        if (card) grid.append(card);
      }
    }

    return renderWithHead(
      "series",
      viewHead(
        "Serial",
        `${numberLabel(entries.length)} judul serial · progres episode per judul`,
        [
          el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            html: `${ICON.refresh}<span>Segarkan</span>`,
            onclick: () => loadAll(),
          }),
        ]
      ),
      [chips, filters, grid]
    );
  }

  /* ------------------------------ Playlists ---------------------------- */

  function renderPlaylistsView() {
    const actions = [
      el("button", {
        class: "btn btn--primary btn--sm",
        type: "button",
        html: `${ICON.plus}<span>Playlist baru</span>`,
        onclick: () => openCreatePlaylistModal([]),
      }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.refresh}<span>Segarkan</span>`,
        onclick: async () => {
          try {
            await refreshPlaylists();
            render();
            toast("Playlist disegarkan", "ok");
          } catch (err) {
            toast(err.message, "error");
          }
        },
      }),
    ];

    const grid = el("div", { class: "pl-grid" });
    if (!state.playlists.length) {
      grid.append(
        el(
          "div",
          { class: "empty", style: "grid-column:1/-1" },
          el("p", { class: "empty__title", text: "Belum ada playlist" }),
          el("p", { text: "Buat playlist dari item Library atau dari tombol Playlist baru." }),
          el("button", {
            class: "btn btn--light",
            style: "margin-top:14px",
            type: "button",
            text: "Buat playlist",
            onclick: () => openCreatePlaylistModal([]),
          })
        )
      );
    } else {
      for (const pl of state.playlists) {
        const count = Array.isArray(pl.items) ? pl.items.length : 0;
        grid.append(
          el(
            "div",
            { class: "pl-card" },
            el("button", {
              class: "pl-card__name",
              type: "button",
              style: "background:none;text-align:left;padding:0",
              text: pl.name || "Tanpa Nama",
              onclick: () => openPlaylistModal(pl.id),
            }),
            el("span", {
              class: "pl-card__meta",
              text: `${numberLabel(count)} item · ${pl.file_name || "M3U belum dibuat"}`,
            }),
            el(
              "div",
              { class: "pl-card__actions" },
              el("button", {
                class: "btn btn--light btn--sm",
                type: "button",
                html: `${ICON.play}<span>Putar</span>`,
                disabled: count === 0,
                onclick: async (ev) => {
                  const btn = ev.currentTarget;
                  btn.disabled = true;
                  try {
                    const data = await api(`/api/playlists/${pl.id}/play`, jsonBody({}));
                    toast(data.message || `Playlist "${pl.name}" dikirim ke player`, "ok");
                  } catch (err) {
                    toast(err.message, "error");
                  } finally {
                    btn.disabled = false;
                  }
                },
              }),
              el("a", {
                class: "btn btn--ghost btn--sm",
                href: `/api/playlists/${pl.id}/download`,
                html: `${ICON.download}<span>M3U</span>`,
              }),
              el("button", {
                class: "btn btn--ghost btn--sm",
                type: "button",
                html: `${ICON.list}<span>Kelola</span>`,
                onclick: () => openPlaylistModal(pl.id),
              })
            )
          )
        );
      }
    }

    return renderWithHead(
      "playlists",
      viewHead("Playlist", `${numberLabel(state.playlists.length)} playlist · putar berurutan atau unduh M3U`, actions),
      grid
    );
  }

  function openPlaylistModal(playlistId) {
    const pl = state.playlists.find((p) => Number(p.id) === Number(playlistId));
    if (!pl) {
      toast("Playlist tidak ditemukan", "error");
      return;
    }
    const items = Array.isArray(pl.items) ? pl.items : [];

    const list = el("div", { class: "rowlist" });
    if (!items.length) {
      list.append(el("p", { class: "panel__empty", text: "Playlist ini masih kosong." }));
    } else {
      items.forEach((item, index) => {
        const meta = [
          episodeLabel(item.episode_num),
          item.year,
          bytesLabel(item.file_size),
          truthyFlag(item.has_subtitle) ? "SUB" : "",
        ]
          .filter(Boolean)
          .join(" · ");
        list.append(
          el(
            "div",
            { class: "episode" },
            el("span", { class: "episode__num", text: String(index + 1), "aria-hidden": "true" }),
            el(
              "div",
              { class: "episode__body" },
              el("span", { class: "episode__title", text: item.title || item.file_name || "Tanpa Judul" }),
              el("span", {
                class: "episode__file",
                text: [item.series_title, meta].filter(Boolean).join(" · ") || item.file_name || "",
              })
            ),
            el(
              "div",
              { class: "episode__actions" },
              el("button", {
                class: "btn btn--ghost btn--sm",
                type: "button",
                html: `${ICON.play}<span>Putar</span>`,
                onclick: () => playItem(item),
              }),
              el("button", {
                class: "btn btn--ghost btn--sm",
                type: "button",
                title: "Hapus dari playlist",
                "aria-label": `Hapus ${item.title || item.file_name} dari playlist`,
                html: ICON.trash,
                onclick: async (ev) => {
                  const btn = ev.currentTarget;
                  btn.disabled = true;
                  try {
                    await api(`/api/playlists/${pl.id}/items/${item.id}`, { method: "DELETE" });
                    await refreshPlaylists();
                    closeModal();
                    render();
                    toast("Item dihapus dari playlist", "ok");
                    openPlaylistModal(pl.id);
                  } catch (err) {
                    btn.disabled = false;
                    toast(err.message, "error");
                  }
                },
              })
            )
          )
        );
      });
    }

    const footer = el(
      "div",
      { style: "display:flex;flex-wrap:wrap;gap:8px;width:100%;align-items:center" },
      el("button", {
        class: "btn btn--danger btn--sm",
        type: "button",
        html: `${ICON.trash}<span>Hapus playlist</span>`,
        onclick: async () => {
          const ok = await confirmDialog({
            title: "Hapus playlist?",
            message: `Playlist "${pl.name}" beserta file M3U-nya akan dihapus. Item media tidak ikut terhapus.`,
            confirmLabel: "Hapus playlist",
          });
          if (!ok) return;
          try {
            await api(`/api/playlists/${pl.id}`, { method: "DELETE" });
            await refreshPlaylists();
            closeModal();
            render();
            toast("Playlist dihapus", "ok");
          } catch (err) {
            toast(err.message, "error");
          }
        },
      }),
      el("span", { style: "flex:1 1 auto" }),
      el("a", {
        class: "btn btn--ghost btn--sm",
        href: `/api/playlists/${pl.id}/download`,
        html: `${ICON.download}<span>Unduh M3U</span>`,
      }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.plus}<span>Tambah media</span>`,
        onclick: () => {
          closeModal();
          openAddToPlaylistModal(pl.id);
        },
      }),
      el("button", {
        class: "btn btn--light btn--sm",
        type: "button",
        html: `${ICON.play}<span>Putar playlist</span>`,
        disabled: items.length === 0,
        onclick: async (ev) => {
          const btn = ev.currentTarget;
          btn.disabled = true;
          try {
            const data = await api(`/api/playlists/${pl.id}/play`, jsonBody({}));
            toast(data.message || "Playlist dikirim ke player", "ok");
          } catch (err) {
            toast(err.message, "error");
          } finally {
            btn.disabled = false;
          }
        },
      })
    );

    openModal({
      title: pl.name || "Playlist",
      subtitle: `${numberLabel(items.length)} item · ${pl.file_name || "M3U belum dibuat"}`,
      body: list,
      footer,
      wide: true,
    });
  }

  function openAddToPlaylistModal(playlistId) {
    const pl = state.playlists.find((p) => Number(p.id) === Number(playlistId));
    if (!pl) return;
    const existing = new Set(
      (Array.isArray(pl.items) ? pl.items : []).map((it) => Number(it.id))
    );
    const selected = new Set();

    const picker = buildMediaPicker(selected, {
      multi: true,
      exclude: existing,
      emptyText: "Semua media sudah ada di playlist ini.",
      onChange: () => {
        addBtn.textContent = selected.size ? `Tambah ${selected.size} item` : "Tambah item";
        addBtn.disabled = selected.size === 0;
      },
    });

    const addBtn = el("button", {
      class: "btn btn--light btn--sm",
      type: "button",
      text: "Tambah item",
      disabled: true,
      onclick: async () => {
        if (!selected.size) return;
        addBtn.disabled = true;
        addBtn.textContent = "Menambahkan...";
        try {
          await api(`/api/playlists/${pl.id}/items`, jsonBody({ media_ids: Array.from(selected) }));
          closeModal();
          toast(`Menambahkan ${selected.size} item ke "${pl.name}"...`, "ok");
          await waitForTask();
          await refreshPlaylists();
          render();
          openPlaylistModal(pl.id);
        } catch (err) {
          toast(err.message, "error");
          addBtn.disabled = false;
          addBtn.textContent = `Tambah ${selected.size} item`;
        }
      },
    });

    openModal({
      title: `Tambah media ke "${pl.name}"`,
      subtitle: `${numberLabel(existing.size)} item saat ini`,
      body: picker.node,
      footer: el(
        "div",
        { style: "display:flex;gap:8px;justify-content:flex-end;width:100%" },
        el("button", { class: "btn btn--ghost btn--sm", type: "button", text: "Batal", onclick: () => closeModal() }),
        addBtn
      ),
      wide: true,
    });
  }

  function openCreatePlaylistModal(preselectedIds) {
    const selected = new Set((preselectedIds || []).map((x) => Number(x)));
    const nameInput = el("input", {
      class: "input",
      type: "text",
      placeholder: "Nama playlist, mis. Nonton Akhir Pekan",
      maxlength: "80",
      "aria-label": "Nama playlist",
    });

    const picker = buildMediaPicker(selected, {
      multi: true,
      onChange: () => {
        createBtn.disabled = !selected.size || !nameInput.value.trim();
        countLine.textContent = `${numberLabel(selected.size)} item dipilih`;
      },
    });

    const countLine = el("p", { class: "sheet__sub", style: "margin:8px 0 0", text: `${numberLabel(selected.size)} item dipilih` });

    const createBtn = el("button", {
      class: "btn btn--light btn--sm",
      type: "button",
      text: "Buat playlist",
      disabled: !selected.size,
      onclick: async () => {
        const name = nameInput.value.trim();
        if (!name || !selected.size) return;
        createBtn.disabled = true;
        createBtn.textContent = "Membuat...";
        try {
          await api("/api/playlists", jsonBody({ name, media_ids: Array.from(selected) }));
          closeModal();
          clearSelection();
          state.selectMode = false;
          toast(`Playlist "${name}" sedang dibuat...`, "ok");
          await waitForTask();
          await refreshPlaylists();
          render();
          const created = state.playlists.find((p) => p.name === name);
          if (created) openPlaylistModal(created.id);
        } catch (err) {
          toast(err.message, "error");
          createBtn.disabled = false;
          createBtn.textContent = "Buat playlist";
        }
      },
    });

    nameInput.addEventListener("input", () => {
      createBtn.disabled = !selected.size || !nameInput.value.trim();
    });

    openModal({
      title: "Playlist baru",
      subtitle: "Pilih item, beri nama, lalu buat",
      body: el(
        "div",
        { class: "picker" },
        el("div", {}, el("label", { class: "sheet__label", text: "Nama playlist" }), nameInput, countLine),
        picker.node
      ),
      footer: el(
        "div",
        { style: "display:flex;gap:8px;justify-content:flex-end;width:100%" },
        el("button", { class: "btn btn--ghost btn--sm", type: "button", text: "Batal", onclick: () => closeModal() }),
        createBtn
      ),
      wide: true,
    });
  }

  function buildMediaPicker(selectedSet, options) {
    const opts = options || {};
    const exclude = opts.exclude || new Set();
    const multi = opts.multi !== false;
    let query = "";
    let limit = 80;

    const listHost = el("div", { class: "picker__list" });
    const searchInput = el("input", {
      class: "input",
      type: "search",
      placeholder: "Cari judul, kode, atau genre...",
      "aria-label": "Cari media untuk playlist",
    });

    function candidates() {
      const q = query.trim().toLowerCase();
      return state.items.filter((it) => {
        if (exclude.has(Number(it.id))) return false;
        if (!q) return true;
        const idx = state.searchIndex.get(Number(it.id)) || "";
        return idx.includes(q);
      });
    }

    function paint() {
      const rows = candidates();
      const visible = rows.slice(0, limit);
      const nodes = [];
      for (const item of visible) {
        const on = selectedSet.has(Number(item.id));
        const thumb = item.has_poster
          ? el("img", { class: "picker-row__thumb", alt: "", loading: "lazy", src: posterUrl(item) })
          : el("div", { class: "picker-row__thumb", style: "display:grid;place-items:center;font-size:11px;font-weight:800", text: initials(item.title || item.file_name) });
        nodes.push(
          el(
            "button",
            {
              class: "picker-row" + (on ? " is-on" : ""),
              type: "button",
              "aria-pressed": on ? "true" : "false",
              onclick: (ev) => {
                const btn = ev.currentTarget;
                if (!multi) selectedSet.clear();
                if (selectedSet.has(Number(item.id))) selectedSet.delete(Number(item.id));
                else selectedSet.add(Number(item.id));
                btn.classList.toggle("is-on", selectedSet.has(Number(item.id)));
                btn.setAttribute("aria-pressed", selectedSet.has(Number(item.id)) ? "true" : "false");
                const check = btn.querySelector(".picker-row__check");
                if (check) check.textContent = selectedSet.has(Number(item.id)) ? "✓" : "";
                if (opts.onChange) opts.onChange();
              },
            },
            thumb,
            el(
              "span",
              { class: "picker-row__body" },
              el("span", { class: "picker-row__title", text: item.title || item.file_name || "Tanpa Judul" }),
              el("span", {
                class: "picker-row__sub",
                text: [
                  isSeriesCategory(item.content_category) ? catLabel(item.content_category) : catLabel(item.content_category),
                  item.series_title && item.series_title !== item.title ? item.series_title : "",
                  item.episode_num != null ? episodeLabel(item.episode_num) : "",
                  item.year || "",
                  item.source_type === "local" ? "Lokal" : "Cloud",
                ]
                  .filter(Boolean)
                  .join(" · "),
              })
            ),
            el("span", { class: "picker-row__check", text: on ? "✓" : "", "aria-hidden": "true" })
          )
        );
      }
      if (!visible.length) {
        nodes.push(el("p", { class: "panel__empty", text: opts.emptyText || "Tidak ada media yang cocok." }));
      } else if (rows.length > limit) {
        nodes.push(
          el(
            "div",
            { style: "display:flex;justify-content:center;padding:6px" },
            el("button", {
              class: "btn btn--ghost btn--sm",
              type: "button",
              text: `Muat lebih banyak (${numberLabel(rows.length - limit)} tersisa)`,
              onclick: () => {
                limit += 80;
                paint();
              },
            })
          )
        );
      }
      listHost.replaceChildren(...nodes);
    }

    searchInput.addEventListener("input", () => {
      query = searchInput.value;
      limit = 80;
      paint();
    });
    paint();

    return {
      node: el("div", { class: "picker" }, searchInput, listHost),
      getSelected: () => Array.from(selectedSet),
    };
  }

  /* --------------------------- Selection/bulk --------------------------- */

  let bulkbarNode = null;

  function toggleSelectMode() {
    state.selectMode = !state.selectMode;
    if (!state.selectMode) state.selection.clear();
    render();
  }

  function clearSelection() {
    state.selection.clear();
  }

  function toggleEntrySelection(entry, card) {
    const ids = entryIds(entry);
    const allSelected = ids.length > 0 && ids.every((id) => state.selection.has(id));
    if (allSelected) {
      for (const id of ids) state.selection.delete(id);
      if (card) card.classList.remove("is-selected");
      if (card) card.setAttribute("aria-pressed", "false");
    } else {
      for (const id of ids) state.selection.add(id);
      if (card) card.classList.add("is-selected");
      if (card) card.setAttribute("aria-pressed", "true");
    }
    const check = card ? card.querySelector(".card__select") : null;
    if (check) check.textContent = allSelected ? "" : "✓";
    syncBulkbar();
  }

  function selectedIds() {
    return Array.from(state.selection);
  }

  function syncBulkbar() {
    if (!state.selection.size) {
      if (bulkbarNode) bulkbarNode.remove();
      bulkbarNode = null;
      return;
    }
    if (!bulkbarNode || !document.body.contains(bulkbarNode)) {
      render();
      return;
    }
    const count = bulkbarNode.querySelector(".bulkbar__count");
    if (count) count.textContent = `${numberLabel(state.selection.size)} item dipilih`;
  }

  function renderBulkbar() {
    const bar = el(
      "div",
      { class: "bulkbar", role: "region", "aria-label": "Aksi massal" },
      el("span", { class: "bulkbar__count", text: `${numberLabel(state.selection.size)} item dipilih` }),
      el(
        "div",
        { class: "bulkbar__actions" },
        el("button", {
          class: "btn btn--light btn--sm",
          type: "button",
          html: `${ICON.play}<span>Putar</span>`,
          onclick: () => playSelected(),
        }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.check}<span>Tandai ditonton</span>`,
          onclick: () => watchedBulk(true),
        }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.plus}<span>Belum ditonton</span>`,
          onclick: () => watchedBulk(false),
        }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.list}<span>Tambah ke playlist</span>`,
          onclick: () => openBulkPlaylistModal(),
        }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.plus}<span>Playlist baru</span>`,
          onclick: () => openCreatePlaylistModal(selectedIds()),
        }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          text: "Pilih semua hasil",
          onclick: () => selectAllVisible(),
        })
      ),
      el("button", {
        class: "bulkbar__close",
        type: "button",
        "aria-label": "Bersihkan pilihan",
        html: ICON.x,
        onclick: () => {
          clearSelection();
          state.selectMode = false;
          render();
        },
      })
    );
    bulkbarNode = bar;
    return bar;
  }

  function selectAllVisible() {
    for (const entry of libraryEntries()) {
      for (const id of entryIds(entry)) state.selection.add(id);
    }
    render();
  }

  async function playSelected() {
    const ids = selectedIds();
    if (!ids.length) return;
    try {
      const data = await api("/api/media/play-batch", jsonBody({ media_ids: ids, label: `Pilihan Library (${ids.length})` }));
      toast(data.message || `${ids.length} item dikirim ke player`, "ok");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function watchedBulk(value) {
    const ids = selectedIds();
    if (!ids.length) return;
    let failures = 0;
    let done = 0;
    const workers = [];
    const queue = ids.slice();
    const run = async () => {
      while (queue.length) {
        const id = queue.shift();
        try {
          await api(`/api/media/${id}/watched`, jsonBody({ is_watched: value ? 1 : 0 }));
          const item = readItem(id);
          if (item) item.is_watched = value ? 1 : 0;
        } catch (err) {
          failures += 1;
        }
        done += 1;
        if (done % 8 === 0) syncBulkbar();
      }
    };
    for (let i = 0; i < Math.min(4, ids.length); i++) workers.push(run());
    await Promise.all(workers);
    toast(
      failures
        ? `${done - failures} item diperbarui, ${failures} gagal`
        : `${done} item ditandai ${value ? "sudah" : "belum"} ditonton`,
      failures ? "error" : "ok"
    );
    clearSelection();
    render();
  }

  function openBulkPlaylistModal() {
    const ids = selectedIds();
    if (!ids.length) return;
    const rows = [];
    for (const pl of state.playlists) {
      const existing = new Set(
        (Array.isArray(pl.items) ? pl.items : []).map((it) => Number(it.id))
      );
      const fresh = ids.filter((id) => !existing.has(id)).length;
      rows.push(
        el(
          "div",
          { class: "row" },
          el(
            "span",
            { class: "row__main" },
            el("span", { class: "row__title", text: pl.name || "Tanpa Nama" }),
            el("span", {
              class: "row__sub",
              text: `${numberLabel(existing.size)} item · ${fresh} dari ${ids.length} item akan ditambahkan`,
            })
          ),
          el("button", {
            class: "btn btn--light btn--sm",
            type: "button",
            text: fresh ? `Tambah ${fresh}` : "Sudah ada",
            disabled: fresh === 0,
            onclick: async (ev) => {
              const btn = ev.currentTarget;
              btn.disabled = true;
              try {
                await api(`/api/playlists/${pl.id}/items`, jsonBody({ media_ids: ids }));
                closeModal();
                toast(`Menambahkan ${warningCount(fresh)} item ke "${pl.name}"...`, "ok");
                await waitForTask();
                await refreshPlaylists();
                clearSelection();
                state.selectMode = false;
                render();
              } catch (err) {
                btn.disabled = false;
                toast(err.message, "error");
              }
            },
          })
        )
      );
    }

    const body = el(
      "div",
      { class: "picker" },
      state.playlists.length
        ? el("div", { class: "rowlist" }, ...rows)
        : el("p", { class: "panel__empty", text: "Belum ada playlist. Buat playlist terlebih dahulu." })
    );

    openModal({
      title: "Tambah ke playlist",
      subtitle: `${numberLabel(ids.length)} item dipilih`,
      body,
      footer: el(
        "div",
        { style: "display:flex;gap:8px;justify-content:flex-end;width:100%" },
        el("button", { class: "btn btn--ghost btn--sm", type: "button", text: "Batal", onclick: () => closeModal() }),
        el("button", {
          class: "btn btn--light btn--sm",
          type: "button",
          html: `${ICON.plus}<span>Playlist baru</span>`,
          onclick: () => {
            closeModal();
            openCreatePlaylistModal(ids);
          },
        })
      ),
      wide: true,
    });
  }

  function warningCount(n) {
    return numberLabel(n);
  }

  /* ------------------------------ Downloads ----------------------------- */

  let downloadsBody = null;
  let downloadsTimer = null;

  const TASK_ENDPOINTS = {
    sync: { path: "/api/tasks/sync-torbox", label: "Sinkronisasi TorBox", icon: ICON.refresh },
    scan: { path: "/api/tasks/scan-local", label: "Scan folder lokal", icon: ICON.folder },
    classify: { path: "/api/tasks/classify-pending", label: "Klasifikasi ulang", icon: ICON.spark },
    enrich: { path: "/api/tasks/batch-enrich", label: "Enrich metadata", icon: ICON.film },
    subtitle: { path: "/api/tasks/batch-subtitle", label: "Cari subtitle", icon: ICON.list },
  };

  async function runTask(type, label) {
    const task = TASK_ENDPOINTS[type];
    if (!task) return;
    try {
      const data = await api(task.path, jsonBody({ auto_enrich: false }));
      if (data && data.task) state.task = data.task;
      toast(label ? `${label} dimulai` : "Tugas dimulai", "ok");
      state.downloads.lastLoaded = 0;
      loadDownloads();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function loadDownloads(options) {
    const opts = options || {};
    if (state.downloads.loading) return;
    state.downloads.loading = true;
    if (!opts.silent && downloadsBody) fillDownloadsBody(downloadsBody);

    const jobs = await Promise.allSettled([
      api("/api/torbox/watch-status"),
      api("/api/task/status"),
      api("/api/roxxy/local/queue"),
      api("/api/roxxy/library-queue"),
    ]);

    const [watch, task, queue, library] = jobs;
    if (watch.status === "fulfilled") state.downloads.watch = watch.value;
    if (task.status === "fulfilled") {
      state.downloads.task = task.value.task || null;
      if (task.value.task) state.task = task.value.task;
    }
    if (queue.status === "fulfilled") state.downloads.queue = queue.value.jobs || [];
    if (library.status === "fulfilled") state.downloads.library = library.value.tasks || [];

    const failures = jobs.filter((x) => x.status === "rejected");
    if (failures.length === jobs.length) {
      state.downloads.error = failures[0].reason ? failures[0].reason.message : "Gagal memuat status unduhan.";
    } else if (failures.length) {
      state.downloads.error = `${failures.length} sumber status gagal dimuat.`;
    } else {
      state.downloads.error = "";
    }
    state.downloads.loading = false;
    state.downloads.lastLoaded = Date.now();

    if (state.view === "downloads" && downloadsBody && document.body.contains(downloadsBody)) {
      fillDownloadsBody(downloadsBody);
    }
  }

  function startDownloadsPoll() {
    stopDownloadsPoll();
    downloadsTimer = setInterval(() => {
      if (state.view !== "downloads") {
        stopDownloadsPoll();
        return;
      }
      if (document.hidden) return;
      loadDownloads({ silent: true });
    }, 5000);
  }

  function stopDownloadsPoll() {
    if (downloadsTimer) {
      clearInterval(downloadsTimer);
      downloadsTimer = null;
    }
  }

  function pill(status) {
    const normalized = String(status || "idle").toLowerCase();
    const known = ["queued", "downloading", "completed", "failed", "ok", "idle"];
    const cls = known.includes(normalized) ? normalized : "idle";
    return el("span", { class: `pill pill--${cls}`, text: normalized.toUpperCase() });
  }

  /* Panel unduhan bisa memuat puluhan riwayat; tampilkan sebagian agar
     halaman tetap terbaca, sisanya diberi catatan jumlah. */
  const ROW_LIMIT = 8;

  function remainderNote(hidden, noun, hint) {
    if (hidden <= 0) return null;
    return el(
      "p",
      {
        class: "panel__empty",
        text: `…dan ${numberLabel(hidden)} ${noun} lain disembunyikan.${hint ? " " + hint : ""}`,
      }
    );
  }

  function statusTally(tasks, matcher) {
    return tasks.filter((task) => matcher(String(task.status || "").toLowerCase())).length;
  }

  function taskPanel() {
    const task = state.downloads.task || state.task;
    const running = Boolean(task && task.running);
    const current = Number(task && task.current) || 0;
    const total = Number(task && task.total) || 0;
    const progress = total > 0 ? pct(current, total) : running ? 5 : 0;
    const logs = (task && task.logs) || [];

    return el(
      "div",
      { class: "panel panel--wide" },
      el(
        "div",
        { class: "panel__head" },
        el("h2", { class: "panel__title", text: "Tugas latar" }),
        pill(running ? "downloading" : "idle"),
        el("span", { style: "flex:1 1 auto" }),
        el("span", { class: "panel__count", text: task && task.task_type ? String(task.task_type) : "tidak aktif" })
      ),
      el(
        "div",
        { class: "panel__body" },
        el("p", {
          class: "task-summary",
          text: (task && task.message) || "Tidak ada tugas latar yang berjalan.",
        }),
        total > 0
          ? el(
              "div",
              { class: "meterline" },
              progressBar(progress),
              el("span", { class: "meterline__val", text: `${numberLabel(current)}/${numberLabel(total)}` })
            )
          : null,
        logs.length
          ? el("pre", { class: "task-log", text: logs.slice(-8).join("\n") })
          : null
      )
    );
  }

  function torboxPanel() {
    const data = state.downloads.watch;
    const active = data && Array.isArray(data.active) ? data.active : [];
    const syncing = data && Array.isArray(data.syncing) ? data.syncing : [];
    const imported = data && Array.isArray(data.newly_imported) ? data.newly_imported : [];
    const rows = [];

    for (const item of active) {
      rows.push(
        el(
          "div",
          { class: "row" },
          el(
            "span",
            { class: "row__main" },
            el("span", { class: "row__title", text: item.title || "TorBox item" }),
            el("span", {
              class: "row__sub",
              text: [item.kind ? item.kind.toUpperCase() : "", item.download_state || "queued"].filter(Boolean).join(" · "),
            }),
            el(
              "div",
              { class: "meterline" },
              progressBar(normalizeProgress(item.progress)),
              el("span", { class: "meterline__val", text: `${Math.round(normalizeProgress(item.progress))}%` })
            )
          )
        )
      );
    }
    for (const item of syncing) {
      rows.push(
        el(
          "div",
          { class: "row" },
          el(
            "span",
            { class: "row__main" },
            el("span", { class: "row__title", text: item.title || "TorBox item" }),
            el("span", {
              class: "row__sub",
              text: [item.library_stage || "import", item.library_message || "Menyiapkan Library..."].filter(Boolean).join(" · "),
            })
          ),
          pill("downloading")
        )
      );
    }
    for (const item of imported) {
      rows.push(
        el(
          "div",
          { class: "row" },
          el(
            "span",
            { class: "row__main" },
            el("span", { class: "row__title", text: item.title || "Item baru" }),
            el("span", { class: "row__sub", text: "Baru diimpor ke Library" })
          ),
          pill("completed")
        )
      );
    }

    return el(
      "div",
      { class: "panel" },
      el(
        "div",
        { class: "panel__head" },
        el("h2", { class: "panel__title", text: "TorBox Cloud" }),
        el("span", { class: "panel__count", text: `${numberLabel(data ? data.active_count || 0 : 0)} aktif` })
      ),
      el(
        "div",
        { class: "panel__body" },
        rows.length
          ? el("div", { class: "rowlist" }, ...rows)
          : el("p", { class: "panel__empty", text: "Tidak ada unduhan TorBox yang dipantau saat ini." })
      )
    );
  }

  function localQueuePanel() {
    const jobs = Array.isArray(state.downloads.queue) ? state.downloads.queue : [];
    const rows = jobs.map((job) => {
      const status = String(job.status || "idle").toLowerCase();
      const progress = normalizeProgress(job.progress);
      const libraryNote = job.library_message
        ? el("span", { class: "row__sub", text: `${job.library_status || "library"} · ${job.library_message}` })
        : null;
      return el(
        "div",
        { class: "row", style: "align-items:flex-start" },
        el(
          "span",
          { class: "row__main" },
          el("span", { class: "row__title row__title--wrap", text: job.title || job.filename || "Unduhan lokal" }),
          el("span", {
            class: "row__sub",
            text: [job.filename, job.engine_active || job.engine, job.message].filter(Boolean).join(" · "),
          }),
          el(
            "div",
            { class: "meterline" },
            progressBar(progress, status === "completed" ? "progress__fill--success" : ""),
            el("span", { class: "meterline__val", text: `${Math.round(progress)}%` })
          ),
          libraryNote
        ),
        el(
          "span",
          { style: "display:flex;flex-direction:column;gap:6px;align-items:flex-end" },
          pill(status),
          status === "failed"
            ? el("button", {
                class: "btn btn--ghost btn--sm",
                type: "button",
                html: `${ICON.refresh}<span>Coba ulang</span>`,
                onclick: async (ev) => {
                  const btn = ev.currentTarget;
                  btn.disabled = true;
                  try {
                    await api(`/api/roxxy/local/retry/${job.id}`, jsonBody({}));
                    toast("Download dicoba ulang", "ok");
                    loadDownloads();
                  } catch (err) {
                    btn.disabled = false;
                    toast(err.message, "error");
                  }
                },
              })
            : null
        )
      );
    });

    return el(
      "div",
      { class: "panel" },
      el(
        "div",
        { class: "panel__head" },
        el("h2", { class: "panel__title", text: "Unduhan lokal" }),
        el("span", { class: "panel__count", text: `${numberLabel(jobs.length)} job` }),
        el("span", { style: "flex:1 1 auto" }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          text: "Bersihkan riwayat",
          onclick: async () => {
            try {
              const data = await api("/api/roxxy/local/clear", jsonBody({}));
              toast(`Riwayat dibersihkan (${data.removed || 0} job)`, "ok");
              loadDownloads();
            } catch (err) {
              toast(err.message, "error");
            }
          },
        })
      ),
      el(
        "div",
        { class: "panel__body" },
        rows.length
          ? el(
              "div",
              { class: "rowlist" },
              ...rows.slice(0, ROW_LIMIT),
              remainderNote(rows.length - ROW_LIMIT, "job")
            )
          : el("p", { class: "panel__empty", text: "Belum ada unduhan lokal di sesi ini." })
      )
    );
  }

  function libraryQueuePanel() {
    const tasks = Array.isArray(state.downloads.library) ? state.downloads.library : [];
    const failedCount = statusTally(tasks, (status) => status.includes("fail") || status.includes("error"));
    const rows = tasks.map((task) =>
      el(
        "div",
        { class: "row" },
        el(
          "span",
          { class: "row__main" },
          el("span", { class: "row__title", text: task.title || task.file_name || "Tugas Library" }),
          el("span", {
            class: "row__sub",
            text: [task.jav_code || task.code_id || "", bytesLabel(task.file_size), task.message || task.error_message || ""].filter(Boolean).join(" · "),
          })
        ),
        pill(task.status || "idle")
      )
    );
    return el(
      "div",
      { class: "panel" },
      el(
        "div",
        { class: "panel__head" },
        el("h2", { class: "panel__title", text: "Validasi & registrasi Library" }),
        el("span", {
          class: "panel__count",
          text: `${numberLabel(tasks.length)} tugas${failedCount ? ` · ${numberLabel(failedCount)} gagal` : ""}`,
        })
      ),
      el(
        "div",
        { class: "panel__body" },
        rows.length
          ? el(
              "div",
              { class: "rowlist" },
              ...rows.slice(0, ROW_LIMIT),
              remainderNote(
                rows.length - ROW_LIMIT,
                "tugas",
                "Buka Video Feeds untuk daftar lengkap."
              )
            )
          : el("p", { class: "panel__empty", text: "Tidak ada tugas registrasi yang menunggu." })
      )
    );
  }

  function fillDownloadsBody(host) {
    const task = state.downloads.task || state.task;
    const running = Boolean(task && task.running);
    const buttons = Object.entries(TASK_ENDPOINTS).map(([key, meta]) =>
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${meta.icon}<span>${meta.label}</span>`,
        disabled: running || state.downloads.loading,
        onclick: () => runTask(key, meta.label),
      })
    );

    const body = [
      el(
        "div",
        { class: "taskbar" },
        ...buttons,
        el("span", { class: "taskbar__spacer" }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.refresh}<span>${state.downloads.loading ? "Memuat..." : "Segarkan"}</span>`,
          disabled: state.downloads.loading,
          onclick: () => loadDownloads(),
        })
      ),
    ];

    if (state.downloads.error) {
      body.push(
        el("div", { class: "view" }, el("p", {
          class: "task-summary",
          style: "margin:0 var(--gutter) 10px;color:var(--danger)",
          text: state.downloads.error,
        }))
      );
    }

    body.push(
      el(
        "div",
        { class: "dl-grid" },
        taskPanel(),
        torboxPanel(),
        localQueuePanel(),
        libraryQueuePanel()
      )
    );

    host.replaceChildren(...body);
  }

  function renderDownloads() {
    const head = el(
      "div",
      { class: "view__head" },
      el(
        "div",
        { class: "view__heading" },
        el("h1", { class: "view__title", text: "Unduhan" }),
        el("p", {
          class: "view__sub",
          text: "Pantau TorBox Cloud, antrean lokal, dan tugas latar. Diperbarui otomatis tiap 5 detik.",
        })
      ),
      el(
        "div",
        { class: "view__actions" },
        el("a", { class: "btn btn--ghost btn--sm", href: "/roxxy", html: `${ICON.external}<span>Video Feeds</span>` })
      )
    );

    const bodyHost = el("div");
    downloadsBody = bodyHost;
    fillDownloadsBody(bodyHost);
    if (!state.downloads.lastLoaded) {
      bodyHost.prepend(el("p", { class: "panel__empty", text: "Memuat status unduhan..." }));
      setTimeout(() => loadDownloads(), 30);
    }
    if (state.view === "downloads") startDownloadsPoll();

    return el("section", { class: "view" }, head, bodyHost);
  }

  /* ------------------------------- Stats -------------------------------- */

  function coverageItem(value, label) {
    return el(
      "div",
      { class: "coverage__item" },
      el("span", { class: "coverage__value", text: String(value) }),
      el("span", { class: "coverage__label", text: label })
    );
  }

  /* ------------------------------ Settings ------------------------------ */

  const PLAYER_LABEL = { vlc: "VLC", mpv: "MPV", custom: "Custom Player" };

  const SECRET_FIELDS = [
    {
      draft: "torbox",
      setting: "torbox_api_key",
      flag: "has_torbox_api_key",
      label: "TorBox API Key",
      hint: "Dipakai untuk sinkronisasi TorBox Cloud dan unduhan.",
    },
    {
      draft: "tmdb",
      setting: "tmdb_api_key",
      flag: "has_tmdb_api_key",
      label: "TMDb API Key (v3 Auth)",
      hint: "Sumber metadata film dan serial TV.",
    },
    {
      draft: "tpdb",
      setting: "tpdb_api_key",
      flag: "has_tpdb_api_key",
      label: "ThePornDB API Key",
      hint: "Fallback metadata English Adult dan JAV.",
    },
    {
      draft: "nzbgeek",
      setting: "nzbgeek_api_key",
      flag: "has_nzbgeek_api_key",
      label: "NZBGeek API Key (premium)",
      hint: "Indexer NZB berbayar untuk pencarian episode baru. Opsional; tanpa key, pencarian hanya memakai NZB gratis.",
    },
  ];

  function applySettingsData(data) {
    const s = state.settings;
    const cfg = data.settings || {};
    const defaults = data.default_paths || {};
    s.data = data;
    s.player = ["vlc", "mpv", "custom"].includes(cfg.media_player) ? cfg.media_player : "vlc";
    s.playerPaths = {
      vlc: cfg.vlc_path || defaults.vlc || "",
      mpv: cfg.mpv_path || defaults.mpv || "",
      custom: cfg.custom_player_path || "",
    };
    s.customArgs = cfg.custom_player_args || "";
    let paths = [];
    try {
      paths = JSON.parse(cfg.local_scan_paths || "[]");
    } catch (_) {
      paths = [];
    }
    s.localPaths = Array.isArray(paths) ? paths.join("\n") : "";
    s.indonesian = Boolean((data.subtitle_preferences || {}).indonesian_enabled);
    s.keyDrafts = { torbox: "", tmdb: "", tpdb: "", nzbgeek: "" };
    s.error = "";
  }

  async function refreshSettings() {
    const s = state.settings;
    if (s.loading) return;
    s.loading = true;
    s.error = "";
    try {
      const data = await api("/api/settings");
      applySettingsData(data);
    } catch (err) {
      logError(err);
      s.error = err.message;
    } finally {
      s.loading = false;
      if (state.view === "settings") render();
    }
  }

  function ensureSettings() {
    const s = state.settings;
    if (s.data || s.loading) return;
    refreshSettings();
  }

  function settingsPanel(title, note, children, side) {
    return el(
      "div",
      { class: "panel settings-panel" },
      el(
        "div",
        { class: "panel__head" },
        el("h2", { class: "panel__title", text: title }),
        side || null
      ),
      el(
        "div",
        { class: "panel__body" },
        note ? el("p", { class: "panel__note", text: note }) : null,
        ...children.filter(Boolean)
      )
    );
  }

  function settingsBadge(on, text) {
    return el("span", { class: "settings-badge" + (on ? " is-on" : ""), text });
  }

  function settingsRow(label, control, hint, side) {
    return el(
      "div",
      { class: "settings-row" },
      el(
        "div",
        { class: "settings-row__top" },
        el("span", { class: "settings-row__label", text: label }),
        side || null
      ),
      control,
      hint ? el("p", { class: "settings-row__hint", text: hint }) : null
    );
  }

  function settingsKv(rows) {
    const grid = el("div", { class: "settings-kv" });
    for (const [label, value] of rows) {
      grid.append(
        el(
          "div",
          { class: "settings-kv__row" },
          el("span", { class: "settings-kv__label", text: label }),
          el("span", { class: "settings-kv__value mono", text: value })
        )
      );
    }
    return grid;
  }

  function secretRow(field) {
    const s = state.settings;
    const cfg = (s.data && s.data.settings) || {};
    const has = Boolean(cfg[field.flag]);
    const mask = String(cfg[field.setting] || "");
    const shown = has ? (mask.includes("•") ? `Tersimpan (${mask})` : "Tersimpan") : "Belum diatur";
    const input = el("input", {
      class: "input",
      type: "password",
      autocomplete: "new-password",
      spellcheck: "false",
      value: s.keyDrafts[field.draft] || "",
      placeholder: has ? "Kosongkan untuk mempertahankan key tersimpan" : "Tempel key baru",
      "aria-label": field.label,
    });
    input.addEventListener("input", () => {
      s.keyDrafts[field.draft] = input.value;
    });
    return settingsRow(field.label, input, field.hint, settingsBadge(has, shown));
  }

  function settingsPlayerSection() {
    const s = state.settings;
    const data = s.data || {};
    const defaults = data.default_paths || {};
    const detected = Array.isArray(data.detected_players) ? data.detected_players : [];

    const playerChips = chipRow({
      label: "Pilihan media player",
      items: ["vlc", "mpv", "custom"].map((type) => ({
        label: PLAYER_LABEL[type],
        active: s.player === type,
        onclick: () => {
          s.player = type;
          render();
        },
      })),
    });

    const pathInput = el("input", {
      class: "input",
      type: "text",
      value: s.playerPaths[s.player] || "",
      placeholder: "Path executable atau klik Browse",
      "aria-label": `${PLAYER_LABEL[s.player]} executable path`,
    });
    pathInput.addEventListener("input", () => {
      s.playerPaths[s.player] = pathInput.value;
    });

    const browseBtn = el("button", {
      class: "btn btn--ghost btn--sm",
      type: "button",
      html: `${ICON.folder}<span>Browse</span>`,
      onclick: browsePlayerPath,
    });

    const pathRow = el(
      "div",
      { class: "settings-inline" },
      pathInput,
      browseBtn
    );

    const defaultBtn = el("button", {
      class: "btn btn--ghost btn--sm",
      type: "button",
      text: "Reset OS default",
      onclick: () => {
        s.playerPaths[s.player] = defaults[s.player] || "";
        render();
        toast("Path default OS dipakai", "ok");
      },
    });

    const detectedChips = detected.length
      ? chipRow({
          label: "Player terdeteksi",
          items: detected.map((p) => ({
            label: p.label,
            active: s.playerPaths[s.player] === p.path,
            onclick: () => {
              const type = ["vlc", "mpv", "custom"].includes(p.type) ? p.type : "custom";
              s.player = type;
              s.playerPaths[type] = p.path;
              render();
              toast(`Player dipakai: ${p.label}`, "ok");
            },
            title: p.path,
          })),
        })
      : el("p", { class: "panel__note", text: "Tidak ada media player standar yang terdeteksi. Tempel path manual atau gunakan Browse." });

    const customArgs = el("input", {
      class: "input",
      type: "text",
      value: s.customArgs,
      placeholder: "--fullscreen (opsional)",
      "aria-label": "Argumen tambahan custom player",
    });
    customArgs.addEventListener("input", () => {
      s.customArgs = customArgs.value;
    });

    return settingsPanel(
      "Media player",
      "Pilih player dan path executable yang dipakai saat memutar media.",
      [
        playerChips,
        el(
          "div",
          { class: "settings-inline" },
          el("span", { class: "settings-row__label", text: "Deteksi otomatis" }),
          el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            html: `${ICON.search}<span>Auto-Detect</span>`,
            onclick: detectPlayersInSettings,
          })
        ),
        detectedChips,
        settingsRow(`${PLAYER_LABEL[s.player]} executable path`, pathRow, null, defaultBtn),
        s.player === "custom"
          ? settingsRow("Argumen tambahan", customArgs, "Diteruskan ke custom player, contoh --fullscreen.")
          : null,
      ]
    );
  }

  function settingsKeysPanel() {
    return settingsPanel(
      "API keys",
      "Nilai tersimpan hanya ditampilkan dalam bentuk tersamarkan. Kolom kosong berarti key lama dipertahankan.",
      SECRET_FIELDS.map(secretRow)
    );
  }

  function settingsScanPanel() {
    const s = state.settings;
    const area = el("textarea", {
      class: "input settings-textarea",
      rows: 4,
      spellcheck: "false",
      placeholder: "E:\\Media\\Movies\nD:\\Series",
      "aria-label": "Folder pemindaian lokal",
    });
    area.value = s.localPaths;
    area.addEventListener("input", () => {
      s.localPaths = area.value;
    });

    return settingsPanel(
      "Folder pemindaian lokal",
      "Satu path per baris. Folder ini dipakai tombol Scan folder lokal di pusat unduhan.",
      [
        settingsRow(
          "Path folder",
          area,
          null,
          el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            html: `${ICON.folder}<span>+ Browse Folder</span>`,
            onclick: browseLocalFolderPath,
          })
        ),
      ]
    );
  }

  function settingsSubtitlePanel() {
    const s = state.settings;
    const stats = (s.data && s.data.subtitle_language_stats) || {};
    const idToggle = el(
      "button",
      {
        class: "toggle" + (s.indonesian ? " is-on" : ""),
        type: "button",
        "aria-pressed": s.indonesian ? "true" : "false",
        onclick: () => {
          s.indonesian = !s.indonesian;
          render();
        },
      },
      el("span", { class: "toggle__dot" }),
      el("span", { text: s.indonesian ? "Aktif" : "Nonaktif" })
    );

    return settingsPanel(
      "Subtitle dua bahasa",
      "Batch subtitle memeriksa track lokal/embedded lebih dulu, lalu hanya meminta bahasa yang aktif dan belum ada.",
      [
        settingsRow(
          "English (utama)",
          el("p", { class: "settings-static", text: "Selalu aktif dan wajib untuk semua item non-adult." }),
          null,
          settingsBadge(true, "Always ON")
        ),
        settingsRow("Indonesian (opsional)", idToggle),
        el(
          "div",
          { class: "settings-stats" },
          el("span", { class: "tag", text: `Complete ${numberLabel(stats.complete || 0)}` }),
          el("span", { class: "tag", text: `Ready ${numberLabel(stats.ready || 0)}` }),
          el("span", { class: "tag", text: `Partial ${numberLabel(stats.partial || 0)}` }),
          el("span", { class: "tag", text: `Not Ready ${numberLabel(stats.not_ready || 0)}` })
        ),
      ]
    );
  }

  function settingsEnvPanel() {
    const env = (state.settings.data && state.settings.data.env) || {};
    const mode = `${truthyFlag(env.is_headless) ? "Headless" : "Desktop"}${truthyFlag(env.is_docker) ? " · Docker" : ""}`;
    return settingsPanel(
      "Lingkungan runtime",
      "Set TBH_HOST=0.0.0.0 agar bisa diakses dari perangkat lain di LAN, atau TBH_DATA_DIR untuk volume Docker.",
      [
        settingsKv([
          ["Platform", `${env.os_platform || "-"} (${env.arch || "-"})`],
          ["Python", env.python_version || "-"],
          ["Mode", mode],
          ["FFprobe", truthyFlag(env.ffprobe_available) ? "Terpasang" : "Tidak ada"],
          ["FFmpeg", truthyFlag(env.ffmpeg_available) ? "Terpasang" : "Tidak ada"],
          ["LAN URL", env.lan_url || "-"],
          ["Data dir", env.data_dir || "-"],
        ]),
      ]
    );
  }

  function settingsDbPanel() {
    const s = state.settings;
    const data = s.data || {};
    const backups = Array.isArray(data.backups) ? data.backups : [];
    const busy = Boolean(s.busy);

    const backupList = el("div", { class: "settings-list settings-list--scroll" });
    if (!backups.length) {
      backupList.append(el("p", { class: "panel__empty", text: "Belum ada backup database." }));
    } else {
      for (const b of backups) {
        backupList.append(
          el(
            "div",
            { class: "settings-list__item" },
            el(
              "div",
              { class: "settings-list__body" },
              el("span", { class: "settings-list__title mono", text: b.file_name || "-" }),
              el("span", {
                class: "settings-list__meta",
                text: `${b.created_at || "-"} · ${numberLabel(Math.round(Number(b.size_kb) || 0))} KB`,
              })
            ),
            el("button", {
              class: "btn btn--ghost btn--sm",
              type: "button",
              text: "Restore",
              disabled: busy,
              onclick: () => restoreBackupFile(b.file_name),
            })
          )
        );
      }
    }

    return settingsPanel(
      "Database & cache",
      s.busy ? `Operasi berjalan: ${s.busy}` : "Backup membuat salinan aman; restore mengganti database aktif setelah membuat backup otomatis.",
      [
        el(
          "div",
          { class: "settings-actions" },
          el("button", {
            class: "btn btn--primary btn--sm",
            type: "button",
            html: `${ICON.download}<span>Backup database</span>`,
            disabled: busy,
            onclick: () => runSettingsDbAction("/api/db/backup", "Backup database"),
          }),
          el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            html: `${ICON.refresh}<span>Maintenance & cleanup</span>`,
            disabled: busy,
            onclick: () => runSettingsDbAction("/api/db/maintenance", "Maintenance"),
          }),
          el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            text: "Reset metadata & poster",
            disabled: busy,
            onclick: () =>
              runSettingsDbAction("/api/db/reset-metadata", "Reset metadata", {
                confirm: {
                  title: "Reset metadata & poster?",
                  message: "Metadata hasil enrich dan cache poster akan dihapus. File media dan playlist tetap aman.",
                  confirmLabel: "Reset metadata",
                },
              }),
          }),
          el("button", {
            class: "btn btn--danger btn--sm",
            type: "button",
            text: "Hapus semua data media",
            disabled: busy,
            onclick: () =>
              runSettingsDbAction("/api/db/erase-all", "Hapus data media", {
                confirm: {
                  title: "Hapus semua data media?",
                  message: "Semua item, playlist, dan cache di database ini dihapus. API key tetap disimpan. Tindakan ini tidak bisa dibatalkan.",
                  confirmLabel: "Hapus semua",
                },
              }),
          })
        ),
        el("h3", { class: "settings-subtitle", text: "Backup files" }),
        backupList,
      ]
    );
  }

  async function detectPlayersInSettings() {
    try {
      const data = await api("/api/players/detect");
      const s = state.settings;
      if (s.data) {
        s.data.detected_players = Array.isArray(data.players) ? data.players : [];
        s.data.default_paths = data.default_paths || s.data.default_paths;
        s.data.os_platform = data.os_platform || s.data.os_platform;
      }
      const count = (s.data && s.data.detected_players.length) || 0;
      toast(count ? `${count} media player terdeteksi` : "Tidak ada player standar; tempel path manual", count ? "ok" : "error");
      render();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function browsePlayerPath() {
    const s = state.settings;
    try {
      const data = await api("/api/system/browse-file", jsonBody({ title: `Pilih executable ${PLAYER_LABEL[s.player]}` }));
      if (data && data.path) {
        s.playerPaths[s.player] = data.path;
        render();
        toast("Path player dipilih", "ok");
      }
    } catch (err) {
      toast(`${err.message} Tempel path secara manual.`, "error");
    }
  }

  async function browseLocalFolderPath() {
    const s = state.settings;
    try {
      const data = await api("/api/system/browse-folder", jsonBody({}));
      if (data && data.path) {
        const lines = s.localPaths.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
        if (!lines.includes(data.path)) lines.push(data.path);
        s.localPaths = lines.join("\n");
        render();
        toast("Folder ditambahkan", "ok");
      }
    } catch (err) {
      toast(`${err.message} Tempel path secara manual.`, "error");
    }
  }

  async function saveSettingsView() {
    const s = state.settings;
    if (s.saving || s.busy || !s.data) return;
    const defaults = s.data.default_paths || {};
    const payload = {
      local_scan_paths: s.localPaths.split(/\r?\n/).map((x) => x.trim()).filter(Boolean),
      media_player: s.player,
      vlc_path: s.playerPaths.vlc || defaults.vlc || "",
      mpv_path: s.playerPaths.mpv || defaults.mpv || "",
      custom_player_path: s.playerPaths.custom || "",
      custom_player_args: (s.customArgs || "").trim(),
      indonesian_enabled: s.indonesian,
    };
    for (const field of SECRET_FIELDS) {
      const value = (s.keyDrafts[field.draft] || "").trim();
      if (value) payload[field.setting] = value;
    }

    s.saving = true;
    render();
    try {
      await api("/api/settings", jsonBody(payload));
      const fresh = await api("/api/settings");
      applySettingsData(fresh);
      toast("Pengaturan disimpan", "ok");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      s.saving = false;
      if (state.view === "settings") render();
    }
  }

  async function runSettingsDbAction(path, label, options) {
    const s = state.settings;
    const opts = options || {};
    if (s.busy || s.saving) return;
    if (opts.confirm) {
      const ok = await confirmDialog(opts.confirm);
      if (!ok) return;
    }
    s.busy = label;
    render();
    try {
      const data = await api(path, jsonBody(opts.payload || {}));
      if (data && data.backups) s.data.backups = data.backups;
      toast(`${label} selesai`, "ok");
      await loadAll({ silent: true });
    } catch (err) {
      toast(err.message, "error");
    } finally {
      s.busy = "";
      s.data.backups = s.data.backups || [];
      if (state.view === "settings") render();
    }
  }

  function restoreBackupFile(fileName) {
    if (!fileName) return;
    runSettingsDbAction("/api/db/restore", "Restore database", {
      payload: { file_name: fileName },
      confirm: {
        title: "Restore database?",
        message: `Database aktif akan diganti dengan ${fileName}. Backup otomatis dibuat lebih dulu.`,
        confirmLabel: "Restore",
      },
    });
  }

  function renderSettings() {
    const s = state.settings;
    ensureSettings();

    const actions = [
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.refresh}<span>Segarkan</span>`,
        disabled: s.loading,
        onclick: refreshSettings,
      }),
      el("a", {
        class: "btn btn--ghost btn--sm",
        href: "/",
        html: `${ICON.external}<span>UI lama</span>`,
      }),
    ];

    if (!s.data) {
      const body = s.error
        ? [
            el("p", { class: "panel__note", text: s.error }),
            el("button", { class: "btn btn--primary btn--sm", type: "button", text: "Coba lagi", onclick: refreshSettings }),
          ]
        : [el("p", { class: "panel__note", text: "Memuat konfigurasi..." })];
      return el(
        "section",
        { class: "view" },
        viewHead("Pengaturan", "API keys, media player, folder lokal, subtitle, dan database.", actions),
        el("div", { class: "settings-grid" }, settingsPanel("Konfigurasi", null, body))
      );
    }

    const wrap = el(
      "section",
      { class: "view" },
      viewHead(
        "Pengaturan",
        `${PLAYER_LABEL[state.settings.player]} aktif · ${(state.settings.data.backups || []).length} backup tersimpan`,
        actions
      ),
      el(
        "div",
        { class: "settings-grid" },
        settingsKeysPanel(),
        settingsPlayerSection(),
        settingsScanPanel(),
        settingsSubtitlePanel(),
        settingsEnvPanel(),
        settingsDbPanel()
      ),
      el(
        "div",
        { class: "settings-foot" },
        el("p", {
          class: "settings-foot__status",
          text: s.busy ? `Operasi berjalan: ${s.busy}` : "Perubahan berlaku setelah disimpan.",
        }),
        el("button", {
          class: "btn btn--primary",
          type: "button",
          text: s.saving ? "Menyimpan..." : "Simpan pengaturan",
          disabled: s.saving || Boolean(s.busy),
          onclick: saveSettingsView,
        })
      )
    );
    return wrap;
  }

  function renderStats() {
    const counts = computedCounts();
    const items = state.items;
    const catCounts = {};
    let seriesCount = 0;
    for (const it of items) {
      const cat = it.content_category || "uncategorized";
      catCounts[cat] = (catCounts[cat] || 0) + 1;
      if (isEpisodeItem(it)) seriesCount += 1;
    }
    const maxCat = Math.max(1, ...Object.values(catCounts));

    const bars = el("div", { class: "bars" });
    for (const cat of CATEGORY_ORDER) {
      const n = catCounts[cat] || 0;
      if (!n) continue;
      bars.append(
        el(
          "div",
          {},
          el(
            "div",
            { class: "bar-meta" },
            el("span", { text: catLabel(cat) }),
            el("span", { class: "bar-meta__value", text: `${numberLabel(n)} · ${Math.round(pct(n, items.length))}%` })
          ),
          progressBar(pct(n, maxCat))
        )
      );
    }
    if (!bars.childElementCount) bars.append(el("p", { class: "panel__empty", text: "Belum ada data kategori." }));

    const stats = state.stats || {};
    const env = state.env || {};
    const total = Math.max(1, counts.total);

    const classification = [
      ["terklasifikasi otomatis", stats.classification_classified_count || 0, "ok"],
      ["diklasifikasikan manual", stats.classification_manual_count || 0, "completed"],
      ["perlu tinjauan", stats.classification_needs_review_count || 0, "queued"],
      ["belum tercatat", stats.classification_pending_count || 0, "idle"],
    ];

    const actions = [
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.refresh}<span>Segarkan data</span>`,
        onclick: () => loadAll(),
      }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.download}<span>Backup database</span>`,
        onclick: async (ev) => {
          const btn = ev.currentTarget;
          btn.disabled = true;
          try {
            const data = await api("/api/db/backup", jsonBody({}));
            toast(`Backup dibuat: ${data.backup_file || "ok"}`, "ok");
          } catch (err) {
            toast(err.message, "error");
          } finally {
            btn.disabled = false;
          }
        },
      }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.spark}<span>Klasifikasi ulang</span>`,
        onclick: () => runTask("classify", "Klasifikasi ulang"),
      }),
    ];

    const wrap = el(
      "section",
      { class: "view" },
      viewHead("Statistik", `${numberLabel(counts.total)} item · ${truthyFlag(env.is_headless) ? "headless" : "desktop"}`, actions),
      el(
        "div",
        { class: "stat-strip" },
        statTile(numberLabel(counts.total), "Total item", `${numberLabel(seriesCount)} baris serial`),
        statTile(numberLabel(counts.cloud), "TorBox Cloud", `${Math.round(pct(counts.cloud, total))}% katalog`),
        statTile(numberLabel(counts.local), "Lokal", `${Math.round(pct(counts.local, total))}% katalog`),
        statTile(numberLabel(counts.watched), "Ditonton", `${Math.round(pct(counts.watched, total))}% katalog`),
        statTile(numberLabel(state.playlists.length), "Playlist", `${numberLabel(stats.assigned_to_playlist_count || 0)} item terdaftar`),
        statTile(`${Number(stats.db_size_kb || 0).toFixed(0)} KB`, "Ukuran DB", "SQLite + WAL")
      ),
      el(
        "div",
        { class: "stats-wrap" },
        el(
          "div",
          { class: "panel" },
          el("div", { class: "panel__head" }, el("h2", { class: "panel__title", text: "Distribusi kategori" })),
          el("div", { class: "panel__body" }, bars)
        ),
        el(
          "div",
          { class: "panel" },
          el("div", { class: "panel__head" }, el("h2", { class: "panel__title", text: "Kelengkapan data" })),
          el(
            "div",
            { class: "panel__body" },
            el(
              "div",
              { class: "coverage" },
              coverageItem(`${Math.round(pct(counts.poster, total))}%`, "Cover tersedia"),
              coverageItem(
                `${Math.round(pct(counts.subEligible, Math.max(1, counts.subEligibleTotal)))}%`,
                "Subtitle non-adult"
              ),
              coverageItem(`${Math.round(pct(counts.watched, total))}%`, "Ditonton"),
              coverageItem(`${numberLabel(counts.total - counts.poster)}`, "Tanpa cover"),
              coverageItem(`${numberLabel(counts.subEligibleTotal - counts.subEligible)}`, "Tanpa subtitle"),
              coverageItem(`${numberLabel(stats.available_for_playlist_count || 0)}`, "Siap playlist")
            ),
            el("p", {
              class: "panel__note",
              text:
                "Cover = judul yang cocok dengan provider metadata. Persentase subtitle hanya menghitung item yang memang dicari subtitle-nya (" +
                numberLabel(counts.subEligibleTotal) +
                " item; konten Indo dan English adult dikecualikan).",
            })
          )
        ),
        el(
          "div",
          { class: "panel" },
          el("div", { class: "panel__head" }, el("h2", { class: "panel__title", text: "Status klasifikasi" })),
          el(
            "div",
            { class: "panel__body" },
            el(
              "div",
              { class: "rowlist" },
              ...classification.map(([label, value, kind]) =>
                el(
                  "div",
                  { class: "row" },
                  el("span", { class: "row__main" }, el("span", { class: "row__title", text: label })),
                  el("span", { class: "pill pill--" + kind, text: numberLabel(value) })
                )
              )
            ),
            el("p", {
              class: "panel__note",
              text:
                "“Belum tercatat” berarti jalur impor/enrich menyimpan kategori tanpa verdict klasifikasi. Tombol Klasifikasi ulang mencatat verdict untuk semua baris itu dan hanya memindahkan kategori bila buktinya cukup.",
            })
          )
        ),
        el(
          "div",
          { class: "panel" },
          el("div", { class: "panel__head" }, el("h2", { class: "panel__title", text: "Lingkungan" })),
          el(
            "div",
            { class: "panel__body" },
            el(
              "div",
              { class: "rowlist" },
              ...[
                ["Platform", `${env.os_platform || "?"} · ${env.arch || "?"}`],
                ["Python", env.python_version || "?"],
                ["ffmpeg / ffprobe", `${env.ffmpeg_available ? "ada" : "tidak ada"} / ${env.ffprobe_available ? "ada" : "tidak ada"}`],
                ["mpv", env.mpv_available ? "terdeteksi" : "tidak terdeteksi"],
                ["Server", `${env.host || "?"}:${env.port || "?"}`],
                ["LAN", env.lan_url || "—"],
                ["Versi aplikasi", state.version || "?"],
              ].map(([label, value]) =>
                el(
                  "div",
                  { class: "row" },
                  el("span", { class: "row__main" }, el("span", { class: "row__title", text: label })),
                  el("span", { class: "row__sub", style: "margin:0;white-space:normal", text: String(value) })
                )
              )
            )
          )
        )
      )
    );
    return wrap;
  }

  /* ---------------------------- Root render ----------------------------- */

  function render() {
    const root = main();
    if (!root) return;
    if (state.loading) {
      renderSkeleton();
      return;
    }
    const standalone = state.view === "settings" || state.view === "discover";
    if (state.error && !state.items.length && !standalone) {
      renderError(state.error);
      return;
    }
    if (!state.items.length && !standalone) {
      root.replaceChildren(renderEmptyCatalog());
      syncNav();
      return;
    }

    const viewFn = VIEWS[state.view] || VIEWS.home;
    root.replaceChildren(viewFn());
    bulkbarNode = root.querySelector(".bulkbar");
    syncNav();
  }

  function syncNav() {
    for (const btn of document.querySelectorAll("[data-view]")) {
      const active = btn.dataset.view === state.view;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    }
    const badge = $("#v2-version");
    if (badge && state.version) badge.textContent = state.version;
  }

  /* ----------------------------- Discovery v2 ---------------------------- */

  const DISCOVER_CATEGORIES = [
    { id: "general_movie", label: "Film" },
    { id: "tv_show", label: "Serial TV" },
    { id: "anime", label: "Anime" },
    { id: "donghua", label: "Donghua" },
  ];

  const DISCOVER_FEEDS = [
    { id: "trending", label: "Trending" },
    { id: "popular", label: "Populer" },
    { id: "top_rated", label: "Rating" },
  ];

  const ADULT_STATUS_LABEL = {
    ok: "aktif",
    cooldown: "jeda sementara",
    blocked: "terblokir",
    search_only: "hanya pencarian",
    disabled: "nonaktif",
    mode_unsupported: "mode tidak didukung",
  };

  const ADULT_GATE_KEY = "tbh_adult_v2_gate";

  function adultGateApproved() {
    try {
      return localStorage.getItem(ADULT_GATE_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function approveAdultGate() {
    try {
      localStorage.setItem(ADULT_GATE_KEY, "1");
    } catch (_) {
      /* mode privat: cukup untuk sesi ini */
    }
    state.discover.adultApproved = true;
    render();
    ensureAdultDiscover(true);
  }

  function durationLabel(seconds) {
    const total = Math.round(Number(seconds) || 0);
    if (total <= 0) return "";
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return `${h}j ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function discoverTabButton(tab, label) {
    const active = state.discover.tab === tab;
    return el("button", {
      class: "discover-tab" + (active ? " is-active" : ""),
      type: "button",
      role: "tab",
      "aria-selected": active ? "true" : "false",
      text: label,
      onclick: () => switchDiscoverTab(tab),
    });
  }

  function switchDiscoverTab(tab) {
    const d = state.discover;
    if (d.tab === tab) return;
    d.tab = tab;
    render();
    if (tab === "mainstream") ensureDiscoverFeed();
    else if (adultGateApproved()) {
      d.adultApproved = true;
      ensureAdultDiscover();
    }
  }

  function discoverChips(label, items) {
    const row = chipRow({ label, items });
    row.classList.add("chips--inline");
    return row;
  }

  function discoverCategoryChips() {
    const d = state.discover;
    return discoverChips(
      "Kategori Discovery",
      DISCOVER_CATEGORIES.map((c) => ({
        label: c.label,
        active: d.category === c.id,
        onclick: () => {
          d.category = c.id;
          d.loadedKey = "";
          ensureDiscoverFeed(true);
        },
      }))
    );
  }

  function discoverFeedChips() {
    const d = state.discover;
    return discoverChips(
      "Urutan feed",
      DISCOVER_FEEDS.map((f) => ({
        label: f.label,
        active: !d.q && d.feed === f.id,
        onclick: () => {
          d.feed = f.id;
          d.q = "";
          d.loadedKey = "";
          ensureDiscoverFeed(true);
        },
      }))
    );
  }

  function adultModeChips() {
    const d = state.discover;
    const modes = d.adultModes.length ? d.adultModes : [{ id: "popular", label: "Populer" }];
    return discoverChips(
      "Urutan katalog adult",
      modes.map((m) => ({
        label: m.label,
        active: !d.adultQ && d.adultMode === m.id,
        onclick: () => {
          d.adultMode = m.id;
          d.adultQ = "";
          d.adultLoadedKey = "";
          loadAdultFeed(true);
        },
      }))
    );
  }

  function discoverStatusNode() {
    const d = state.discover;
    let text = "";
    let cls = "discover-status";
    if (d.tab === "adult") {
      if (!d.adultApproved) return null;
      if (d.adultLoading) text = "Memuat katalog adult...";
      else if (d.adultError) {
        text = `Gagal memuat: ${d.adultError}`;
        cls += " is-error";
      } else {
        text = `${numberLabel(d.adultItems.length)} item · ${
          d.adultQ ? `pencarian "${d.adultQ}"` : "feed katalog"
        }`;
      }
    } else if (d.loading) {
      text = "Memuat katalog Discovery...";
    } else if (d.error) {
      text = `Gagal memuat: ${d.error}`;
      cls += " is-error";
    } else {
      const categoryLabel =
        (DISCOVER_CATEGORIES.find((c) => c.id === d.category) || {}).label || d.category;
      text = `${numberLabel(d.items.length)} judul · ${categoryLabel}${d.q ? ` · "${d.q}"` : ""}`;
    }
    if (!text) return null;
    return el("span", { class: cls, text, title: text });
  }

  function renderDiscover() {
    const d = state.discover;
    const refresh = el("button", {
      class: "btn btn--ghost btn--sm",
      type: "button",
      html: `${ICON.refresh}<span>Segarkan</span>`,
      onclick: () => {
        if (d.tab === "adult") ensureAdultDiscover(true);
        else ensureDiscoverFeed(true);
      },
    });

    const tabs = el(
      "div",
      { class: "discover-tabs", role: "tablist", "aria-label": "Sumber Discovery" },
      discoverTabButton("mainstream", "Film & Serial"),
      discoverTabButton("adult", "English Adult 18+")
    );

    const toolbar = el(
      "div",
      { class: "discover-toolbar" },
      tabs,
      d.tab === "adult"
        ? d.adultApproved
          ? adultModeChips()
          : null
        : [discoverCategoryChips(), discoverFeedChips()],
      discoverStatusNode()
    );

    return el(
      "section",
      { class: "view" },
      viewHead(
        "Discovery",
        "Cari judul baru dari sumber publik, lalu kirim ke TorBox Cloud. Item yang selesai diimpor otomatis masuk Library.",
        [refresh]
      ),
      toolbar,
      d.tab === "adult" ? renderAdultDiscover() : renderMainstreamDiscover()
    );
  }

  function ensureDiscoverFeed(force) {
    const d = state.discover;
    const key = `${d.category}|${d.feed}|${d.q}`;
    if (!force && d.loadedKey === key && d.items.length) {
      render();
      return;
    }
    loadDiscoverFeed(force);
  }

  function ensureDiscoveryView() {
    if (state.view !== "discover") return;
    const d = state.discover;
    if (d.tab === "adult") {
      if (!adultGateApproved()) return;
      d.adultApproved = true;
      if (!d.adultItems.length && !d.adultLoading) ensureAdultDiscover();
      return;
    }
    if (!d.loadedKey && !d.loading) ensureDiscoverFeed();
  }

  async function loadDiscoverFeed(force) {
    const d = state.discover;
    if (d.loading) return;
    const key = `${d.category}|${d.feed}|${d.q}`;
    if (!force && d.loadedKey === key && d.items.length) {
      render();
      return;
    }
    d.loading = true;
    d.error = "";
    render();
    try {
      const params = new URLSearchParams({
        category: d.category,
        feed: d.q ? "search" : d.feed,
        limit: "50",
      });
      if (d.q) params.set("query", d.q);
      const data = await api(`/api/discover?${params}`);
      d.items = Array.isArray(data.results) ? data.results : [];
      d.loadedKey = key;
    } catch (err) {
      d.items = [];
      d.error = err.message;
    } finally {
      d.loading = false;
      render();
    }
  }

  function discoverCard(item) {
    const title = item.title || "Tanpa judul";
    const frame = el("div", { class: "card__frame" });
    frame.append(el("div", { class: "card__fallback", text: initials(title), "aria-hidden": "true" }));
    if (item.poster_url) {
      const img = el("img", {
        class: "card__img",
        alt: "",
        loading: "lazy",
        decoding: "async",
        referrerpolicy: "no-referrer",
        src: item.poster_url,
      });
      img.addEventListener("load", () => img.classList.add("is-loaded"));
      img.addEventListener("error", () => img.remove());
      frame.append(img);
    }

    const badges = el("div", { class: "card__badges" });
    if (item.category) {
      badges.append(el("span", { class: "card__badge card__badge--cat", text: catLabel(item.category) }));
    }
    if (Number(item.rating) > 0) {
      badges.append(el("span", { class: "card__badge", text: `★ ${Number(item.rating).toFixed(1)}` }));
    }
    if (badges.childElementCount) frame.append(badges);

    const subParts = [];
    if (item.year) subParts.push(String(item.year));
    if (item.episodes) subParts.push(`${item.episodes} EP`);

    return el(
      "button",
      {
        class: "card",
        type: "button",
        "aria-label": `Buka rilis ${title}`,
        onclick: () => openDiscoverItem(item),
      },
      frame,
      el(
        "div",
        { class: "card__body" },
        el("p", { class: "card__title clamp-2", text: title }),
        subParts.length ? el("p", { class: "card__sub", text: subParts.join(" · ") }) : null
      )
    );
  }

  function renderMainstreamDiscover() {
    const d = state.discover;

    const input = el("input", {
      class: "input",
      type: "search",
      value: d.q,
      placeholder: "Cari judul film, serial, atau anime...",
      "aria-label": "Cari di Discovery",
    });
    input.addEventListener("input", () => {
      d.q = input.value;
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      d.loadedKey = "";
      ensureDiscoverFeed(true);
    });

    const clearBtn = d.q
      ? el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          text: "Reset",
          onclick: () => {
            d.q = "";
            d.loadedKey = "";
            ensureDiscoverFeed(true);
          },
        })
      : null;

    const searchRow = el(
      "div",
      { class: "discover-search" },
      input,
      el("button", {
        class: "btn btn--primary",
        type: "button",
        html: `${ICON.search}<span>Cari</span>`,
        onclick: () => {
          d.loadedKey = "";
          ensureDiscoverFeed(true);
        },
      }),
      clearBtn
    );

    const grid = el("div", { class: "lib-grid" });
    if (d.loading && !d.items.length) {
      grid.append(
        el(
          "div",
          { class: "search-panel", style: "grid-column:1/-1" },
          el("p", { class: "search-panel__note", text: "Mengambil feed dari sumber publik..." })
        )
      );
    } else if (!d.items.length) {
      const missingKey = /TMDb API Key/i.test(d.error || "");
      grid.append(
        el(
          "div",
          { class: "empty", style: "grid-column:1/-1" },
          el("p", { class: "empty__title", text: d.error ? "Katalog tidak tersedia" : "Tidak ada hasil" }),
          el("p", {
            text: d.error
              ? d.error
              : "Coba kategori lain, urutan feed lain, atau kata kunci yang lebih pendek.",
          }),
          missingKey
            ? el(
                "div",
                { style: "margin-top:14px" },
                el("button", {
                  class: "btn btn--light",
                  type: "button",
                  text: "Buka Pengaturan",
                  onclick: () => setView("settings"),
                })
              )
            : null
        )
      );
    } else {
      for (const item of d.items) grid.append(discoverCard(item));
    }

    return el("div", { class: "discovery" }, searchRow, grid);
  }

  function openDiscoverItem(item) {
    const isSeries = SERIES_CATEGORIES.has(item.category);
    const seasonInput = isSeries
      ? el("input", { class: "input", type: "number", min: "1", value: "1", "aria-label": "Season" })
      : null;
    const episodeInput = isSeries
      ? el("input", {
          class: "input",
          type: "number",
          min: "1",
          placeholder: "Episode (opsional)",
          "aria-label": "Episode",
        })
      : null;

    const results = el("div", { class: "disc-releases" });
    results.append(
      el("p", {
        class: "search-panel__note",
        text: "Tekan Cari rilis untuk memeriksa indexer dan status cache TorBox.",
      })
    );
    const note = el("p", {
      class: "search-panel__note",
      text: "Rilis CACHED siap instan. UNCACHED tetap bisa dikirim, tetapi TorBox mengunduh lebih dulu sebelum impor ke Library.",
    });

    const metaLine = [item.year, Number(item.rating) > 0 ? `★ ${Number(item.rating).toFixed(1)}` : "", catLabel(item.category)]
      .filter(Boolean)
      .join(" · ");

    const poster = el(
      "div",
      { class: "disc-detail__poster" },
      el("div", { class: "card__fallback", text: initials(item.title), "aria-hidden": "true" })
    );
    if (item.poster_url) {
      const img = el("img", {
        alt: "",
        referrerpolicy: "no-referrer",
        src: item.poster_url,
      });
      img.addEventListener("error", () => img.remove());
      poster.append(img);
    }

    const controls = el(
      "div",
      { class: "discover-search" },
      isSeries ? field("Season", seasonInput) : null,
      isSeries ? field("Episode", episodeInput) : null,
      el("button", {
        class: "btn btn--primary",
        type: "button",
        html: `${ICON.search}<span>Cari rilis</span>`,
        onclick: (ev) => {
          const btn = ev.currentTarget;
          const season = isSeries ? Number(seasonInput.value) || 1 : 1;
          const episode = isSeries && episodeInput.value ? Number(episodeInput.value) : null;
          runDiscoverReleaseSearch(item, { season, episode }, results, note, btn, modal);
        },
      })
    );

    const modal = openModal({
      title: item.title || "Tanpa judul",
      subtitle: metaLine,
      wide: true,
      body: el(
        "div",
        { class: "disc-detail" },
        el(
          "div",
          { class: "disc-detail__head" },
          poster,
          el(
            "div",
            { class: "disc-detail__info" },
            item.overview
              ? el("p", { class: "detail__plot", text: item.overview })
              : el("p", { class: "search-panel__note", text: "Belum ada sinopsis dari sumber katalog." }),
            el(
              "div",
              { class: "tagrow" },
              ...(item.year ? [el("span", { class: "tag", text: String(item.year) })] : []),
              ...(Number(item.rating) > 0
                ? [el("span", { class: "tag", text: `★ ${Number(item.rating).toFixed(1)}` })]
                : []),
              ...(item.episodes ? [el("span", { class: "tag", text: `${item.episodes} episode` })] : [])
            )
          )
        ),
        el(
          "div",
          { class: "sheet__section" },
          el("h3", { class: "sheet__label", text: "Kirim ke TorBox Cloud" }),
          controls,
          results,
          note
        )
      ),
    });
  }

  async function runDiscoverReleaseSearch(item, opts, host, note, button, modal) {
    const label = button ? button.innerHTML : "";
    if (button) {
      button.disabled = true;
      button.innerHTML = `${ICON.refresh}<span>Mencari...</span>`;
    }
    host.replaceChildren(
      el("div", { class: "search-panel" }, el("p", { class: "search-panel__note", text: "Mencari rilis di indexer dan memeriksa cache TorBox..." }))
    );
    try {
      const data = await api(
        "/api/discover/search-cache",
        jsonBody({
          query: item.search_query || item.title,
          category: item.category,
          tmdb_id: item.tmdb_id || null,
          year: item.year || null,
          season: SERIES_CATEGORIES.has(item.category) ? opts.season : null,
          episode: SERIES_CATEGORIES.has(item.category) ? opts.episode : null,
        })
      );
      const rows = Array.isArray(data.results) ? data.results : [];
      if (!rows.length) {
        host.replaceChildren(
          el("p", { class: "search-panel__note", text: "Tidak ada rilis yang cocok untuk judul ini." })
        );
        return;
      }
      const summary = el("p", {
        class: "search-panel__note",
        text: `${numberLabel(data.cached_count || 0)} CACHED / ${numberLabel(rows.length)} rilis ditemukan.`,
      });
      host.replaceChildren(summary, ...rows.map((rel) => discoverReleaseRow(rel, item, opts, modal)));
      if (note) note.textContent = "Pilih rilis CACHED untuk impor instan, atau UNCACHED untuk unduh di TorBox lebih dulu.";
    } catch (err) {
      host.replaceChildren(el("p", { class: "search-panel__note", text: `Gagal mencari rilis: ${err.message}` }));
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = label || `${ICON.search}<span>Cari rilis</span>`;
      }
    }
  }

  function discoverReleaseRow(rel, item, opts, modal) {
    const cached = Boolean(rel.cached);
    const row = el(
      "div",
      { class: "disc-release" },
      el(
        "div",
        { class: "disc-release__main" },
        el(
          "div",
          { class: "tagrow" },
          el("span", { class: "tag " + (cached ? "tag--ok" : "tag--warn"), text: cached ? "CACHED" : "UNCACHED" }),
          rel.kind ? el("span", { class: "tag", text: String(rel.kind) }) : null,
          rel.source ? el("span", { class: "tag", text: String(rel.source) }) : null
        ),
        el("p", { class: "disc-release__title", text: rel.title || "" })
      )
    );
    const add = el("button", {
      class: "btn " + (cached ? "btn--primary" : "btn--ghost") + " btn--sm",
      type: "button",
      text: "Tambahkan",
    });
    add.addEventListener("click", async () => {
      add.disabled = true;
      add.textContent = "Mengirim...";
      try {
        await api(
          "/api/torbox/add-cached",
          jsonBody({
            ...rel,
            series_title: SERIES_CATEGORIES.has(item.category) ? item.title : "",
            category: item.category,
            season: SERIES_CATEGORIES.has(item.category) ? opts.season : 1,
            delete_remote_on_replace: true,
          })
        );
        toast("Dikirim ke TorBox Cloud. Auto-import ke Library berjalan.", "ok");
        if (modal) modal.close();
      } catch (err) {
        add.disabled = false;
        add.textContent = "Tambahkan";
        toast(err.message, "error");
      }
    });
    row.append(add);
    return row;
  }

  function ensureAdultDiscover(force) {
    const d = state.discover;
    if (!d.adultApproved) d.adultApproved = adultGateApproved();
    if (!d.adultApproved) return;
    if (!d.adultProviders.length) {
      loadAdultProviders().then(() => loadAdultFeed(force));
      return;
    }
    loadAdultFeed(force);
  }

  async function loadAdultProviders() {
    const d = state.discover;
    try {
      const data = await api("/api/adult/providers");
      d.adultProviders = Array.isArray(data.providers) ? data.providers : [];
      d.adultModes = Array.isArray(data.modes) && data.modes.length
        ? data.modes
        : [
            { id: "popular", label: "Populer" },
            { id: "new", label: "Terbaru" },
            { id: "top_rated", label: "Rating Tinggi" },
          ];
    } catch (err) {
      d.adultError = err.message;
    }
  }

  async function loadAdultFeed(force, append) {
    const d = state.discover;
    if (!d.adultApproved || d.adultLoading) return;
    const q = d.adultQ.trim();
    const page = append ? d.adultPage + 1 : 1;
    const key = `${d.adultProvider}|${d.adultMode}|${q}|${page}`;
    if (!force && !append && d.adultLoadedKey === key && d.adultItems.length) {
      render();
      return;
    }
    d.adultLoading = true;
    d.adultError = "";
    d.adultPage = page;
    render();
    try {
      let data;
      if (q) {
        const params = new URLSearchParams({ q, page: String(page) });
        if (d.adultProvider !== "all") params.set("providers", d.adultProvider);
        data = await api(`/api/adult/search?${params}`);
      } else {
        const params = new URLSearchParams({
          provider: d.adultProvider,
          mode: d.adultMode,
          page: String(page),
        });
        data = await api(`/api/adult/feed?${params}`);
      }
      const items = Array.isArray(data.items) ? data.items : [];
      d.adultItems = append ? d.adultItems.concat(items) : items;
      d.adultWarnings = Array.isArray(data.warnings) ? data.warnings : [];
      d.adultHasMore = Boolean(data.has_more);
      d.adultLoadedKey = key;
    } catch (err) {
      if (!append) {
        d.adultItems = [];
        d.adultWarnings = [];
      } else {
        d.adultPage = page - 1;
      }
      d.adultError = err.message;
    } finally {
      d.adultLoading = false;
      render();
    }
  }

  function renderAdultDiscover() {
    const d = state.discover;
    if (!d.adultApproved) {
      return el(
        "div",
        { class: "disc-gate" },
        el("span", { class: "disc-gate__badge", text: "18+" }),
        el("h2", { class: "disc-gate__title", text: "English Adult Discovery" }),
        el("p", {
          class: "disc-gate__text",
          text: "Bagian ini memuat katalog dewasa dari sumber publik. Konfirmasi bahwa Anda berusia 18 tahun atau lebih untuk melanjutkan.",
        }),
        el("button", {
          class: "btn btn--primary",
          type: "button",
          text: "Saya 18+ — tampilkan katalog",
          onclick: () => approveAdultGate(),
        })
      );
    }

    const providerOptions = [{ value: "all", label: "Semua sumber" }];
    for (const p of d.adultProviders) {
      if (!p.enabled) continue;
      const status = ADULT_STATUS_LABEL[p.status] || p.status || "";
      providerOptions.push({ value: p.id, label: `${p.label || p.id}${status ? ` (${status})` : ""}` });
    }

    const providerSelect = selectNode(providerOptions, d.adultProvider, (value) => {
      d.adultProvider = value;
      d.adultLoadedKey = "";
      loadAdultFeed(true);
    });

    const input = el("input", {
      class: "input",
      type: "search",
      value: d.adultQ,
      placeholder: "Cari judul di sumber adult...",
      "aria-label": "Cari di katalog English Adult",
    });
    input.addEventListener("input", () => {
      d.adultQ = input.value;
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      runAdultSearch();
    });

    const searchRow = el(
      "div",
      { class: "discover-search" },
      input,
      el("button", {
        class: "btn btn--primary",
        type: "button",
        html: `${ICON.search}<span>Cari</span>`,
        onclick: () => runAdultSearch(),
      }),
      d.adultQ
        ? el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            text: "Reset",
            onclick: () => {
              d.adultQ = "";
              d.adultLoadedKey = "";
              loadAdultFeed(true);
            },
          })
        : null
    );

    const warnings = el("div", { class: "disc-warnings" });
    if (d.adultWarnings.length) {
      for (const warn of d.adultWarnings.slice(0, 3)) {
        warnings.append(el("p", { class: "search-panel__note", text: warn.message || String(warn) }));
      }
    }

    const controls = el(
      "div",
      { class: "discover-controls__row" },
      field("Sumber", providerSelect),
      searchRow
    );

    const grid = el("div", { class: "lib-grid disc-adult-grid" });
    if (!d.adultItems.length && !d.adultLoading) {
      grid.append(
        el(
          "div",
          { class: "empty", style: "grid-column:1/-1" },
          el("p", { class: "empty__title", text: d.adultError ? "Katalog tidak tersedia" : "Tidak ada item" }),
          el("p", {
            text: d.adultError
              ? d.adultError
              : "Coba sumber lain, mode lain, atau kata kunci yang lebih pendek.",
          })
        )
      );
    } else {
      d.adultItems.forEach((item, index) => grid.append(adultDiscoverCard(item, index)));
    }

    const loadMore = d.adultHasMore
      ? el(
          "div",
          { class: "disc-loadmore" },
          el("button", {
            class: "btn btn--ghost",
            type: "button",
            text: d.adultLoading ? "Memuat..." : "Muat lebih banyak",
            disabled: d.adultLoading ? true : null,
            onclick: () => loadAdultFeed(false, true),
          })
        )
      : null;

    return el("div", { class: "discovery discovery--adult" }, controls, warnings, grid, loadMore);
  }

  function runAdultSearch() {
    const d = state.discover;
    const q = d.adultQ.trim();
    if (q && q.length < 2) {
      toast("Kata kunci minimal 2 karakter.", "error");
      return;
    }
    d.adultLoadedKey = "";
    loadAdultFeed(true);
  }

  function adultDiscoverCard(item, index) {
    const title = item.title || "Tanpa judul";
    const frame = el("div", { class: "card__frame" });
    frame.append(el("div", { class: "card__fallback", text: initials(title), "aria-hidden": "true" }));
    const thumb = /^https?:\/\//i.test(String(item.thumb || "")) ? String(item.thumb) : "";
    if (thumb) {
      const img = el("img", {
        class: "card__img",
        alt: "",
        loading: "lazy",
        decoding: "async",
        referrerpolicy: "no-referrer",
        src: thumb,
      });
      img.addEventListener("load", () => img.classList.add("is-loaded"));
      img.addEventListener("error", () => img.remove());
      frame.append(img);
    }

    const badges = el("div", { class: "card__badges" });
    badges.append(
      el("span", { class: "card__badge card__badge--cat", text: item.site_label || item.provider || "Adult" })
    );
    if (item.duration_sec) {
      badges.append(el("span", { class: "card__badge", text: durationLabel(item.duration_sec) }));
    }
    frame.append(badges);

    const subParts = [];
    if (Number(item.rating) > 0) subParts.push(`★ ${Number(item.rating).toFixed(1)}`);
    if (item.views) subParts.push(`${numberLabel(item.views)} tayangan`);

    return el(
      "button",
      {
        class: "card disc-video-card",
        type: "button",
        "aria-label": `Buka ${title}`,
        dataset: { index: String(index) },
        onclick: () => openAdultDiscoverItem(item),
      },
      frame,
      el(
        "div",
        { class: "card__body" },
        el("p", { class: "card__title clamp-2", text: title }),
        subParts.length ? el("p", { class: "card__sub", text: subParts.join(" · ") }) : null
      )
    );
  }

  function openAdultDiscoverItem(item) {
    const host = el("div", { class: "disc-detail" });
    const metaLine = [item.site_label || item.provider, durationLabel(item.duration_sec)].filter(Boolean).join(" · ");
    const modal = openModal({
      title: item.title || "English Adult",
      subtitle: metaLine,
      wide: true,
      body: host,
    });
    host.append(
      el(
        "div",
        { class: "search-panel" },
        el("p", { class: "search-panel__note", text: "Membaca cover, sinopsis, dan durasi dari halaman provider..." })
      )
    );

    (async () => {
      let detail = null;
      let page = {};
      try {
        detail = await api(
          `/api/adult/detail?provider=${encodeURIComponent(item.provider)}&id=${encodeURIComponent(item.provider_id)}`
        );
      } catch (_) {
        detail = null;
      }
      try {
        const streams = await api(
          "/api/adult/streams",
          jsonBody({ provider: item.provider, id: item.provider_id })
        );
        page = streams.page_meta || {};
      } catch (_) {
        page = {};
      }
      paintAdultDiscoverDetail(host, modal, item, detail, page);
    })();
  }

  function paintAdultDiscoverDetail(host, modal, item, detail, page) {
    const merged = Object.assign({}, item, (detail && detail.item) || {});
    const tpdb = detail && detail.tpdb && detail.tpdb.status === "found" ? detail.tpdb : {};
    const inLibrary = detail && Array.isArray(detail.in_library) ? detail.in_library : [];
    const tags = [...(Array.isArray(merged.tags) ? merged.tags : []), ...(Array.isArray(tpdb.tags) ? tpdb.tags : [])]
      .map((t) => String(t).trim())
      .filter(Boolean);
    const uniqueTags = [...new Set(tags)];
    const posterUrlValue = String(page.poster || merged.poster || merged.thumb || "").trim();
    const description = String(page.description || tpdb.plot || merged.description || "").trim();
    const duration = durationLabel(merged.duration_sec || page.duration_sec);

    const poster = el(
      "div",
      { class: "disc-detail__poster disc-detail__poster--video" },
      el("div", { class: "card__fallback", text: initials(merged.title), "aria-hidden": "true" })
    );
    if (/^https?:\/\//i.test(posterUrlValue)) {
      const img = el("img", { alt: "", referrerpolicy: "no-referrer", src: posterUrlValue });
      img.addEventListener("error", () => img.remove());
      poster.append(img);
    }

    const note = el("p", {
      class: "search-panel__note",
      text: "Link halaman dikirim ke TorBox WebDL; monitor akan mengimpor ke Library sebagai English Adult. Batas WebDL: 100 GB per tautan.",
    });

    const submit = el("button", {
      class: "btn btn--primary",
      type: "button",
      html: `${ICON.download}<span>Simpan ke TorBox Cloud (WebDL)</span>`,
    });
    submit.addEventListener("click", async () => {
      submit.disabled = true;
      submit.textContent = "Mengirim...";
      try {
        const payload = adultWebdlPayload(merged, tpdb, page);
        const data = await api("/api/adult/webdl", jsonBody(payload));
        note.textContent = `${data.message || "Terkirim ke TorBox WebDL."}${data.tracked_id ? ` (monitor #${data.tracked_id})` : ""}`;
        toast(data.message || "Link dikirim ke TorBox WebDL.", "ok");
        modal.close();
      } catch (err) {
        note.textContent = `Gagal mengirim: ${err.message}`;
        toast(`Gagal mengirim: ${err.message}`, "error");
      } finally {
        submit.disabled = false;
        submit.innerHTML = `${ICON.download}<span>Simpan ke TorBox Cloud (WebDL)</span>`;
      }
    });

    host.replaceChildren(
      el(
        "div",
        { class: "disc-detail__head" },
        poster,
        el(
          "div",
          { class: "disc-detail__info" },
          el("h3", { class: "disc-detail__title", text: merged.title || "Tanpa judul" }),
          el(
            "div",
            { class: "tagrow" },
            el("span", { class: "tag", text: merged.site_label || merged.provider || "Adult" }),
            duration ? el("span", { class: "tag", text: duration }) : null,
            Number(merged.rating) > 0 ? el("span", { class: "tag", text: `★ ${Number(merged.rating).toFixed(1)}` }) : null,
            merged.views ? el("span", { class: "tag", text: `${numberLabel(merged.views)} tayangan` }) : null
          ),
          description
            ? el("p", { class: "detail__plot", text: description })
            : el("p", { class: "search-panel__note", text: "Sinopsis belum tersedia dari halaman provider." }),
          uniqueTags.length
            ? el("div", { class: "tagrow" }, ...uniqueTags.slice(0, 8).map((t) => el("span", { class: "tag", text: t })))
            : null,
          el("p", {
            class: "search-panel__note",
            text: inLibrary.length
              ? `Sudah ada di Library (${inLibrary.length}): ${inLibrary.map((it) => it.title || it.id).join(" · ")}`
              : "Belum ada judul serupa di Library kategori English Adult.",
          })
        )
      ),
      el(
        "div",
        { class: "sheet__section" },
        el("h3", { class: "sheet__label", text: "Simpan ke Library" }),
        el("div", { class: "disc-detail__actions" }, submit),
        note
      )
    );
  }

  function adultWebdlPayload(merged, tpdb, page) {
    const tags = [...(Array.isArray(merged.tags) ? merged.tags : []), ...(Array.isArray(tpdb.tags) ? tpdb.tags : [])]
      .map((t) => String(t).trim())
      .filter(Boolean);
    const uniqueTags = [...new Set(tags)];
    const poster = String(page.poster || merged.poster || merged.thumb || "").trim();
    const metadata = {
      name: tpdb.title || page.title || merged.title || "English Adult",
      description: String(page.description || tpdb.plot || uniqueTags.join(", ")).trim(),
      studio: tpdb.studio || "",
      year: tpdb.year || 0,
      runtime: Math.round(Number(merged.duration_sec || page.duration_sec || 0) / 60) || 0,
      poster,
      genres: uniqueTags,
      cast: Array.isArray(tpdb.performers) ? tpdb.performers : [],
      imdbRating: Number(tpdb.rating || 0) || 0,
      _roxxySource: "english_adult",
    };
    const payload = { url: merged.page_url, title: merged.title, metadata };
    if (/^https?:\/\//i.test(poster)) payload.poster_url = poster;
    return payload;
  }

  /* ---------------------------- Curation -------------------------------- */

  function field(labelText, control) {
    return el("label", { class: "field" }, el("span", { class: "field__label", text: labelText }), control);
  }

  function textInput(value, attrs) {
    const props = Object.assign(
      { class: "input", type: "text", value: value == null ? "" : String(value) },
      attrs || {}
    );
    return el("input", props);
  }

  function categorySelect(current) {
    const node = el("select", { class: "select" });
    for (const cat of CATEGORY_ORDER) {
      node.append(el("option", { value: cat, text: catLabel(cat) }));
    }
    node.value = CATEGORY_ORDER.includes(current) ? current : "uncategorized";
    return node;
  }

  function refreshMediaAfterCuration(itemId) {
    return loadAll({ silent: true }).then(() => {
      closeDetail();
      const fresh = readItem(itemId);
      if (fresh) openDetail(fresh);
    });
  }

  function openEditMetadataSheet(item) {
    const title = textInput(item.title || "");
    const seriesTitle = textInput(item.series_title || "");
    const category = categorySelect(item.content_category);
    const season = textInput(item.season_num || "", { type: "number", min: "0" });
    const episode = textInput(item.episode_num || "", { type: "number", min: "0" });
    const code = textInput(item.code_id || "");
    const year = textInput(item.year || "", { type: "number", min: "0" });
    const runtime = textInput(item.runtime || "", { type: "number", min: "0" });
    const studio = textInput(item.studio || "");
    const plot = el("textarea", { class: "input", rows: "3", value: item.plot || "" });

    const save = el("button", { class: "btn btn--primary", type: "button", text: "Simpan metadata" });
    const modal = openModal({
      title: "Edit metadata",
      subtitle: item.file_name || "",
      wide: true,
      body: el(
        "div",
        { class: "curate-grid" },
        field("Judul", title),
        field("Serial", seriesTitle),
        field("Kategori", category),
        field("Season", season),
        field("Episode", episode),
        field("Kode", code),
        field("Tahun", year),
        field("Durasi (menit)", runtime),
        field("Studio", studio),
        el("div", { class: "curate-grid__full" }, field("Sinopsis", plot))
      ),
      footer: el(
        "div",
        { class: "curate-actions" },
        el("button", { class: "btn btn--ghost", type: "button", text: "Batal", onclick: () => modal.close() }),
        save
      ),
    });

    save.addEventListener("click", async () => {
      save.disabled = true;
      save.textContent = "Menyimpan...";
      try {
        await api(
          `/api/media/${item.id}/manual`,
          jsonBody({
            title: title.value,
            series_title: seriesTitle.value,
            content_category: category.value,
            season_num: season.value,
            episode_num: episode.value,
            code_id: code.value,
            year: year.value,
            runtime: runtime.value,
            studio: studio.value,
            plot: plot.value,
          })
        );
        modal.close();
        toast("Metadata disimpan", "ok");
        await refreshMediaAfterCuration(item.id);
      } catch (err) {
        toast(err.message, "error");
        save.disabled = false;
        save.textContent = "Simpan metadata";
      }
    });
  }

  function openClassificationSheet(item) {
    const normalized = textInput(item.title || "");
    const category = categorySelect(item.content_category);
    const lock = el("input", { type: "checkbox", checked: true, "aria-label": "Kunci klasifikasi" });
    const previewBox = el("div", { class: "classification-preview" });

    const previewBtn = el("button", {
      class: "btn btn--ghost",
      type: "button",
      html: `${ICON.spark}<span>Pratinjau</span>`,
    });
    const applyBtn = el("button", { class: "btn btn--primary", type: "button", text: "Simpan klasifikasi" });

    const modal = openModal({
      title: "Klasifikasi manual",
      subtitle: item.title || "",
      wide: true,
      body: el(
        "div",
        { class: "curate-grid" },
        field("Judul ternormalisasi", normalized),
        field("Kategori", category),
        el(
          "label",
          { class: "field field--inline" },
          lock,
          el("span", { class: "field__label", text: "Kunci klasifikasi (jangan ditimpa otomatis)" })
        ),
        el("div", { class: "curate-grid__full" }, previewBox)
      ),
      footer: el("div", { class: "curate-actions" }, previewBtn, applyBtn),
    });

    previewBtn.addEventListener("click", async () => {
      previewBtn.disabled = true;
      previewBox.replaceChildren(el("p", { class: "search-panel__note", text: "Menjalankan klasifikasi..." }));
      try {
        const data = await api(
          `/api/media/${item.id}/classification/preview`,
          jsonBody({ normalized_title: normalized.value })
        );
        const result = data.result || {};
        const detected = result.detected_category || "needs_review";
        const confidence = result.category_confidence != null ? Number(result.category_confidence) : null;
        const warnings = Array.isArray(result.warnings) ? result.warnings : [];
        const signals = Array.isArray(result.signals) ? result.signals : [];
        previewBox.replaceChildren(
          el("p", {
            class: "classification-preview__title",
            text: `Rekomendasi: ${CATEGORY_LABEL[detected] || "Needs Review"}${confidence != null ? ` (${Math.round(confidence * 100)}% yakin)` : ""}`,
          }),
          el("p", { class: "search-panel__note", text: signals.length ? `Sinyal: ${signals.join(", ")}` : "Tidak ada sinyal kuat." }),
          el("p", { class: "search-panel__note", text: warnings.length ? `Peringatan: ${warnings.join("; ")}` : "Tanpa peringatan." })
        );
        if (result.detected_category && CATEGORY_ORDER.includes(result.detected_category)) {
          category.value = result.detected_category;
        }
      } catch (err) {
        previewBox.replaceChildren(el("p", { class: "search-panel__note", text: `Gagal pratinjau: ${err.message}` }));
      } finally {
        previewBtn.disabled = false;
      }
    });

    applyBtn.addEventListener("click", async () => {
      applyBtn.disabled = true;
      applyBtn.textContent = "Menyimpan...";
      try {
        await api(
          `/api/media/${item.id}/classification/apply`,
          jsonBody({
            category: category.value,
            normalized_title: normalized.value,
            lock: lock.checked,
            note: "Manual confirmation from v2 Discovery",
          })
        );
        modal.close();
        toast(`Kategori disimpan: ${catLabel(category.value)}`, "ok");
        await refreshMediaAfterCuration(item.id);
      } catch (err) {
        toast(err.message, "error");
        applyBtn.disabled = false;
        applyBtn.textContent = "Simpan klasifikasi";
      }
    });
  }

  function openPosterSheet(item) {
    const status = item.poster_status || (item.has_poster ? "found" : "not_checked");
    const input = textInput(item.poster_is_manual ? item.poster_url || "" : "", {
      placeholder: "https://.../poster.jpg",
    });
    const save = el("button", { class: "btn btn--primary", type: "button", text: "Simpan poster" });
    const automatic = item.poster_is_manual
      ? el("button", { class: "btn btn--ghost", type: "button", text: "Kembali ke poster otomatis" })
      : null;
    const retry = el("button", { class: "btn btn--ghost", type: "button", text: "Coba poster provider" });

    const modal = openModal({
      title: "Poster",
      subtitle: `Status: ${String(status).replace(/_/g, " ")}${item.poster_is_manual ? " · manual" : ""}`,
      body: el(
        "div",
        { class: "curate-grid" },
        el("div", { class: "curate-grid__full" }, field("URL poster manual", input)),
        el("p", {
          class: "search-panel__note",
          text: "Gambar diunduh dan divalidasi dulu; poster manual tidak akan ditimpa oleh pencarian otomatis.",
        })
      ),
      footer: el(
        "div",
        { class: "curate-actions" },
        automatic,
        retry,
        save
      ),
    });

    save.addEventListener("click", async () => {
      const url = input.value.trim();
      if (!url) {
        toast("Tempel URL gambar poster dulu.", "error");
        return;
      }
      save.disabled = true;
      save.textContent = "Mengunduh...";
      try {
        await api(`/api/media/${item.id}/poster/manual`, jsonBody({ poster_url: url }));
        modal.close();
        toast("Poster manual disimpan dan dikunci", "ok");
        await refreshMediaAfterCuration(item.id);
      } catch (err) {
        toast(err.message, "error");
        save.disabled = false;
        save.textContent = "Simpan poster";
      }
    });

    if (automatic) {
      automatic.addEventListener("click", async () => {
        automatic.disabled = true;
        try {
          await api(`/api/media/${item.id}/poster/automatic`, jsonBody({}));
          modal.close();
          toast("Poster otomatis dipulihkan", "ok");
          await refreshMediaAfterCuration(item.id);
        } catch (err) {
          toast(err.message, "error");
          automatic.disabled = false;
        }
      });
    }

    retry.addEventListener("click", async () => {
      retry.disabled = true;
      try {
        await api(`/api/media/${item.id}/poster/retry`, jsonBody({}));
        modal.close();
        toast("Pencarian poster dijalankan", "ok");
        await refreshMediaAfterCuration(item.id);
      } catch (err) {
        toast(err.message, "error");
        retry.disabled = false;
      }
    });
  }

  async function deletePhysicalItem(item) {
    const where = item.source_type === "cloud" ? "TorBox Cloud" : "disk lokal";
    const target = item.file_path || item.file_name || "";
    const ok = await confirmDialog({
      title: "Hapus file permanen",
      message: `Hapus "${item.title || item.file_name}" dari ${where}?\n\nTarget: ${target}\n\nFile/torrent, subtitle, poster cache, entri database, dan keanggotaan playlist akan dihapus. Tindakan ini tidak bisa dibatalkan.`,
      confirmLabel: "Hapus permanen",
      danger: true,
    });
    if (!ok) return;
    try {
      const data = await api(
        `/api/media/${item.id}/delete-physical`,
        jsonBody({ delete_local: true, delete_remote: true })
      );
      closeDetail();
      toast(data.message || `"${item.title || item.file_name}" dihapus permanen`, "ok");
      await loadAll({ silent: true });
    } catch (err) {
      toast(err.message, "error");
    }
  }

  /* ------------------------------ Routing ------------------------------- */

  function viewFromHash() {
    const match = String(location.hash || "").match(/^#\/([a-z]+)/);
    if (match && VIEWS[match[1]]) return match[1];
    return "home";
  }

  function setView(view) {
    if (!VIEWS[view]) view = "home";
    state.view = view;
    state.limit = 60;
    closeAllModals();
    for (const panel of document.querySelectorAll(".detail")) panel.remove();
    state.detailId = null;
    document.body.style.overflow = "";
    state.selectMode = false;
    clearSelection();
    if (state.view !== "downloads") stopDownloadsPoll();
    if (location.hash !== `#/${view}`) {
      history.pushState({ view }, "", `#/${view}`);
    }
    render();
    window.scrollTo({ top: 0, behavior: "auto" });
    if (view === "downloads") {
      if (!state.downloads.lastLoaded || Date.now() - state.downloads.lastLoaded > 15000) loadDownloads();
      startDownloadsPoll();
    }
    ensureDiscoveryView();
  }

  function openFromHash() {
    const match = String(location.hash || "").match(/^#media-(\d+)/);
    if (!match || !state.items.length) return;
    if (document.querySelector(".detail")) return;
    const item = readItem(match[1]);
    if (item) openDetail(item.id, { push: false });
  }

  function onHashChange() {
    const match = String(location.hash || "").match(/^#media-(\d+)/);
    let closedDetail = false;
    if (!match && document.querySelector(".detail")) {
      for (const panel of document.querySelectorAll(".detail")) panel.remove();
      state.detailId = null;
      closedDetail = true;
      if (!modalStack.length) document.body.style.overflow = "";
    }
    if (match) {
      if (state.items.length) {
        const item = readItem(match[1]);
        if (item && !document.querySelector(".detail--single")) openDetail(item.id, { push: false });
      }
      return;
    }
    const view = viewFromHash();
    if (view !== state.view) {
      state.view = view;
      state.limit = 60;
      if (view !== "downloads") stopDownloadsPoll();
      render();
      window.scrollTo({ top: 0, behavior: "auto" });
      if (view === "downloads") {
        loadDownloads();
        startDownloadsPoll();
      }
      ensureDiscoveryView();
    } else if (closedDetail) {
      render();
    }
  }

  /* -------------------------------- Cards -------------------------------- */

  const VIEWS = {
    home: renderHome,
    discover: renderDiscover,
    library: renderLibrary,
    series: renderSeriesView,
    playlists: renderPlaylistsView,
    downloads: renderDownloads,
    stats: renderStats,
    settings: renderSettings,
  };

  /* ------------------------------- Detail -------------------------------- */

  function fact(label, value, isNode) {
    if (!value && value !== 0) return null;
    return el(
      "div",
      { class: "fact" },
      el("span", { class: "fact__label", text: label }),
      isNode ? value : el("span", { class: "fact__value", text: String(value) })
    );
  }

  function buildStars(item) {
    const row = el("div", { class: "stars", role: "group", "aria-label": "Rating" });
    const current = Number(item.rating_star || 0);
    for (let i = 1; i <= 5; i++) {
      const star = el("button", {
        class: "stars__star" + (i <= current ? " is-on" : ""),
        type: "button",
        "aria-label": `${i} bintang`,
        text: "★",
        onclick: async () => {
          try {
            await api(`/api/media/${item.id}/rating`, jsonBody({ rating_star: i }));
            item.rating_star = i;
            [...row.children].forEach((c, idx) => c.classList.toggle("is-on", idx < i));
            toast(`Rating ${i} bintang disimpan`, "ok");
          } catch (err) {
            toast(err.message, "error");
          }
        },
      });
      row.append(star);
    }
    return row;
  }

  function playItem(item) {
    return api(`/api/media/${item.id}/play`, jsonBody({}))
      .then(() => toast(`Memutar: ${item.title || item.file_name}`, "ok"))
      .catch((err) => toast(err.message, "error"));
  }

  function closeDetail() {
    const panels = document.querySelectorAll(".detail");
    const panel = panels[panels.length - 1];
    if (!panel) return;
    panel.remove();
    if (!document.querySelector(".detail")) document.body.style.overflow = "";
    if (panel.classList.contains("detail--single")) {
      state.detailId = null;
      if (String(location.hash).startsWith("#media-")) {
        history.replaceState(null, "", `#/${state.view}`);
      }
    }
    render();
  }

  function playlistsForItem(itemId) {
    return state.playlists.filter((pl) =>
      (Array.isArray(pl.items) ? pl.items : []).some((it) => Number(it.id) === Number(itemId))
    );
  }

  function openDetail(idOrItem, options) {
    const item = idOrItem && typeof idOrItem === "object" ? idOrItem : readItem(idOrItem);
    if (!item) return;
    const opts = options || {};
    state.detailId = Number(item.id);

    const poster = item.has_poster
      ? el("img", { class: "detail__poster", alt: "", src: posterUrl(item) })
      : el("div", { class: "detail__poster card__fallback", text: initials(item.title || item.file_name) });

    const metaRow = el("div", { class: "detail__meta" });
    [item.year, runtimeLabel(item.runtime), catLabel(item.content_category), bytesLabel(item.file_size), dateLabel(item.updated_at)]
      .filter(Boolean)
      .forEach((t, i) => {
        if (i > 0) metaRow.append(el("span", { class: "dot", text: "·" }));
        metaRow.append(el("span", { text: t }));
      });

    const tags = [];
    if (Array.isArray(item.genres) && item.genres.length) tags.push(...item.genres.slice(0, 10));
    if (Array.isArray(item.actresses) && item.actresses.length) {
      tags.push(...item.actresses.slice(0, 8).map((a) => String(a)));
    }

    const isWatched = () => truthyFlag(item.is_watched);
    const watchedBtn = el("button", {
      class: "btn btn--ghost",
      type: "button",
      html: `${isWatched() ? ICON.check : ICON.plus}<span>${isWatched() ? "Sudah Ditonton" : "Tandai Ditonton"}</span>`,
      onclick: async (ev) => {
        const btn = ev.currentTarget;
        const next = isWatched() ? 0 : 1;
        btn.disabled = true;
        try {
          await api(`/api/media/${item.id}/watched`, jsonBody({ is_watched: next }));
          item.is_watched = next;
          btn.innerHTML = `${next ? ICON.check : ICON.plus}<span>${next ? "Sudah Ditonton" : "Tandai Ditonton"}</span>`;
          btn.classList.toggle("is-on", Boolean(next));
          toast(next ? "Ditandai sudah ditonton" : "Ditandai belum ditonton", "ok");
        } catch (err) {
          toast(err.message, "error");
        } finally {
          btn.disabled = false;
        }
      },
    });
    watchedBtn.classList.toggle("is-on", isWatched());

    const chipsHost = el("div", { class: "detail__playlists" });
    const addHost = el("div", { class: "detail__extra" });
    const curationHost = el(
      "div",
      { class: "detail__extra detail__extra--curation" },
      el("span", { class: "detail__extra-label", text: "Kurasi" }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.list}<span>Edit metadata</span>`,
        onclick: () => openEditMetadataSheet(item),
      }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.spark}<span>Klasifikasi</span>`,
        onclick: () => openClassificationSheet(item),
      }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        html: `${ICON.film}<span>Poster</span>`,
        onclick: () => openPosterSheet(item),
      }),
      el("span", { style: "flex:1 1 auto" }),
      el("button", {
        class: "btn btn--danger btn--sm",
        type: "button",
        html: `${ICON.trash}<span>Hapus fisik</span>`,
        onclick: () => deletePhysicalItem(item),
      })
    );

    function paintPlaylistSection() {
      const mine = playlistsForItem(item.id);
      chipsHost.replaceChildren(
        ...mine.map((pl) =>
          el(
            "span",
            { class: "pl-chip" },
            el("span", { text: pl.name || "Playlist" }),
            el("button", {
              class: "pl-chip__x",
              type: "button",
              "aria-label": `Hapus dari ${pl.name}`,
              text: "×",
              onclick: async (ev) => {
                ev.currentTarget.disabled = true;
                try {
                  await api(`/api/playlists/${pl.id}/items/${item.id}`, { method: "DELETE" });
                  await refreshPlaylists();
                  paintPlaylistSection();
                  toast(`Dihapus dari "${pl.name}"`, "ok");
                } catch (err) {
                  toast(err.message, "error");
                }
              },
            })
          )
        )
      );

      const available = state.playlists.filter((pl) => !mine.some((m) => Number(m.id) === Number(pl.id)));
      let selectEl = null;
      if (available.length) {
        selectEl = el("select", { class: "select", "aria-label": "Pilih playlist tujuan" });
        for (const pl of available) {
          selectEl.append(el("option", { value: String(pl.id), text: pl.name || "Playlist" }));
        }
      }

      addHost.replaceChildren(
        el("span", { class: "detail__extra-label", text: "Tambah ke playlist" }),
        selectEl || el("span", { class: "muted", style: "font-size:12px", text: state.playlists.length ? "Sudah ada di semua playlist" : "Belum ada playlist" }),
        available.length
          ? el("button", {
              class: "btn btn--ghost btn--sm",
              type: "button",
              html: `${ICON.plus}<span>Tambah</span>`,
              onclick: async (ev) => {
                const btn = ev.currentTarget;
                btn.disabled = true;
                try {
                  await api(`/api/playlists/${selectEl.value}/items`, jsonBody({ media_ids: [item.id] }));
                  await waitForTask();
                  await refreshPlaylists();
                  paintPlaylistSection();
                  toast("Ditambahkan ke playlist", "ok");
                } catch (err) {
                  toast(err.message, "error");
                } finally {
                  btn.disabled = false;
                }
              },
            })
          : null,
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.list}<span>Playlist baru</span>`,
          onclick: () => openCreatePlaylistModal([item.id]),
        })
      );
    }

    function subtitleAction(language, label) {
      return el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        text: label,
        onclick: async (ev) => {
          const btn = ev.currentTarget;
          btn.disabled = true;
          const original = btn.textContent;
          btn.textContent = "Mencari...";
          try {
            const data = await api(`/api/media/${item.id}/subtitle/${language}`, jsonBody({}));
            item.has_subtitle = 1;
            item.subtitle_lang = language;
            btn.textContent = data && data.already_available ? `${label} ✓` : `${label} siap`;
            toast(
              data && data.already_available
                ? `Subtitle ${language.toUpperCase()} sudah tersedia`
                : `Subtitle ${language.toUpperCase()} diunduh`,
              "ok"
            );
          } catch (err) {
            btn.textContent = original;
            btn.disabled = false;
            toast(err.message, "error");
          }
        },
      });
    }

    const enriching = el("button", {
      class: "btn btn--ghost btn--sm",
      type: "button",
      html: `${ICON.spark}<span>Enrich ulang</span>`,
      onclick: async (ev) => {
        const btn = ev.currentTarget;
        btn.disabled = true;
        btn.innerHTML = `${ICON.refresh}<span>Mengambil metadata...</span>`;
        try {
          await api(`/api/media/${item.id}/enrich`, jsonBody({}));
          await loadAll({ silent: true });
          const fresh = readItem(item.id);
          if (fresh) {
            closeDetail();
            openDetail(fresh, { push: "replace" });
          }
          toast("Metadata diperbarui", "ok");
        } catch (err) {
          toast(err.message, "error");
          btn.disabled = false;
          btn.innerHTML = `${ICON.spark}<span>Enrich ulang</span>`;
        }
      },
    });

    const posterBtn = !item.has_poster
      ? el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.refresh}<span>Coba poster</span>`,
          onclick: async (ev) => {
            const btn = ev.currentTarget;
            btn.disabled = true;
            try {
              await api(`/api/media/${item.id}/poster/retry`, jsonBody({}));
              toast("Pencarian poster dijalankan", "ok");
            } catch (err) {
              toast(err.message, "error");
            } finally {
              btn.disabled = false;
            }
          },
        })
      : null;

    const badges = el(
      "div",
      { class: "detail__badges" },
      el("span", { class: "pill pill--idle", text: catLabel(item.content_category) }),
      el("span", { class: "pill pill--idle", text: item.source_type === "local" ? "Lokal" : "TorBox Cloud" }),
      el("span", { class: "pill pill--" + (isWatched() ? "ok" : "idle"), text: isWatched() ? "Ditonton" : "Belum ditonton" }),
      el("span", { class: "pill pill--" + (truthyFlag(item.has_subtitle) ? "ok" : "idle"), text: truthyFlag(item.has_subtitle) ? "Subtitle ada" : "Tanpa subtitle" }),
      el("span", { class: "pill pill--" + (truthyFlag(item.has_poster) ? "ok" : "idle"), text: truthyFlag(item.has_poster) ? "Cover ada" : "Tanpa cover" })
    );

    const panel = el(
      "div",
      { class: "detail detail--single" },
      el("button", { class: "detail__close", type: "button", "aria-label": "Tutup", html: ICON.back, onclick: closeDetail }),
      el(
        "div",
        { class: "detail__hero" },
        el("div", {
          class: "detail__art",
          style: item.has_poster ? `background-image:url('${posterUrl(item)}')` : "",
        }),
        el("div", { class: "detail__scrim" }),
        el(
          "div",
          { class: "detail__inner" },
          el(
            "div",
            { class: "detail__layout" },
            poster,
            el(
              "div",
              {},
              el("h1", { class: "detail__title", text: item.title || item.file_name || "Tanpa Judul" }),
              metaRow,
              badges,
              item.plot ? el("p", { class: "detail__plot", text: item.plot }) : null,
              el(
                "div",
                { class: "detail__actions" },
                el("button", {
                  class: "btn btn--light",
                  type: "button",
                  html: `${ICON.play}<span>Putar</span>`,
                  onclick: () => playItem(item),
                }),
                watchedBtn,
                buildStars(item)
              )
            )
          ),
          el(
            "div",
            { class: "detail__extra" },
            el("span", { class: "detail__extra-label", text: "Subtitle" }),
            subtitleAction("id", "Cari subtitle ID"),
            subtitleAction("en", "Cari subtitle EN"),
            el("span", { style: "flex:1 1 auto" }),
            enriching,
            posterBtn
          ),
          chipsHost,
          addHost,
          curationHost,
          el(
            "div",
            { class: "detail__facts" },
            fact("Studio", item.studio),
            fact("Sutradara", item.director),
            fact("Sumber", item.source_type === "local" ? "Lokal" : item.source_type === "cloud" ? "TorBox Cloud" : item.source_type),
            fact("Ukuran", bytesLabel(item.file_size)),
            fact("Tahun", item.year),
            fact("Kode", item.code_id),
            fact("Seri", item.series_title),
            fact(
              "Episode",
              item.season_num || item.episode_num
                ? `S${item.season_num || "?"} · E${item.episode_num || "?"}`
                : null
            ),
            fact("Klasifikasi", item.classification_status || "pending"),
            fact("Kategori", catLabel(item.content_category)),
            fact("Playlist", item.playlist_names && item.playlist_names.length ? item.playlist_names.join(", ") : null),
            tags.length
              ? el(
                  "div",
                  { class: "fact" },
                  el("span", { class: "fact__label", text: "Tag" }),
                  el("div", { class: "tagrow" }, ...tags.map((t) => el("span", { class: "tag", text: t })))
                )
              : null
          ),
          item.file_name ? el("p", { class: "detail__filename", text: item.file_name }) : null
        )
      )
    );

    paintPlaylistSection();
    document.body.append(panel);
    document.body.style.overflow = "hidden";

    if (opts.push !== false) {
      const url = `#media-${item.id}`;
      if (location.hash !== url) {
        if (opts.push === "replace") history.replaceState({ detail: item.id }, "", url);
        else history.pushState({ detail: item.id }, "", url);
      }
    }

    panel.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeDetail();
      }
    });
    $(".detail__close", panel)?.focus();
  }

  /* --------------------------- Series panel ----------------------------- */

  function openSeries(group) {
    closeDetail();
    const fresh = groupSeries(state.items).find((g) => g.key === group.key);
    if (fresh) group = fresh;

    const posterItem = group.posterItem || group.items[0];
    const seasons = seasonNumbers(group);
    let activeSeason = seasons[0] || 1;
    let status = null;
    let officialSeasons = [];
    let loadingStatus = false;
    let statusToken = 0;

    state.detailId = null;

    const seasonBar = el("div", { class: "series__seasons", role: "tablist", "aria-label": "Pilih season" });
    const statusLine = el("p", { class: "series__status" });
    const listHost = el("div", { class: "series__list" });
    const searchHost = el("div", { class: "series__search" });
    const downloadHost = el("div", { class: "series__downloads", "aria-live": "polite" });
    const seasonActions = el("div", { class: "detail__actions", style: "margin-top:12px" });
    const searchState = { loading: false, scope: "", data: null, error: "" };
    const downloadState = {
      active: [],
      syncing: [],
      imported: [],
      handled: new Set(),
      loading: false,
      error: "",
      timer: null,
    };
    let searchToken = 0;

    const allSeasons = () => {
      const set = new Set(seasons);
      for (const s of officialSeasons) set.add(Number(s.season_number) || 1);
      return Array.from(set).sort((a, b) => a - b);
    };

    const countIn = (season) =>
      group.items.filter((it) => Number(it.season_num || 1) === Number(season)).length;

    const itemsFor = (season) => {
      if (status && Number(status.active_season) === Number(season) && Array.isArray(status.items)) {
        return status.items
          .map((it) => state.items.find((x) => Number(x.id) === Number(it.id)) || it)
          .sort(episodeOrder);
      }
      return group.items.filter((it) => Number(it.season_num || 1) === Number(season));
    };

    function paintTabs() {
      seasonBar.replaceChildren(
        ...allSeasons().map((season) => {
          const count = countIn(season);
          const official = officialSeasons.find((s) => Number(s.season_number) === Number(season));
          const expected = official ? Number(official.episode_count || 0) : 0;
          const isActive = Number(season) === Number(activeSeason);
          const summary = expected
            ? `${count}/${expected}`
            : count
              ? String(count)
              : "";
          return el("button", {
            class: "series__tab" + (isActive ? " is-active" : ""),
            type: "button",
            role: "tab",
            "aria-selected": isActive ? "true" : "false",
            text: `Season ${String(season).padStart(2, "0")}${summary ? ` · ${summary}` : ""}`,
            title: expected
              ? `${count} dari ${expected} episode ada di library`
              : `${count} episode di library`,
            onclick: () => selectSeason(season),
          });
        })
      );
    }

    function paintStatus() {
      let text;
      if (loadingStatus) {
        text = "Memuat struktur season resmi...";
      } else if (status && Number(status.active_season) === Number(activeSeason)) {
        text = status.status_message || "";
        const missing = status.missing_episodes || [];
        if (missing.length) {
          const shown = missing.slice(0, 24).map(episodeLabel).join(", ");
          text += `${text ? " · " : ""}Belum ada: ${shown}${missing.length > 24 ? " …" : ""}`;
        }
      } else {
        const count = itemsFor(activeSeason).length;
        text = count
          ? `${count} episode tersedia di library`
          : "Belum ada episode season ini di library";
      }
      statusLine.textContent = text;
      statusLine.classList.toggle("is-loading", loadingStatus);
    }

    function missingEpisodes() {
      if (status && Number(status.active_season) === Number(activeSeason) && Array.isArray(status.missing_episodes)) {
        return status.missing_episodes.map(Number).filter((n) => n > 0);
      }
      return [];
    }

    function paintSeasonActions() {
      const items = itemsFor(activeSeason);
      const missing = missingEpisodes();
      const statusReady = Boolean(status && Number(status.active_season) === Number(activeSeason));
      const allWatched = items.length > 0 && items.every((it) => truthyFlag(it.is_watched));
      seasonActions.replaceChildren(
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${allWatched ? ICON.check : ICON.plus}<span>${allWatched ? "Tandai belum ditonton" : "Tandai season ditonton"}</span>`,
          disabled: items.length === 0,
          onclick: () => markSeason(items, !allWatched),
        }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.play}<span>Putar season</span>`,
          disabled: items.length === 0,
          onclick: () => playSeason(),
        }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          html: `${ICON.search}<span>Cari semua episode hilang</span>`,
          disabled: !statusReady || missing.length === 0 || searchState.loading,
          onclick: () => runEpisodeSearch(missing),
        })
      );
    }

    function episodeRow(episode) {
      const watched = truthyFlag(episode.is_watched);
      const meta = [
        runtimeLabel(episode.runtime),
        bytesLabel(episode.file_size),
        truthyFlag(episode.has_subtitle) ? `SUB ${String(episode.subtitle_lang || "").toUpperCase()}` : "",
      ]
        .filter(Boolean)
        .join(" · ");

      return el(
        "article",
        { class: "episode" + (watched ? " is-watched" : "") },
        el("span", { class: "episode__num", text: episodeLabel(episode.episode_num), "aria-hidden": "true" }),
        el(
          "button",
          { class: "episode__body", type: "button", onclick: () => openDetail(episode) },
          el("span", { class: "episode__title", text: episode.title || episode.file_name || "Tanpa Judul" }),
          episode.file_name ? el("span", { class: "episode__file", text: episode.file_name }) : null,
          meta ? el("span", { class: "episode__meta", text: meta }) : null
        ),
        el(
          "div",
          { class: "episode__actions" },
          el("button", {
            class: "btn btn--ghost btn--sm" + (watched ? " is-on" : ""),
            type: "button",
            "aria-pressed": watched ? "true" : "false",
            title: watched ? "Tandai belum ditonton" : "Tandai ditonton",
            "aria-label": watched ? "Tandai belum ditonton" : "Tandai ditonton",
            html: ICON.check,
            onclick: () => toggleEpisodeWatched(episode),
          }),
          el("button", {
            class: "btn btn--light btn--sm",
            type: "button",
            html: `${ICON.play}<span>Putar</span>`,
            onclick: () => playItem(episode),
          })
        )
      );
    }

    function missingRow(episode) {
      return el(
        "article",
        { class: "episode episode--missing" },
        el("span", { class: "episode__num", text: episodeLabel(episode), "aria-hidden": "true" }),
        el(
          "div",
          { class: "episode__body" },
          el("span", { class: "episode__title", text: `Episode ${String(episode).padStart(2, "0")} — belum ada di library` }),
          el("span", { class: "episode__meta", text: "Cari rilis dari TorBox cache, torrent publik, NZB, atau Roxxy." })
        ),
        el(
          "div",
          { class: "episode__actions" },
          el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            html: `${ICON.search}<span>Cari episode</span>`,
            disabled: searchState.loading,
            onclick: () => runEpisodeSearch([episode]),
          })
        )
      );
    }

    function releaseRow(release, torboxReady) {
      const seeds = release.seeders;
      const leech = release.leechers;
      const peers = release.peers != null ? release.peers : (seeds != null || leech != null ? (seeds || 0) + (leech || 0) : null);
      const peerText = seeds != null || leech != null
        ? `Seed ${numberLabel(seeds || 0)} · Leech ${numberLabel(leech || 0)}${peers != null ? ` · Peer ${numberLabel(peers)}` : ""}`
        : "";
      const meta = [
        peerText,
        release.size ? bytesLabel(release.size) : "",
        release.quality || "",
        release.kind === "usenet" ? "NZB" : "Torrent",
      ]
        .filter(Boolean)
        .join(" · ");

      return el(
        "div",
        { class: "release" },
        el(
          "div",
          { class: "release__main" },
          el(
            "div",
            { class: "release__badges" },
            el("span", {
              class:
                "pill " +
                (release.cached === true ? "pill--ok" : release.cached === false ? "" : "pill--idle"),
              text: release.cached === true ? "CACHED" : release.cached === false ? "UNCACHED" : "CACHE ?",
              title:
                release.cached === true
                  ? "Sudah tersedia di cache TorBox"
                  : release.cached === false
                    ? "Belum ada di cache TorBox"
                    : "Status cache NZB tidak dapat dipastikan",
            }),
            el("span", { class: "release__source", text: release.source || release.indexer || "-" }),
            release.indexer && release.indexer !== release.source
              ? el("span", { class: "release__source", text: release.indexer })
              : null,
            release.episode ? el("span", { class: "release__ep", text: episodeLabel(release.episode) }) : null
          ),
          el("div", { class: "release__title", text: release.title || "-", title: release.title || "" }),
          el("div", { class: "release__meta", text: meta })
        ),
        el("button", {
          class: "btn btn--ghost btn--sm",
          type: "button",
          text: release.busy ? "Menambah..." : "+ TorBox",
          disabled: Boolean(release.busy) || !torboxReady,
          title: torboxReady ? "" : "TorBox API key belum diatur",
          onclick: () => addReleaseToTorbox(release),
        })
      );
    }

    function releaseGroup(entry, torboxReady) {
      const label = entry.episode
        ? `Episode ${String(entry.episode).padStart(2, "0")}`
        : entry.label || "Paket season";
      return el(
        "div",
        { class: "release-group" },
        el(
          "div",
          { class: "release-group__head" },
          el("span", { class: "release-group__title", text: label }),
          el("span", {
            class: "release-group__meta",
            text: `${entry.candidates.length} rilis · ${entry.cached_count || 0} cached`,
          })
        ),
        ...entry.candidates.map((release) => releaseRow(release, torboxReady))
      );
    }

    function sourceChip(source) {
      const kind = source.state === "ok" ? "is-ok" : source.state === "error" ? "is-error" : source.state === "unconfigured" ? "is-warn" : "";
      const count = source.count ? ` · ${source.count}` : "";
      return el(
        "span",
        {
          class: "source-chip " + kind,
          title: source.error || source.note || "",
        },
        el("span", { class: "source-chip__dot" }),
        el("span", { text: `${source.label}${count}` })
      );
    }

    function searchPanel(data) {
      const groups = (data.episodes || []).filter((entry) => (entry.candidates || []).length);
      const torboxReady = Boolean(data.torbox && data.torbox.configured);
      const notes = Array.isArray(data.notes) ? data.notes : [];
      const sourceRow = el("div", { class: "source-row" }, ...(data.sources || []).map(sourceChip));

      const body = [];
      if (notes.length) {
        body.push(el("p", { class: "search-panel__note", text: notes.join(" ") }));
      }
      body.push(sourceRow);
      const directRoxxy = (data.sources || []).find(
        (source) => source.key === "roxxy" && source.note && source.state === "ok"
      );
      if (directRoxxy) {
        body.push(
          el(
            "p",
            { class: "search-panel__note" },
            el("span", { text: `${directRoxxy.note} ` }),
            el("a", { class: "search-panel__link", href: "/roxxy", text: "Buka Roxxy" })
          )
        );
      }
      if (groups.length) {
        body.push(...groups.map((entry) => releaseGroup(entry, torboxReady)));
      } else {
        body.push(
          el("p", {
            class: "search-panel__empty",
            text: data.total_checked
              ? "Tidak ada rilis magnet/NZB yang cocok untuk episode ini."
              : "Tidak ada rilis ditemukan. Torrent publik bisa sedang sepi; NZB premium (NZBGeek) membantu untuk episode baru.",
          })
        );
      }

      return el(
        "div",
        { class: "search-panel" },
        el(
          "div",
          { class: "search-panel__head" },
          el("span", { class: "search-panel__title", text: `Hasil pencarian · Season ${String(activeSeason).padStart(2, "0")}` }),
          el("span", {
            class: "search-panel__meta",
            text: `${numberLabel(data.cached_count || 0)} cached / ${numberLabel(data.total_checked || 0)} rilis · ${numberLabel(data.elapsed_ms || 0)} ms`,
          })
        ),
        ...body
      );
    }

    function paintSearch() {
      const missing = missingEpisodes();
      const statusReady = Boolean(status && Number(status.active_season) === Number(activeSeason));
      const seasonLabel = String(activeSeason).padStart(2, "0");
      const children = [];
      children.push(
        el(
          "div",
          { class: "series__searchbar" },
          el(
            "div",
            { class: "series__searchbar-text" },
            el("strong", {
              text: !statusReady
                ? "Memuat struktur season..."
                : missing.length
                  ? `${missing.length} episode belum ada di Season ${seasonLabel}`
                  : `Season ${seasonLabel} lengkap`,
            }),
            el("span", {
              text: !statusReady
                ? "Jumlah episode resmi sedang diambil dari TMDb/TVMaze/AniList."
                : missing.length
                  ? "Cari di TorBox cache, torrent publik, NZB, dan Roxxy (Anime/Donghua)."
                  : "Semua episode resmi sudah ada di library.",
            })
          ),
          el("button", {
            class: "btn btn--primary btn--sm",
            type: "button",
            html: `${ICON.search}<span>Cari semua episode hilang</span>`,
            disabled: !statusReady || searchState.loading || missing.length === 0,
            onclick: () => runEpisodeSearch(missing),
          })
        )
      );
      if (searchState.loading) {
        children.push(
          el(
            "div",
            { class: "search-panel" },
            el("p", { class: "search-panel__note", text: searchState.label || "Mencari rilis..." })
          )
        );
      } else if (searchState.error) {
        children.push(
          el(
            "div",
            { class: "search-panel" },
            el("p", { class: "search-panel__note", text: searchState.error })
          )
        );
      } else if (searchState.data) {
        children.push(searchPanel(searchState.data));
      }
      searchHost.replaceChildren(...children);
    }

    function downloadTitle(entry) {
      return [entry.episode ? episodeLabel(entry.episode) : "", entry.title || "TorBox item"]
        .filter(Boolean)
        .join(" · ");
    }

    function paintDownloads() {
      const { active, syncing, imported, error } = downloadState;
      const total = active.length + syncing.length + imported.length;
      if (!total && !error) {
        downloadHost.replaceChildren();
        return;
      }
      const rows = [];
      for (const entry of active) {
        const progress = normalizeProgress(entry.progress);
        rows.push(
          el(
            "div",
            { class: "row series-dl" },
            el(
              "span",
              { class: "row__main" },
              el("span", {
                class: "row__title row__title--wrap",
                text: downloadTitle(entry),
                title: entry.title || "",
              }),
              el("span", {
                class: "row__sub",
                text: [entry.kind === "usenet" ? "NZB" : "Torrent", entry.download_state || "queued"]
                  .filter(Boolean)
                  .join(" · "),
              }),
              el(
                "div",
                { class: "meterline" },
                progressBar(progress),
                el("span", { class: "meterline__val", text: `${Math.round(progress)}%` })
              )
            ),
            pill("downloading")
          )
        );
      }
      for (const entry of syncing) {
        rows.push(
          el(
            "div",
            { class: "row series-dl" },
            el(
              "span",
              { class: "row__main" },
              el("span", { class: "row__title row__title--wrap", text: downloadTitle(entry) }),
              el("span", {
                class: "row__sub",
                text: [entry.library_stage || "import", entry.library_message || "Menyiapkan Library..."]
                  .filter(Boolean)
                  .join(" · "),
              })
            ),
            el("span", { class: "pill pill--queued", text: "IMPORT" })
          )
        );
      }
      for (const entry of imported) {
        rows.push(
          el(
            "div",
            { class: "row series-dl" },
            el(
              "span",
              { class: "row__main" },
              el("span", { class: "row__title row__title--wrap", text: downloadTitle(entry) }),
              el("span", { class: "row__sub", text: "Selesai · sudah masuk Library" })
            ),
            pill("completed")
          )
        );
      }
      downloadHost.replaceChildren(
        el(
          "div",
          { class: "search-panel series__downloads-panel" },
          el(
            "div",
            { class: "search-panel__head" },
            el("span", { class: "search-panel__title", text: "Unduhan episode" }),
            el("span", { class: "search-panel__meta", text: `${numberLabel(total)} dipantau` })
          ),
          error ? el("p", { class: "search-panel__note", text: error }) : null,
          el("div", { class: "rowlist" }, ...rows)
        )
      );
    }

    async function refreshImportedEpisodes(entries) {
      const fresh = [];
      for (const entry of entries || []) {
        const key = String(entry.key || "");
        if (!key || downloadState.handled.has(key)) continue;
        downloadState.handled.add(key);
        fresh.push(entry);
      }
      if (!fresh.length) return;
      const episodes = fresh
        .map((entry) => Number(entry.episode))
        .filter((num) => num > 0);
      toast(
        episodes.length
          ? `${episodes.map(episodeLabel).join(", ")} selesai diimpor ke Library`
          : "Unduhan selesai diimpor ke Library",
        "ok"
      );
      if (searchState.data && Array.isArray(searchState.data.episodes)) {
        searchState.data.episodes = searchState.data.episodes.filter(
          (entry) => !episodes.includes(Number(entry.episode))
        );
      }
      await loadAll({ silent: true });
      const freshGroup = groupSeries(state.items).find((g) => g.key === group.key);
      if (freshGroup) group = freshGroup;
      paintDownloads();
      await loadStatus(activeSeason);
    }

    async function pollSeriesDownloads() {
      if (downloadState.loading) return;
      downloadState.loading = true;
      const seasonAtStart = Number(activeSeason);
      try {
        const params = new URLSearchParams({
          series_title: group.title,
          season: String(seasonAtStart),
        });
        const data = await api(`/api/series/download-status?${params.toString()}`);
        if (!document.body.contains(panel) || seasonAtStart !== Number(activeSeason)) return;
        downloadState.active = Array.isArray(data.active) ? data.active : [];
        downloadState.syncing = Array.isArray(data.syncing) ? data.syncing : [];
        downloadState.imported = Array.isArray(data.newly_imported) ? data.newly_imported : [];
        downloadState.error = "";
        paintDownloads();
        await refreshImportedEpisodes(downloadState.imported);
      } catch (err) {
        if (seasonAtStart !== Number(activeSeason)) return;
        downloadState.error = err.message;
        paintDownloads();
      } finally {
        downloadState.loading = false;
      }
    }

    function stopDownloadPoll() {
      if (downloadState.timer) {
        clearInterval(downloadState.timer);
        downloadState.timer = null;
      }
    }

    function startDownloadPoll() {
      stopDownloadPoll();
      downloadState.timer = setInterval(() => {
        if (!document.body.contains(panel)) {
          stopDownloadPoll();
          return;
        }
        if (document.hidden) return;
        pollSeriesDownloads();
      }, 5000);
    }

    async function runEpisodeSearch(episodes) {
      const wanted = (episodes || []).map(Number).filter((n) => n > 0);
      if (!wanted.length || searchState.loading) return;
      const token = ++searchToken;
      const seasonAtStart = Number(activeSeason);
      searchState.loading = true;
      searchState.error = "";
      searchState.data = null;
      searchState.scope = wanted.join(",");
      searchState.label =
        wanted.length === 1
          ? `Mencari Episode ${String(wanted[0]).padStart(2, "0")} di TorBox cache, indexer, dan Roxxy...`
          : `Mencari ${wanted.length} episode hilang di TorBox cache, indexer, dan Roxxy...`;
      paintSearch();
      try {
        const data = await api(
          "/api/series/episode-search",
          jsonBody({
            series_title: group.title,
            category: group.category || "tv_show",
            season: seasonAtStart,
            episodes: wanted,
            tmdb_id: (status && status.tmdb_id) || null,
          })
        );
        if (token !== searchToken || seasonAtStart !== Number(activeSeason)) return;
        searchState.data = data;
      } catch (err) {
        if (token !== searchToken) return;
        searchState.error = err.message;
      } finally {
        if (token === searchToken) {
          searchState.loading = false;
          paintSearch();
        }
      }
    }

    async function addReleaseToTorbox(release) {
      if (!release || release.busy) return;
      release.busy = true;
      paintSearch();
      try {
        const data = await api(
          "/api/torbox/add-cached",
          jsonBody({
            kind: release.kind || "torrent",
            magnet: release.magnet || "",
            nzb_url: release.nzb_url || "",
            title: release.title || "",
            series_title: group.title,
            category: group.category || "tv_show",
            season: Number(activeSeason),
            episode: Number(release.episode) || null,
          })
        );
        toast(data.message || "Rilis ditambahkan ke TorBox", "ok");
        pollSeriesDownloads();
      } catch (err) {
        toast(err.message, "error");
      } finally {
        release.busy = false;
        paintSearch();
      }
    }

    function paintEpisodes() {
      const items = itemsFor(activeSeason);
      paintSeasonActions();
      const expected = status && Number(status.active_season) === Number(activeSeason)
        ? Number(status.expected_total || 0)
        : 0;

      const byEpisode = new Map();
      const unnumbered = [];
      let maxPresent = 0;
      for (const item of items) {
        const number = parseInt(item.episode_num, 10);
        if (Number.isFinite(number) && number > 0) {
          if (!byEpisode.has(number)) byEpisode.set(number, []);
          byEpisode.get(number).push(item);
          maxPresent = Math.max(maxPresent, number);
        } else {
          unnumbered.push(item);
        }
      }

      const total = Math.max(expected, maxPresent);
      const rows = [];
      for (let episode = 1; episode <= total; episode += 1) {
        const copies = byEpisode.get(episode);
        if (copies && copies.length) {
          rows.push(...copies.map(episodeRow));
        } else {
          rows.push(missingRow(episode));
        }
      }
      rows.push(...unnumbered.map(episodeRow));

      if (!rows.length) {
        listHost.replaceChildren(
          el("p", { class: "series__empty", text: `Belum ada episode Season ${activeSeason} di library.` })
        );
        return;
      }
      listHost.replaceChildren(...rows);
    }

    function paintAll() {
      paintTabs();
      paintStatus();
      paintEpisodes();
      paintDownloads();
      paintSearch();
    }

    function selectSeason(season) {
      const next = Number(season);
      if (next === Number(activeSeason) && status && Number(status.active_season) === next) return;
      activeSeason = next;
      status = null;
      searchToken += 1;
      searchState.loading = false;
      searchState.data = null;
      searchState.error = "";
      downloadState.active = [];
      downloadState.syncing = [];
      downloadState.imported = [];
      downloadState.error = "";
      paintAll();
      loadStatus(next);
      pollSeriesDownloads();
    }

    async function loadStatus(season) {
      const token = ++statusToken;
      loadingStatus = true;
      paintStatus();
      try {
        const params = new URLSearchParams({
          series_title: group.title,
          category: group.category || "tv_show",
          season: String(season),
        });
        const data = await api(`/api/series/episodes-status?${params.toString()}`);
        if (token !== statusToken || Number(season) !== Number(activeSeason)) return;
        status = data;
        if (Array.isArray(data.seasons) && data.seasons.length) {
          officialSeasons = data.seasons;
        }
      } catch (err) {
        if (token !== statusToken) return;
        status = null;
      } finally {
        if (token === statusToken) {
          loadingStatus = false;
          paintAll();
        }
      }
    }

    async function toggleEpisodeWatched(episode) {
      const next = truthyFlag(episode.is_watched) ? 0 : 1;
      try {
        await api(`/api/media/${episode.id}/watched`, jsonBody({ is_watched: next }));
        episode.is_watched = next;
        const local = group.items.find((it) => Number(it.id) === Number(episode.id));
        if (local) local.is_watched = next;
        paintAll();
      } catch (err) {
        toast(err.message, "error");
      }
    }

    async function markSeason(items, value) {
      let failures = 0;
      for (const it of items) {
        try {
          await api(`/api/media/${it.id}/watched`, jsonBody({ is_watched: value ? 1 : 0 }));
          it.is_watched = value ? 1 : 0;
          const local = group.items.find((x) => Number(x.id) === Number(it.id));
          if (local) local.is_watched = value ? 1 : 0;
        } catch (err) {
          failures += 1;
        }
      }
      paintAll();
      toast(
        failures
          ? `${items.length - failures} episode diperbarui, ${failures} gagal`
          : `${items.length} episode ditandai ${value ? "sudah" : "belum"} ditonton`,
        failures ? "error" : "ok"
      );
    }

    async function playSeason() {
      const items = itemsFor(activeSeason);
      if (!items.length) {
        toast(`Belum ada episode Season ${activeSeason} di library`, "error");
        return;
      }
      try {
        const data = await api("/api/media/play-batch", jsonBody({
          media_ids: items.map((it) => it.id),
          label: `${group.title} · Season ${activeSeason}`,
        }));
        toast(data.message || `Season ${activeSeason} dikirim ke player`, "ok");
      } catch (err) {
        toast(err.message, "error");
      }
    }

    const metaRow = el("div", { class: "detail__meta" });
    [group.year, `${seasons.length} Season`, `${group.items.length} Episode`, catLabel(group.category)]
      .filter(Boolean)
      .forEach((text, i) => {
        if (i > 0) metaRow.append(el("span", { class: "dot", text: "·" }));
        metaRow.append(el("span", { text: String(text) }));
      });
    if (entryStar(group)) {
      metaRow.append(el("span", { class: "dot", text: "·" }));
      metaRow.append(el("span", { text: `★ ${entryStar(group)}/5` }));
    }

    const poster = posterItem
      ? el("img", { class: "detail__poster", alt: "", src: posterUrl(posterItem) })
      : el("div", { class: "detail__poster card__fallback", text: initials(group.title) });

    const panel = el(
      "div",
      { class: "detail detail--series" },
      el("button", { class: "detail__close", type: "button", "aria-label": "Tutup", html: ICON.back, onclick: closeDetail }),
      el(
        "div",
        { class: "detail__hero" },
        el("div", {
          class: "detail__art",
          style: posterItem && posterItem.has_poster ? `background-image:url('${posterUrl(posterItem)}')` : "",
        }),
        el("div", { class: "detail__scrim" }),
        el(
          "div",
          { class: "detail__inner" },
          el(
            "div",
            { class: "detail__layout" },
            poster,
            el(
              "div",
              {},
              el("span", { class: "billboard__eyebrow", text: catLabel(group.category) }),
              el("h1", { class: "detail__title", text: group.title }),
              metaRow,
              group.plot ? el("p", { class: "detail__plot clamp-3", text: group.plot }) : null,
              el(
                "div",
                { class: "detail__actions" },
                el("button", {
                  class: "btn btn--light",
                  html: `${ICON.play}<span>Putar Episode Berikutnya</span>`,
                  onclick: () => playSeries(group),
                }),
                el("button", {
                  class: "btn btn--ghost",
                  html: `${ICON.list}<span>Putar Season</span>`,
                  onclick: playSeason,
                })
              )
            )
          ),
          el("div", { class: "series" }, seasonBar, statusLine, seasonActions, downloadHost, searchHost, listHost)
        )
      )
    );

    document.body.append(panel);
    document.body.style.overflow = "hidden";
    paintAll();
    loadStatus(activeSeason);
    startDownloadPoll();
    pollSeriesDownloads();
    $(".detail__close", panel)?.focus();
  }

  /* ------------------------------- Navbar -------------------------------- */

  function initNavbar() {
    const nav = $(".topnav");
    if (nav) {
      const onScroll = () => nav.classList.toggle("is-scrolled", window.scrollY > 24);
      window.addEventListener("scroll", onScroll, { passive: true });
      onScroll();
    }

    for (const btn of document.querySelectorAll("[data-view]")) {
      btn.addEventListener("click", () => setView(btn.dataset.view));
    }

    const input = $("#v2-search");
    if (input) {
      let timer = null;
      input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          const next = input.value.trim();
          if (next === state.q) return;
          state.q = next.toLowerCase();
          state.limit = 60;
          if (next && ["home", "downloads", "stats", "playlists"].includes(state.view)) {
            state.view = "library";
            if (location.hash !== "#/library") history.pushState({ view: "library" }, "", "#/library");
          }
          render();
          if (next) {
            window.scrollTo({ top: 0, behavior: "auto" });
          }
        }, 260);
      });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          input.value = "";
          state.q = "";
          render();
          input.blur();
        }
      });
    }

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "/" && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        const tag = (document.activeElement && document.activeElement.tagName) || "";
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
        ev.preventDefault();
        const search = $("#v2-search");
        if (search) search.focus();
      }
    });

    document.addEventListener("click", (ev) => {
      const panels = document.querySelectorAll(".detail");
      const panel = panels[panels.length - 1];
      if (!panel) return;
      if (ev.target === panel) closeDetail();
    });

    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      // Modal paling atas ditutup lebih dulu, baru panel detail.
      if (modalStack.length) {
        closeModal(modalStack[modalStack.length - 1].overlay);
        return;
      }
      if (document.querySelector(".detail")) closeDetail();
    });

    window.addEventListener("popstate", onHashChange);
    window.addEventListener("hashchange", onHashChange);
  }

  /* -------------------------------- Boot -------------------------------- */

  document.addEventListener("DOMContentLoaded", () => {
    initNavbar();
    const startView = viewFromHash();
    state.view = startView;
    state.discover.adultApproved = adultGateApproved();
    if (location.hash && !String(location.hash).startsWith("#media-") && location.hash !== `#/${startView}`) {
      history.replaceState({ view: startView }, "", `#/${startView}`);
    }
    loadAll();
  });

  // Ekspos minimal untuk debugging manual di konsol
  window.TMH = {
    state,
    load: loadAll,
    setView,
    openDetail,
    openSeries,
    closeDetail,
    groupSeries,
    mixedEntries,
    isEpisodeItem,
    loadDownloads,
  };
})();