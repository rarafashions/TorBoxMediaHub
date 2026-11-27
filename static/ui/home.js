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

  function posterUrl(id) {
    return `/api/media/${id}/poster`;
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

  async function load(query) {
    state.loading = true;
    renderSkeleton();
    try {
      const params = new URLSearchParams();
      if (query) params.set("search", query);
      params.set("playlist_scope", "all");
      const data = await api(`/api/bootstrap?${params.toString()}`);
      state.items = Array.isArray(data.items) ? data.items : [];
      state.playlists = data.playlists || [];
      state.stats = data.stats || null;
      state.env = data.env || null;
      state.version = data.version || "";
      state.loading = false;
      render();
    } catch (err) {
      state.loading = false;
      renderError(err.message);
    }
  }

  async function refreshAll() {
    await load(state.query);
    toast("Katalog disegarkan", "ok");
  }

  /* ------------------------------ Billboard ----------------------------- */

  function pickHero() {
    const withPoster = state.items.filter((it) => it.has_poster);
    const pool = withPoster.length ? withPoster : state.items;
    if (!pool.length) return null;
    return pool.slice().sort((a, b) => {
      const sa = Number(a.rating_star || 0) * 2 + Number(a.rating || 0);
      const sb = Number(b.rating_star || 0) * 2 + Number(b.rating || 0);
      if (sb !== sa) return sb - sa;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    })[0];
  }

  function renderBillboard(item) {
    const meta = [item.year, runtimeLabel(item.runtime), catLabel(item.content_category)]
      .filter(Boolean)
      .map((t) => el("span", { text: t }));

    const metaRow = el("div", { class: "billboard__meta" });
    meta.forEach((node, i) => {
      if (i > 0) metaRow.append(el("span", { class: "dot", "aria-hidden": "true" }));
      metaRow.append(node);
    });
    if (item.rating_star) {
      metaRow.append(el("span", { class: "dot", "aria-hidden": "true" }));
      metaRow.append(el("span", { class: "billboard__score", text: `★ ${item.rating_star}/5` }));
    }

    return el(
      "section",
      { class: "billboard", "aria-label": "Sorotan utama" },
      el("div", {
        class: "billboard__art",
        style: item.has_poster ? `background-image:url('${posterUrl(item.id)}')` : "",
      }),
      el("div", { class: "billboard__scrim" }),
      el(
        "div",
        { class: "billboard__content" },
        el("span", { class: "billboard__eyebrow", text: "Sorotan" }),
        el("h1", { class: "billboard__title", text: item.title || item.file_name || "Tanpa Judul" }),
        metaRow,
        item.plot ? el("p", { class: "billboard__plot clamp-3", text: item.plot }) : null,
        el(
          "div",
          { class: "billboard__actions" },
          el("button", {
            class: "btn btn--light",
            html: `${ICON.play}<span>Putar</span>`,
            onclick: () => playItem(item),
          }),
          el("button", {
            class: "btn btn--ghost",
            html: `${ICON.plus}<span>Detail</span>`,
            onclick: () => openDetail(item.id),
          })
        )
      )
    );
  }

  /* -------------------------------- Card -------------------------------- */

  function buildCard(item, opts) {
    const options = opts || {};

    const frame = el("div", { class: "card__frame" });

    const fallback = el("div", {
      class: "card__fallback",
      text: initials(item.title || item.file_name),
      "aria-hidden": "true",
    });
    frame.append(fallback);

    if (item.has_poster) {
      const img = el("img", {
        class: "card__img",
        alt: "",
        loading: "lazy",
        decoding: "async",
        src: posterUrl(item.id),
      });
      img.addEventListener("load", () => img.classList.add("is-loaded"));
      img.addEventListener("error", () => img.remove());
      frame.append(img);
    }

    // Badge kategori + sumber
    const badges = el("div", { class: "card__badges" });
    if (item.content_category && item.content_category !== "uncategorized") {
      badges.append(el("span", { class: "card__badge card__badge--cat", text: catLabel(item.content_category) }));
    }
    if (item.source_type === "local") {
      badges.append(el("span", { class: "card__badge card__badge--local", text: "Lokal" }));
    } else if (item.source_type === "cloud") {
      badges.append(el("span", { class: "card__badge card__badge--cloud", text: "Cloud" }));
    }
    if (badges.childElementCount) frame.append(badges);

    // Panel hover
    const hoverFacts = [item.year || null, runtimeLabel(item.runtime) || null]
      .filter(Boolean)
      .join(" · ");

    const hoverinfo = el(
      "div",
      { class: "card__hoverinfo" },
      el("div", { class: "truncate", style: "font-weight:700;font-size:12.5px", text: item.title || item.file_name }),
      el("div", { style: "margin-top:3px;font-size:11px;color:var(--text-3)", text: hoverFacts || "—" }),
      item.genres && item.genres.length
        ? el("div", { class: "truncate", style: "margin-top:5px;font-size:11px;color:var(--text-2)", text: item.genres.slice(0, 3).join(" · ") })
        : null
    );
    frame.append(hoverinfo);

    const body = el(
      "div",
      { class: "card__body" },
      el("p", { class: "card__title clamp-2", text: item.title || item.file_name || "Tanpa Judul" }),
      item.year ? el("p", { class: "card__sub", text: String(item.year) }) : null
    );

    const card = el(
      "button",
      {
        class: "card" + (options.rank ? " card--ranked" : ""),
        type: "button",
        "aria-label": `Buka detail ${item.title || item.file_name}`,
        onclick: () => openDetail(item.id),
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
              : "Tambahkan media dari Library, Discovery, atau Roxxy untuk mulai membangun katalog.",
          }),
          el("div", { style: "margin-top:20px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap" },
            el("a", { class: "btn btn--light", href: "/", text: "Buka Library" }),
            el("a", { class: "btn btn--ghost", href: "/roxxy", text: "Buka Roxxy" })
          )
        )
      );
      return;
    }

    const hero = pickHero();
    const sections = [];

    if (hero) sections.push(renderBillboard(hero));

    // Baru ditambahkan
    const recent = state.items.slice().sort((a, b) =>
      String(b.updated_at || "").localeCompare(String(a.updated_at || ""))
    );
    sections.push(buildRail("Baru Ditambahkan", recent.slice(0, 20)));

    // Belum ditonton
    const unwatched = recent.filter((it) => !Number(it.is_watched));
    sections.push(buildRail("Belum Ditonton", unwatched.slice(0, 20)));

    // Rating tertinggi (dengan badge peringkat)
    const rated = state.items
      .filter((it) => Number(it.rating_star) > 0)
      .sort((a, b) => Number(b.rating_star) - Number(a.rating_star))
      .slice(0, 10);
    sections.push(buildRail("Rating Tertinggi", rated, { ranked: true }));

    // Rail per kategori (hanya yang cukup isinya)
    const byCat = {};
    for (const item of state.items) {
      const cat = item.content_category || "uncategorized";
      (byCat[cat] = byCat[cat] || []).push(item);
    }
    const catOrder = ["general_movie", "tv_show", "anime", "donghua", "jav", "english_adult", "indo"];
    const catKeys = [...catOrder.filter((c) => byCat[c]), ...Object.keys(byCat).filter((c) => !catOrder.includes(c))];
    for (const cat of catKeys) {
      if (byCat[cat].length < 4) continue;
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

  function openDetail(id) {
    const item = readItem(id);
    if (!item) return;

    state.detailId = Number(item.id);
    const poster = item.has_poster
      ? el("img", { class: "detail__poster", alt: "", src: posterUrl(item.id) })
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
      { class: "detail" },
      el("button", { class: "detail__close", type: "button", "aria-label": "Tutup", html: ICON.back, onclick: closeDetail }),
      el(
        "div",
        { class: "detail__hero" },
        el("div", {
          class: "detail__art",
          style: item.has_poster ? `background-image:url('${posterUrl(item.id)}')` : "",
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
      if (ev.key === "Escape") closeDetail();
    });
    $(".detail__close", panel)?.focus();
  }

  function closeDetail() {
    const panel = $(".detail");
    if (!panel) return;
    panel.remove();
    document.body.style.overflow = "";
    state.detailId = null;
    if (location.hash.startsWith("#media-")) {
      history.replaceState(null, "", location.pathname + location.search);
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
          state.query = input.value.trim();
          load(state.query);
        }, 320);
      });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          input.value = "";
          state.query = "";
          load("");
        }
      });
    }

    // Klik di luar detail menutup
    document.addEventListener("click", (ev) => {
      const panel = $(".detail");
      if (!panel) return;
      if (ev.target === panel) closeDetail();
    });

    window.addEventListener("popstate", () => {
      if (!location.hash.startsWith("#media-")) closeDetail();
    });
  }

  /* -------------------------------- Boot -------------------------------- */

  document.addEventListener("DOMContentLoaded", () => {
    initNavbar();
    load("");
  });

  // Ekspos minimal untuk debugging manual di konsol
  window.TMH = { state, load, openDetail, closeDetail };
})();