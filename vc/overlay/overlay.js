/* vc demo overlay: the one on-camera cursor, click ripple and pressed state (spec Q-40, Q-43, Q-45, Q-48, Q-73..Q-76).
 *
 * This file is a single function expression. The controller (controller.py) evaluates `(<this file>)(STATE)`:
 *   - on every new document, through Page.addScriptToEvaluateOnNewDocument (runs before any page script, so the
 *     cursor is in the DOM before the first paint of the new document: Q-22, Q-43), and
 *   - in the current document, through Runtime.evaluate, followed by a method call on the returned API.
 * STATE = {x, y, ripple: {x, y, t0} | null, press: t0 | null}; t0 values are wall-clock epoch ms (Date.now()).
 *
 * Guarantees:
 *   - no layout change: one fixed, zero-size, pointer-events:none custom element with a closed shadow root; the page's
 *     elementFromPoint, hit testing and MutationObservers never see our internals;
 *   - drawn in the top layer (manual popover) and re-raised when the page opens a modal dialog or popover;
 *   - all motion runs as compositor animations (transform/opacity), so a busy main thread cannot freeze or stick them;
 *   - ripples end at opacity 0 by animation fill AND are removed by a wall-clock sweep (Q-45).
 */
(function (STATE) {
  'use strict';
  if (window.top !== window) return null;           // top document only; iframes never draw a cursor
  const KEY = Symbol.for('vc.overlay');
  if (window[KEY]) return window[KEY];

  const TIP_X = 5, TIP_Y = 4;                          // arrow tip inside the 28x28 drawing box (Q-73)
  // RIPPLE_D is the ring's own outer diameter at 100 %; with its 2 px halo and 18 px glow the ripple spans about
  // 110 px (max radius 55), and the ring reads about 92 px across at 68 % of the animation (Q-76).
  const RIPPLE_MS = 520, RIPPLE_D = 96, PRESS_MS = 250, SWEEP_MS = 600;
  // measured from cursor.png (white fill ~12x18 px) and scaled so the outline box is ~16x22 px (Q-73: ~16x22 +-2)
  const ARROW = 'M0 0 L0 17.6 L4.1 13.6 L8 21.6 L11.1 20.2 L7.5 12.7 L14.85 12.5 Z';
  const CSS = `
    :host{all:initial}
    .c{position:absolute;left:0;top:0;width:28px;height:28px;will-change:transform}
    .c svg{position:absolute;left:0;top:0;width:28px;height:28px;overflow:visible;
           transform-origin:${TIP_X}px ${TIP_Y}px;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.6))}
    .r{position:absolute;left:${-RIPPLE_D / 2}px;top:${-RIPPLE_D / 2}px;width:${RIPPLE_D}px;height:${RIPPLE_D}px;
       box-sizing:border-box;border-radius:50%;border:5px solid #4da3ff;background:rgba(77,163,255,.38);
       box-shadow:0 0 0 2px rgba(255,255,255,.55),0 0 18px rgba(77,163,255,.5);opacity:0;will-change:transform,opacity}
    .p{position:absolute;display:none;background:rgba(0,0,0,.16);box-shadow:inset 0 1px 3px rgba(0,0,0,.25)}
    .b{position:absolute;left:${TIP_X + 13}px;top:${TIP_Y + 17}px;width:12px;height:12px;box-sizing:border-box;
       border-radius:50%;border:2.5px solid rgba(255,255,255,.95);border-top-color:#333;display:none;
       box-shadow:0 0 0 1px rgba(17,17,17,.7)}
    .b.on{display:block;animation:vcspin .8s linear infinite}
    @keyframes vcspin{to{transform:rotate(360deg)}}`;

  const S = {
    x: +STATE.x || 0, y: +STATE.y || 0,
    ripple: STATE.ripple || null, press: STATE.press || null,
  };
  let host = null, root = null, cur = null, svg = null, busy = null, pressedBox = null;
  let glideAnim = null, glideFrom = null, glideTo = null;
  const ripples = new Set();
  let sweeper = 0;

  function tr(x, y) { return `translate(${x - TIP_X}px,${y - TIP_Y}px)`; }

  function build() {
    host = document.createElement('vc-overlay');
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText =
      'all:initial!important;display:block!important;position:fixed!important;left:0!important;top:0!important;' +
      'right:auto!important;bottom:auto!important;width:0!important;height:0!important;margin:0!important;' +
      'padding:0!important;border:0!important;background:transparent!important;overflow:visible!important;' +
      'pointer-events:none!important;z-index:2147483647!important;contain:layout style!important;';
    try { host.popover = 'manual'; } catch (e) { /* no popover API: z-index only */ }
    root = host.attachShadow({ mode: 'closed' });
    root.innerHTML =
      `<style>${CSS}</style><div class="p"></div><div class="rl"></div>` +
      `<div class="c"><svg viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">` +
      `<path transform="translate(${TIP_X} ${TIP_Y})" d="${ARROW}" fill="#fff" stroke="#111" stroke-width="1.5" ` +
      `stroke-linejoin="round" stroke-linecap="round"/></svg><div class="b"></div></div>`;
    cur = root.querySelector('.c');
    svg = root.querySelector('svg');
    busy = root.querySelector('.b');
    pressedBox = root.querySelector('.p');
    cur.style.transform = tr(S.x, S.y);
  }

  function raise() {
    if (!host || !host.isConnected || !host.popover) return;
    try {
      if (host.matches(':popover-open')) host.hidePopover();
      host.showPopover();
    } catch (e) { /* keep the z-index fallback */ }
  }

  function attach() {
    const de = document.documentElement;
    if (!de) return false;
    if (!host) build();
    if (host.parentNode !== de) { de.appendChild(host); raise(); }
    return true;
  }

  // --- keep attached and on top -------------------------------------------------------------------------------
  function watch() {
    new MutationObserver(() => { if (!host || !host.isConnected) attach(); })
      .observe(document, { childList: true, subtree: false });
    const deObs = new MutationObserver(() => { if (!host.isConnected) { attach(); } });
    const hook = () => { if (document.documentElement) deObs.observe(document.documentElement, { childList: true }); };
    hook();
    new MutationObserver((muts) => {
      for (const m of muts) if (m.target !== host && m.target.hasAttribute && m.target.hasAttribute('open')) { raise(); break; }
    }).observe(document, { subtree: true, attributes: true, attributeFilter: ['open'] });
    document.addEventListener('toggle', (e) => { if (e.target !== host && e.newState === 'open') raise(); }, true);
    document.addEventListener('fullscreenchange', raise, true);
    document.addEventListener('DOMContentLoaded', () => { attach(); hook(); }, { once: true });
  }

  // --- ripple (Q-76), never sticks (Q-45) -------------------------------------------------------------------
  function sweep() {
    const now = Date.now();
    for (const r of ripples) if (now - r.t0 > SWEEP_MS) { r.el.remove(); ripples.delete(r); }
    if (!ripples.size && sweeper) { clearInterval(sweeper); sweeper = 0; }
  }

  function startRipple(rp) {
    const elapsed = Date.now() - rp.t0;
    if (elapsed >= RIPPLE_MS || elapsed < -1000) return;
    const layer = root.querySelector('.rl');
    const el = document.createElement('div');
    el.className = 'r';
    layer.appendChild(el);
    const pos = `translate(${rp.x}px,${rp.y}px)`;
    const a1 = el.animate([{ transform: pos + ' scale(0.14)' }, { transform: pos + ' scale(1)' }],
      { duration: RIPPLE_MS, easing: 'cubic-bezier(0.2,0.7,0.3,1)', fill: 'forwards' });
    const a2 = el.animate([{ opacity: 0, offset: 0 }, { opacity: 1, offset: 0.16 }, { opacity: 1, offset: 0.42 },
      { opacity: 0.9, offset: 0.68 }, { opacity: 0, offset: 1 }], { duration: RIPPLE_MS, easing: 'linear', fill: 'forwards' });
    if (elapsed > 0) { a1.currentTime = elapsed; a2.currentTime = elapsed; }
    const rec = { el, t0: rp.t0 };
    ripples.add(rec);
    // wall-clock removal races the animation end; the interval sweep is the backstop (Q-45)
    setTimeout(() => { el.remove(); ripples.delete(rec); }, RIPPLE_MS - Math.max(0, elapsed) + 40);
    if (!sweeper) sweeper = setInterval(sweep, 150);
  }

  function startPress(t0) {
    const elapsed = Date.now() - t0;
    if (elapsed >= PRESS_MS || elapsed < -1000) return;
    const a = svg.animate([
      { transform: 'scale(1)', offset: 0, easing: 'cubic-bezier(0.3,0,0.4,1)' },
      { transform: 'scale(0.85)', offset: 110 / PRESS_MS, easing: 'cubic-bezier(0.3,0,0.4,1)' },
      { transform: 'scale(1.03)', offset: 180 / PRESS_MS, easing: 'ease-in-out' },
      { transform: 'scale(1)', offset: 1 }], { duration: PRESS_MS });
    if (elapsed > 0) a.currentTime = elapsed;
  }

  // --- cursor position and glide (Q-74, Q-75) ---------------------------------------------------------------
  function ease(p) { return (1 - Math.cos(Math.PI * p)) / 2; }          // sine ease-in-out

  function currentPos() {
    if (glideAnim && glideAnim.playState === 'running') {
      const t = glideAnim.currentTime || 0, d = glideAnim.effect.getTiming().duration;
      const e = ease(Math.min(1, Math.max(0, t / d)));
      return [glideFrom[0] + (glideTo[0] - glideFrom[0]) * e, glideFrom[1] + (glideTo[1] - glideFrom[1]) * e];
    }
    return [S.x, S.y];
  }

  function stopGlide() {
    if (glideAnim) { glideAnim.cancel(); glideAnim = null; }
  }

  const api = {
    version: 1,
    state() { return { x: S.x, y: S.y, attached: !!(host && host.isConnected), ripples: ripples.size }; },
    place(x, y) {                                         // jump without motion (off camera only)
      stopGlide(); S.x = x; S.y = y; if (cur) cur.style.transform = tr(x, y); return api.state();
    },
    glide(x, y, ms) {
      sweep();
      const from = currentPos();
      stopGlide();
      S.x = x; S.y = y;
      cur.style.transform = tr(x, y);                   // final resting place; the animation overrides it meanwhile
      if (!(ms > 0) || (Math.abs(from[0] - x) < 0.5 && Math.abs(from[1] - y) < 0.5)) return Promise.resolve(0);
      const N = 48, kf = [];
      for (let i = 0; i <= N; i++) {
        const e = ease(i / N);
        kf.push({ offset: i / N, transform: tr(from[0] + (x - from[0]) * e, from[1] + (y - from[1]) * e) });
      }
      glideFrom = from; glideTo = [x, y];
      const a = glideAnim = cur.animate(kf, { duration: ms, easing: 'linear' });
      // resolves when the glide has really ended on screen (its start waits for the next frame), with a wall-clock cap
      return new Promise((res) => {
        const done = () => { if (glideAnim === a) glideAnim = null; res(ms); };
        a.finished.then(done, done);
        setTimeout(done, ms + 300);
      });
    },
    press(t0) {                                           // ripple + press spring start together (Q-76)
      sweep();
      t0 = t0 || Date.now();
      S.ripple = { x: S.x, y: S.y, t0 }; S.press = t0;
      startRipple(S.ripple); startPress(t0);
      api._armPressed(S.x, S.y);
      // resolves in the frame in which ring and press start; the caller times the real click from here
      return new Promise((res) => { requestAnimationFrame(() => res(Date.now())); setTimeout(() => res(Date.now()), 50); });
    },
    // Q-48: when the page has not reacted ~0.5 s after the real click, show the control pressed and a busy
    // pointer until the page reacts (DOM children change, URL change, page hide) or `release()` is called.
    _pressed: null,
    _armPressed(x, y) {
      api.release();
      const el = document.elementFromPoint(x, y);         // our host is pointer-events:none, so this is the control
      // only controls that trigger something can "hang"; a click into a field reacts by focusing it
      const field = el && el.closest && el.closest(
        'textarea,select,[contenteditable=""],[contenteditable="true"],input:not([type=button]):not([type=submit]):not([type=reset]):not([type=image])');
      if (field) return;
      const p = api._pressed = { el, url: location.href, reacted: false, timers: [] };
      // watch from the press on, so a reaction inside the click's own event dispatch is not missed: DOM, text or
      // attribute changes (a class, aria-expanded, open), focus moving elsewhere, input, scroll
      const react = (e) => {
        if (e && e.type === 'focusin' && p.el && (p.el.contains(e.target) || e.target.contains(p.el))) return;
        // input/change of the clicked control only (a field left behind fires 'change' on blur)
        if (e && (e.type === 'input' || e.type === 'change') && !(p.el && p.el.contains(e.target))) return;
        p.reacted = true; if (api._pressed === p) api.release();
      };
      p.react = react;
      p.obs = new MutationObserver(react);
      p.obs.observe(document, { subtree: true, childList: true, characterData: true, attributes: true });
      p.evs = ['focusin', 'input', 'change', 'scroll'];
      p.evs.forEach(t => document.addEventListener(t, react, true));
      p.timers.push(setTimeout(() => api.release(), 20000));
      p.poll = setInterval(() => { if (location.href !== p.url) api.release(); }, 100);
    },
    clicked(delayMs) {
      const p = api._pressed; if (!p || p.reacted) return;
      p.timers.push(setTimeout(() => {
        if (p.reacted || api._pressed !== p || location.href !== p.url) return api.release();
        const r = p.el && p.el.isConnected ? p.el.getBoundingClientRect() : null;
        if (r && r.width && r.height && r.width < innerWidth * 0.6 && r.height < innerHeight * 0.6) {
          const cs = getComputedStyle(p.el);
          Object.assign(pressedBox.style, { display: 'block', left: r.left + 'px', top: r.top + 'px',
            width: r.width + 'px', height: r.height + 'px', borderRadius: cs.borderRadius });
        }
        busy.classList.add('on');
        p.shown = Date.now();
      }, delayMs || 500));
    },
    release() {
      const p = api._pressed; if (!p) return;
      api._pressed = null;
      p.timers.forEach(clearTimeout); clearInterval(p.poll); if (p.obs) p.obs.disconnect();
      if (p.evs) p.evs.forEach(t => document.removeEventListener(t, p.react, true));
      if (pressedBox) pressedBox.style.display = 'none';
      if (busy) busy.classList.remove('on');
    },
    raise,
  };

  Object.defineProperty(window, KEY, { value: api, configurable: false, enumerable: false, writable: false });
  build();
  if (!attach()) {
    // the document element does not exist yet (new-document script): attach the moment it does, before first paint
    const o = new MutationObserver(() => { if (attach()) o.disconnect(); });
    o.observe(document, { childList: true });
  }
  watch();
  if (S.ripple) startRipple(S.ripple);                  // a navigation during the ripple continues it seamlessly
  if (S.press) startPress(S.press);
  window.addEventListener('pagehide', () => api.release());
  return api;
})
