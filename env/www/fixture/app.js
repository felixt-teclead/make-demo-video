// Fixture test page (Q-91). Page behaviour only: this script never sends clicks, keys or input (C-14).
// Planted defects are OFF unless switched on by URL query parameters (see README.md).
'use strict';
const Q = new URLSearchParams(location.search);
const num = (k, d) => { const v = Q.get(k); return v === null || v === '' || isNaN(+v) ? d : +v; };
const on = k => num(k, 0) > 0;
const FRAME = 1000 / 30; // one capture frame (30 fps)
const D = {
  whiteout: on('whiteout'),              // Q-20: white-out at every navigation
  black: on('black'),                    // Q-20: 1.5 s black gap at every navigation
  orphan: on('orphan'),                  // Q-21: backdrop without dialog for one frame on close
  nesteddim: on('nesteddim'),            // Q-21: nested double dim on close
  skeleton: Math.max(0, Math.round(num('skeleton', 0))), // Q-23: N skeleton frames after a route change
  sticky: on('sticky'),                  // Q-31: sticky header rides along 56 px, then pins
  dash: on('dash'),                      // Q-31: marching dashed edges in the pan view
  jitter: on('jitter'),                  // Q-31: list scroll with a reversal
  halfpaint: on('halfpaint'),            // Q-31/Q-34: pan view paints only every 2nd frame while moving
  pointer: on('pointer'),                // Q-40: a second pointer-like sprite
  banner: on('banner'),                  // Q-30: late banner that pushes content down (layout shift)
};
const LATE_MS = Math.max(0, num('late', 3000)); // Q-30 late-content target: answer delay in ms
const $ = id => document.getElementById(id);
const wait = ms => new Promise(r => setTimeout(r, ms));
// Wait at least ms, aligned to animation frames, so a "1 frame" defect really paints.
const frames = ms => new Promise(r => { const t0 = performance.now(); const f = t => (t - t0 >= ms ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });

// ---- local item store (nothing leaves the page) ----
const KEY = 'vc-fixture-items';
const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } };
const save = items => localStorage.setItem(KEY, JSON.stringify(items));
if (localStorage.getItem(KEY) === null) save([{ id: 'seed-1', name: 'Beispiel Alpha', created: '2026-09-01' }, { id: 'seed-2', name: 'Beispiel Beta', created: '2026-09-02' }]);

function renderItems() {
  const items = load(), box = $('items');
  box.textContent = '';
  for (const it of items) {
    const row = document.createElement('div');
    row.className = 'row'; row.setAttribute('role', 'row'); row.setAttribute('aria-label', 'Eintrag ' + it.name);
    row.dataset.id = it.id;
    const c1 = document.createElement('span'); c1.setAttribute('role', 'cell');
    const open = document.createElement('button'); open.type = 'button'; open.className = 'link'; open.textContent = it.name;
    open.onclick = () => openDialog(it);
    const date = document.createElement('span'); date.className = 'muted'; date.textContent = ' · angelegt ' + it.created;
    c1.append(open, date);
    const c2 = document.createElement('span'); c2.setAttribute('role', 'cell');
    const del = document.createElement('button'); del.type = 'button'; del.className = 'plain'; del.textContent = 'Löschen';
    del.onclick = () => { save(load().filter(x => x.id !== it.id)); $('create-status').textContent = 'Eintrag gelöscht.'; renderItems(); };
    c2.append(del);
    row.append(c1, c2);
    box.append(row);
  }
  $('items-empty').hidden = items.length > 0;
  $('det-count').textContent = $('sum-count').textContent = String(items.length);
}

$('create').addEventListener('submit', e => {
  e.preventDefault();
  const name = $('name').value.replace(/\s+/g, ' ').trim(), st = $('create-status');
  if (!name) { st.textContent = 'Bitte einen Namen angeben.'; return; }
  const items = load();
  if (items.some(x => x.name === name)) { st.textContent = 'Dieser Name ist schon vergeben.'; return; }
  items.push({ id: 'i' + Date.now().toString(36), name, created: new Date().toISOString().slice(0, 10) });
  save(items); $('name').value = ''; st.textContent = 'Eintrag angelegt.'; renderItems();
});

// ---- tabs ----
function selectTab(which) {
  for (const t of ['liste', 'details']) {
    $('tab-' + t).setAttribute('aria-selected', String(t === which));
    $('panel-' + t).hidden = t !== which;
  }
}
$('tab-liste').onclick = () => selectTab('liste');
$('tab-details').onclick = () => selectTab('details');

// ---- item dialog: opens and closes in one frame (clean), unless orphan/nesteddim ----
function openDialog(it) {
  $('dlg-h').textContent = it.name;
  $('dlg-body').textContent = 'Eintrag „' + it.name + '“, angelegt ' + it.created + '.';
  $('backdrop').hidden = false; $('dlg').hidden = false; $('dlg-close').focus({ preventScroll: true });
}
async function closeDialog() {
  if (D.nesteddim) { $('dim2').hidden = false; await frames(3 * FRAME); }
  if (D.orphan) { $('dlg').hidden = true; $('dim2').hidden = true; await frames(FRAME); }
  $('dlg').hidden = true; $('backdrop').hidden = true; $('dim2').hidden = true;
}
$('dlg-close').onclick = closeDialog;
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('dlg').hidden) closeDialog(); });

// ---- routes (hash) with navigation defects ----
const ROUTES = ['uebersicht', 'eintraege', 'ansicht', 'protokoll', 'werkzeuge'];
let first = true;
function cover(cls) { const c = document.createElement('div'); c.className = 'cover ' + cls; document.body.append(c); return c; }
function skeleton() {
  const s = document.createElement('div'); s.className = 'skel';
  for (const w of [40, 90, 75, 90, 60, 85]) { const b = document.createElement('div'); b.style.width = w + '%'; s.append(b); }
  $('main').append(s); return s;
}
async function route() {
  let r = location.hash.replace(/^#\/?/, '');
  if (!ROUTES.includes(r)) r = 'uebersicht';
  const nav = !first; first = false;
  if (nav && D.black) { const c = cover('black'); await frames(1500); if (!D.whiteout) c.remove(); else setTimeout(() => c.remove(), 50); }
  const white = nav && D.whiteout ? cover('white') : null;
  if (white) await frames(300);
  const skel = nav && D.skeleton ? skeleton() : null;
  if (white) white.remove();
  for (const el of document.querySelectorAll('[data-route]')) el.hidden = el.dataset.route !== r;
  for (const a of document.querySelectorAll('[data-nav]')) {
    if (a.dataset.nav === r) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  if (r === 'eintraege') { selectTab('liste'); renderItems(); }
  if (skel) { await frames(D.skeleton * FRAME); skel.remove(); }
}
window.addEventListener('hashchange', route);

// ---- pan view: sticky header, marching dashes, half-rate paint ----
(function buildPan() {
  const lanes = $('lanes'), svg = $('graph'), NS = 'http://www.w3.org/2000/svg';
  for (let i = 0; i < 10; i++) { const d = document.createElement('div'); d.textContent = 'Bereich ' + (i + 1); lanes.append(d); }
  if (!D.sticky) $('pan-spacer').remove();
  if (D.dash) svg.classList.add('march');
  const el = (n, a) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); svg.append(e); return e; };
  for (let c = 0; c < 10; c++) for (let r = 0; r < 6; r++) {
    const x = c * 420 + 70, y = r * 380 + 80;
    if (c < 9) el('path', { class: 'edge', d: `M${x + 260} ${y + 45} H${x + 420}` });
    if (r < 5 && (c + r) % 2 === 0) el('path', { class: 'edge', d: `M${x + 130} ${y + 90} V${y + 380}` });
    el('rect', { x, y, width: 260, height: 90, rx: 8, fill: (c + r) % 3 ? '#e8eefc' : '#fdf0dc', stroke: '#8a94a8', 'stroke-width': 2 });
    el('text', { x: x + 20, y: y + 52, 'font-size': 20, fill: '#1d2330' }).textContent = `Schritt ${c + 1}.${r + 1}`;
  }
  if (!D.halfpaint) return;
  // Hold the old picture on every 2nd frame: counter-translate by the scroll made since the last "paint".
  const pan = $('pan'), inner = $('pan-inner');
  let n = 0, hx = 0, hy = 0;
  const tick = () => {
    n++;
    if (n % 2) { hx = pan.scrollLeft; hy = pan.scrollTop; inner.style.transform = ''; }
    else inner.style.transform = `translate(${pan.scrollLeft - hx}px,${pan.scrollTop - hy}px)`;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();

// ---- log list with a scripted scroll (optional reversal) ----
(function buildLog() {
  const log = $('log');
  for (let i = 1; i <= 160; i++) { const d = document.createElement('div'); d.textContent = `#${String(i).padStart(3, '0')} · Prüfung ${i} abgeschlossen`; log.append(d); }
  const ease = t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
  $('to-end').onclick = () => {
    const from = log.scrollTop, to = log.scrollHeight - log.clientHeight, dur = 2400, t0 = performance.now();
    const step = t => {
      const p = Math.min(1, (t - t0) / dur);
      let y = from + (to - from) * ease(p);
      if (D.jitter && p > 0.45 && p < 0.55) y -= 90 * Math.sin((p - 0.45) / 0.1 * Math.PI); // reversal
      log.scrollTop = y;
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
})();

// ---- copy-then-paste target (Q-55) ----
$('copy').onclick = async () => {
  const v = $('copy-value').textContent.trim();
  try { await navigator.clipboard.writeText(v); } catch (e) { /* clipboard may be unavailable; the value is still shown */ }
  $('copy-status').textContent = 'In die Zwischenablage kopiert.';
};
$('paste').addEventListener('input', () => {
  const ok = $('paste').value.trim() === $('copy-value').textContent.trim();
  $('paste-status').textContent = ok ? 'Wert stimmt überein.' : '';
});

// ---- late-content target (Q-30) ----
let lateRun = 0;
$('late').onclick = async () => {
  const me = ++lateRun;
  $('late-status').textContent = 'Anfrage gesendet.';
  await wait(LATE_MS);
  if (me === lateRun) $('late-status').textContent = 'Antwort erhalten: 12 Prüfungen abgeschlossen.';
};

// ---- second pointer sprite (Q-40), late banner (Q-30) ----
if (D.pointer) $('sprite').removeAttribute('hidden'); // SVG: no .hidden property
if (D.banner) setTimeout(() => {
  const b = document.createElement('div'); b.className = 'banner'; b.textContent = 'Hinweis: Es gibt neue Prüfergebnisse.';
  $('banner-slot').append(b);
}, 2500);

// ---- keep the query (defect switches) on the full-load link ----
$('to-page2').href = 'page2.html' + location.search;

// ---- defect settings panel: only with ?panel=1, never on camera ----
if (on('panel')) {
  const p = document.createElement('form'); p.id = 'panel'; p.setAttribute('aria-label', 'Defekte');
  p.innerHTML = '<strong>Defekte</strong>';
  const params = { ...Object.fromEntries(Object.keys(D).map(k => [k, 1])), skeleton: 11 };
  for (const k of Object.keys(params)) {
    const l = document.createElement('label'), c = document.createElement('input');
    c.type = 'checkbox'; c.name = k; c.checked = Q.has(k) && Q.get(k) !== '0';
    c.onchange = () => { c.checked ? Q.set(k, Q.get(k) && Q.get(k) !== '0' ? Q.get(k) : params[k]) : Q.delete(k); location.search = Q.toString(); };
    l.append(c, ' ' + k); p.append(l);
  }
  const l = document.createElement('label'), s = document.createElement('input');
  s.type = 'number'; s.step = 500; s.value = LATE_MS; s.onchange = () => { Q.set('late', s.value); location.search = Q.toString(); };
  l.append('late (ms) ', s); p.append(l);
  document.body.append(p);
}

renderItems();
route();
