/* Mobile menu toggle functionality. Loaded on all pages. */
(() => {
  "use strict";
  
  function initMobileMenu() {
    var toggle = document.getElementById('mobileMenuToggle');
    var sidebar = document.getElementById('sidebar');
    if (!toggle || !sidebar) return;
    
    toggle.addEventListener('click', function() {
      sidebar.classList.toggle('open');
    });
    
    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', function(e) {
      if (window.innerWidth <= 768 && !sidebar.contains(e.target) && e.target !== toggle) {
        sidebar.classList.remove('open');
      }
    });
  }
  
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMobileMenu);
  } else {
    initMobileMenu();
  }
})();
