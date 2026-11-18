/*! ux-enhancements.js — premium UX layer for the AI job-matching app
 * ---------------------------------------------------------------------------
 * DROP-IN: save as  static/js/ux-enhancements.js
 * Include with `defer`, AFTER main.js and app.js, on every page:
 *     <script src="/static/js/ux-enhancements.js" defer></script>
 *
 * Safety contract (why this cannot break your Flask routes or forms)
 *  • Never touches window.fetch, window.onload, `submit` events, form data,
 *    URLs, tokens or any API call. app.js keeps full ownership of those
 *    (it intercepts fetch and handles submits in the capture phase with
 *    stopImmediatePropagation, so this file deliberately never listens to
 *    `submit`; it watches the aria-busy attribute that app.js sets instead).
 *  • Uses addEventListener / observers only, so it coexists with pages that
 *    assign `window.onload = …` (seeker_dashboard.html does).
 *  • Every module is wrapped in try/catch: a failure in one feature logs a
 *    warning and the rest of the page keeps working.
 *  • Idempotent: loading the file twice is a no-op.
 *
 * Modules
 *   1. Scroll reveal (IntersectionObserver) for cards, rows, stats
 *   2. Match gauges: ring draws from 0 + percentage counts up
 *   3. Count-up numbers (admin stats, pipeline)
 *   4. Ripple + magnetic-pull buttons
 *   5. Active navigation state + sliding login tabs
 *   6. Page exit transition + top progress bar
 *   7. Resume-upload / OCR loading overlay
 *
 * Optional config — define BEFORE this script loads:
 *     window.UX_CONFIG = { magnetic: false, pageTransitions: false };
 * ------------------------------------------------------------------------- */
(function () {
  'use strict';

  if (window.__uxEnhancements) return;
  window.__uxEnhancements = true;

  var CONFIG = Object.assign({
    reveal: true,
    countUp: true,
    gauges: true,
    ripple: true,
    magnetic: true,
    navState: true,
    tabSlider: true,
    pageTransitions: true,
    resumeOverlay: true
  }, window.UX_CONFIG || {});

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  var canHover = window.matchMedia('(hover: hover) and (pointer: fine)');

  /* ---------------------------------------------------------------- utils */
  function guard(name, fn) {
    return function () {
      try { return fn.apply(this, arguments); }
      catch (err) { console.warn('[ux] ' + name + ' failed:', err); }
    };
  }
  function clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }
  function easeOutExpo(t) { return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t); }
  function collect(root, selector) {
    var out = [];
    if (root.matches && root.matches(selector)) out.push(root);
    root.querySelectorAll(selector).forEach(function (n) { out.push(n); });
    return out;
  }

  /* ------------------------------------------------------ 1. SCROLL REVEAL */
  var REVEAL_SELECTOR = [
    '.job-card', '.applicant-card', '.applicant-row',
    '.stat-card', '.recent-card', '.time-card', '.pipeline-stage',
    '.notification', '.grid > .card', '.role-container .card',
    '.data-table tbody tr',
    'main section .max-w-2xl > *',   /* landing hero copy   */
    'main section .shadow-card'      /* landing match card  */
  ].join(',');

  var revealObserver = null;

  function setupReveal() {
    if (!CONFIG.reveal || reduceMotion.matches || !('IntersectionObserver' in window)) return;
    revealObserver = new IntersectionObserver(onRevealEntries, {
      rootMargin: '0px 0px -6% 0px',
      threshold: 0.06
    });
  }

  /* Admin placeholders ("--", "Loading…") are replaced a moment later by the
     real nodes; skipping them avoids a double animation. */
  function isPlaceholder(el) {
    var text = (el.textContent || '').trim();
    return text.length < 80 && text.indexOf('--') !== -1;
  }

  function queueReveal(el) {
    if (!revealObserver || el.dataset.uxReveal) return;
    if (isPlaceholder(el)) return;
    el.dataset.uxReveal = '1';
    el.classList.add('ux-reveal');          /* hides it before first paint */
    revealObserver.observe(el);
  }

  function onRevealEntries(entries) {
    var visible = entries
      .filter(function (e) { return e.isIntersecting; })
      .sort(function (a, b) {
        return (a.boundingClientRect.top - b.boundingClientRect.top) ||
               (a.boundingClientRect.left - b.boundingClientRect.left);
      });

    visible.forEach(function (entry, i) {
      var el = entry.target;
      revealObserver.unobserve(el);
      el.style.setProperty('--ux-delay', Math.min(i, 7) * 55 + 'ms');   /* stagger */
      el.classList.add('ux-in');

      var finished = false;
      function done() {
        if (finished) return;
        finished = true;
        el.removeEventListener('animationend', onEnd);
        el.classList.remove('ux-reveal', 'ux-in');   /* hand control back to hover CSS */
        el.style.removeProperty('--ux-delay');
      }
      function onEnd(ev) { if (ev.target === el) done(); }
      el.addEventListener('animationend', onEnd);
      setTimeout(done, 1800);                         /* safety net */
    });
  }

  /* ---------------------------------------------- 2 + 3. GAUGES & COUNT-UP */
  var METRIC_SELECTOR = '.stat-card h3, .pipeline-stage h4, .mini-text, .app-match-gauge-value';
  var GAUGE_SELECTOR = '.mini-circle, .app-match-gauge';
  var metricObserver = null;
  var gaugeObserver = null;

  function setupMetrics() {
    if (reduceMotion.matches || !('IntersectionObserver' in window)) return;
    if (CONFIG.countUp) {
      metricObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          metricObserver.unobserve(entry.target);
          runMetric(entry.target);
        });
      }, { threshold: 0.4 });
    }
    if (CONFIG.gauges) {
      gaugeObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          gaugeObserver.unobserve(entry.target);
          runGauge(entry.target);
        });
      }, { threshold: 0.4 });
    }
  }

  function formatMetric(value, decimals) {
    return decimals ? value.toFixed(decimals) : String(Math.round(value));
  }

  function prepMetric(el) {
    if (!metricObserver || el.dataset.uxMetric) return;
    var m = /^\s*(\d+(?:\.\d+)?)(\s*%)?\s*$/.exec(el.textContent || '');
    if (!m) return;                                   /* skips "--" placeholders */
    var node = el.firstChild;
    if (!node || node.nodeType !== 3 || el.childNodes.length !== 1) return;

    var decimals = (m[1].split('.')[1] || '').length;
    el._uxMetric = { to: parseFloat(m[1]), suffix: m[2] || '', decimals: decimals, node: node };
    el.dataset.uxMetric = '1';
    /* Editing nodeValue is a characterData mutation, so app.js's
       childList MutationObserver is not woken on every animation frame. */
    node.nodeValue = formatMetric(0, decimals) + el._uxMetric.suffix;
    metricObserver.observe(el);
  }

  function runMetric(el) {
    var d = el._uxMetric;
    if (!d) return;
    var start = performance.now();
    var duration = 1000;
    (function frame(now) {
      if (!el.isConnected) return;
      var t = clamp((now - start) / duration, 0, 1);
      var value = t >= 1 ? d.to : d.to * easeOutExpo(t);
      d.node.nodeValue = formatMetric(value, d.decimals) + d.suffix;
      if (t < 1) requestAnimationFrame(frame);
    })(start);
  }

  /* Works for the legacy .mini-circle AND the .app-match-gauge that app.js
     swaps in; both end up as <circle> elements using stroke-dashoffset. */
  function prepGauge(el) {
    if (!gaugeObserver || el.dataset.uxGauge) return;
    var ring = el.querySelector('circle.progress') || el.querySelector('svg circle:last-of-type');
    if (!ring) return;

    function read(styleProp, attr) { return parseFloat(ring.style[styleProp] || ring.getAttribute(attr)); }
    var circumference = read('strokeDasharray', 'stroke-dasharray');
    var target = read('strokeDashoffset', 'stroke-dashoffset');
    if (!isFinite(circumference) || !isFinite(target)) return;   /* not ready: a later mutation retries */

    el.dataset.uxGauge = '1';
    ring._uxTarget = target;
    el._uxRing = ring;
    ring.style.transition = 'none';
    ring.style.strokeDashoffset = String(circumference);          /* start empty */
    gaugeObserver.observe(el);
  }

  function runGauge(el) {
    var ring = el._uxRing;
    if (!ring) return;
    void ring.getBoundingClientRect();                            /* commit the empty state first */
    ring.style.transition = 'stroke-dashoffset 1.15s cubic-bezier(0.22, 1, 0.36, 1)';
    ring.style.strokeDashoffset = String(ring._uxTarget);
    setTimeout(function () { ring.style.transition = ''; }, 1400);
  }

  /* Landing page preview ring (static markup, stroke-dasharray "276.5 314.2") */
  function setupLandingRing() {
    var svg = document.querySelector('svg[aria-label$="percent match score"]');
    if (!svg || !gaugeObserver) return;
    var ring = svg.querySelector('circle:last-of-type');
    var parts = ring && (ring.getAttribute('stroke-dasharray') || '').trim().split(/\s+/).map(parseFloat);
    if (!parts || parts.length !== 2 || parts.some(isNaN)) return;

    ring.style.transition = 'none';
    ring.style.strokeDasharray = '0 ' + parts[1];
    svg._uxRun = function () {
      void ring.getBoundingClientRect();
      ring.style.transition = 'stroke-dasharray 1.3s cubic-bezier(0.22, 1, 0.36, 1)';
      ring.style.strokeDasharray = parts[0] + ' ' + parts[1];
    };
    var io = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) { io.disconnect(); svg._uxRun(); }
    }, { threshold: 0.4 });
    io.observe(svg);

    var label = svg.parentElement && svg.parentElement.querySelector('.text-3xl');
    if (label) prepMetric(label);
  }

  /* ------------------------------------------------ 4. RIPPLE + MAGNETIC */
  var RIPPLE_SELECTOR = 'button, .btn, .primary-btn, .btn-primary, .apply-btn, a.ux-cta';
  var RIPPLE_SKIP = '.tab, .modal-close, .toggle-password, .password-toggle, .app-toast-close, [data-ux-skip]';

  function spawnRipple(el, clientX, clientY) {
    var rect = el.getBoundingClientRect();
    var size = Math.max(rect.width, rect.height) * 2.2;
    el.classList.add('ux-ripple-host');
    var span = document.createElement('span');
    span.className = 'ux-ripple';
    span.setAttribute('aria-hidden', 'true');
    span.style.setProperty('--x', (clientX - rect.left) + 'px');
    span.style.setProperty('--y', (clientY - rect.top) + 'px');
    span.style.setProperty('--d', size + 'px');
    el.appendChild(span);
    var remove = function () { span.remove(); };
    span.addEventListener('animationend', remove, { once: true });
    setTimeout(remove, 900);
  }

  function setupRipple() {
    if (!CONFIG.ripple || reduceMotion.matches) return;

    document.addEventListener('pointerdown', guard('ripple', function (e) {
      if (e.button !== 0) return;
      var el = e.target.closest && e.target.closest(RIPPLE_SELECTOR);
      if (!el || el.disabled || el.matches(RIPPLE_SKIP)) return;
      spawnRipple(el, e.clientX, e.clientY);
    }), { passive: true });

    /* Keyboard activation (Enter / Space) has no pointer position: centre it. */
    document.addEventListener('click', guard('ripple-kbd', function (e) {
      if (e.detail !== 0) return;
      var el = e.target.closest && e.target.closest(RIPPLE_SELECTOR);
      if (!el || el.disabled || el.matches(RIPPLE_SKIP)) return;
      var r = el.getBoundingClientRect();
      spawnRipple(el, r.left + r.width / 2, r.top + r.height / 2);
    }), { passive: true });
  }

  var MAGNETIC_SELECTOR = '.primary-btn, .btn-primary, .btn-apply, .apply-btn, .btn, a.ux-cta';

  function setupMagnetic() {
    if (!CONFIG.magnetic || reduceMotion.matches || !canHover.matches) return;

    var current = null, frame = 0, lastX = 0, lastY = 0, lastTarget = null;

    function release(el) {
      el.style.setProperty('--ux-mx', '0px');
      el.style.setProperty('--ux-my', '0px');
    }

    function update() {
      frame = 0;
      var el = lastTarget && lastTarget.closest ? lastTarget.closest(MAGNETIC_SELECTOR) : null;
      if (el && (el.disabled || el.closest('table'))) el = null;
      if (current && current !== el) release(current);
      current = el;
      if (!el) return;

      var rect = el.getBoundingClientRect();
      var curX = parseFloat(el.style.getPropertyValue('--ux-mx')) || 0;   /* subtract our own offset so   */
      var curY = parseFloat(el.style.getPropertyValue('--ux-my')) || 0;   /* the pull doesn't feed back   */
      var cx = rect.left + rect.width / 2 - curX;
      var cy = rect.top + rect.height / 2 - curY;
      var k = rect.width > 260 ? 0.06 : 0.18;                            /* full-width form buttons: subtle */
      el.style.setProperty('--ux-mx', clamp((lastX - cx) * k, -8, 8).toFixed(2) + 'px');
      el.style.setProperty('--ux-my', clamp((lastY - cy) * k * 1.4, -6, 6).toFixed(2) + 'px');
    }

    document.addEventListener('pointermove', guard('magnetic', function (e) {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      lastX = e.clientX; lastY = e.clientY; lastTarget = e.target;
      if (!frame) frame = requestAnimationFrame(update);
    }), { passive: true });

    document.documentElement.addEventListener('pointerleave', function () {
      lastTarget = null;
      if (current) { release(current); current = null; }
    });
  }

  /* ---------------------------------------- 5. NAVIGATION + LOGIN TABS */
  function normPath(p) { return (p.length > 1 ? p.replace(/\/+$/, '') : p) || '/'; }

  function markCurrentNav(root) {
    if (!CONFIG.navState) return;
    var here = normPath(location.pathname);
    collect(root, '.sidebar a[href], .navbar a[href], .nav-right a[href]').forEach(function (a) {
      var raw = a.getAttribute('href') || '';
      if (raw.charAt(0) === '#' || /^javascript:/i.test(raw)) return;
      var same = a.origin === location.origin && normPath(a.pathname) === here;
      a.classList.toggle('is-current', same);
      if (same) a.setAttribute('aria-current', 'page');
      else if (a.getAttribute('aria-current') === 'page') a.removeAttribute('aria-current');
    });
  }

  function initTabSlider(toggle) {
    if (!CONFIG.tabSlider || toggle.dataset.uxSlider) return;
    var tabs = Array.prototype.slice.call(toggle.querySelectorAll(':scope > .tab'));
    if (tabs.length < 2) return;
    toggle.dataset.uxSlider = '1';

    function sync() {
      var idx = tabs.findIndex(function (t) { return t.classList.contains('active'); });
      toggle.style.setProperty('--ux-tab-i', Math.max(0, idx));
      toggle.style.setProperty('--ux-tab-n', tabs.length);
    }
    sync();
    toggle.classList.add('ux-slider');
    /* enable the transition only after the first paint so the thumb doesn't "fly in" */
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { toggle.classList.add('ux-ready'); });
    });
    new MutationObserver(sync).observe(toggle, { subtree: true, attributes: true, attributeFilter: ['class'] });
  }

  /* ---------------------------------- 6. PAGE EXIT + TOP PROGRESS BAR */
  var progressEl = null;
  function startProgress() {
    if (!progressEl) {
      progressEl = document.createElement('div');
      progressEl.className = 'ux-progress';
      progressEl.setAttribute('aria-hidden', 'true');
      document.body.appendChild(progressEl);
    }
    progressEl.classList.remove('is-active');
    void progressEl.offsetWidth;
    progressEl.classList.add('is-active');
  }

  function setupPageTransitions() {
    if (!CONFIG.pageTransitions || reduceMotion.matches) return;

    document.addEventListener('click', guard('page-transition', function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      if ((a.target && a.target !== '_self') || a.hasAttribute('download') || a.hasAttribute('onclick')) return;
      if (a.id === 'logout') return;                                  /* leave the app's own logout alone */
      if (a.protocol !== 'http:' && a.protocol !== 'https:') return;   /* skips javascript:history.back() */
      if (a.origin !== location.origin) return;
      if (a.pathname === location.pathname && a.search === location.search) return;   /* hash / same page */

      e.preventDefault();
      var href = a.href;
      document.body.classList.add('ux-leaving');
      startProgress();
      setTimeout(function () { location.assign(href); }, 180);
    }));

    /* Coming back via the browser's back/forward cache */
    window.addEventListener('pageshow', function (e) {
      if (!e.persisted) return;
      document.body.classList.remove('ux-leaving');
      if (progressEl) progressEl.classList.remove('is-active');
    });
  }

  /* --------------------------------------- 7. RESUME / OCR LOADING OVERLAY */
  var OCR_STAGES = [
    [0,     'Uploading your resume…'],
    [2500,  'Reading the document…'],
    [6000,  'Extracting text and skills…'],
    [12000, 'Scanned PDFs need OCR and can take a little longer…'],
    [28000, 'Still working, thanks for your patience…']
  ];

  function hasResumePicked(form) {
    return Array.prototype.some.call(form.querySelectorAll('input[type="file"]'), function (input) {
      return /resume|cv/i.test((input.id || '') + ' ' + (input.name || '')) && input.files && input.files.length;
    });
  }

  function showBusyOverlay(form, title) {
    if (!form || form._uxOcr) return;
    var overlay = document.createElement('div');
    overlay.className = 'ux-ocr';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML =
      '<div class="ux-ocr__panel">' +
        '<div class="ux-ocr__ring" aria-hidden="true"></div>' +
        '<p class="ux-ocr__title"></p>' +
        '<p class="ux-ocr__step"></p>' +
        '<div class="ux-ocr__bar" aria-hidden="true"></div>' +
        '<div class="ux-ocr__skeleton" aria-hidden="true"><span></span><span></span><span></span></div>' +
      '</div>';
    overlay.querySelector('.ux-ocr__title').textContent = title || 'Analysing your resume';
    var step = overlay.querySelector('.ux-ocr__step');
    step.textContent = OCR_STAGES[0][1];

    form.classList.add('ux-busy-host');
    form.appendChild(overlay);

    var timers = OCR_STAGES.slice(1).map(function (stage) {
      return setTimeout(function () {
        step.classList.add('is-swapping');
        setTimeout(function () { step.textContent = stage[1]; step.classList.remove('is-swapping'); }, 200);
      }, stage[0]);
    });
    form._uxOcr = { overlay: overlay, timers: timers };
  }

  function hideBusyOverlay(form) {
    var state = form && form._uxOcr;
    if (!state) return;
    form._uxOcr = null;
    state.timers.forEach(clearTimeout);
    state.overlay.classList.add('is-leaving');
    setTimeout(function () {
      state.overlay.remove();
      form.classList.remove('ux-busy-host');
    }, 260);
  }

  /* app.js's setBusy() toggles aria-busy on the form's submit button around
     the profile-save request (which uploads the resume and runs
     utils/ai_matching.py OCR on the server). Observing that attribute means
     we never have to intercept the submit or the fetch. */
  function setupResumeOverlay() {
    if (!CONFIG.resumeOverlay) return;
    new MutationObserver(guard('ocr-overlay', function (mutations) {
      mutations.forEach(function (m) {
        var btn = m.target;
        var form = btn.closest && btn.closest('form');
        if (!form) return;
        if (btn.getAttribute('aria-busy') === 'true') {
          if (hasResumePicked(form)) showBusyOverlay(form);
        } else {
          hideBusyOverlay(form);
        }
      });
    })).observe(document.body, { attributes: true, attributeFilter: ['aria-busy'], subtree: true });
  }

  /* ------------------------------------------------------ SCAN + OBSERVE */
  function markCard(card) {
    /* .card is reused for both clickable tiles and form containers
       (login, register…). Only tiles get the hover treatment. */
    if (card.dataset.uxCard) return;
    card.dataset.uxCard = '1';
    if (!card.querySelector('form, input, select, textarea, label')) card.classList.add('ux-card');
  }

  function processTree(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.classList && (root.classList.contains('ux-ripple') || root.classList.contains('ux-ocr'))) return;

    guard('cards', function () { collect(root, '.card').forEach(markCard); })();
    guard('reveal', function () { collect(root, REVEAL_SELECTOR).forEach(queueReveal); })();
    guard('metrics', function () { collect(root, METRIC_SELECTOR).forEach(prepMetric); })();
    guard('gauges', function () { collect(root, GAUGE_SELECTOR).forEach(prepGauge); })();
    guard('nav', function () { markCurrentNav(root); })();
    guard('tabs', function () { collect(root, '.toggle').forEach(initTabSlider); })();
  }

  function observeDom() {
    var pending = new Set();
    var scheduled = false;

    function flush() {
      scheduled = false;
      pending.forEach(function (node) { if (node.isConnected) processTree(node); });
      pending.clear();
    }

    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (n) { if (n.nodeType === 1) pending.add(n); });
      });
      if (pending.size && !scheduled) { scheduled = true; requestAnimationFrame(flush); }
    }).observe(document.body, { childList: true, subtree: true });
  }

  function markLandingCtas() {
    if (location.pathname !== '/') return;
    document.querySelectorAll('header a[class*="bg-indigo"], main a[class*="bg-indigo"]').forEach(function (a) {
      a.classList.add('ux-cta');
    });
  }

  /* ----------------------------------------------------------------- INIT */
  function init() {
    guard('setup-reveal', setupReveal)();
    guard('setup-metrics', setupMetrics)();
    guard('landing-ctas', markLandingCtas)();
    guard('landing-ring', setupLandingRing)();

    processTree(document.body);
    guard('observe', observeDom)();

    guard('ripple', setupRipple)();
    guard('magnetic', setupMagnetic)();
    guard('page-transitions', setupPageTransitions)();
    guard('ocr-overlay', setupResumeOverlay)();

    /* Small public surface for pages that need manual control, e.g.:
         UX.showBusy(document.getElementById('profileForm'));
         UX.hideBusy(document.getElementById('profileForm'));  */
    window.UX = {
      scan: processTree,
      showBusy: showBusyOverlay,
      hideBusy: hideBusyOverlay,
      config: CONFIG
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
