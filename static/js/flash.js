// Auto-dismiss successful flash messages after 5 seconds.
// Error messages stay visible until the user closes them.
(function () {
  var AUTO_DISMISS_MS = 5000;
  var FADE_MS = 400;

  function dismiss(el) {
    if (!el || el.getAttribute('data-dismissing')) return;
    el.setAttribute('data-dismissing', '1');
    el.style.transition = 'opacity ' + FADE_MS + 'ms ease, transform ' + FADE_MS + 'ms ease';
    el.style.opacity = '0';
    el.style.transform = 'translateY(-6px)';
    setTimeout(function () { el.remove(); }, FADE_MS);
  }

  function init() {
    var alerts = document.querySelectorAll('.flash-alert.flash-success');
    Array.prototype.forEach.call(alerts, function (el) {
      setTimeout(function () { dismiss(el); }, AUTO_DISMISS_MS);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
