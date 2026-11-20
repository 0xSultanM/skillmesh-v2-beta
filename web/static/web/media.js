/* In-app learning media + automatic study tracking. Depends on common.js (window.SM).
 *
 * - Video player popup: YouTube IFrame API on youtube-nocookie.com. Watch time counts only
 *   while the video is actually PLAYING and this tab is visible.
 * - Content popup (full tab): the original page in a sandboxed frame when the site allows it,
 *   otherwise a script-free reader view. Reading time counts only while it's open, the tab is
 *   visible and you're active on the content (pointer over it / scrolling within 45 s).
 * - Daily streak: a day counts when you watch >= 10 min OR read >= 5 min inside SkillMesh.
 *   Activity is stored only in this browser (localStorage "sm:activity").
 */
(() => {
  "use strict";
  const { h, store, post, toast } = window.SM;
  const DEFAULT_GOALS = { video: 600, read: 300 };
  const clampGoal = (v, d) => { const x = Math.round(Number(v)); return Number.isFinite(x) && x >= 60 && x <= 14400 ? x : d; };
  // Daily goal in seconds. User-editable in TutMesh; kept in this browser (localStorage "sm:goals").
  const saved = store.get("goals", null) || {};
  const GOALS = { video: clampGoal(saved.video, DEFAULT_GOALS.video), read: clampGoal(saved.read, DEFAULT_GOALS.read) };
  const IDLE_MS = 45_000;
  const KEEP_DAYS = 400;

  const dayKey = (d = new Date()) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const fmt = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const visible = () => document.visibilityState === "visible";

  /* ---------------- activity log ---------------- */
  const activity = {
    data() {
      const a = store.get("activity", { days: {} });
      return a && typeof a.days === "object" ? a : { days: {} };
    },
    add(kind, secs, url) {
      if (!secs) return;
      const a = this.data();
      const k = dayKey();
      const d = (a.days[k] ||= { video: 0, read: 0 });
      const before = this.qualifies(d);
      d[kind] = Math.min(86400, (d[kind] || 0) + secs);
      const keys = Object.keys(a.days).sort();
      keys.slice(0, Math.max(0, keys.length - KEEP_DAYS)).forEach(x => delete a.days[x]);
      store.set("activity", a);
      window.dispatchEvent(new CustomEvent("sm:activity", { detail: { kind, secs, url, today: { ...d } } }));
      if (!before && this.qualifies(d)) {
        toast(`🔥 Streak day earned! ${this.streaks().current}-day streak.`);
        window.dispatchEvent(new CustomEvent("sm:streak"));
      }
    },
    today() { const d = this.data().days[dayKey()] || {}; return { video: d.video || 0, read: d.read || 0 }; },
    // Past days keep the verdict they had under the goal of that time (`ok`, frozen by setGoals).
    qualifies(d) { return typeof d?.ok === "boolean" ? d.ok : (d?.video || 0) >= GOALS.video || (d?.read || 0) >= GOALS.read; },
    /** Change the daily goal (seconds) without rewriting which past days counted. */
    setGoals({ video, read }) {
      const a = this.data();
      const today = dayKey();
      for (const [k, d] of Object.entries(a.days)) {
        if (k < today && d && typeof d.ok !== "boolean") d.ok = this.qualifies(d);
      }
      GOALS.video = clampGoal(video, GOALS.video);
      GOALS.read = clampGoal(read, GOALS.read);
      store.set("activity", a);
      store.set("goals", { video: GOALS.video, read: GOALS.read });
      paintGoals();
      window.dispatchEvent(new CustomEvent("sm:activity", { detail: {} }));
    },
    streaks() {
      const days = this.data().days;
      const good = new Set(Object.keys(days).filter(k => this.qualifies(days[k])));
      const d = new Date();
      if (!good.has(dayKey(d))) d.setDate(d.getDate() - 1); // today can still be earned
      let current = 0;
      while (good.has(dayKey(d))) { current++; d.setDate(d.getDate() - 1); }
      let best = 0, run = 0, prev = null;
      for (const k of [...good].sort()) {
        const t = new Date(`${k}T00:00:00`);
        run = prev && Math.round((t - prev) / 86400000) === 1 ? run + 1 : 1;
        best = Math.max(best, run);
        prev = t;
      }
      return { current, best, total: good.size };
    },
    /** Weeks in a row (Sunday to Saturday) with at least one streak day. The running week can still be earned. */
    weeks() {
      const days = this.data().days;
      const now = new Date();
      const week = new Date(now.getFullYear(), now.getMonth(), now.getDate() - now.getDay());
      const earnedIn = start => {
        let n = 0;
        for (let i = 0; i < 7; i++) {
          if (this.qualifies(days[dayKey(new Date(start.getFullYear(), start.getMonth(), start.getDate() + i))])) n++;
        }
        return n;
      };
      const thisWeek = earnedIn(week);
      if (!thisWeek) week.setDate(week.getDate() - 7);
      let current = 0;
      while (earnedIn(week)) { current++; week.setDate(week.getDate() - 7); }
      return { current, thisWeek };
    },
  };

  const goalMin = k => { const m = GOALS[k] / 60; return String(Number.isInteger(m) ? m : Math.round(m * 10) / 10); };
  /** Fill every [data-goal="video|read"] element with the current goal in minutes. */
  function paintGoals() {
    document.querySelectorAll("[data-goal]").forEach(el => { if (GOALS[el.dataset.goal]) el.textContent = goalMin(el.dataset.goal); });
  }
  document.addEventListener("DOMContentLoaded", paintGoals);
  window.addEventListener("storage", e => {
    if (e.key !== "sm:goals") return;
    const g = store.get("goals", null) || {};
    GOALS.video = clampGoal(g.video, DEFAULT_GOALS.video);
    GOALS.read = clampGoal(g.read, DEFAULT_GOALS.read);
    paintGoals();
    window.dispatchEvent(new CustomEvent("sm:activity", { detail: {} }));
  });

  /** Accumulates seconds while isActive() is true; flushes to the activity log every 5 s and on stop. */
  function createTracker(kind, url, isActive, onTick) {
    let pending = 0;
    const flush = () => { if (pending) { const s = pending; pending = 0; activity.add(kind, s, url); } };
    const iv = setInterval(() => {
      if (isActive()) { pending++; onTick?.(pending); }
      if (pending >= 5) flush();
    }, 1000);
    const onHide = () => { if (!visible()) flush(); };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", flush);
    return {
      pending: () => pending,
      stop() { clearInterval(iv); flush(); document.removeEventListener("visibilitychange", onHide); window.removeEventListener("pagehide", flush); },
    };
  }

  /* ---------------- goal widget ---------------- */
  function goalWidget({ compact = false } = {}) {
    const el = h("div", { class: `goal-widget ${compact ? "compact" : ""}`, "aria-live": "polite" });
    const render = (extra = {}) => {
      const t = activity.today();
      const v = t.video + (extra.video || 0), r = t.read + (extra.read || 0);
      const earned = v >= GOALS.video || r >= GOALS.read;
      const s = activity.streaks();
      const bar = (label, icon, val, goal, cls) => h("div", { class: `goal ${cls} ${val >= goal ? "met" : ""}` },
        h("div", { class: "goal-top" }, h("span", { text: `${icon} ${label}` }), h("b", { text: `${fmt(Math.min(val, goal))} / ${fmt(goal)}` })),
        h("div", { class: "goal-bar" }, h("div", { style: `width:${Math.min(100, (val * 100) / goal)}%` })));
      el.replaceChildren(
        h("div", { class: `goal-streak ${earned ? "earned" : ""}` },
          h("span", { class: "flame", "aria-hidden": "true", text: "🔥" }),
          h("b", { text: String(s.current) }), h("span", { text: compact ? "day streak" : "day streak · best " + s.best }),
          earned ? h("span", { class: "goal-badge", text: "Today ✓" }) : null),
        bar("Watch", "🎬", v, GOALS.video, "video"),
        h("span", { class: "goal-or", text: "or" }),
        bar("Read", "📖", r, GOALS.read, "read"),
      );
    };
    render();
    let mounted = false;
    const guard = fn => e => {  // detach listeners once the widget leaves the page
      if (!el.isConnected) { if (mounted) cleanup(); return; }
      mounted = true;
      fn(e);
    };
    const onAct = guard(() => render());
    const onLive = guard(e => render(e.detail || {}));
    const onStorage = guard(e => { if (e.key === "sm:activity") render(); });
    const cleanup = () => {
      window.removeEventListener("sm:activity", onAct);
      window.removeEventListener("sm:live", onLive);
      window.removeEventListener("storage", onStorage);
    };
    window.addEventListener("sm:activity", onAct);
    window.addEventListener("sm:live", onLive);
    window.addEventListener("storage", onStorage);
    return el;
  }

  /* ---------------- YouTube player popup ---------------- */
  let ytPromise;
  function loadYT() {
    if (window.YT?.Player) return Promise.resolve(window.YT);
    ytPromise ||= new Promise((resolve, reject) => {
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { prev?.(); resolve(window.YT); };
      const s = h("script", { src: "https://www.youtube.com/iframe_api" });
      s.onerror = () => { ytPromise = null; reject(new Error("Couldn't load the YouTube player. Check your connection or content blocker.")); };
      document.head.append(s);
      setTimeout(() => { if (!window.YT?.Player) { ytPromise = null; reject(new Error("The YouTube player took too long to load.")); } }, 15000);
    });
    return ytPromise;
  }

  let openPlayer = null;
  async function playVideo(r) {
    const embed = r?.embed;
    if (!embed || !/^[A-Za-z0-9_-]{6,64}$/.test(embed.id || "")) { toast("This video can't be played here.", true); return; }
    openPlayer?.close();
    openContent?.close();
    const lastFocus = document.activeElement;
    const mount = h("div", { class: "player-mount" });
    const live = h("span", { class: "player-live", text: "Paused" });
    const closeBtn = h("button", { type: "button", class: "icon-btn", "aria-label": "Close video", text: "×" });
    const dialog = h("div", { class: "player-modal", role: "dialog", "aria-modal": "true", "aria-label": r.title || "Video" },
      h("header", { class: "player-head" },
        h("div", { class: "player-title" }, h("strong", { text: r.title || "Video" }), h("span", { text: r.author || "" })),
        closeBtn),
      h("div", { class: "player-frame" }, mount),
      h("footer", { class: "player-foot" },
        live,
        goalWidget({ compact: true }),
        h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", class: "linklike", text: "Open on YouTube ↗" })));
    const backdrop = h("div", { class: "modal-backdrop" }, dialog);
    document.body.append(backdrop);
    document.body.classList.add("modal-open");
    closeBtn.focus();

    let playing = false, player = null, tracker = null;
    const close = () => {
      tracker?.stop();
      try { player?.destroy(); } catch { /* ignore */ }
      backdrop.remove();
      document.body.classList.remove("modal-open");
      document.removeEventListener("keydown", onKey);
      openPlayer = null;
      window.dispatchEvent(new CustomEvent("sm:media-closed"));
      lastFocus?.focus?.();
    };
    const onKey = e => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    closeBtn.addEventListener("click", close);
    backdrop.addEventListener("click", e => { if (e.target === backdrop) close(); });
    openPlayer = { close };

    try {
      const YT = await loadYT();
      if (openPlayer?.close !== close) return; // closed while loading
      player = new YT.Player(mount, {
        host: "https://www.youtube-nocookie.com",
        width: "100%", height: "100%",
        ...(embed.kind === "video" ? { videoId: embed.id } : {}),
        playerVars: { rel: 0, modestbranding: 1, playsinline: 1, ...(embed.kind === "playlist" ? { listType: "playlist", list: embed.id } : {}) },
        events: {
          onStateChange: e => {
            playing = e.data === YT.PlayerState.PLAYING;
            live.textContent = playing ? "● Tracking watch time" : "Paused (not counting)";
            live.classList.toggle("on", playing);
          },
          onError: () => { live.textContent = "This video can't be embedded. Use “Open on YouTube”."; },
        },
      });
      tracker = createTracker("video", r.url, () => playing && visible(),
        n => window.dispatchEvent(new CustomEvent("sm:live", { detail: { video: n } })));
    } catch (err) {
      mount.replaceWith(h("p", { class: "player-error", text: err.message }));
    }
  }

  /* ---------------- content popup (full tab, framed) ----------------
   * "Original" mode: the real page in a sandboxed <iframe> (only when the site allows framing).
   *   Browsers hide activity inside a cross-origin frame, so time counts while the popup is open
   *   and visible, with a "Still reading?" check every 3 minutes (clicking in the page also counts).
   * "Reader" mode: our extracted, script-free article in a sandboxed srcdoc <iframe>; time counts
   *   while you scroll / move / type inside it (45 s idle timeout).
   */
  let openContent = null;

  const READER_CSS = `
    :root { color-scheme: light dark; }
    body { margin: 0; font: 17px/1.75 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #1f2333; background: #fff; }
    main { max-width: 760px; margin: 0 auto; padding: 28px 22px 80px; }
    h1 { font-size: 1.8rem; line-height: 1.25; margin: .2rem 0 1rem; }
    h2 { font-size: 1.3rem; margin: 1.8rem 0 .5rem; } h3 { font-size: 1.08rem; margin: 1.3rem 0 .4rem; }
    p { margin: 0 0 1rem; } ul, ol { padding-left: 1.4rem; margin: 0 0 1rem; }
    pre { background: #0f172a; color: #e2e8f0; padding: 12px 14px; border-radius: 10px; overflow-x: auto; font-size: .85rem; line-height: 1.5; }
    blockquote { margin: 0 0 1rem; padding: .4rem 1rem; border-left: 4px solid #6d5efc; background: #f1f0ff; border-radius: 0 8px 8px 0; }
    a { color: #4f46e5; } .meta { color: #6b7280; font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; }
    .attrib { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e5e7eb; color: #6b7280; font-size: .85rem; }
    @media (prefers-color-scheme: dark) { body { color: #e7ebff; background: #10162a; } blockquote { background: #1c2142; } a { color: #a5a0ff; } .attrib { border-color: #263052; } }
    body.dark { color: #e7ebff; background: #10162a; } body.dark blockquote { background: #1c2142; } body.dark a { color: #a5a0ff; } body.dark .attrib { border-color: #263052; }
    body.light { color: #1f2333; background: #fff; }`;

  function buildReaderDoc(doc, page) {
    const art = page.article;
    doc.body.className = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    const el = (tag, text, attrs = {}) => { const e = doc.createElement(tag); if (text != null) e.textContent = text; Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); return e; };
    const main = el("main");
    main.append(el("p", `${art.site} · ~${art.minutes} min read`, { class: "meta" }), el("h1", art.title || ""));
    let list = null, ordered = null;
    for (const b of art.blocks) {
      if (b.type !== "li") list = null;
      const link = text => (b.href && window.SM.safeUrl(b.href)) ? el("a", text, { href: b.href, target: "_blank", rel: "noopener noreferrer" }) : doc.createTextNode(text);
      if (b.type === "li") {
        if (!list || ordered !== !!b.ordered) { list = el(b.ordered ? "ol" : "ul"); ordered = !!b.ordered; main.append(list); }
        const li = el("li"); li.append(link(b.text)); list.append(li); continue;
      }
      if (b.type === "code") { const pre = el("pre"); pre.append(el("code", b.text)); main.append(pre); continue; }
      const tag = { h2: "h2", h3: "h3", quote: "blockquote" }[b.type] || "p";
      const node = el(tag); node.append(tag === "p" ? link(b.text) : doc.createTextNode(b.text)); main.append(node);
    }
    const at = el("p", `Content © ${art.site} and its authors, shown in reader view for personal study. `, { class: "attrib" });
    at.append(el("a", "Read the original ↗", { href: page.final_url || page.url, target: "_blank", rel: "noopener noreferrer" }));
    main.append(at);
    doc.body.replaceChildren(main);
  }

  async function readContent(r) {
    const url = window.SM.safeUrl(r?.url);
    if (!url) return;
    openContent?.close();
    openPlayer?.close();
    const lastFocus = document.activeElement;

    const closeBtn = h("button", { type: "button", class: "content-close", "aria-label": "Close", title: "Close (Esc)", text: "×" });
    const modeOrig = h("button", { type: "button", role: "tab", "aria-selected": "false", text: "Original site", disabled: true });
    const modeRead = h("button", { type: "button", role: "tab", "aria-selected": "false", text: "Reader view", disabled: true });
    const status = h("span", { class: "reader-live", text: "Loading…" });
    const confirmBtn = h("button", { type: "button", class: "btn small primary still-reading", hidden: true, text: "Still reading? Keep counting" });
    const stage = h("div", { class: "content-stage" }, h("div", { class: "reader-skeleton" }, h("i"), h("i"), h("i"), h("i")));
    const modal = h("div", { class: "content-modal", role: "dialog", "aria-modal": "true", "aria-label": r.title || "Content" },
      h("header", { class: "content-head" },
        h("div", { class: "content-title" }, h("strong", { text: r.title || url }), h("span", { text: r.author || new URL(url).hostname })),
        h("div", { class: "content-tools" },
          h("div", { class: "seg", role: "tablist", "aria-label": "View" }, modeOrig, modeRead),
          h("a", { class: "btn small", href: url, target: "_blank", rel: "noopener noreferrer", text: "Open in new tab ↗" })),
        closeBtn),
      h("div", { class: "content-bar" }, h("div", { class: "content-status" }, status, confirmBtn), goalWidget({ compact: true })),
      stage);
    document.body.append(modal);
    document.body.classList.add("modal-open");
    closeBtn.focus();

    let mode = null, page = null, frame = null, tracker = null;
    const CONFIRM_MS = 180_000;
    let lastInteraction = Date.now(), lastConfirm = Date.now();
    const touch = () => { lastInteraction = Date.now(); };
    const confirm = () => { lastConfirm = Date.now(); confirmBtn.hidden = true; };
    confirmBtn.addEventListener("click", confirm);
    const active = () => {
      if (!visible() || !frame) return false;
      if (mode === "original") return Date.now() - lastConfirm < CONFIRM_MS;
      return Date.now() - lastInteraction < IDLE_MS;
    };
    const statusIv = setInterval(() => {
      if (!mode) return;
      const on = active();
      if (mode === "original") confirmBtn.hidden = Date.now() - lastConfirm < CONFIRM_MS - 30_000;
      else confirmBtn.hidden = true;
      status.textContent = on ? (mode === "original" ? "● Tracking reading time · we'll check in every 3 min" : "● Tracking reading time")
        : mode === "original" ? "Paused: confirm you're still reading"
        : "Paused: scroll or move the mouse to keep counting";
      status.classList.toggle("on", on);
    }, 1000);
    // Clicking inside the framed page moves focus into the iframe: treat it as "still reading".
    const onBlur = () => { setTimeout(() => { if (frame && document.activeElement === frame) confirm(); }, 0); };
    const onFocus = () => {};
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);

    const close = () => {
      tracker?.stop();
      clearInterval(statusIv);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("keydown", onKey);
      modal.remove();
      document.body.classList.remove("modal-open");
      openContent = null;
      window.dispatchEvent(new CustomEvent("sm:media-closed"));
      lastFocus?.focus?.();
    };
    const onKey = e => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    closeBtn.addEventListener("click", close);
    openContent = { close };

    function show(next) {
      if (!page || next === mode) return;
      mode = next;
      modeOrig.setAttribute("aria-selected", String(mode === "original"));
      modeRead.setAttribute("aria-selected", String(mode === "reader"));
      confirm();
      if (mode === "original") {
        frame = h("iframe", {
          src: page.final_url || page.url, title: r.title || "Content", referrerpolicy: "no-referrer",
          sandbox: "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-forms",
        });
        frame.addEventListener("mouseenter", confirm);
        stage.replaceChildren(frame, h("div", { class: "content-note", text: "Showing the original site. If it looks broken, switch to Reader view." }));
        setTimeout(() => stage.querySelector(".content-note")?.remove(), 6000);
      } else {
        // srcdoc + sandbox without allow-scripts: nothing in the article can run.
        frame = h("iframe", { class: "reader-frame", title: `Reader: ${r.title || ""}`, sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox" });
        frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><style>${READER_CSS}</style></head><body></body></html>`;
        frame.addEventListener("load", () => {
          const d = frame.contentDocument;
          if (!d) return;
          buildReaderDoc(d, page);
          ["scroll", "wheel", "mousemove", "keydown", "touchstart", "pointerdown"].forEach(ev =>
            (ev === "scroll" ? d : d.body).addEventListener(ev, touch, { passive: true }));
          touch();
        });
        stage.replaceChildren(frame);
      }
      touch();
    }
    modeOrig.addEventListener("click", () => show("original"));
    modeRead.addEventListener("click", () => show("reader"));

    try {
      ({ page } = await post("/api/reader/", { json: { url } }));
      if (openContent?.close !== close) return;
      modeOrig.disabled = !page.framable;
      modeRead.disabled = !page.article;
      if (!page.framable) modeOrig.title = "This site doesn't allow being shown inside other websites";
      if (!page.article) modeRead.title = page.reader_error || "No reader view for this page";
      show(page.framable ? "original" : "reader");
      tracker = createTracker("read", url, active, n => window.dispatchEvent(new CustomEvent("sm:live", { detail: { read: n } })));
    } catch (err) {
      status.textContent = "Not counting";
      stage.replaceChildren(h("div", { class: "reader-error" },
        h("p", { text: err.message || "Couldn't open this page here." }),
        h("a", { class: "btn primary small", href: url, target: "_blank", rel: "noopener noreferrer", text: "Open in a new tab ↗" }),
        h("p", { class: "hint", text: "Time spent on other websites can't be tracked, so it won't count toward your streak." })));
    }
  }

  Object.assign(window.SM, { media: { activity, goalWidget, playVideo, readContent, GOALS, DEFAULT_GOALS, dayKey, fmt } });
})();
