/* Theme: loaded synchronously in <head> so the saved theme applies before first paint (no flash).
 * Follows the OS setting until the user picks one with the toggle; the choice lives in localStorage. */
(() => {
  "use strict";
  const KEY = "sm:theme";
  const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const saved = () => { try { return localStorage.getItem(KEY); } catch { return null; } };

  function apply() {
    const s = saved();
    const theme = s === "dark" || s === "light" ? s : (mq && mq.matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    const btn = document.getElementById("themeToggle");
    if (btn) {
      btn.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
      btn.setAttribute("title", theme === "dark" ? "Light mode" : "Dark mode");
      btn.dataset.mode = theme;
    }
  }

  apply();
  if (mq && mq.addEventListener) mq.addEventListener("change", () => { if (!saved()) apply(); });
  window.addEventListener("storage", e => { if (e.key === KEY) apply(); });
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    const btn = document.getElementById("themeToggle");
    if (btn) btn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch { /* still switch for this page */ }
      document.documentElement.classList.add("theme-anim");
      apply();
      setTimeout(() => document.documentElement.classList.remove("theme-anim"), 400);
    });
  });
})();
