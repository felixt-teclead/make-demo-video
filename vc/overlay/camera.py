"""The camera: pans and reveals (spec actions `op = "pan" | "reveal"`), Q-31 and Q-37.

A pan moves one scroll container (the nearest scrollable ancestor of the target on the pan's axis, else the page) as
one rigid surface along one axis, in one direction, with a sine ease-in-out. It is driven INSIDE the page once per
display frame (requestAnimationFrame), so every painted frame shows exactly one new, evenly spaced scroll offset; no
per-frame round trip over the DevTools connection. Offsets are whole pixels (a sub-pixel offset re-rasterises text and
breaks the "one single shift" proof of Q-31).

Speed (Q-31, was Q-35): the mean speed is 400 px/s by default (baseline 300-495 px/s); a spec's `seconds` is honoured
unless it would exceed the hard limit of 40 px per frame at the sine peak (then the pan is lengthened). A pan is at
least 0.5 s long, so it has more than 3 shifted steps. After the pan the camera keeps the view still for 1.0 s
(Q-37: at least 0.9 s) unless the action carries its own hold.

Scrolling a view is a view-only change (C-14 allows direct access for view-only mitigations; jev never offers scroll).
Clicks and typing still go through jev only.
"""

import json
import math
import time

FPS = 30
MAX_STEP_PX = 40.0          # Q-31: at most 40 px per displayed frame
MEAN_SPEED_PX_S = 400.0     # Q-31 baseline 300-495 px/s mean along a sine curve
MIN_PAN_S = 0.5             # >= 15 frames: at least 3 shifted steps, eased
STILL_AFTER_S = 1.0         # Q-37: >= 0.9 s of stillness after a pan or reveal
PAUSE_MARGIN_FRAMES = 10   # animations stay paused this many frames (0.33 s) before the first and after the last step,
                           # longer than the gate's 0.25 s episode pad, so no animation step lands inside a pan episode (Q-31)
EDGE_MARGIN_PX = 48         # a revealed target sits at least this far inside the scroller's edge


def pan_seconds(distance, seconds=None):
    """Duration of a pan of `distance` px: the spec's value, or distance / 400 px/s, never shorter than the 40 px/frame
    limit at the sine peak (peak speed = pi/2 x mean) allows, and never below MIN_PAN_S."""
    d = abs(float(distance))
    floor = math.pi * d / (2.0 * MAX_STEP_PX * FPS)
    want = float(seconds) if seconds else d / MEAN_SPEED_PX_S
    return max(MIN_PAN_S, floor, want)


def sine_offsets(distance, seconds, fps=FPS):
    """Whole-pixel offsets per displayed frame (frame 0 = start), sine ease-in-out; the page code uses the same curve."""
    n = max(1, int(round(seconds * fps)))
    return [round(distance * (1 - math.cos(math.pi * k / n)) / 2) for k in range(n + 1)]


_JS = r"""
(async (o) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const inOverlay = e => { for (let n = e; n; n = n.parentNode || n.host) if (n.tagName === 'VC-OVERLAY') return true;
                           return false; };
  // 1. the target
  let el = null;
  if (o.selector) el = document.querySelector(o.selector);
  if (!el && o.text) {
    const want = clean(o.text), w = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let best = null, n;
    while ((n = w.nextNode())) {
      if (n.closest('script,style,noscript,template') || inOverlay(n)) continue;
      if (clean(n.innerText || n.textContent).includes(want) || clean(n.getAttribute('aria-label')) === want) best = n;
    }
    el = best;     // tree order: the last match is the deepest one
  }
  if (!el) return {ok: false, reason: 'target not found in the page: ' + JSON.stringify(o.text || o.selector)};
  // 2. the axis and the scroller
  const r0 = el.getBoundingClientRect();
  // 2b. no scroll container: a clipped canvas that pans on the wheel (a diagram viewport moved by a transform, e.g.
  // a flow chart). The camera sends the page a wheel event once per displayed frame (view-only, like scrolling) and
  // measures the target's own movement: the first frames calibrate how many px one wheel unit moves, every later
  // frame asks exactly for the whole-pixel offset the sine curve wants, so the surface moves rigidly and evenly.
  const wheelPan = async () => {
    let surf = null;
    for (let e = el.parentElement; e && !surf; e = e.parentElement) {
      const st = getComputedStyle(e), ov = axis === 'x' ? st.overflowX : st.overflowY;
      const room = axis === 'x' ? e.scrollWidth - e.clientWidth : e.scrollHeight - e.clientHeight;
      if (room > 1 && /(hidden|clip)/.test(ov)) surf = e;
    }
    if (!surf) return {ok: false, reason: 'no scroll container or wheel-panned canvas on the ' + axis + ' axis'};
    const bx = surf.getBoundingClientRect();
    const lo = axis === 'x' ? bx.left : bx.top, hi = axis === 'x' ? bx.right : bx.bottom;
    const a = axis === 'x' ? r0.left : r0.top, b = axis === 'x' ? r0.right : r0.bottom;
    const m = o.margin;
    const delta = o.kind === 'reveal' ? (b > hi - m ? Math.min(b - (hi - m), a - (lo + m)) : (a < lo + m ? a - (lo + m) : 0))
                                      : (a + b) / 2 - (lo + hi) / 2;
    const dist = Math.round(delta);
    if (o.direction && dist !== 0 && Math.sign(dist) !== ({left: -1, up: -1, right: 1, down: 1})[o.direction])
      return {ok: false, reason: 'the target lies ' + (dist < 0 ? 'before' : 'after') + ' the view, not ' + o.direction};
    const now = () => performance.timeOrigin + performance.now();
    if (Math.abs(dist) < 1) return {ok: true, distance: 0, axis, t0: now(), t1: now(), frames: 0, still: true};
    const T = o.seconds_for(Math.abs(dist)) * 1000;
    const paused = o.pause_animations ? document.getAnimations().filter(x => x.playState === 'running' &&
        !(x.effect && x.effect.target && inOverlay(x.effect.target))) : [];
    paused.forEach(x => x.pause());
    const frame0 = () => new Promise(r => requestAnimationFrame(r));
    if (paused.length) for (let q = 0; q < o.pause_margin; q++) await frame0();
    const pos = () => { const r = el.getBoundingClientRect(); return axis === 'x' ? r.left : r.top; };
    const p0 = pos(), cx = (bx.left + bx.right) / 2, cy = (bx.top + bx.bottom) / 2;
    const wheel = d => el.dispatchEvent(new WheelEvent('wheel', {deltaX: axis === 'x' ? d : 0,
        deltaY: axis === 'y' ? d : 0, deltaMode: 0, bubbles: true, cancelable: true, composed: true, clientX: cx, clientY: cy}));
    const frame = () => new Promise(r => requestAnimationFrame(r));
    // Steps follow the page's own painted frames (at most one per capture frame), not the clock alone: a page that paints slower than the capture then
    // repeats frames evenly (Q-34 WARN) instead of catching up with a double-size step (Q-34 FAIL). The curve has
    // one step per capture frame of T; each frame asks for the curve's increment plus at most 1 px of correction.
    const N = Math.max(15, Math.round(T / 1000 * 30));
    const curve = i => Math.round(dist * (1 - Math.cos(Math.PI * Math.min(1, i / N))) / 2);
    let k = 1, calibrated = false, lastReq = 0, lastMoved = 0, frames = 0, stuck = 0, i = 0, t0 = null, ts = 0;
    while (true) {
      ts = await frame();
      if (t0 === null) t0 = ts;
      const moved = p0 - pos();
      if (lastReq) {
        const got = moved - lastMoved;
        if (Math.abs(got) >= 0.5 && got / lastReq > 0) {
          if (!calibrated) { k = got / lastReq; calibrated = true; }
          stuck = 0;
        } else stuck++;
      }
      frames++;
      lastMoved = moved; lastReq = 0;
      if (stuck >= 4) break;                      // the canvas stops at its edge: the check below decides
      if (i >= N && Math.abs(curve(N) - moved) < 0.5) break;
      if (frames > N * 3 + 30) break;
      // advance one curve step per painted frame, but never ahead of the capture clock (30 steps per second)
      const next = Math.min(N, i + 1, Math.max(i, Math.floor((ts - t0) / 1000 * 30) + 1));
      const want = curve(next), inc = want - curve(i);
      const step = !calibrated ? want - moved : inc + Math.max(-1, Math.min(1, curve(i) - moved));
      i = next;
      if (Math.abs(step) >= 0.5) { lastReq = step / k; wheel(lastReq); }
    }
    if (paused.length) for (let q = 0; q < o.pause_margin; q++) await frame();
    paused.forEach(x => x.play());
    return {ok: true, distance: Math.round(p0 - pos()), axis, ms: T, scroller: 'wheel', k, paused: paused.length,
            t0: performance.timeOrigin + t0, t1: performance.timeOrigin + ts, frames};
  };
  let axis = {left: 'x', right: 'x', up: 'y', down: 'y'}[o.direction] || null;
  const canScroll = (e, ax) => {
    const st = getComputedStyle(e), ov = ax === 'x' ? st.overflowX : st.overflowY;
    const room = ax === 'x' ? e.scrollWidth - e.clientWidth : e.scrollHeight - e.clientHeight;
    return room > 1 && (/(auto|scroll|overlay)/.test(ov) || e === document.scrollingElement);
  };
  const scrollerFor = ax => {
    for (let e = el.parentElement; e; e = e.parentElement) if (canScroll(e, ax)) return e;
    const d = document.scrollingElement; return canScroll(d, ax) ? d : null;
  };
  if (!axis) {
    const dx = Math.max(0, r0.right - innerWidth, -r0.left), dy = Math.max(0, r0.bottom - innerHeight, -r0.top);
    axis = dx > dy ? 'x' : 'y';
  }
  const sc = scrollerFor(axis);
  if (!sc) return await wheelPan();
  const isDoc = sc === document.scrollingElement;
  const box = isDoc ? {left: 0, top: 0, right: innerWidth, bottom: innerHeight} : sc.getBoundingClientRect();
  const lo = axis === 'x' ? box.left : box.top, hi = axis === 'x' ? box.right : box.bottom;
  const a = axis === 'x' ? r0.left : r0.top, b = axis === 'x' ? r0.right : r0.bottom;
  // 3. how far: a pan centres the target, a reveal brings it just inside the edge
  let delta;
  if (o.kind === 'reveal') {
    const m = o.margin;
    delta = b > hi - m ? Math.min(b - (hi - m), a - (lo + m)) : (a < lo + m ? a - (lo + m) : 0);
  } else {
    delta = (a + b) / 2 - (lo + hi) / 2;
  }
  const pos0 = axis === 'x' ? sc.scrollLeft : sc.scrollTop;
  const max = axis === 'x' ? sc.scrollWidth - sc.clientWidth : sc.scrollHeight - sc.clientHeight;
  const target = Math.max(0, Math.min(max, Math.round(pos0 + delta)));
  const dist = target - pos0;
  if (o.direction && dist !== 0 && Math.sign(dist) !== ({left: -1, up: -1, right: 1, down: 1})[o.direction])
    return {ok: false, reason: 'the target lies ' + (dist < 0 ? 'before' : 'after') + ' the view, not ' + o.direction};
  if (Math.abs(dist) < 1) return {ok: true, distance: 0, axis, t0: performance.timeOrigin + performance.now(),
                                   t1: performance.timeOrigin + performance.now(), frames: 0, still: true};
  // 4. the pan: one whole-pixel offset per displayed frame on a sine curve; decorative animations optionally paused
  const T = o.seconds_for(Math.abs(dist)) * 1000;
  const paused = o.pause_animations ? document.getAnimations().filter(x => x.playState === 'running' &&
      !(x.effect && x.effect.target && inOverlay(x.effect.target))) : [];
  paused.forEach(x => x.pause());
  const idle = async () => { for (let q = 0; q < o.pause_margin; q++) await new Promise(r => requestAnimationFrame(r)); };
  if (paused.length) await idle();
  const prevBehaviour = sc.style.scrollBehavior; sc.style.scrollBehavior = 'auto';
  const set = v => { if (axis === 'x') sc.scrollLeft = v; else sc.scrollTop = v; };
  const res = await new Promise(done => {
    let t0 = null, frames = 0;
    const tick = ts => {
      if (t0 === null) t0 = ts;
      const p = Math.min(1, (ts - t0) / T);
      set(Math.round(pos0 + dist * (1 - Math.cos(Math.PI * p)) / 2));
      frames++;
      if (p < 1) requestAnimationFrame(tick);
      else done({t0: performance.timeOrigin + t0, t1: performance.timeOrigin + ts, frames});
    };
    requestAnimationFrame(tick);
  });
  sc.style.scrollBehavior = prevBehaviour;
  if (paused.length) await idle();
  paused.forEach(x => x.play());
  return {ok: true, distance: dist, axis, ms: T, scroller: isDoc ? 'page' : (sc.id ? '#' + sc.id : sc.tagName.toLowerCase()),
          paused: paused.length, ...res};
})
"""


def _page_source(opts):
    """The page function with the duration rule inlined (the page decides the distance)."""
    js_seconds = (f"(d) => Math.max({MIN_PAN_S}, Math.PI * d / (2 * {MAX_STEP_PX} * {FPS}), "
                  f"{float(opts['seconds']) if opts.get('seconds') else 0} || d / {MEAN_SPEED_PX_S})")
    arg = json.dumps({k: v for k, v in opts.items() if k != "seconds"}, ensure_ascii=False)
    return f"({_JS.strip()})(Object.assign({arg}, {{seconds_for: {js_seconds}}}))"


def target_of(to):
    """The page target of a camera action's `to` check (text_visible value or element_visible selector)."""
    checks = to if isinstance(to, list) else [to]
    for c in checks:
        if c.get("type") == "text_visible" and c.get("value"):
            return {"text": c["value"]}
        if c.get("type") == "element_visible" and (c.get("selector") or c.get("label") or c.get("label_contains")):
            return {"selector": c.get("selector"), "text": c.get("label") or c.get("label_contains")}
    return None


def move(tab, action, *, emit=None, check=None, sleep=time.sleep):
    """Run one pan or reveal on the filmed tab. Returns {"ok": bool, "reason"?, "event"?}.

    tab:   has .js(expression, await_promise=True) (M2's FilmedTab)
    emit:  receives the camera event {"type": "pan"|"reveal", "t", "end", "distance", "axis", "frames", "ms"}
    check: optional check(to) -> bool, the independent check that the target is now in view
    """
    kind = action.get("op", "pan")
    tgt = target_of(action.get("to") or [])
    if tgt is None:
        return {"ok": False, "reason": f"{kind}: `to` must be a text_visible or element_visible check"}
    opts = dict(tgt, kind=kind, direction=action.get("direction"), seconds=action.get("seconds"),
                margin=EDGE_MARGIN_PX, pause_margin=PAUSE_MARGIN_FRAMES, pause_animations=bool(action.get("pause_animations", False)))
    try:
        r = tab.js(_page_source(opts), await_promise=True)
    except Exception as e:  # noqa: BLE001 - a navigating page; the step fails, the fixer decides
        return {"ok": False, "reason": f"{kind}: page script failed: {e}"}
    if not r or not r.get("ok"):
        return {"ok": False, "reason": f"{kind}: {(r or {}).get('reason', 'no result')}"}
    ev = {"type": kind, "t": round(r["t0"] / 1000.0, 4), "end": round(r["t1"] / 1000.0, 4),
          "distance": r.get("distance"), "axis": r.get("axis"), "frames": r.get("frames"),
          "ms": round(r.get("ms") or 0, 1), "scroller": r.get("scroller")}
    if emit is not None and r.get("frames"):
        emit(ev)
    if action.get("hold") is None:
        sleep(STILL_AFTER_S)
    if check is not None and not check(action["to"]):
        return {"ok": False, "reason": f"{kind}: the target is still not in view after the move", "event": ev}
    return {"ok": True, "event": ev}
