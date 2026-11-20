/* Results page functionality. Depends on common.js and app.js (window.SM). */
(() => {
  "use strict";
  
  function init() {
    const resultsData = sessionStorage.getItem('sm:analysisResults');
    if (!resultsData) {
      // No results, redirect to analyzer
      window.location.href = '/';
      return;
    }
    
    try {
      const data = JSON.parse(resultsData);
      if (window.SM && typeof window.SM.renderResultsPage === 'function') {
        window.SM.renderResultsPage(data);
      } else {
        console.error('renderResultsPage not available');
      }
    } catch (e) {
      console.error('Failed to parse results data:', e);
    }
  }
  
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
