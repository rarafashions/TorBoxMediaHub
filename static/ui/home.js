/* ==========================================================================
   TorBox Media Hub — Home v2 (vanilla JS, tanpa dependensi)
   Data: GET /api/bootstrap  →  { items[], playlists[], stats, env, version }
   Aksi: POST /api/media/<id>/{play,watched,rating}
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
    query: "",
    loading: true,
    detailId: null,
  };

  const CATEGORY_LABEL = {
    general_movie: "Film",
    tv_show: "Serial TV",
    anime: "Anime",
    donghua: "Donghua",
    jav: "JAV",
    english_adult: "Adult",
    indo: "Indonesia",
    uncategorized: "Belum Dikategorikan",
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

  function catLabel(cat) {
    return CATEGORY_LABEL[cat] || "Lainnya";
  }

  /* --------------------------- Series helpers ---------------------------- */

  const SERIES_CATEGORIES = new Set(["tv_show", "anime", "donghua"]);

  function isSeriesCategory(cat) {
    return SERIES_CATEGORIES.has(cat);
  }

  // Item episodic = kategori serial dan punya series_title atau nomor episode.
  // Anime movie tanpa keduanya tetap tampil sebagai kartu tunggal.
  function isEpisodeItem(item) {
    return isSeriesCategory(item.content_category) &&
      (Boolean(String(item.series_title || "").trim()) || item.episode_num != null);
  }

  function seriesKey(item) {
    return String(item.series_title || item.title || item.file_name || "").trim().toLowerCase();
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
          posterItem: null,
          year: null,
          rating: 0,
          plot: "",
          studio: "",
          updated_at: "",
        };
        map.set(key, group);
      }

      group.items.push(item);
      group.seasons.add(parseInt(item.season_num || 1, 10) || 1);
      if (Number(item.is_watched)) group.watched += 1;
      if (!group.posterItem && item.has_poster) group.posterItem = item;
      if (!group.year && item.year) group.year = item.year;
      if (Number(item.rating) > group.rating) group.rating = Number(item.rating);
      if (!group.plot && item.plot) group.plot = item.plot;
      if (!group.studio && item.studio) group.studio = item.studio;
      if (String(item.updated_at || "") > group.updated_at) group.updated_at = String(item.updated_at || "");
    }

    const groups = Array.from(map.values());
    for (const group of groups) {
      group.items.sort(
        (a, b) =>
          (Number(a.season_num || 1) - Number(b.season_num || 1)) ||
          (Number(a.episode_num || 0) - Number(b.episode_num || 0))
      );
      group.posterItem = group.posterItem || group.items.find((it) => it.has_poster) || group.items[0] || null;
    }
    return groups;
  }

  // Campurkan serial (satu entri per judul) dan item tunggal (film) agar rail
  // tidak dipenuhi episode individual.
  function mixedEntries(items) {
    const groups = groupSeries(items);
    const grouped = new Set(groups.flatMap((g) => g.items.map((it) => String(it.id))));
    const singles = items.filter((it) => !grouped.has(String(it.id)));
    return [...groups, ...singles];
  }

  function entryTitle(entry) {
    return entry.kind === "series" ? entry.title : entry.title || entry.file_name || "Tanpa Judul";
  }

  function entryRating(entry) {
    return Number(entry.rating || 0);
  }

  function entryStar(entry) {
    return entry.kind === "series"
      ? Number((entry.posterItem && entry.posterItem.rating_star) || 0)
      : Number(entry.rating_star || 0);
  }

  function entryUpdated(entry) {
    return String(entry.updated_at || "");
  }

  function entryCategory(entry) {
    return entry.kind === "series" ? entry.category : entry.content_category;
  }

  function entryWatched(entry) {
    if (entry.kind !== "series") return Number(entry.is_watched) === 1;
    return entry.items.length > 0 && entry.items.every((it) => Number(it.is_watched) === 1);
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
    }, 3200);
  }

  /* ------------------------------ API ----------------------------------- */

  let loadController = null;

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

  async function load(query, options) {
    const opts = options || {};
    if (loadController) loadController.abort();
    const controller = new AbortController();
    loadController = controller;

    state.loading = true;
    // Pencarian memakai skeleton: false agar grid lama tidak berkedip.
    if (opts.skeleton !== false) renderSkeleton();
    try {
      const params = new URLSearchParams();
      if (query) params.set("search", query);
      params.set("playlist_scope", "all");
      const data = await api(`/api/bootstrap?${params.toString()}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      state.items = Array.isArray(data.items) ? data.items : [];
      state.playlists = data.playlists || [];
      state.stats = data.stats || null;
      state.env = data.env || null;
      state.version = data.version || "";
      state.loading = false;
      render();
    } catch (err) {
      if (err.name === "AbortError" || controller.signal.aborted) return;
      state.loading = false;
      renderError(err.message);
    } finally {
      if (loadController === controller) loadController = null;
    }
  }

  async function refreshAll() {
    await load(state.query);
    toast("Katalog disegarkan", "ok");
  }

  /* ------------------------------ Billboard ----------------------------- */

  function pickHero(entries) {
    const withPoster = entries.filter((entry) => {
      const item = entry.kind === "series" ? entry.posterItem : entry;
      return Boolean(item && item.has_poster);
    });
    const pool = withPoster.length ? withPoster : entries.slice();
    if (!pool.length) return null;
    return pool.slice().sort((a, b) => {
      const sa = entryStar(a) * 2 + entryRating(a);
      const sb = entryStar(b) * 2 + entryRating(b);
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

    const primary = isSeries
      ? el("button", {
          class: "btn btn--light",
          html: `${ICON.play}<span>Putar Episode</span>`,
          onclick: () => playSeries(entry),
        })
      : el("button", {
          class: "btn btn--light",
          html: `${ICON.play}<span>Putar</span>`,
          onclick: () => playItem(item),
        });

    const secondary = isSeries
      ? el("button", {
          class: "btn btn--ghost",
          html: `${ICON.plus}<span>Episode</span>`,
          onclick: () => openSeries(entry),
        })
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
        el("div", { class: "billboard__actions" }, primary, secondary)
      )
    );
  }

  function playSeries(group) {
    const target =
      group.items.find((it) => !Number(it.is_watched)) || group.items[0];
    if (target) playItem(target);
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
          .sort(
            (a, b) =>
              Number(a.episode_num || 9999) - Number(b.episode_num || 9999) ||
              String(a.file_name || "").localeCompare(String(b.file_name || ""))
          );
      }
      return group.items.filter((it) => Number(it.season_num || 1) === Number(season));
    };

    function paintTabs() {
      seasonBar.replaceChildren(
        ...allSeasons().map((season) => {
          const count = countIn(season);
          const isActive = Number(season) === Number(activeSeason);
          return el("button", {
            class: "series__tab" + (isActive ? " is-active" : ""),
            type: "button",
            role: "tab",
            "aria-selected": isActive ? "true" : "false",
            text: `Season ${season}${count ? ` · ${count}` : ""}`,
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
          text += `${text ? " · " : ""}Missing: ${shown}${missing.length > 24 ? " …" : ""}`;
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

    function episodeRow(episode) {
      const watched = Number(episode.is_watched) === 1;
      const meta = [
        runtimeLabel(episode.runtime),
        bytesLabel(episode.file_size),
        episode.has_subtitle ? `SUB ${String(episode.subtitle_lang || "").toUpperCase()}` : "",
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

    function paintEpisodes() {
      const items = itemsFor(activeSeason);
      if (!items.length) {
        listHost.replaceChildren(
          el("p", { class: "series__empty", text: `Belum ada episode Season ${activeSeason} di library.` })
        );
        return;
      }
      listHost.replaceChildren(...items.map(episodeRow));
    }

    function paintAll() {
      paintTabs();
      paintStatus();
      paintEpisodes();
    }

    function selectSeason(season) {
      const next = Number(season);
      if (next === Number(activeSeason) && status && Number(status.active_season) === next) return;
      activeSeason = next;
      status = null;
      paintAll();
      loadStatus(next);
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
      } catch (_) {
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
      const next = Number(episode.is_watched) === 1 ? 0 : 1;
      try {
        await api(`/api/media/${episode.id}/watched`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_watched: next }),
        });
        episode.is_watched = next;
        const local = group.items.find((it) => Number(it.id) === Number(episode.id));
        if (local) local.is_watched = next;
        paintAll();
      } catch (err) {
        toast(err.message, "error");
      }
    }

    async function playSeason() {
      const items = itemsFor(activeSeason);
      if (!items.length) {
        toast(`Belum ada episode Season ${activeSeason} di library`, "error");
        return;
      }
      try {
        const data = await api("/api/media/play-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            media_ids: items.map((it) => it.id),
            label: `${group.title} · Season ${activeSeason}`,
          }),
        });
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
      el("button", {
        class: "detail__close",
        type: "button",
        "aria-label": "Tutup",
        html: ICON.back,
        onclick: closeDetail,
      }),
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
                  html: `${ICON.play}<span>Putar Season</span>`,
                  onclick: playSeason,
                })
              )
            )
          ),
          el("div", { class: "series" }, seasonBar, statusLine, listHost)
        )
      )
    );

    document.body.append(panel);
    document.body.style.overflow = "hidden";
    paintAll();
    loadStatus(activeSeason);
    $(".detail__close", panel)?.focus();
  }

  /* -------------------------------- Card -------------------------------- */

  function buildCard(entry, opts) {
    const options = opts || {};
    const isSeries = entry.kind === "series";
    const item = isSeries ? entry.posterItem : entry;
    if (!item) return null;

    const title = entryTitle(entry);
    const category = entryCategory(entry);
    const seasonCount = isSeries ? seasonNumbers(entry).length : 0;

    const frame = el("div", { class: "card__frame" });

    const fallback = el("div", {
      class: "card__fallback",
      text: initials(title),
      "aria-hidden": "true",
    });
    frame.append(fallback);

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

    // Badge kategori + jumlah season + sumber
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

    // Panel hover
    const hoverFacts = isSeries
      ? `${seasonCount} season · ${entry.items.length} episode`
      : [item.year || null, runtimeLabel(item.runtime) || null].filter(Boolean).join(" · ");

    const hoverinfo = el(
      "div",
      { class: "card__hoverinfo" },
      el("div", { class: "truncate", style: "font-weight:700;font-size:12.5px", text: title }),
      el("div", { style: "margin-top:3px;font-size:11px;color:var(--text-3)", text: hoverFacts || "—" }),
      item.genres && item.genres.length
        ? el("div", { class: "truncate", style: "margin-top:5px;font-size:11px;color:var(--text-2)", text: item.genres.slice(0, 3).join(" · ") })
        : null
    );
    frame.append(hoverinfo);

    const body = el(
      "div",
      { class: "card__body" },
      el("p", { class: "card__title clamp-2", text: title }),
      isSeries
        ? el("p", { class: "card__sub", text: `${entry.items.length} Episode` })
        : item.year
          ? el("p", { class: "card__sub", text: String(item.year) })
          : null
    );

    const card = el(
      "button",
      {
        class: "card" + (options.rank ? " card--ranked" : ""),
        type: "button",
        "aria-label": `${isSeries ? "Buka serial" : "Buka detail"} ${title}`,
        onclick: () => (isSeries ? openSeries(entry) : openDetail(item.id)),
      },
      options.rank ? el("span", { class: "card__rank", text: String(options.rank), "aria-hidden": "true" }) : null,
      frame,
      body
    );

    return card;
  }

  /* -------------------------------- Rail -------------------------------- */

  function railTitle(title, count) {
    return el(
      "div",
      { class: "rail__head" },
      el("h2", { class: "rail__title", text: title }),
      count ? el("span", { class: "rail__count", text: `${count} judul` }) : null
    );
  }

  function buildRail(title, items, opts) {
    const options = opts || {};
    if (!items.length) return null;

    const track = el("div", { class: "rail__track", role: "list" });
    items.forEach((item, idx) => {
      const wrapper = buildCard(item, { rank: options.ranked ? idx + 1 : 0 });
      if (!wrapper) return;
      wrapper.setAttribute("role", "listitem");
      track.append(wrapper);
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
      railTitle(title, items.length),
      el("div", { class: "rail__viewport" }, prev, track, next)
    );
  }

  /* ------------------------------ Rendering ----------------------------- */

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
        "div",
        { class: "empty", style: "margin-top:14vh" },
        el("p", { class: "empty__title", text: "Katalog gagal dimuat" }),
        el("p", { text: message }),
        el("button", { class: "btn btn--ghost", style: "margin-top:18px", html: `${ICON.refresh}<span>Coba lagi</span>`, onclick: () => load(state.query) })
      )
    );
  }

  function render() {
    const root = main();
    if (!root) return;

    if (!state.items.length) {
      root.replaceChildren(
        el(
          "div",
          { class: "empty", style: "margin-top:14vh" },
          el("p", { class: "empty__title", text: state.query ? "Tidak ada hasil" : "Library masih kosong" }),
          el("p", {
            text: state.query
              ? `Tidak ada judul yang cocok dengan "${state.query}".`
              : "Tambahkan media dari Library, Discovery, atau Video Feeds untuk mulai membangun katalog.",
          }),
          el("div", { style: "margin-top:20px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap" },
            el("a", { class: "btn btn--light", href: "/", text: "Buka Library" }),
            el("a", { class: "btn btn--ghost", href: "/roxxy", text: "Buka Video Feeds" })
          )
        )
      );
      return;
    }

    const entries = mixedEntries(state.items);
    const hero = pickHero(entries);
    const sections = [];

    if (hero) sections.push(renderBillboard(hero));

    // Baru ditambahkan (serial memakai episode terbaru sebagai penanda)
    const recent = entries
      .slice()
      .sort((a, b) => entryUpdated(b).localeCompare(entryUpdated(a)));
    sections.push(buildRail("Baru Ditambahkan", recent.slice(0, 20)));

    // Belum ditonton (serial dianggap selesai bila semua episodenya ditonton)
    const unwatched = recent.filter((entry) => !entryWatched(entry));
    sections.push(buildRail("Belum Ditonton", unwatched.slice(0, 20)));

    // Rating tertinggi (dengan badge peringkat)
    const rated = entries
      .filter((entry) => entryStar(entry) > 0)
      .sort((a, b) => entryStar(b) - entryStar(a))
      .slice(0, 10);
    sections.push(buildRail("Rating Tertinggi", rated, { ranked: true }));

    // Rail per kategori: serial tampil satu kartu per judul, bukan per episode
    const byCat = {};
    for (const entry of entries) {
      const cat = entryCategory(entry) || "uncategorized";
      (byCat[cat] = byCat[cat] || []).push(entry);
    }
    const catOrder = ["general_movie", "tv_show", "anime", "donghua", "jav", "english_adult", "indo"];
    const catKeys = [...catOrder.filter((c) => byCat[c]), ...Object.keys(byCat).filter((c) => !catOrder.includes(c))];
    for (const cat of catKeys) {
      // Semua kategori yang punya isi ditampilkan; rail bisa digeser horizontal.
      sections.push(buildRail(catLabel(cat), byCat[cat].slice(0, 20)));
    }

    root.replaceChildren(...sections.filter(Boolean));
  }

  /* ------------------------------ Detail -------------------------------- */

  function readItem(id) {
    return state.items.find((it) => Number(it.id) === Number(id)) || null;
  }

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
            await api(`/api/media/${item.id}/rating`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ rating_star: i }),
            });
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

  function openDetail(idOrItem) {
    const item = idOrItem && typeof idOrItem === "object" ? idOrItem : readItem(idOrItem);
    if (!item) return;

    state.detailId = Number(item.id);
    const poster = item.has_poster
      ? el("img", { class: "detail__poster", alt: "", src: posterUrl(item) })
      : el("div", { class: "detail__poster card__fallback", text: initials(item.title || item.file_name) });

    const metaRow = el("div", { class: "detail__meta" });
    [item.year, runtimeLabel(item.runtime), catLabel(item.content_category), bytesLabel(item.file_size)]
      .filter(Boolean)
      .forEach((t, i) => {
        if (i > 0) metaRow.append(el("span", { class: "dot", text: "·" }));
        metaRow.append(el("span", { text: t }));
      });

    const tags = [];
    if (item.genres && item.genres.length) tags.push(...item.genres.slice(0, 10));
    if (item.actresses && item.actresses.length) tags.push(...item.actresses.slice(0, 8).map((a) => String(a)));

    const isWatched = Number(item.is_watched) === 1;

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
              item.plot ? el("p", { class: "detail__plot", text: item.plot }) : null,
              el(
                "div",
                { class: "detail__actions" },
                el("button", {
                  class: "btn btn--light",
                  html: `${ICON.play}<span>Putar</span>`,
                  onclick: () => playItem(item),
                }),
                el("button", {
                  class: "btn btn--ghost",
                  html: `${isWatched ? ICON.check : ICON.plus}<span>${isWatched ? "Sudah Ditonton" : "Tandai Ditonton"}</span>`,
                  onclick: async (ev) => {
                    const next = Number(item.is_watched) === 1 ? 0 : 1;
                    try {
                      await api(`/api/media/${item.id}/watched`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ is_watched: next }),
                      });
                      item.is_watched = next;
                      ev.currentTarget.innerHTML = `${next ? ICON.check : ICON.plus}<span>${next ? "Sudah Ditonton" : "Tandai Ditonton"}</span>`;
                      toast(next ? "Ditandai sudah ditonton" : "Ditandai belum ditonton", "ok");
                    } catch (err) {
                      toast(err.message, "error");
                    }
                  },
                }),
                buildStars(item)
              ),
              item.code_id ? el("p", { style: "margin:14px 0 0;font-size:12px;color:var(--text-3)", text: `Kode: ${item.code_id}` }) : null,
              el(
                "div",
                { class: "detail__facts" },
                fact("Studio", item.studio),
                fact("Sutradara", item.director),
                fact("Sumber", item.source_type === "local" ? "Lokal" : item.source_type === "cloud" ? "TorBox Cloud" : item.source_type),
                fact("Ukuran", bytesLabel(item.file_size)),
                fact("Metadata", item.has_metadata ? "Tersedia" : "Belum"),
                fact("Subtitle", item.has_subtitle ? "Tersedia" : "Belum"),
                fact("Seri", item.series_title),
                fact(
                  "Episode",
                  item.season_num || item.episode_num
                    ? `S${item.season_num || "?"} · E${item.episode_num || "?"}`
                    : null
                ),
                fact(
                  "Playlist",
                  item.playlist_names && item.playlist_names.length
                    ? el("span", { class: "fact__value", text: item.playlist_names.join(", ") })
                    : null,
                  true
                ),
                tags.length
                  ? el(
                      "div",
                      { class: "fact" },
                      el("span", { class: "fact__label", text: "Tag" }),
                      el("div", { class: "tagrow" }, ...tags.map((t) => el("span", { class: "tag", text: t })))
                    )
                  : null
              )
            )
          )
        )
      )
    );

    document.body.append(panel);
    document.body.style.overflow = "hidden";

    if (!history.state || history.state.detail !== item.id) {
      history.pushState({ detail: item.id }, "", `#media-${item.id}`);
    }

    panel.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeDetail();
      }
    });
    $(".detail__close", panel)?.focus();
  }

  function closeDetail() {
    const panels = document.querySelectorAll(".detail");
    const panel = panels[panels.length - 1];
    if (!panel) return;
    panel.remove();
    if (!document.querySelector(".detail")) document.body.style.overflow = "";
    if (panel.classList.contains("detail--single")) {
      state.detailId = null;
      if (location.hash.startsWith("#media-")) {
        history.replaceState(null, "", location.pathname + location.search);
      }
    }
  }

  async function playItem(item) {
    try {
      await api(`/api/media/${item.id}/play`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      toast(`Memutar: ${item.title || item.file_name}`, "ok");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  /* ------------------------------- Navbar -------------------------------- */

  function initNavbar() {
    const nav = $(".topnav");
    if (!nav) return;
    const onScroll = () => nav.classList.toggle("is-scrolled", window.scrollY > 24);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    const input = $("#v2-search");
    if (input) {
      let timer = null;
      input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          const next = input.value.trim();
          // Hindari fetch ulang bila kata kunci belum berubah.
          if (next === state.query) return;
          state.query = next;
          load(state.query, { skeleton: false });
        }, 420);
      });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          input.value = "";
          state.query = "";
          load("");
        }
      });
    }

    // Klik di luar detail menutup panel teratas
    document.addEventListener("click", (ev) => {
      const panels = document.querySelectorAll(".detail");
      const panel = panels[panels.length - 1];
      if (!panel) return;
      if (ev.target === panel) closeDetail();
    });

    // Escape menutup panel teratas (detail episode atau panel serial)
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && document.querySelector(".detail")) closeDetail();
    });

    window.addEventListener("popstate", () => {
      const single = document.querySelector(".detail--single");
      if (!location.hash.startsWith("#media-") && single) closeDetail();
    });
  }

  /* -------------------------------- Boot -------------------------------- */

  document.addEventListener("DOMContentLoaded", () => {
    initNavbar();
    load("");
  });

  // Ekspos minimal untuk debugging manual di konsol
  window.TMH = {
    state,
    load,
    openDetail,
    openSeries,
    closeDetail,
    groupSeries,
    mixedEntries,
    isEpisodeItem,
  };
})();