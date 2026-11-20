/* Shared helpers for the SkillMesh dashboard and TutMesh.
 * Security: everything is rendered with textContent / DOM APIs, never innerHTML,
 * because content can come from scraped pages or user-imported JSON files.
 */
(() => {
  "use strict";

  const LS_PREFIX = "sm:";
  const ROADMAP_FORMAT = "skillmesh.roadmap";
  const ROADMAP_VERSION = 1;
  const DIFF_LABEL = { easy: "Easy", medium: "Medium", hard: "Hard" };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  const store = {
    get(key, fallback = null) {
      try { const v = localStorage.getItem(LS_PREFIX + key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(LS_PREFIX + key, JSON.stringify(value)); return true; } catch { return false; }
    },
    remove(key) { try { localStorage.removeItem(LS_PREFIX + key); } catch { /* ignore */ } },
    wipe() {
      try { Object.keys(localStorage).filter(k => k.startsWith(LS_PREFIX)).forEach(k => localStorage.removeItem(k)); } catch { /* ignore */ }
    },
  };

  function csrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function post(path, { json, form, raw = false } = {}) {
    const opts = { method: "POST", headers: { "X-CSRFToken": csrf() }, credentials: "same-origin" };
    if (form) opts.body = form;
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(json || {}); }
    let res;
    try { res = await fetch(path, opts); } catch { throw new Error("Network error. Check your connection and try again."); }
    if (raw && res.ok) return res;
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) throw Object.assign(new Error(data?.error?.message || `Request failed (${res.status})`), { code: data?.error?.code });
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const t = $("#toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.toggle("error", isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 7000 : 3500);
  }

  function download(filename, blob) {
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: filename });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  /** Render a roadmap export document to PDF on the server and save it. */
  async function downloadRoadmapPdf(doc, filename) {
    const res = await post("/api/roadmap/pdf/", { json: doc });
    const bin = atob(res.data);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    download(filename || res.filename, new Blob([bytes], { type: "application/pdf" }));
  }

  const slugify = s => String(s || "roadmap").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "roadmap";
  const safeUrl = u => (typeof u === "string" && /^https?:\/\/[^\s"'<>]+$/i.test(u) && u.length <= 1000) ? u : null;
  const str = (v, n = 400) => (v == null ? "" : String(v)).slice(0, n);
  const num = (v, d = 0, lo = 0, hi = 10000) => { const x = Number(v); return Number.isFinite(x) ? Math.max(lo, Math.min(hi, x)) : d; };

  /* ---------- roadmap document (export / import format) ---------- */
  /**
   * Stable identity of a roadmap: the same job with the same skills is the same roadmap,
   * however many times it is analyzed, saved or opened. (cyrb53 hash, base 36)
   */
  function roadmapKey(job, roadmap) {
    const norm = s => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
    const steps = (Array.isArray(roadmap?.phases) ? roadmap.phases : [])
      .flatMap(ph => (Array.isArray(ph?.steps) ? ph.steps : []).map(st => slugify(st?.slug || st?.name))).sort();
    const text = [norm(job?.title), norm(job?.company), ...steps].join("|");
    let a = 0xdeadbeef, b = 0x41c6ce57;
    for (let i = 0; i < text.length; i++) {
      const c = text.charCodeAt(i);
      a = Math.imul(a ^ c, 2654435761);
      b = Math.imul(b ^ c, 1597334677);
    }
    a = Math.imul(a ^ (a >>> 16), 2246822507) ^ Math.imul(b ^ (b >>> 13), 3266489909);
    b = Math.imul(b ^ (b >>> 16), 2246822507) ^ Math.imul(a ^ (a >>> 13), 3266489909);
    return (4294967296 * (2097151 & b) + (a >>> 0)).toString(36);
  }

  /** Id of the TutMesh roadmap that is the same roadmap as `doc` (same id, or same job + skills), or null. */
  function findRoadmap(tm, doc) {
    if (tm.roadmaps[doc.id]) return doc.id;
    const key = roadmapKey(doc.job, doc.roadmap);
    return tm.order.find(id => tm.roadmaps[id] && roadmapKey(tm.roadmaps[id].job, tm.roadmaps[id].roadmap) === key) || null;
  }

  function mergeProgress(a = {}, b = {}) {
    const time = { ...b.time };
    for (const [url, secs] of Object.entries(a.time || {})) time[url] = Math.max(secs || 0, time[url] || 0);
    const notes = { ...b.notes };
    for (const [slug, text] of Object.entries(a.notes || {})) if (text) notes[slug] = text;
    return { steps: { ...b.steps, ...a.steps }, resources: { ...b.resources, ...a.resources }, topics: { ...b.topics, ...a.topics }, notes, time };
  }

  /** Collapse roadmaps stored more than once into a single entry (progress merged). Returns true if `tm` changed. */
  function dedupeTutmesh(tm) {
    const kept = new Map();
    let changed = false;
    tm.order = tm.order.filter(id => {
      const doc = tm.roadmaps[id];
      if (!doc) { changed = true; return false; }
      const key = roadmapKey(doc.job, doc.roadmap);
      if (!kept.has(key)) { kept.set(key, id); return true; }
      const first = tm.roadmaps[kept.get(key)];
      first.progress = mergeProgress(first.progress, doc.progress);
      delete tm.roadmaps[id];
      changed = true;
      return false;
    });
    return changed;
  }

  /** Saved analyses, newest first, one per roadmap. Returns the entries that were dropped as duplicates. */
  function dedupeHistory(hist) {
    const seen = new Set(), dropped = [];
    const unique = hist.filter(it => {
      const key = roadmapKey(it?.result?.job, it?.result?.roadmap);
      if (!seen.has(key)) { seen.add(key); return true; }
      dropped.push(it);
      return false;
    });
    hist.splice(0, hist.length, ...unique);
    return dropped;
  }

  function buildExport(result, id) {
    return {
      format: ROADMAP_FORMAT,
      version: ROADMAP_VERSION,
      id: id || `rm_${Date.now().toString(36)}`,
      exported_at: new Date().toISOString(),
      job: { title: result.job.title || "", company: result.job.company || "", seniority: result.job.seniority || "" },
      summary: {
        score: result.report.score,
        verdict: result.report.verdict.label,
        readiness: result.report.readiness,
        critical_gaps: result.report.critical_gaps,
      },
      roadmap: result.roadmap,
      progress: { steps: {}, resources: {}, topics: {}, notes: {}, time: {} },
    };
  }

  function sanitizeResource(r) {
    if (!r || typeof r !== "object") return null;
    const url = safeUrl(r.url);
    if (!url) return null;
    return {
      title: str(r.title, 200) || url, url, kind: str(r.kind, 20), kind_label: str(r.kind_label, 40),
      author: str(r.author, 120), description: str(r.description, 1200),
      highlights: Array.isArray(r.highlights) ? r.highlights.slice(0, 8).map(x => str(x, 80)) : [],
      est_hours: r.est_hours == null ? null : num(r.est_hours, null, 0, 1000), source: str(r.source, 20),
      embed: r.embed && ["video", "playlist"].includes(r.embed.kind) && /^[A-Za-z0-9_-]{6,64}$/.test(r.embed.id || "")
        ? { kind: r.embed.kind, id: r.embed.id } : null,
    };
  }

  function sanitizeTopics(t) {
    if (!t || typeof t !== "object" || !Array.isArray(t.topics)) return null;
    const topics = t.topics.slice(0, 15).map(x => ({
      name: str(x?.name, 120), description: str(x?.description, 300),
      minutes: x?.minutes == null ? null : num(x.minutes, null, 0, 10000), courses: num(x?.courses, 1, 1, 20),
      sources: (Array.isArray(x?.sources) ? x.sources : []).slice(0, 3)
        .map(s => ({ course: str(s?.course, 200), url: safeUrl(s?.url) || "", provider: str(s?.provider, 80) })),
    })).filter(x => x.name);
    if (!topics.length) return null;
    return {
      topics, source: str(t.source, 80),
      outcomes: (Array.isArray(t.outcomes) ? t.outcomes : []).slice(0, 8).map(o => str(o, 300)).filter(Boolean),
      courses: (Array.isArray(t.courses) ? t.courses : []).slice(0, 6)
        .map(c => ({ title: str(c?.title, 200), url: safeUrl(c?.url) || "", provider: str(c?.provider, 80) })).filter(c => c.title),
    };
  }

  /** Validate and sanitize an imported roadmap document. Throws on invalid input. */
  function sanitizeRoadmapDoc(doc) {
    if (!doc || typeof doc !== "object" || doc.format !== ROADMAP_FORMAT) throw new Error("This isn't a SkillMesh roadmap file.");
    if (Number(doc.version) > ROADMAP_VERSION) throw new Error("This roadmap was made by a newer version of SkillMesh.");
    const rm = doc.roadmap;
    if (!rm || !Array.isArray(rm.phases) || !rm.phases.length) throw new Error("The roadmap file has no phases.");
    const phases = rm.phases.slice(0, 3).map(ph => ({
      key: ["foundation", "required", "preferred"].includes(ph?.key) ? ph.key : "required",
      title: str(ph?.title, 80) || "Phase", description: str(ph?.description, 200),
      weeks: Array.isArray(ph?.weeks) ? ph.weeks.slice(0, 2).map(w => num(w, 1, 1, 520)) : [1, 1],
      hours: num(ph?.hours, 0, 0, 10000),
      steps: (Array.isArray(ph?.steps) ? ph.steps : []).slice(0, 60).map(st => ({
        slug: str(st?.slug, 100) || slugify(st?.name), name: str(st?.name, 100) || "Skill",
        category: str(st?.category, 20), priority: str(st?.priority, 20), reason: str(st?.reason, 200),
        hours: num(st?.hours, 10, 0, 1000), difficulty: DIFF_LABEL[st?.difficulty] ? st.difficulty : "medium",
        start_week: num(st?.start_week, 1, 1, 520), end_week: num(st?.end_week, 1, 1, 520),
        partial: !!st?.partial, via: str(st?.via, 100), project: str(st?.project, 500),
        resources: {
          video: (Array.isArray(st?.resources?.video) ? st.resources.video : []).slice(0, 8).map(sanitizeResource).filter(Boolean),
          content: (Array.isArray(st?.resources?.content) ? st.resources.content : []).slice(0, 8).map(sanitizeResource).filter(Boolean),
          topics: sanitizeTopics(st?.resources?.topics),
        },
      })),
    })).filter(ph => ph.steps.length);
    if (!phases.length) throw new Error("The roadmap file has no steps.");
    const p = doc.progress && typeof doc.progress === "object" ? doc.progress : {};
    const boolMap = o => Object.fromEntries(Object.entries(o && typeof o === "object" ? o : {}).slice(0, 2000)
      .filter(([, v]) => v === true).map(([k]) => [str(k, 1000), true]));
    const time = Object.fromEntries(Object.entries(p.time && typeof p.time === "object" ? p.time : {}).slice(0, 2000)
      .filter(([k]) => safeUrl(k)).map(([k, v]) => [k, num(v, 0, 0, 1e7)]));
    const notes = Object.fromEntries(Object.entries(p.notes && typeof p.notes === "object" ? p.notes : {}).slice(0, 500)
      .map(([k, v]) => [str(k, 100), str(v, 4000)]));
    return {
      format: ROADMAP_FORMAT, version: ROADMAP_VERSION,
      id: /^rm_[a-z0-9_]{3,40}$/i.test(doc.id || "") ? doc.id : `rm_${Date.now().toString(36)}`,
      exported_at: str(doc.exported_at, 40), imported_at: new Date().toISOString(),
      job: { title: str(doc.job?.title, 200), company: str(doc.job?.company, 200), seniority: str(doc.job?.seniority, 20) },
      summary: { score: doc.summary?.score == null ? null : num(doc.summary.score, 0, 0, 100), verdict: str(doc.summary?.verdict, 40),
                 readiness: doc.summary?.readiness == null ? null : num(doc.summary.readiness, 0, 0, 100) },
      roadmap: {
        hours_per_week: num(rm.hours_per_week, 10, 1, 80), total_hours: num(rm.total_hours, 0, 0, 10000),
        total_weeks: num(rm.total_weeks, 1, 0, 520), step_count: phases.reduce((n, ph) => n + ph.steps.length, 0), phases,
      },
      progress: { steps: boolMap(p.steps), resources: boolMap(p.resources), topics: boolMap(p.topics), notes, time },
    };
  }

  /* ---------- shared roadmap UI pieces ---------- */
  function difficultyPill(d) {
    return h("span", { class: `diff diff-${d}`, title: `Difficulty: ${DIFF_LABEL[d] || d}` }, h("span", { class: "diff-dot" }), DIFF_LABEL[d] || d);
  }

  /**
   * Easy / Medium / Hard toggle chips. `selected` is a Set the caller owns; an empty set means "show all".
   * Clicking a chip shows only that difficulty; clicking it again brings everything back.
   */
  function difficultyFilter(counts, selected, onChange) {
    const wrap = h("div", { class: "diff-filter", role: "group", "aria-label": "Filter steps by difficulty" });
    const render = () => {
      wrap.classList.toggle("filtering", selected.size > 0);
      wrap.replaceChildren(
        h("span", { class: "diff-filter-label", "aria-hidden": "true", text: "Filter" }),
        ...Object.keys(DIFF_LABEL).map(d => {
          const name = DIFF_LABEL[d].toLowerCase(), on = selected.has(d);
          return h("button", {
            type: "button", class: `diff diff-${d} diff-toggle`, "aria-pressed": String(on), disabled: !counts[d],
            title: !counts[d] ? `No ${name} steps` : on ? `Showing ${name} steps. Click again to show all.` : `Show only ${name} steps`,
            onclick: () => { if (on) selected.delete(d); else selected.add(d); render(); onChange(selected); },
          }, h("span", { class: "diff-dot" }), DIFF_LABEL[d], h("b", { text: String(counts[d] || 0) }));
        }));
    };
    render();
    return wrap;
  }

  let tabSeq = 0;
  /**
   * Video / Blog & Content tabs with expandable resource details.
   * opts.checked(url) -> bool and opts.onCheck(url, bool) enable per-resource checkboxes (TutMesh).
   */
  function resourceTabs(resources, opts = {}) {
    const topics = resources?.topics?.topics?.length ? resources.topics : null;
    const groups = [
      { key: "video", label: "Video", count: (resources?.video || []).length, render: p => renderItems(p, resources?.video || [], opts) },
      { key: "content", label: "Blog / Content", count: (resources?.content || []).length, render: p => renderItems(p, resources?.content || [], opts) },
    ];
    if (topics) groups.push({ key: "topics", label: "Topics", count: topics.topics.length, render: p => renderTopics(p, topics, opts) });
    const id = `rt${++tabSeq}`;
    const wrap = h("div", { class: "res-tabs" });
    const bar = h("div", { class: "res-tabbar", role: "tablist" });
    const panels = groups.map((g, i) => {
      const panel = h("div", { class: "res-panel", role: "tabpanel", id: `${id}-${g.key}`, hidden: i !== 0 });
      g.render(panel);
      return panel;
    });
    groups.forEach((g, i) => {
      const btn = h("button", {
        type: "button", role: "tab", class: "res-tab", "aria-selected": String(i === 0), "aria-controls": `${id}-${g.key}`,
      }, h("span", { class: `res-tab-icon icon-${g.key}`, "aria-hidden": "true" }), g.label, h("span", { class: "count", text: String(g.count) }));
      btn.addEventListener("click", () => {
        $$(".res-tab", bar).forEach((b, j) => b.setAttribute("aria-selected", String(j === i)));
        panels.forEach((p, j) => { p.hidden = j !== i; });
      });
      bar.append(btn);
    });
    wrap.append(bar, ...panels);
    return wrap;
  }

  function renderItems(panel, items, opts) {
    if (!items.length) panel.append(h("p", { class: "hint", text: "Nothing here yet." }));
    items.forEach(r => panel.append(resourceItem(r, opts)));
  }

  /** Essential topics for the skill, mined from public course syllabi. */
  function renderTopics(panel, data, opts) {
    const n = data.courses?.length || 0;
    const list = h("ol", { class: "topic-list" });
    let topicNum = 0;
    data.topics.forEach(t => {
      topicNum++;
      const key = `topic:${t.name}`;
      const item = h("li", { class: `topic-item ${opts.topicChecked?.(key) ? "done" : ""}` });
      let lead = null;
      if (opts.onTopicCheck) {
        lead = h("input", { type: "checkbox", "aria-label": `Mark topic ${t.name} as learned` });
        lead.checked = !!opts.topicChecked?.(key);
        lead.addEventListener("change", () => { item.classList.toggle("done", lead.checked); opts.onTopicCheck(key, lead.checked); });
      }
      item.append(
        h("div", { class: "topic-num", text: String(topicNum).padStart(2, "0") }),
        h("div", { class: "topic-body" },
          h("div", { class: "topic-header" },
            h("span", { class: "topic-name", text: t.name }),
            t.minutes ? h("span", { class: "topic-duration", text: t.minutes >= 60 ? `~${Math.round(t.minutes / 6) / 10} h` : `~${t.minutes} min` }) : null,
            t.courses > 1 ? h("span", { class: "topic-courses", text: `${t.courses} courses` }) : null,
          ),
          t.description ? h("p", { class: "topic-desc", text: t.description }) : null,
          (t.sources || []).length ? h("div", { class: "topic-source" },
            "From: ",
            ...t.sources.slice(0, 2).map((s, i) => {
              const el = safeUrl(s.url)
                ? h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.course })
                : h("span", { text: s.course });
              return i ? [h("span", { text: " · " }), el] : [el];
            }).flat()) : null,
        ),
        ...(lead ? [lead] : []),
      );
      list.append(item);
    });
    panel.append(list);
    if (data.outcomes?.length) {
      panel.append(h("div", { class: "topic-outcomes" }, h("strong", { text: "You'll be able to:" }),
        h("ul", {}, data.outcomes.map(o => h("li", { text: o })))));
    }
  }

  const thumbFor = r => r.embed?.kind === "video" ? `https://i.ytimg.com/vi/${r.embed.id}/hqdefault.jpg`
    : (r.thumbnail && /^https:\/\/i\.ytimg\.com\//.test(r.thumbnail) ? r.thumbnail : null);

  function resourceItem(r, opts) {
    const url = safeUrl(r.url);
    const isSearch = r.kind === "search";
    const media = window.SM.media;
    const thumb = thumbFor(r);
    const details = h("details", { class: `res-item ${isSearch ? "is-search" : ""}` });
    
    // Summary with thumbnail
    const summary = h("summary", { class: "res-summary" },
      thumb ? h("img", { class: "res-thumb-small", src: thumb, alt: "", loading: "lazy" }) : 
        h("span", { class: `res-kind-icon ${r.embed ? "playable" : ""}`, text: r.embed ? "▶" : "📄" }),
      h("span", { class: "res-title", text: r.title || url || "Resource" }),
      h("span", { class: "res-author", text: r.author || "" }),
      h("span", { class: "chev", "aria-hidden": "true" }),
    );
    details.append(summary);
    
    // Expanded body with thumbnail and details
    const body = h("div", { class: "res-body" });
    
    if (thumb) {
      const thumbContainer = h("div", { class: "res-thumb-container" },
        h("img", { class: "res-thumb-large", src: thumb, alt: "", loading: "lazy" })
      );
      body.append(thumbContainer);
    }
    
    const detailsContainer = h("div", { class: "res-details-container" });
    if (r.description) detailsContainer.append(h("p", { class: "res-description", text: r.description }));
    if (r.highlights?.length) detailsContainer.append(h("div", { class: "res-highlights" }, r.highlights.map(x => h("span", { class: "res-highlight", text: x }))));
    body.append(detailsContainer);
    
    // Actions
    const actions = h("div", { class: "res-actions" });
    if (media && r.embed) {
      actions.append(h("button", { type: "button", class: "btn small primary", text: "▶ Play video", onclick: () => media.playVideo(r) }));
    } else if (media && url && !isSearch && r.group !== "video" && !["playlist", "video"].includes(r.kind)) {
      actions.append(h("button", { type: "button", class: "btn small primary", text: "👁 Read article", onclick: () => media.readContent(r) }));
    }
    if (url) actions.append(h("a", { class: `btn small ${actions.childElementCount ? "" : "primary"}`, href: url, target: "_blank", rel: "noopener noreferrer", text: isSearch ? "Open search ↗" : "Open original ↗" }));
    if (opts.timeFor && url && opts.timeFor(url) >= 30) {
      actions.append(h("span", { class: "res-time", text: `${Math.round(opts.timeFor(url) / 60)} min studied` }));
    }
    if (opts.onCheck && url) {
      const cb = h("input", { type: "checkbox", id: `res-check-${url}` });
      cb.checked = !!opts.checked?.(url);
      cb.addEventListener("change", () => {
        opts.onCheck(url, cb.checked);
        // Update label styling
        const label = document.querySelector(`label[for="res-check-${url}"]`);
        if (label) {
          label.classList.toggle("completed", cb.checked);
        }
      });
      const label = h("label", { class: `res-check-label ${cb.checked ? "completed" : ""}`, for: `res-check-${url}` }, cb, "Completed");
      actions.append(label);
    }
    body.append(actions);
    details.append(body);
    return details;
  }

  window.SM = {
    $, $$, h, store, post, toast, download, downloadRoadmapPdf, slugify, safeUrl, buildExport, sanitizeRoadmapDoc,
    difficultyPill, difficultyFilter, resourceTabs, DIFF_LABEL, ROADMAP_FORMAT,
    roadmapKey, findRoadmap, dedupeTutmesh, dedupeHistory,
  };
})();
