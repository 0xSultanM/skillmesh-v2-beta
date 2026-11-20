/* History page functionality. Depends on common.js (window.SM). */
(() => {
  "use strict";
  const { $, $$, h, store, toast, roadmapKey } = window.SM;
  
  function renderHistory() {
    const history = store.get("history", []);
    const empty = $("#historyEmpty");
    const list = $("#historyList");
    const tbody = $("#historyTableBody");
    const count = $("#historyCount");
    
    if (!count) return;
    
    count.textContent = `${history.length} ${history.length === 1 ? "analysis" : "analyses"}`;
    
    if (history.length === 0) {
      if (empty) empty.hidden = false;
      if (list) list.hidden = true;
      return;
    }
    
    if (empty) empty.hidden = true;
    if (list) list.hidden = false;
    
    if (tbody) {
      tbody.replaceChildren(...history.map(entry => {
        const date = new Date(entry.saved_at).toLocaleDateString();
        const scoreClass = entry.report.score >= 70 ? "status-matched" : entry.report.score >= 50 ? "status-weak" : "status-missing";
        return h("tr", null,
          h("td", null,
            h("div", { class: "history-opportunity", text: entry.job.title || "Untitled" }),
            h("div", { class: "history-company", text: `${entry.job.company || "Unknown"} · ${entry.roadmap.step_count} skills in your learning roadmap` }),
          ),
          h("td", null,
            h("span", { class: `history-score ${scoreClass}`, text: `${entry.report.score} / 100 · ${entry.report.verdict.label}` }),
          ),
          h("td", { class: "history-date", text: date }),
          h("td", { class: "history-actions" },
            h("button", { class: "btn small", text: "Open →", onclick: () => openAnalysis(entry) }),
          ),
        );
      }));
    }
  }
  
  function openAnalysis(entry) {
    const tm = store.get("tutmesh", { roadmaps: {}, order: [] });
    const doc = {
      id: entry.id,
      job: entry.job,
      summary: { score: entry.report.score, verdict: entry.report.verdict.label },
      roadmap: entry.roadmap,
      progress: { steps: {}, resources: {}, topics: {}, notes: {}, time: {} },
    };
    const existing = tm.roadmaps[doc.id];
    if (existing) {
      doc.progress = existing.progress;
    } else {
      tm.order.unshift(doc.id);
    }
    tm.roadmaps[doc.id] = doc;
    store.set("tutmesh", tm);
    window.location.href = "/tutmesh/";
  }
  
  function init() {
    const wipeBtn = $("#wipeDataBtn");
    if (wipeBtn) {
      wipeBtn.addEventListener("click", () => {
        if (confirm("Delete all saved analyses and roadmap progress? This cannot be undone.")) {
          store.wipe();
          toast("All local data deleted.");
          renderHistory();
        }
      });
    }
    renderHistory();
  }
  
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
