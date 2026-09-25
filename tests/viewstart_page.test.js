// Browserless test of vc/overlay/viewstart.js with a tiny fake DOM (node, no dependencies).
// Run: node tests/viewstart_page.test.js   (tests/test_viewstart.py runs it when node is on PATH)
// The fake app behaves like the measured example diagram: a wheel event moves the viewport's translation by
// -k * delta, applied in a microtask (React's render after a store update), then MutationObserver callbacks run.
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

function world(href) {
  const G = {};
  const rafQ = [];
  const observers = [];
  const listeners = [];
  const els = [];
  let frameNo = 0;
  const notify = () => observers.forEach(o => { if (!o.queued) { o.queued = true;
    queueMicrotask(() => { o.queued = false; o.cb([]); }); } });
  class El {
    constructor(cls, parent) { this.cls = new Set(cls.split(' ')); this.parentElement = parent || null; this.tx = 0;
      this.ty = 0; this.scrollLeft = 0; this.scrollTop = 0; this.isConnected = true; this.k = 0.5; this.wheels = []; }
    matches(sel) { return sel.split('.').filter(Boolean).every(c => this.cls.has(c)); }
    contains(e) { for (let n = e; n; n = n.parentElement) if (n === this) return true; return false; }
    getBoundingClientRect() { return {left: 0, top: 0, width: 800, height: 600}; }
    dispatchEvent(ev) {
      ev.target = this;
      listeners.filter(l => l.type === ev.type).forEach(l => l.fn(ev));
      if (ev.type === 'wheel') {
        this.wheels.push([ev.deltaX, ev.deltaY]);
        queueMicrotask(() => { this.tx -= this.k * ev.deltaX; this.ty -= this.k * ev.deltaY; notify(); });
      }
      return true;
    }
    set(tx, ty) { this.tx = tx; this.ty = ty; notify(); }
  }
  const root = new El('html');
  const doc = {
    documentElement: root,
    querySelector: sel => els.find(e => e.isConnected && e.matches(sel)) || null,
    querySelectorAll: sel => els.filter(e => e.isConnected && e.matches(sel)),
    addEventListener() {},
  };
  G.window = G;
  G.document = doc;
  G.location = {href};
  G.performance = {now: () => frameNo * 16.7};
  G.requestAnimationFrame = fn => rafQ.push(fn);
  G.addEventListener = (type, fn) => listeners.push({type, fn});
  G.getComputedStyle = e => ({transform: `matrix(1, 0, 0, 1, ${e.tx}, ${e.ty})`});
  G.MutationObserver = class { constructor(cb) { this.cb = cb; } observe() { observers.push(this); } };
  G.WheelEvent = class { constructor(type, o) { Object.assign(this, o); this.type = type; } };
  G.mount = (cls, tx, ty, parent) => { const e = new El(cls, parent || root); e.tx = tx; e.ty = ty; els.push(e);
    notify(); return e; };
  G.frame = async () => { await settle(); frameNo++; const q = rafQ.splice(0); q.forEach(f => f(frameNo * 16.7));
    await settle(); };
  G.El = El;
  return G;
}
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

function load(G, rules) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'vc', 'overlay', 'viewstart.js'), 'utf8');
  const keys = Object.keys(G);
  // eslint-disable-next-line no-new-func
  new Function(...keys, `return (${src.trim()})`)(...keys.map(k => G[k]))(rules);
  return G.window.__vcViewStart;
}

const RULE = {id: 'sticky', url_regex: '/processes/[0-9a-f-]{36}', selector: '.react-flow__viewport',
              when: '.lane', by: 'wheel', x: 0, y: 0, wheel_px_per_unit: 0.5, settle_ms: 1500};
const URL = 'https://app.test/de/processes/00000000-0000-4000-8000-000000000001';

const tests = {
  async 'set before the first painted frame'() {
    const G = world(URL); const S = load(G, [RULE]);
    await G.frame(); await G.frame();
    const vp = G.mount('react-flow__viewport', 24, 56); G.mount('lane', 0, 0, vp);
    await settle();                      // the MutationObserver microtask after the commit, no frame yet
    assert.deepStrictEqual([vp.tx, vp.ty], [0, 0]);
    assert.strictEqual(vp.wheels.length, 1);
    assert.deepStrictEqual(vp.wheels[0], [48, 112]);
    const e = S.log[0];
    assert.strictEqual(e.frame, e.seen_frame); assert.strictEqual(e.end_frame, e.frame);
    assert.deepStrictEqual(e.from, {x: 24, y: 56}); assert.deepStrictEqual(e.got, {x: 0, y: 0});
  },
  async 're-applied when the app moves it during mount, not after a foreign wheel'() {
    const G = world(URL); const S = load(G, [RULE]);
    const vp = G.mount('react-flow__viewport lane', 24, 56);
    await G.frame();
    vp.set(24, 56); await settle();      // the app resets it in a later frame: undone before that frame paints
    assert.deepStrictEqual([vp.tx, vp.ty], [0, 0]);
    assert.strictEqual(S.log.length, 2);
    vp.dispatchEvent(new G.WheelEvent('wheel', {deltaX: 0, deltaY: 100}));   // the camera's pan
    await settle(); await G.frame();
    assert.deepStrictEqual([vp.tx, vp.ty], [0, -50]);
    assert.strictEqual(S.log.length, 2);
  },
  async 'settle window ends the rule'() {
    const G = world(URL); const S = load(G, [Object.assign({}, RULE, {settle_ms: 30})]);
    const vp = G.mount('react-flow__viewport lane', 24, 56);
    await G.frame(); await G.frame(); await G.frame();
    vp.set(10, 10); await settle(); await G.frame();
    assert.deepStrictEqual([vp.tx, vp.ty], [10, 10]);
    assert.strictEqual(S.log.length, 1);
  },
  async 'a remount (another view, then back) starts again'() {
    const G = world(URL); const S = load(G, [RULE]);
    const a = G.mount('react-flow__viewport lane', 24, 56); await settle();
    a.dispatchEvent(new G.WheelEvent('wheel', {deltaX: 0, deltaY: 40})); await settle();
    a.isConnected = false;
    const b = G.mount('react-flow__viewport lane', 24, 56); await settle();
    assert.deepStrictEqual([b.tx, b.ty], [0, 0]);
    assert.strictEqual(S.log.length, 2);
  },
  async 'other URLs and views without the `when` element are left alone'() {
    const G = world('https://app.test/de/chat'); const S = load(G, [RULE]);
    const vp = G.mount('react-flow__viewport lane', 24, 56); await settle(); await G.frame();
    assert.deepStrictEqual([vp.tx, vp.ty], [24, 56]);
    const G2 = world(URL); const S2 = load(G2, [RULE]);
    const bpmn = G2.mount('react-flow__viewport', 24, 56); await settle(); await G2.frame();
    assert.deepStrictEqual([bpmn.tx, bpmn.ty], [24, 56]);
    assert.strictEqual(S.log.length + S2.log.length, 0);
  },
  async 'wheel scale is refined in the page when the first guess is wrong'() {
    const G = world(URL); const S = load(G, [RULE]);
    const vp = new G.El('react-flow__viewport lane', G.document.documentElement); vp.k = 0.25;
    vp.tx = 24; vp.ty = 56;
    G.document.querySelectorAll = sel => vp.matches(sel) ? [vp] : [];
    G.document.querySelector = sel => vp.matches(sel) ? vp : null;
    S.scan(); await settle();
    assert.deepStrictEqual([vp.tx, vp.ty], [0, 0]);
    assert.strictEqual(S.log[0].rounds, 2);
  },
  async 'only the named axes move'() {
    const G = world(URL); const S = load(G, [Object.assign({}, RULE, {x: undefined})]);
    const vp = G.mount('react-flow__viewport lane', 24, 56); await settle();
    assert.deepStrictEqual([vp.tx, vp.ty], [24, 0]);
    assert.strictEqual(S.log.length, 1);
  },
  async 'scroll containers are set directly'() {
    const G = world(URL); const S = load(G, [{id: 's', url_regex: '.*', selector: '.list', by: 'scroll', y: 0}]);
    const box = G.mount('list', 0, 0); box.scrollTop = 56; box.scrollLeft = 7; G.document.documentElement;
    S.scan(); await settle();
    assert.strictEqual(box.scrollTop, 0); assert.strictEqual(box.scrollLeft, 7);
    assert.strictEqual(box.wheels.length, 0);
  },
  async 'translation parser'() {
    const G = world(URL); const S = load(G, []);
    assert.deepStrictEqual(S.translation('none'), {x: 0, y: 0});
    assert.deepStrictEqual(S.translation('matrix(1, 0, 0, 1, 24, 56)'), {x: 24, y: 56});
    assert.deepStrictEqual(S.translation('matrix3d(1,0,0,0,0,1,0,0,0,0,1,0,-3,4.5,0,1)'), {x: -3, y: 4.5});
  },
};

(async () => {
  let failed = 0;
  for (const [name, fn] of Object.entries(tests)) {
    try { await fn(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n' + e.stack); }
  }
  console.log(failed ? `${failed} failed` : 'all passed');
  process.exit(failed ? 1 : 0);
})();
