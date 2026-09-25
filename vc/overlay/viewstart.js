/* vc view start: set a view's initial position in the same frame the view first paints (D-33 item 3).
 *
 * Installed by vc/overlay/viewstart.py as a document-start script (Page.addScriptToEvaluateOnNewDocument) and once in
 * the current document. Called with the profile's rules (`view_start` in profiles/<app>/profile.json):
 *
 *   {id, url_regex, selector, when?, by: "wheel"|"scroll", x?, y?, wheel_px_per_unit?, settle_ms?}
 *
 * A MutationObserver sees every element that matches `selector` on a page whose URL matches `url_regex` (and, with
 * `when`, only while that selector also exists). It moves the element to (x, y) at once: its MutationObserver callback
 * runs as a microtask right after the app's DOM commit, before the browser's next rendering step, so the first painted
 * frame of the view already shows the start position. `by`:
 *   - "scroll": a scroll container; scrollLeft/scrollTop are set directly;
 *   - "wheel":  a canvas panned by the page on wheel events (a transform, no scroll container, e.g. a flow chart).
 *               The position is the element's computed transform translation; the script dispatches one synthetic
 *               wheel event per correction (deltaX/deltaY = offset / px-per-unit), reads the result after the app's
 *               microtask render and refines px-per-unit (at most 4 rounds, all before the paint).
 * For `settle_ms` (default 1500) after the element first qualifies, a move by the app itself is undone the same way
 * (re-applied during mount). A wheel or pointer-down that is not ours inside the element (the camera's pan, a user)
 * ends the rule for that element; a new element (a remount, another view) starts again.
 *
 * View-only: nothing is clicked, typed or navigated. Window state: window.__vcViewStart.log, one entry per correction
 * {id, t, frame, seen_frame, from, to, got, rounds}; `frame` counts animation frames, so frame === seen_frame means the
 * position was set before the element's first paint.
 */
(function (rules) {
  'use strict';
  var W = window;
  if (W.__vcViewStart) { W.__vcViewStart.setRules(rules); return; }
  var S = W.__vcViewStart = {rules: [], log: [], frame: 0};
  var armed = typeof WeakMap === 'function' ? new WeakMap() : null;
  var live = [];                 // [el, state] pairs still armed (WeakMap is not iterable)
  var ours = 0;
  var now = function () { return (W.performance && performance.now) ? performance.now() : Date.now(); };
  var microtask = function () { return new Promise(function (r) { Promise.resolve().then(r); }); };

  S.setRules = function (rs) {
    S.rules = (rs || []).map(function (r) {
      var o = {}; for (var k in r) o[k] = r[k];
      try { o.re = new RegExp(r.url_regex || '.*'); } catch (e) { o.re = null; }
      o.settle = r.settle_ms == null ? 1500 : +r.settle_ms;
      o.k = +r.wheel_px_per_unit || 1;
      return o;
    });
    scan();
  };

  // "matrix(a, b, c, d, e, f)" / "matrix3d(..16..)" / "none" -> {x, y}
  S.translation = function (tf) {
    var m = /^matrix(3d)?\(([^)]*)\)/.exec(tf || '');
    if (!m) return {x: 0, y: 0};
    var v = m[2].split(',').map(parseFloat);
    return m[1] ? {x: v[12], y: v[13]} : {x: v[4], y: v[5]};
  };
  var pos = function (el, r) {
    if (r.by === 'scroll') return {x: el.scrollLeft, y: el.scrollTop};
    return S.translation(getComputedStyle(el).transform);
  };
  // how far the element is from the rule's start position, per axis (only the axes the rule names)
  S.offset = function (p, r) {
    return {x: r.x == null ? 0 : p.x - r.x, y: r.y == null ? 0 : p.y - r.y};
  };
  var off = function (d) { return Math.abs(d.x) >= 0.5 || Math.abs(d.y) >= 0.5; };

  var wheel = function (el, dx, dy) {
    var b = el.getBoundingClientRect ? el.getBoundingClientRect() : {left: 0, top: 0, width: 0, height: 0};
    var box = el.parentElement && el.parentElement.getBoundingClientRect ? el.parentElement.getBoundingClientRect() : b;
    ours++;
    try {
      el.dispatchEvent(new WheelEvent('wheel', {deltaX: dx, deltaY: dy, deltaMode: 0, bubbles: true, cancelable: true,
        composed: true, clientX: box.left + box.width / 2, clientY: box.top + box.height / 2}));
    } finally { ours--; }
  };

  var fix = function (el, st) {
    var r = st.rule;
    if (st.busy || st.done || S.frame < (st.hold || 0)) return;
    var p = pos(el, r), d = S.offset(p, r);
    if (!off(d)) return;
    st.busy = true;
    var entry = {id: r.id, t: Math.round(now()), frame: S.frame, seen_frame: st.seen, from: p, to: {x: r.x, y: r.y},
                 got: p, rounds: 0};
    var finish = function () { entry.got = pos(el, r); entry.end_frame = S.frame; S.log.push(entry); st.busy = false; };
    if (r.by === 'scroll') {
      if (r.x != null) el.scrollLeft = r.x;
      if (r.y != null) el.scrollTop = r.y;
      entry.rounds = 1; finish(); return;
    }
    var round = function () {
      if (entry.rounds >= 4) return finish();
      var before = pos(el, r), dd = S.offset(before, r);
      if (!off(dd)) return finish();
      entry.rounds++;
      // the page moves the canvas against the wheel: +delta moves the translation by -k*delta
      wheel(el, dd.x / r.k, dd.y / r.k);
      microtask().then(microtask).then(function () {
        var after = pos(el, r), mx = before.x - after.x, my = before.y - after.y;
        var ax = Math.abs(dd.x) >= 0.5 ? mx / (dd.x / r.k) : null, ay = Math.abs(dd.y) >= 0.5 ? my / (dd.y / r.k) : null;
        var k = ax && ax > 0 ? ax : (ay && ay > 0 ? ay : null);
        if (!k) { entry.stuck = true; st.hold = S.frame + 2; return finish(); }   // not moved (yet): retry 2 frames on
        r.k = k;
        round();
      });
    };
    round();
  };

  var scan = function () {
    if (!S.rules.length || !W.document || !document.documentElement) return;
    var href = String(location.href), t = now();
    for (var i = 0; i < S.rules.length; i++) {
      var r = S.rules[i];
      if (!r.re || !r.re.test(href) || !r.selector) continue;
      if (r.when && !document.querySelector(r.when)) continue;
      var els = document.querySelectorAll(r.selector);
      for (var j = 0; j < els.length; j++) {
        var el = els[j], st = armed ? armed.get(el) : el.__vcViewStartState;
        if (!st) {
          st = {rule: r, t0: t, seen: S.frame, done: false};
          if (armed) armed.set(el, st); else el.__vcViewStartState = st;
          live.push([el, st]);
        }
        if (st.done) continue;
        if (t - st.t0 > r.settle) { st.done = true; continue; }
        fix(el, st);
      }
    }
    live = live.filter(function (p) { return !p[1].done && p[0].isConnected !== false; });
  };
  S.scan = scan;

  // input that is not ours inside an armed element (the camera's pan, a person) ends that element's rule
  var foreign = function (e) {
    if (ours) return;
    for (var i = 0; i < live.length; i++) {
      var el = live[i][0];
      if (e.target && (e.target === el || (el.contains && el.contains(e.target)))) live[i][1].done = true;
    }
  };
  W.addEventListener('wheel', foreign, {capture: true, passive: true});
  W.addEventListener('pointerdown', foreign, {capture: true, passive: true});

  var tick = function () {
    S.frame++;
    if (live.length) scan();           // a scroll container can move without a DOM mutation
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);

  var start = function () {
    new MutationObserver(scan).observe(document.documentElement, {subtree: true, childList: true, attributes: true,
                                                                   attributeFilter: ['style', 'class']});
    scan();
  };
  if (document.documentElement) start();
  else document.addEventListener('readystatechange', start, {once: true});
  S.setRules(rules);
})
