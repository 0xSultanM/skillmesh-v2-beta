/* TutMesh: browser-only roadmap tracker. Depends on common.js (window.SM).
 * Storage: localStorage "sm:tutmesh" = { roadmaps: {id: doc}, order: [id, ...] }
 * doc.progress = { steps: {slug: true}, resources: {url: true}, notes: {slug: text}, time: {url: seconds} }
 * Daily streaks come from automatic tracking (media.js): 10 min of video OR 5 min of reading in-app.
 */
(() => {
  "use strict";
  const { $, h, store, toast, download, slugify, sanitizeRoadmapDoc, difficultyPill, resourceTabs, findRoadmap, dedupeTutmesh } = window.SM;
  const { activity, dayKey, GOALS, DEFAULT_GOALS } = window.SM.media;
  const MAX_IMPORT_BYTES = 2 * 1024 * 1024;

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  let tm = store.get("tutmesh", { roadmaps: {}, order: [] });
  let currentId = null;
  const diffSel = new Set(); // difficulty filter for the open roadmap; empty = show all

  const save = () => { if (!store.set("tutmesh", tm)) toast("Couldn't save. Browser storage is full or disabled.", true); };
  const current = () => tm.roadmaps[currentId];
  // Attribute tracked study time to the roadmap that owns the resource (per-resource "min studied").
  function onActivity(e) {
    const { url, secs } = e.detail || {};
    if (!url || !secs) { renderStats(); return; }
    let touched = false;
    for (const id of tm.order) {
      const doc = tm.roadmaps[id];
      const has = doc.roadmap.phases.some(ph => ph.steps.some(st =>
        [...st.resources.video, ...st.resources.content].some(r => r.url === url)));
      if (has) { doc.progress.time ||= {}; doc.progress.time[url] = (doc.progress.time[url] || 0) + secs; touched = true; }
    }
    if (touched) save();
    renderStats();
  }

  // ---------- import ----------
  async function importFile(file) {
    if (!file) return;
    if (file.size > MAX_IMPORT_BYTES) { toast("That file is too large to be a roadmap.", true); return; }
    let doc;
    try {
      doc = sanitizeRoadmapDoc(JSON.parse(await file.text()));
    } catch (e) {
      toast(e instanceof SyntaxError ? "That file isn't valid JSON." : e.message, true);
      return;
    }
    const existing = findRoadmap(tm, doc); // same id, or the same job + skills under another id
    if (existing && !confirm("This roadmap is already in TutMesh. Replace it with the imported copy (including its progress)?")) return;
    if (existing) doc.id = existing; else tm.order.unshift(doc.id);
    tm.roadmaps[doc.id] = doc;
    save();
    
    // Auto-save to history when importing
    if (doc.summary) {
      const hist = store.get("history", []);
      const entry = {
        id: doc.id,
        job: doc.job,
        report: { score: doc.summary.score, verdict: { label: doc.summary.verdict } },
        roadmap: doc.roadmap,
        saved_at: new Date().toISOString(),
      };
      const idx = hist.findIndex(h => h.id === entry.id);
      if (idx >= 0) hist[idx] = entry; else hist.unshift(entry);
      store.set("history", hist.slice(0, 20));
    }
    
    select(doc.id);
    toast("Roadmap imported. Happy learning!");
  }

  function setupImport() {
    const zone = $("#importDrop"), input = $("#importFile");
    input.addEventListener("change", () => { importFile(input.files[0]); input.value = ""; });
    zone.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    ["dragenter", "dragover"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.remove("drag"); }));
    zone.addEventListener("drop", e => importFile(e.dataTransfer.files[0]));
  }

  // ---------- list ----------
  function pctDone(doc) {
    const steps = doc.roadmap.phases.flatMap(p => p.steps);
    const done = steps.filter(s => doc.progress.steps[s.slug]).length;
    return steps.length ? Math.round((done * 100) / steps.length) : 0;
  }

  function renderList() {
    tm.order = tm.order.filter(id => tm.roadmaps[id]);
    $("#tmEmpty").hidden = tm.order.length > 0;
  }

  function select(id) {
    if (!tm.roadmaps[id]) return;
    if (id !== currentId) diffSel.clear();
    currentId = id;
    history.replaceState(null, "", `#${encodeURIComponent(id)}`);
    
    // Auto-save to history when opening a roadmap
    const doc = tm.roadmaps[id];
    if (doc && doc.summary) {
      const hist = store.get("history", []);
      const entry = {
        id: doc.id,
        job: doc.job,
        report: { score: doc.summary.score, verdict: { label: doc.summary.verdict } },
        roadmap: doc.roadmap,
        saved_at: new Date().toISOString(),
      };
      const idx = hist.findIndex(h => h.id === entry.id);
      if (idx >= 0) hist[idx] = entry; else hist.unshift(entry);
      store.set("history", hist.slice(0, 20));
    }
    
    renderList();
    renderMain();
  }

  // ---------- main ----------
  function renderMain() {
    const doc = current();
    $("#tmMain").hidden = !doc;
    if (!doc) return;
    const pct = pctDone(doc);
    const steps = doc.roadmap.phases.flatMap(p => p.steps);
    const done = steps.filter(s => doc.progress.steps[s.slug]).length;

    // Roadmap selector card
    $("#tmTitle").textContent = [doc.job.title, doc.job.company].filter(Boolean).join(" – ") || "Untitled roadmap";
    $("#tmSubtitle").textContent = `${doc.roadmap.step_count} steps · ${pct}% complete`;
    $("#tmProgress").style.width = `${pct}%`;
    $("#tmProgressText").textContent = `${pct}% complete · ${done} of ${steps.length} steps`;

    // Second roadmap card
    $("#tmTitle2").textContent = [doc.job.title, doc.job.company].filter(Boolean).join(" – ") || "Untitled roadmap";
    $("#tmSubtitle2").textContent = doc.summary?.score != null ? `Fit score at export: ${doc.summary.score}/100 · ${doc.summary.verdict || ""}` : "Learning roadmap";

    renderStats();
    renderSteps(doc);
  }

  function fmtTime(mins) {
    const m = Math.round(mins);
    return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
  }

  function renderStats() {
    const s = activity.streaks();
    const today = activity.data().days[dayKey(new Date())] || {};
    const videoMin = Math.round((today.video || 0) / 60);
    const readMin = Math.round((today.read || 0) / 60);
    const videoGoalMin = Math.round(GOALS.video / 60);
    const readGoalMin = Math.round(GOALS.read / 60);

    // Streak subtitle
    $("#streakSubtitle").textContent = `Watch ${videoGoalMin} minutes or read ${readGoalMin} minutes to earn a streak day.`;

    // Goal display bars
    const watchTimeEl = $("#watchTime");
    const readTimeEl = $("#readTime");
    const watchGoalEl = $("#watchGoal");
    const readGoalEl = $("#readGoal");
    const watchBarEl = $("#watchBar");
    const readBarEl = $("#readBar");
    if (watchTimeEl) watchTimeEl.textContent = fmtTime(videoMin);
    if (readTimeEl) readTimeEl.textContent = fmtTime(readMin);
    if (watchGoalEl) watchGoalEl.textContent = fmtTime(videoGoalMin);
    if (readGoalEl) readGoalEl.textContent = fmtTime(readGoalMin);
    if (watchBarEl) watchBarEl.style.width = `${Math.min(100, videoGoalMin ? Math.round((videoMin / videoGoalMin) * 100) : 0)}%`;
    if (readBarEl) readBarEl.style.width = `${Math.min(100, readGoalMin ? Math.round((readMin / readGoalMin) * 100) : 0)}%`;

    // Metrics
    $("#dayStreak").textContent = s.current;
    $("#dayStreakSub").textContent = `Best: ${s.best} day${s.best !== 1 ? "s" : ""}`;

    renderHeatmap(activity.data().days);
  }

  // One column per week (Sunday first) for the past 12 months, with month names on top.
  function renderHeatmap(days) {
    const now = new Date();
    const end = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const start = new Date(end.getFullYear() - 1, end.getMonth(), end.getDate() + 1);
    start.setDate(start.getDate() - start.getDay());
    const cells = [], months = [];
    let col = 0, video = 0, read = 0;
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      const dow = d.getDay();
      if (dow === 0) {
        col++;
        if (!months.length || months[months.length - 1].month !== d.getMonth()) months.push({ col, month: d.getMonth() });
      }
      const day = days[dayKey(d)] || {};
      const v = day.video || 0, r = day.read || 0;
      video += v; read += r;
      const m = Math.round((v + r) / 60);
      const earned = activity.qualifies(day);
      const lvl = m <= 0 ? 0 : !earned ? 1 : m < 20 ? 2 : m < 45 ? 3 : 4;
      const label = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
      cells.push(h("div", { class: `heatmap-cell l${lvl}${earned ? " earned" : ""}`, style: `grid-column:${col + 1};grid-row:${dow + 2}`,
        title: `${label}: ${m ? `${Math.round(v / 60)} min video, ${Math.round(r / 60)} min reading` : "no tracked study"}${earned ? " · streak day ✓" : ""}` }));
    }
    if (months.length > 1 && months[1].col - months[0].col < 3) months.shift(); // a clipped first month would overlap the next label
    const last = months[months.length - 1];
    last.end = col - last.col < 2; // too few columns left for the name: anchor it to the right edge instead of overflowing
    const map = $("#heatmap");
    map.style.setProperty("--weeks", String(col));
    map.replaceChildren(
      ...months.map(mo => h("span", { class: `heat-month${mo.end ? " end" : ""}`, style: `grid-column:${mo.col + 1}${mo.end ? " / -1" : ""}`, text: MONTHS[mo.month] })),
      ...[["Mon", 3], ["Wed", 5], ["Fri", 7]].map(([text, row]) => h("span", { class: "heat-day", style: `grid-row:${row}`, text })),
      ...cells);
    map.scrollLeft = map.scrollWidth; // narrow screens: start at the most recent weeks

    const total = Math.floor((video + read) / 60);
    const w = activity.weeks();
    $("#totalTracked").textContent = `${total.toLocaleString()} min`;
    $("#totalTrackedSub").textContent = `${Math.floor(video / 60).toLocaleString()} watched · ${Math.floor(read / 60).toLocaleString()} read`;
    $("#weeklyStreak").textContent = w.current;
    $("#weeklyStreakSub").textContent = `${w.current === 1 ? "1 week" : `${w.current} weeks`} · ${w.thisWeek} of 7 days this week`;
    map.setAttribute("aria-label", `Study activity for the past 12 months: ${total} minutes tracked, ${w.current}-week streak`);
  }

  function renderSteps(doc) {
    const p = doc.progress;
    const hideDone = $("#hideCompleted").checked;
    const all = doc.roadmap.phases.flatMap(ph => ph.steps);
    const count = d => all.filter(s => s.difficulty === d).length;
    const doneAll = all.filter(s => p.steps[s.slug]).length;
    [...diffSel].forEach(d => { if (!count(d)) diffSel.delete(d); });

    // Update difficulty filter button counts and pressed state
    const diffButtons = [
      { diff: "easy", btn: document.querySelector(".diff-easy"), countEl: $("#easyCount") },
      { diff: "medium", btn: document.querySelector(".diff-medium"), countEl: $("#mediumCount") },
      { diff: "hard", btn: document.querySelector(".diff-hard"), countEl: $("#hardCount") },
    ];
    diffButtons.forEach(({ diff, btn, countEl }) => {
      if (countEl) countEl.textContent = count(diff);
      if (btn) btn.setAttribute("aria-pressed", String(!diffSel.size || diffSel.has(diff)));
    });

    const shown = st => !(hideDone && p.steps[st.slug]) && (!diffSel.size || diffSel.has(st.difficulty));
    let n = 0;
    let phaseIndex = 0;
    const phases = doc.roadmap.phases.map(ph => {
      const done = ph.steps.filter(s => p.steps[s.slug]).length;
      if (!ph.steps.some(shown)) { n += ph.steps.length; return null; }
      // Dynamic phase numbering based on actual order
      phaseIndex++;
      const phaseNum = String(phaseIndex).padStart(2, "0");
      const phaseTitle = ph.title.includes(":") ? ph.title.split(":").slice(1).join(":").trim().toUpperCase() : ph.title.toUpperCase();
      
      // Sort steps: incomplete first, completed last
      const sortedSteps = [...ph.steps].sort((a, b) => {
        const aDone = !!p.steps[a.slug];
        const bDone = !!p.steps[b.slug];
        if (aDone === bDone) return 0;
        return aDone ? 1 : -1; // incomplete first, completed last
      });
      
      return h("section", { class: `phase phase-${ph.key}` },
        h("div", { class: "phase-head" },
          h("span", { class: "phase-label", text: `PHASE ${phaseNum} / ${phaseTitle}` })),
        h("div", { class: "timeline" }, ...sortedSteps.map(st => {
          n++;
          return shown(st) ? stepCard(doc, st, n) : null;
        })));
    }).filter(Boolean);
    $("#tmSteps").replaceChildren(...(phases.length ? phases
      : [h("p", { class: "hint steps-empty", text: all.length === doneAll && hideDone && !diffSel.size
          ? "Everything is completed. Turn off \u201cHide completed\u201d to see the steps again."
          : "No steps match these filters." })]));
  }

  function stepCard(doc, st, n) {
    const p = doc.progress;
    const id = `tm-step-${slugify(st.slug)}`;
    const card = h("article", { class: `step-card ${p.steps[st.slug] ? "done" : ""}` });
    const cb = h("input", { type: "checkbox", id, class: "step-check", "aria-label": `Mark ${st.name} as done` });
    cb.checked = !!p.steps[st.slug];
    cb.addEventListener("change", () => {
      if (cb.checked) p.steps[st.slug] = true; else delete p.steps[st.slug];
      save();
      renderList();
      renderMain();
      if (cb.checked) toast(`Nice! ${st.name} completed.`);
    });

    const resDone = [...st.resources.video, ...st.resources.content].filter(r => p.resources[r.url]).length;
    const resTotal = st.resources.video.length + st.resources.content.length;
    const studied = Math.round([...st.resources.video, ...st.resources.content].reduce((n, r) => n + ((p.time || {})[r.url] || 0), 0) / 60);
    const notes = h("textarea", { rows: "2", placeholder: "Notes, links to your project, things to revisit\u2026", "aria-label": `Notes for ${st.name}`, maxlength: "4000" });
    notes.value = p.notes[st.slug] || "";
    let t;
    notes.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { p.notes[st.slug] = notes.value; save(); }, 400); });

    card.append(
      h("div", { class: "step-main" },
        h("header", { class: "step-header" },
          h("div", { class: "step-heading" }, cb, h("label", { for: id, class: "step-title", text: st.name })),
          difficultyPill(st.difficulty)),
        h("div", { class: "step-meta" },
          h("span", { text: `${st.reason} \u00b7 ~${st.hours} h \u00b7 Week ${st.start_week}${st.end_week > st.start_week ? `\u2013${st.end_week}` : ""}${resTotal ? ` \u00b7 ${resDone}/${resTotal} resources` : ""}${studied ? ` \u00b7 ${studied} min studied` : ""}` })),
        st.project ? h("div", { class: "step-project" },
          h("span", { class: "proj-label", text: "PROVE IT" }),
          h("p", { text: st.project })) : null,
        resourceTabs(st.resources, {
          checked: url => !!p.resources[url],
          onCheck: (url, on) => { if (on) p.resources[url] = true; else delete p.resources[url]; save(); renderMain(); },
          timeFor: url => (p.time || {})[url] || 0,
          topicChecked: key => !!(p.topics || {})[`${st.slug}|${key}`],
          onTopicCheck: (key, on) => { p.topics ||= {}; if (on) p.topics[`${st.slug}|${key}`] = true; else delete p.topics[`${st.slug}|${key}`]; save(); },
        }),
        h("div", { class: "notes-section" },
          h("div", { class: "notes-header" },
            h("span", { class: "notes-icon", text: "📝" }),
            h("span", { class: "notes-title", text: "Notes" }),
          ),
          notes,
        ),
      ),
    );
    return card;
  }

  // ---------- edit daily goal ----------
  // Only the goal is editable. Tracked time is never typed in: it is counted by the player and the reader (media.js).
  function setupGoalEdit() {
    const form = $("#goalEdit"), btn = $("#editGoalBtn");
    const display = $("#goalDisplay");
    const video = $("#watchGoalInput"), read = $("#readGoalInput");
    const toggle = open => {
      form.hidden = !open;
      if (display) display.hidden = !!open;
      btn.setAttribute("aria-expanded", String(open));
    };
    btn.addEventListener("click", () => {
      if (!form.hidden) { toggle(false); return; }
      video.value = Math.round(GOALS.video / 60);
      read.value = Math.round(GOALS.read / 60);
      toggle(true);
      video.focus();
    });
    $("#cancelGoalBtn").addEventListener("click", () => toggle(false));
    $("#resetGoalBtn").addEventListener("click", () => { video.value = DEFAULT_GOALS.video / 60; read.value = DEFAULT_GOALS.read / 60; });
    $("#saveGoalBtn").addEventListener("click", () => {
      activity.setGoals({ video: Number(video.value) * 60, read: Number(read.value) * 60 });
      toggle(false);
      renderStats();
      toast(`Daily goal saved: watch ${video.value} min or read ${read.value} min.`);
    });
  }

  // ---------- actions ----------
  function setupActions() {
    window.addEventListener("sm:activity", onActivity);
    window.addEventListener("sm:media-closed", () => { if (current()) { renderList(); renderSteps(current()); } });
    window.addEventListener("storage", e => { if (e.key === "sm:activity") renderStats(); });
    $("#hideCompleted").addEventListener("change", () => current() && renderSteps(current()));

    // Difficulty filter toggle buttons - exclusive selection
    document.querySelectorAll(".diff-toggle").forEach(btn => {
      btn.addEventListener("click", () => {
        const diff = btn.classList.contains("diff-easy") ? "easy"
          : btn.classList.contains("diff-medium") ? "medium"
          : "hard";
        
        // If already selected, clear all filters (show all)
        if (diffSel.has(diff)) {
          diffSel.clear();
        } else {
          // Exclusive selection - clear others and select this one
          diffSel.clear();
          diffSel.add(diff);
        }
        
        if (current()) renderSteps(current());
      });
    });

    $("#exportJsonProgressBtn").addEventListener("click", () => {
      const doc = current();
      if (!doc) return;
      // Export with progress included
      const out = { ...doc, exported_at: new Date().toISOString() };
      download(`tutmesh-${slugify(doc.job.title)}-with-progress.json`, new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }));
    });
    $("#exportPdfBtn").addEventListener("click", async e => {
      const doc = current();
      if (!doc) return;
      const btn = e.currentTarget;
      btn.classList.add("loading"); btn.disabled = true;
      try {
        await window.SM.downloadRoadmapPdf(doc);
      } catch (err) { toast(err.message, true); }
      finally { btn.classList.remove("loading"); btn.disabled = false; }
    });
    $("#removeRoadmapBtn").addEventListener("click", () => {
      $("#removeModal").hidden = false;
    });
    $("#closeRemoveModal").addEventListener("click", () => {
      $("#removeModal").hidden = true;
    });
    $("#cancelRemoveBtn").addEventListener("click", () => {
      $("#removeModal").hidden = true;
    });
    $("#exportBeforeRemoveBtn").addEventListener("click", () => {
      const doc = current();
      if (!doc) return;
      const out = { ...doc, exported_at: new Date().toISOString() };
      download(`tutmesh-${slugify(doc.job.title)}-with-progress.json`, new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }));
    });
    $("#confirmRemoveBtn").addEventListener("click", () => {
      const doc = current();
      if (!doc) return;
      delete tm.roadmaps[currentId];
      tm.order = tm.order.filter(id => id !== currentId);
      save();
      currentId = tm.order[0] || null;
      $("#removeModal").hidden = true;
      renderList();
      if (currentId) select(currentId); else { $("#tmMain").hidden = true; history.replaceState(null, "", location.pathname); }
      toast("Roadmap removed.");
    });
    $("#switchRoadmapBtn").addEventListener("click", () => {
      const dropdown = $("#roadmapDropdown");
      dropdown.hidden = !dropdown.hidden;
      if (!dropdown.hidden) {
        renderRoadmapDropdown();
      }
    });
    // Close dropdown when clicking outside
    document.addEventListener("click", e => {
      const dropdown = $("#roadmapDropdown");
      const btn = $("#switchRoadmapBtn");
      if (dropdown && !dropdown.hidden && !dropdown.contains(e.target) && e.target !== btn) {
        dropdown.hidden = true;
      }
    });
    window.addEventListener("storage", e => { // another tab changed TutMesh
      if (e.key !== "sm:tutmesh") return;
      tm = store.get("tutmesh", { roadmaps: {}, order: [] });
      if (!tm.roadmaps[currentId]) currentId = tm.order[0] || null;
      renderList(); renderMain();
    });
  }

  function renderRoadmapDropdown() {
    const dropdown = $("#roadmapDropdown");
    dropdown.replaceChildren();
    tm.order.forEach(id => {
      const doc = tm.roadmaps[id];
      if (!doc) return;
      const item = document.createElement("div");
      item.className = `roadmap-dropdown-item ${id === currentId ? "active" : ""}`;
      item.innerHTML = `
        <div class="roadmap-dropdown-item-title">${doc.job.title || "Untitled roadmap"}</div>
        <div class="roadmap-dropdown-item-meta">${doc.job.company || ""} · ${doc.roadmap.step_count} steps</div>
      `;
      item.addEventListener("click", () => {
        select(id);
        dropdown.hidden = true;
      });
      dropdown.appendChild(item);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupImport();
    setupActions();
    setupGoalEdit();
    if (dedupeTutmesh(tm)) save(); // older versions could store the same roadmap more than once
    renderList();
    const wanted = decodeURIComponent(location.hash.slice(1));
    if (wanted && tm.roadmaps[wanted]) select(wanted);
    else if (tm.order[0]) select(tm.order[0]);
  });
})();
