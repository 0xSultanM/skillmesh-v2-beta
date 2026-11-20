/* SkillMesh AI dashboard. Depends on common.js (window.SM). */
(() => {
  "use strict";
  const { $, $$, h, store, post, toast, download, slugify, buildExport, difficultyPill, difficultyFilter, resourceTabs, roadmapKey, findRoadmap, dedupeTutmesh, dedupeHistory } = window.SM;
  const TESSERACT_URL = "https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js";

  const state = {
    profile: null, job: null, github: null, result: null, analysisId: null,
    jobMode: "url", resumeFile: null,
  };

  async function busy(btn, fn) {
    btn.classList.add("loading"); btn.disabled = true;
    try { return await fn(); }
    catch (e) { toast(e.message || "Something went wrong.", true); }
    finally { btn.classList.remove("loading"); btn.disabled = false; updateButtons(); }
  }

  function setStep(n) {
    $$(".step-item").forEach(el => {
      const s = Number(el.dataset.step);
      el.classList.toggle("active", s === n);
      el.classList.toggle("done", s < n);
    });
    $$(".step-line").forEach((line, i) => {
      line.classList.toggle("done", i + 1 < n);
    });
  }

  function updateButtons() {
    $('[data-profile-mode="resume"]').classList.toggle("has-data", !!state.profile);
    $('[data-profile-mode="github"]').classList.toggle("has-data", !!state.github);
    $("#analyzeBtn").disabled = !((state.profile || state.github) && state.job);
  }

  function tabs(attr, onChange) {
    $$(`[data-${attr}-mode]`).forEach(btn => btn.addEventListener("click", () => {
      const mode = btn.dataset[`${attr}Mode`];
      $$(`[data-${attr}-mode]`).forEach(b => b.setAttribute("aria-selected", String(b === btn)));
      onChange(mode);
    }));
  }

  function dropzone(zone, input, onFile, accept = () => true) {
    input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));
    zone.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    ["dragenter", "dragover"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.remove("drag"); }));
    zone.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f && accept(f)) onFile(f); });
  }

  function activeJobInput() {
    if (state.jobMode === "paste") return $("#jobTextPaste");
    if (state.jobMode === "screenshot") return $("#jobTextScreenshot");
    return $("#jobText");
  }

  // ---------- capabilities ----------
  async function loadHealth() {
    try {
      const d = await (await fetch("/api/health/")).json();
      $("#capabilities").replaceChildren(
        h("span", { class: `badge ${d.llm ? "ok" : "off"}`, text: d.llm ? "AI extraction on" : "Rule-based extraction (no AI key)" }),
        h("span", { class: `badge ${d.youtube ? "ok" : ""}`, text: d.youtube ? "Live YouTube discovery" : "Curated resources" }),
        h("span", { class: "badge ok", text: "Nothing stored on our servers" }),
      );
    } catch { /* non-critical */ }
  }

  function autoStatus(el, text, kind = "") {
    if (!el) return;
    el.hidden = !text;
    el.className = `auto-status ${kind}`.trim();
    el.textContent = text || "";
  }

  // ---------- profile source (Resume / GitHub) ----------
  function setProfileMode(mode) {
    $$("[data-profile-mode]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.profileMode === mode)));
    $$("[data-profile-panel]").forEach(p => { p.hidden = p.dataset.profilePanel !== mode; });
  }

  // ---------- resume ----------
  let resumeSeq = 0;
  async function parseResume(file) {
    const seq = ++resumeSeq, status = $("#resumeStatus"), zone = $("#resumeDrop");
    autoStatus(status, "Reading your resume…", "busy");
    zone.classList.add("working");
    try {
      const form = new FormData();
      form.append("file", file);
      const data = await post("/api/resume/parse/", { form });
      if (seq !== resumeSeq) return;
      state.profile = data.profile;
      renderProfile();
      setStep(2);
      autoStatus(status, `Found ${data.profile.skills.length} skills. Review them below.`, "ok");
      toast(`Found ${data.profile.skills.length} skills in your resume.`);
    } catch (e) {
      if (seq !== resumeSeq) return;
      autoStatus(status, e.message || "We couldn't read that file.", "error");
      toast(e.message || "We couldn't read that file.", true);
    } finally {
      if (seq === resumeSeq) { zone.classList.remove("working"); updateButtons(); }
    }
  }

  function setupResume() {
    tabs("profile", setProfileMode);
    const fileInput = $("#resumeFile");
    const dropZone = $("#resumeDrop");
    const browseBtn = $("#browseResumeBtn");
    
    // Browse button click handler
    if (browseBtn) {
      browseBtn.addEventListener("click", () => fileInput.click());
    }
    
    dropzone(dropZone, fileInput, file => {
      fileInput.value = "";
      if (file.size > 5 * 1024 * 1024) { toast("Please choose a file under 5 MB.", true); return; }
      state.resumeFile = file;
      $("#resumeFileName").textContent = file.name;
      parseResume(file);
    });
  }

  function toggleStrength(s) { s.strength = s.strength === "applied" ? "listed" : "applied"; renderProfile(); }

  function renderProfile() {
    const p = state.profile;
    $("#profileBox").hidden = false;
    $("#profileSource").textContent = p.source === "llm" ? "AI extracted" : "Rule-based";
    $("#pYears").value = p.years_experience ?? 0;
    $("#pSeniority").value = p.seniority || "unknown";
    $("#pEducation").value = p.education || "";
    const box = $("#profileChips");
    box.replaceChildren(...p.skills.map((s, i) => h("span", {
      class: `chip ${s.strength === "applied" ? "applied" : "listed"} ${s.known ? "" : "custom"}`,
      title: s.evidence ? `Evidence: ${s.evidence}` : "Click to toggle used / listed",
      role: "button", tabindex: "0",
      onclick: e => { if (!e.target.closest(".x")) toggleStrength(s); },
      onkeydown: e => { if (e.key === "Enter") toggleStrength(s); },
    }, s.name, h("button", { class: "x", type: "button", "aria-label": `Remove ${s.name}`, text: "×", onclick: () => { p.skills.splice(i, 1); renderProfile(); } }))));
    if (!p.skills.length) box.append(h("p", { class: "hint", text: "No skills detected. Add them below." }));
    updateButtons();
  }

  // ---------- GitHub ----------
  let ghAuth = { signed_in: false, oauth_configured: false, free_total: 1, free_remaining: 1, login: "" };

  async function loadGithubStatus() {
    try { ghAuth = await (await fetch("/api/github/status/", { credentials: "same-origin" })).json(); } catch { /* ignore */ }
    renderGithubAuth();
  }

  function stashDraft() {
    try {
      sessionStorage.setItem("sm:draft", JSON.stringify({
        profile: state.profile, job: state.job, github: state.github,
        jobText: $("#jobText").value, githubUser: $("#githubUser").value,
      }));
    } catch { /* ignore */ }
  }

  function restoreDraft() {
    let d = null;
    try { d = JSON.parse(sessionStorage.getItem("sm:draft") || "null"); sessionStorage.removeItem("sm:draft"); } catch { /* ignore */ }
    if (!d) return null;
    if (d.profile) { state.profile = d.profile; renderProfile(); }
    if (d.jobText) $("#jobText").value = d.jobText;
    if (d.job) { state.job = d.job; renderJob(); }
    if (d.github) { state.github = d.github; renderGithub(); }
    if (d.githubUser) $("#githubUser").value = d.githubUser;
    updateButtons();
    return d;
  }

  function signIn() {
    if (!ghAuth.oauth_configured) {
      toast("GitHub sign-in needs a one-time setup on this server: add GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET to .env, then restart it.", true);
      return;
    }
    stashDraft();
    window.location.href = "/auth/github/login/?next=/";
  }

  function renderGithubAuth(loginRequired = false) {
    const box = $("#ghAuth");
    if (ghAuth.signed_in) {
      box.className = "gh-auth-card ok";
      box.replaceChildren(
        h("strong", { style: "color: var(--good); font-size: 0.95rem;", text: "✓ Signed in as " }),
        h("strong", { text: `@${ghAuth.login}`, style: "color: var(--good);" }),
        h("span", { style: "color: var(--muted); font-size: 0.85rem; margin-left: 8px;", text: "· unlimited analyses" }),
        h("button", { type: "button", class: "linklike", style: "margin-left: auto; color: var(--bad);", text: "Sign out", onclick: async () => {
          try { await post("/auth/github/logout/", { json: {} }); } catch { /* ignore */ }
          loadGithubStatus();
          toast("Signed out of GitHub. The token was revoked.");
        } }));
      return;
    }
    const left = ghAuth.free_remaining;
    const used = loginRequired || !left;
    box.className = `gh-auth-card ${used ? "warn" : ""}`;
    box.replaceChildren(
      h("div", { style: "flex: 1;" },
        h("strong", { style: `color: ${used ? "var(--warn)" : "var(--primary)"}; font-size: 0.95rem;`, text: used ? "Today's free GitHub analysis is used" : `${left} free GitHub ${left === 1 ? "analysis" : "analyses"} available today` }),
        h("p", { style: "margin: 4px 0 0; font-size: 0.85rem; color: var(--text-secondary);", text: used
          ? "Sign in with GitHub to keep analyzing right now, or come back tomorrow for another free one."
          : "Public data only · No permissions requested" }),
      ),
    );
  }

  function setupGithub() {
    $("#githubForm").addEventListener("submit", e => {
      e.preventDefault();
      const raw = $("#githubUser").value.trim();
      if (!raw) { toast("Enter a GitHub username or profile URL.", true); return; }
      busy($("#githubBtn"), async () => {
        const data = await post("/api/github/analyze/", { json: { username: raw } });
        state.github = data.github;
        ghAuth = { ...ghAuth, ...data.github_auth };
        renderGithub();
        renderGithubAuth();
        updateButtons();
      });
    });
    $("#githubClear").addEventListener("click", () => {
      state.github = null;
      $("#githubBox").hidden = true;
      $("#githubUser").value = "";
      updateButtons();
    });
  }

  function renderGithub() {
    const g = state.github;
    $("#githubBox").hidden = false;
    $("#ghSummary").replaceChildren(
      h("a", { class: "gh-user", href: g.profile_url, target: "_blank", rel: "noopener", text: `@${g.username}` }),
      h("span", { text: `· ${g.public_repos} public repos · ${g.repos_analyzed} analyzed · ${g.total_stars} stars` }),
    );
    const bar = $("#ghLangBar");
    bar.replaceChildren(...g.top_languages.map(l => h("span", {
      style: `width:${l.percent}%;background:var(--primary)`,
      title: `${l.name}: ${l.percent}%`,
    })));
    $("#ghLangLegend").replaceChildren(...g.top_languages.map(l => h("span", null,
      h("i", { style: `background:var(--primary)` }), l.name)));
    const box = $("#githubChips");
    box.replaceChildren(...g.skills.map(s => h("span", {
      class: "chip gh",
      title: s.evidence,
    }, s.name, h("span", { class: "lvl", text: String(s.level) }))));
    updateButtons();
  }

  // ---------- job input ----------
  function setJobMode(mode) {
    state.jobMode = mode;
    $$("[data-job-mode]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.jobMode === mode)));
    $$("[data-job-panel]").forEach(p => { p.hidden = p.dataset.jobPanel !== mode; });
  }

  function setupJob() {
    tabs("job", setJobMode);
    $("#jobUrlForm").addEventListener("submit", e => {
      e.preventDefault();
      const url = $("#jobUrl").value.trim();
      if (!url) { toast("Enter a job URL.", true); return; }
      busy($("#fetchJobBtn"), async () => {
        const data = await post("/api/job/scrape/", { json: { url } });
        $("#jobText").value = data.text || "";
        state.job = null;
        if (data.text && data.text.length >= 80) {
          await parseJobText(data.text);
        } else {
          toast("Could not extract job description. Try pasting the text instead.", true);
        }
      });
    });
    $("#jobText").addEventListener("input", debounce(() => {
      const text = $("#jobText").value.trim();
      if (text.length >= 80) parseJobText(text);
    }, 1500));
    $("#jobTextPaste").addEventListener("input", debounce(() => {
      const text = $("#jobTextPaste").value.trim();
      if (text.length >= 80) parseJobText(text);
    }, 1500));
    setupScreenshot();
  }

  function setupScreenshot() {
    const fileInput = $("#screenshotFile");
    const dropZone = $("#screenshotDrop");
    dropzone(dropZone, fileInput, async file => {
      if (!file.type.startsWith("image/")) { toast("Please choose an image file.", true); return; }
      fileInput.value = "";
      await runOCR(file);
    });
    document.addEventListener("paste", e => {
      if (state.jobMode !== "screenshot") return;
      const items = e.clipboardData?.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith("image/")) {
          e.preventDefault();
          runOCR(item.getAsFile());
          break;
        }
      }
    });
  }

  async function runOCR(file) {
    const status = $("#jobStatus");
    autoStatus(status, "Reading image with on-device OCR…", "busy");
    try {
      if (!window.Tesseract) {
        await new Promise((resolve, reject) => {
          const s = document.createElement("script");
          s.src = TESSERACT_URL; s.onload = resolve; s.onerror = reject;
          document.head.appendChild(s);
        });
      }
      const { data: { text } } = await Tesseract.recognize(file, "eng");
      $("#jobTextScreenshot").hidden = false;
      $("#jobTextScreenshot").value = text;
      $("#screenshotHint").textContent = "✓ OCR complete. Review and edit if needed.";
      if (text.trim().length >= 80) await parseJobText(text);
      autoStatus(status, "Screenshot read successfully.", "ok");
    } catch (e) {
      autoStatus(status, "OCR failed. Try a clearer image.", "error");
      toast("OCR failed: " + e.message, true);
    }
  }

  let jobSeq = 0;
  async function parseJobText(text) {
    const seq = ++jobSeq;
    try {
      const data = await post("/api/job/parse/", { json: { text } });
      if (seq !== jobSeq) return;
      state.job = data.job;
      renderJob();
      setStep(2);
      updateButtons();
    } catch (e) {
      if (seq !== jobSeq) return;
      if (e.code === "no_skills") {
        toast(e.message, true);
      } else {
        toast(e.message || "Couldn't extract job details.", true);
      }
    }
  }

  function renderJob() {
    const j = state.job;
    if (!j) return;
    
    // Determine which panel to show skills in based on job mode
    const suffix = state.jobMode === "paste" ? "Paste" : state.jobMode === "screenshot" ? "Screenshot" : "";
    
    // Show the skills box
    const skillsBox = $(`#jobSkillsBox${suffix}`);
    if (skillsBox) skillsBox.hidden = false;
    
    // Render required skills
    const requiredBox = $(`#requiredSkills${suffix}`);
    if (requiredBox) {
      requiredBox.replaceChildren(...(j.required || []).map((s, i) => h("span", {
        class: "chip applied",
        title: s.name,
      }, s.name, h("button", { class: "x", type: "button", "aria-label": `Remove ${s.name}`, text: "×", onclick: () => { j.required.splice(i, 1); renderJob(); } }))));
    }
    
    // Render preferred/nice-to-have skills
    const preferredBox = $(`#preferredSkills${suffix}`);
    if (preferredBox) {
      preferredBox.replaceChildren(...(j.preferred || []).map((s, i) => h("span", {
        class: "chip preferred",
        title: s.name,
      }, s.name, h("button", { class: "x", type: "button", "aria-label": `Remove ${s.name}`, text: "×", onclick: () => { j.preferred.splice(i, 1); renderJob(); } }))));
    }
    
    updateButtons();
  }

  // ---------- analysis ----------
  function setupAnalysis() {
    const slider = $("#hoursSlider");
    const value = $("#hoursValue");
    const pace = $("#studyPace");
    slider.addEventListener("input", () => {
      const h = Number(slider.value);
      value.textContent = h;
      const perDay = (h / 7).toFixed(1);
      pace.textContent = h <= 10 ? `Steady · about ${perDay} h a day` : h <= 20 ? `Focused · about ${perDay} h a day` : `Intensive · about ${perDay} h a day`;
    });
    $("#analyzeBtn").addEventListener("click", () => {
      if (!(state.profile || state.github) || !state.job) return;
      busy($("#analyzeBtn"), async () => {
        const profile = state.profile || { skills: [], years_experience: 0, seniority: "unknown", education: "", domains: [] };
        profile.years_experience = Number($("#pYears").value) || 0;
        profile.seniority = $("#pSeniority").value;
        profile.education = $("#pEducation").value;
        const data = await post("/api/analyze/", { json: {
          job: state.job, profile, github: state.github,
          hours_per_week: Number(slider.value),
          include_preferred: $("#includePreferred").checked,
        }});
        state.result = data;
        state.analysisId = `rm_${roadmapKey(data.job, data.roadmap)}`;
        renderResults(data);
        setStep(3);
      });
    });
  }

  function renderResults(data) {
    const { report, roadmap, job } = data;
    
    // Save results to session storage and redirect to results page
    sessionStorage.setItem('sm:analysisResults', JSON.stringify(data));
    window.location.href = '/result/';
  }

  // Expose renderResultsPage for the results sub-page
  function renderResultsPage(data) {
    const { report, roadmap, job } = data;
    const container = $("#resultsContainer");
    if (container) {
      container.replaceChildren(buildResultsSkeleton(report, roadmap, job));
      setupResultsActions(data);
    }
  }

  // Add to window.SM
  window.SM.renderResultsPage = renderResultsPage;

  function buildResultsSkeleton(report, roadmap, job) {
    const verdictClass = `v-${report.verdict.key}`;
    const circumference = 2 * Math.PI * 52;
    const offset = circumference * (1 - report.score / 100);
    
    return h("div", { class: "results-container" },
      // Header with title and action buttons
      h("div", { class: "results-header" },
        h("div", null,
          h("div", { class: "page-eyebrow", text: "ANALYZER" }),
          h("h1", { class: "results-title", text: "Your next move, made clearer" }),
          h("p", { class: "results-subtitle", text: `${job.title}${job.company ? ` · ${job.company}` : ""}` }),
        ),
        h("div", { class: "results-header-actions" },
          h("button", { class: "btn", id: "printReportBtn", text: "Print report" }),
          h("button", { class: "btn", id: "exportJsonBtn", text: "Export JSON" }),
        ),
      ),
      
      // Score hero section
      h("div", { class: "result-hero" },
        h("div", { class: "score-circle" },
          h("svg", { viewBox: "0 0 120 120" },
            h("circle", { class: "score-circle-bg", cx: "60", cy: "60", r: "52" }),
            h("circle", { class: "score-circle-fg", cx: "60", cy: "60", r: "52", style: `stroke-dasharray: ${circumference}; stroke-dashoffset: ${offset}` }),
          ),
          h("div", { class: "score-label" },
            h("b", { text: String(report.score) }),
            h("small", { text: "OUT OF 100" }),
          ),
        ),
        h("div", { class: "verdict-info" },
          h("span", { class: `verdict-pill ${verdictClass}`, text: report.verdict.label }),
          h("h2", { class: "verdict-title", text: `${job.title}${job.company ? ` – ${job.company}` : ""}` }),
          h("p", { class: "verdict-message", text: report.verdict.message }),
          report.caps.length ? h("p", { class: "verdict-cap", text: `Score capped: ${report.caps[0]}` }) : null,
        ),
      ),
      
      // Breakdown + Quick wins
      h("div", { class: "grid-2" },
        h("div", { class: "breakdown-card" },
          h("h3", { class: "breakdown-title", text: "What's behind your score" }),
          ...report.breakdown.map(b => h("div", { class: "bar-row" },
            h("div", { class: "bar-label" },
              h("span", { text: b.label }),
              h("span", { text: `${b.points} / ${Math.round(b.weight * 100)} pts` }),
            ),
            h("div", { class: "bar" }, h("div", { style: `width: ${(b.value / b.weight) * 100}%` })),
          )),
        ),
        h("div", { class: "breakdown-card" },
          h("h3", { class: "breakdown-title", text: "Small changes, stronger evidence" }),
          report.quick_wins.length ? h("div", { class: "quick-wins-list" },
            ...report.quick_wins.map((w, i) => {
              const parts = w.split(".");
              const title = parts[0] + ".";
              const desc = parts.slice(1).join(".").trim();
              return h("div", { class: "quick-win-item" },
                h("div", { class: "quick-win-number", text: String(i + 1).padStart(2, "0") }),
                h("div", { class: "quick-win-content" },
                  h("div", { class: "quick-win-title", text: title }),
                  h("div", { class: "quick-win-desc", text: desc }),
                ),
              );
            }),
          ) : h("p", { class: "hint", text: "No quick wins identified." }),
          h("p", { class: "hint", style: "margin-top: 16px; font-size: 0.85rem;", text: "An estimate based on your resume—not a hiring decision." }),
        ),
      ),
      
      // Skill fit table
      h("div", { class: "skill-fit-card" },
        h("div", { class: "skill-fit-header" },
          h("h3", { class: "skill-fit-title", text: "Your skill fit" }),
          h("span", { class: "skill-fit-evidence", text: `${report.readiness}% skill evidence` }),
        ),
        h("table", { class: "skill-table" },
          h("thead", null,
            h("tr", null,
              h("th", { text: "SKILL" }),
              h("th", { text: "EVIDENCE IN YOUR RESUME" }),
              h("th", { text: "MATCH" }),
            ),
          ),
          h("tbody", null,
            ...[...report.required, ...report.preferred.map(r => ({ ...r, name: `${r.name} · optional` }))].map(r => {
              const statusClass = r.status === "matched" ? "status-matched" : r.status === "partial" ? "status-weak" : "status-missing";
              const statusText = r.status === "matched" ? "Matched" : r.status === "partial" ? "Weak signal" : "Missing";
              const evidence = r.status === "matched" ? (r.strength === "applied" ? "Used in work" : "Listed in your resume") :
                              r.status === "partial" ? (r.via ? `${r.via} experience may transfer` : "Partial evidence") :
                              "No supporting evidence";
              return h("tr", null,
                h("td", { class: "skill-name", text: r.name }),
                h("td", { class: "skill-evidence", text: evidence }),
                h("td", null,
                  h("div", { class: "skill-match-bar" }, h("div", { style: `width: ${r.your_level}%` })),
                  h("span", { class: `skill-status ${statusClass}`, text: statusText }),
                ),
              );
            }),
          ),
        ),
      ),
      
      // Roadmap preview
      h("div", { class: "roadmap-preview" },
        h("div", { class: "roadmap-preview-header" },
          h("div", null,
            h("h3", { class: "roadmap-preview-title", text: "Close the gaps. Build the evidence." }),
            h("p", { class: "roadmap-preview-subtitle", text: `${roadmap.step_count} skills · ${roadmap.total_hours} hours · ${roadmap.total_weeks} weeks at ${roadmap.hours_per_week} h/week` }),
          ),
        ),
        h("div", { class: "roadmap-stats" },
          ...roadmap.phases.flatMap(p => p.steps).slice(0, 4).map((st, i) => h("div", { class: `roadmap-stat-card ${st.priority === 'preferred' ? 'preferred' : ''}` },
            h("div", { class: "roadmap-stat-number", text: String(i + 1).padStart(2, "0") }),
            h("div", { class: "roadmap-stat-content" },
              h("div", { class: "roadmap-stat-title", text: st.name }),
              h("div", { class: "roadmap-stat-meta", text: `${st.hours} h · Week${st.end_week > st.start_week ? `s ${st.start_week}-${st.end_week}` : ` ${st.start_week}`}` }),
              st.project ? h("div", { class: "roadmap-stat-desc", text: st.project }) : null,
            ),
          )),
        ),
        h("div", { class: "roadmap-actions" },
          h("button", { class: "btn primary large", id: "openRoadmapBtn", text: "Open learning roadmap →" }),
          h("div", { class: "roadmap-actions-right" },
            h("button", { class: "btn", id: "saveHistoryBtn", text: "Save to history" }),
            h("button", { class: "btn", id: "exportPdfBtn", text: "Export PDF" }),
          ),
        ),
      ),
      h("p", { class: "hint", style: "text-align: center; margin-top: 24px; font-size: 0.85rem;", text: "Your resume and job data are processed in memory and never stored on our servers." }),
    );
  }

  function setupResultsActions(data) {
    $("#openRoadmapBtn")?.addEventListener("click", () => {
      const doc = buildExport(data, state.analysisId);
      const tm = store.get("tutmesh", { roadmaps: {}, order: [] });
      const existing = findRoadmap(tm, doc);
      if (existing) {
        doc.id = existing;
        tm.roadmaps[existing] = doc;
      } else {
        tm.order.unshift(doc.id);
        tm.roadmaps[doc.id] = doc;
      }
      store.set("tutmesh", tm);
      
      // Auto-save to history
      const history = store.get("history", []);
      const entry = {
        id: doc.id,
        job: data.job,
        report: data.report,
        roadmap: data.roadmap,
        saved_at: new Date().toISOString(),
      };
      const idx = history.findIndex(h => h.id === entry.id);
      if (idx >= 0) history[idx] = entry; else history.unshift(entry);
      store.set("history", history.slice(0, 20));
      
      window.location.href = "/tutmesh/";
    });
    $("#saveHistoryBtn")?.addEventListener("click", () => {
      const history = store.get("history", []);
      const entry = {
        id: state.analysisId,
        job: data.job,
        report: data.report,
        roadmap: data.roadmap,
        saved_at: new Date().toISOString(),
      };
      const idx = history.findIndex(h => h.id === entry.id);
      if (idx >= 0) history[idx] = entry; else history.unshift(entry);
      store.set("history", history.slice(0, 20));
      toast("Saved to history.");
    });
    $("#printReportBtn")?.addEventListener("click", () => {
      // Add print class to body for print-specific styling
      document.body.classList.add("print-mode");
      window.print();
      // Remove print class after print dialog closes
      setTimeout(() => {
        document.body.classList.remove("print-mode");
      }, 1000);
    });
    $("#exportJsonBtn")?.addEventListener("click", () => {
      const doc = buildExport(data, state.analysisId);
      download(`skillmesh-roadmap-${slugify(data.job.title)}.json`, new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }));
    });
    $("#exportPdfBtn")?.addEventListener("click", () => {
      const doc = buildExport(data, state.analysisId);
      downloadRoadmapPdf(doc, `skillmesh-roadmap-${slugify(data.job.title)}.pdf`);
    });
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  // ---------- init ----------
  document.addEventListener("DOMContentLoaded", () => {
    // Only run analyzer setup if we're on the analyzer page
    if ($("#resumeDrop") || $("#jobUrlForm")) {
      setupResume();
      setupGithub();
      setupJob();
      setupAnalysis();
      loadHealth();
      loadGithubStatus();
      restoreDraft();
      updateButtons();
    }
  });
})();
