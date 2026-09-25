"""Read-only page scripts (C-14 allows direct browser access to read state).

They use the node identities that jev-ultrafast's snapshot stores in
window.__jevFast, so every read refers to the element jev was offered.
"""

import json

# Same control selector as the upstream snapshot, to know what a "nested control" is.
_CONTROLS = (
    "a[href],button,input,textarea,select,summary,[contenteditable=\"true\"],"
    + ",".join(
        f'[role="{r}"]'
        for r in (
            "button link checkbox radio switch tab menuitem menuitemradio option gridcell "
            "combobox textbox searchbox spinbutton"
        ).split()
    )
)

ENRICH = (
    """(nodes => {
  const C = %s;
  const cache = window.__jevFast; if (!cache) return null;
  const clean = s => (s||'').replace(/\\s+/g,' ').trim();
  const byIds = ids => clean((ids||'').split(/\\s+/).map(id => document.getElementById(id))
      .filter(Boolean).map(n => n.innerText || n.textContent).join(' '));
  const text = (e) => {
    let out = '';
    for (const n of e.childNodes) {
      if (n.nodeType === 3) out += ' ' + n.textContent;
      else if (n.nodeType === 1) {
        if (n.getAttribute('aria-hidden') === 'true' || n.matches(C)) continue;   // a nested control is not ours
        if (['SCRIPT','STYLE','TEMPLATE','NOSCRIPT'].includes(n.tagName)) continue;
        out += ' ' + (n.tagName === 'IMG' ? (n.getAttribute('alt')||'') : text(n));
      }
    }
    return out;
  };
  const own = e => clean(byIds(e.getAttribute('aria-labelledby')) || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l => clean(text(l))).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName === 'INPUT' ? '' : text(e)) || e.getAttribute('title') || e.getAttribute('placeholder') || '');
  const LANDMARK = {NAV:'navigation', ASIDE:'complementary', HEADER:'banner', FOOTER:'contentinfo', MAIN:'main',
      FORM:'form', DIALOG:'dialog', SECTION:'region', TABLE:'table', TR:'row', LI:'listitem', UL:'list', OL:'list'};
  const context = e => {
    const out = [];
    for (let a = e.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const role = a.getAttribute('role') || LANDMARK[a.tagName] || '';
      const name = clean(byIds(a.getAttribute('aria-labelledby')) || a.getAttribute('aria-label') || '');
      if (role || name) out.push(clean(role + (name ? ' ' + name : '')));
      if (a.tagName === 'DIALOG' || a.getAttribute('role') === 'dialog' || a.getAttribute('aria-modal') === 'true') {
        const h = a.querySelector('h1,h2,h3,[role=heading]');
        if (h) out.push('dialog ' + clean(h.innerText));
      }
    }
    return out;
  };
  const res = {};
  for (const id of nodes) {
    const e = cache.nodes.get(id);
    if (!e || !e.isConnected) continue;
    res[id] = {own_label: own(e), context: context(e)};
  }
  return res;
})"""
    % json.dumps(_CONTROLS)
)

# The exact point upstream's executor will click: the centre of the observed
# element, hit-tested, or null if the element is gone, disabled or covered.
POINT = """(node => {
  const e = window.__jevFast?.nodes.get(node);
  if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
      !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
  const r = e.getBoundingClientRect(), x = r.x + r.width/2, y = r.y + r.height/2;
  if (!r.width || !r.height || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return null;
  if (!e.contains(document.elementFromPoint(x, y))) return null;
  return {x, y, w: r.width, h: r.height};
})"""

# Login page detector (C-31), app-agnostic: a visible password field.
LOGIN_FORM = """(() => [...document.querySelectorAll('input[type=password]')].some(e =>
  e.checkVisibility({checkOpacity:true, checkVisibilityCSS:true}) && e.getBoundingClientRect().width > 0))()"""

# Independent success checks (C-02). Each returns {ok, detail}.
CHECK = """(check => {
  const clean = s => (s||'').replace(/\\s+/g,' ').trim().toLowerCase();
  const vis = e => e && e.isConnected && e.checkVisibility({checkOpacity:true, checkVisibilityCSS:true}) &&
      (() => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom > 0 &&
        r.right > 0 && r.top < innerHeight && r.left < innerWidth; })();
  const visibleText = () => {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), range = document.createRange();
    const out = []; let n;
    while ((n = w.nextNode())) {
      const p = n.parentElement; if (!p || !n.textContent.trim() || p.closest('script,style,noscript,template')) continue;
      if (!p.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})) continue;
      range.selectNodeContents(n); const r = range.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth)
        out.push(n.textContent);
    }
    return clean(out.join(' '));
  };
  const dialogs = () => [...document.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]')]
      .filter(vis);
  const one = c => {
    switch (c.type) {
      case 'url_contains': return {ok: location.href.includes(c.value), detail: location.href};
      case 'url_regex': return {ok: new RegExp(c.value).test(location.href), detail: location.href};
      case 'text_visible': return {ok: visibleText().includes(clean(c.value)), detail: c.value};
      case 'text_absent': return {ok: !visibleText().includes(clean(c.value)), detail: c.value};
      case 'dialog_open': {
        const d = dialogs();
        const ok = d.length > 0 && (!c.value || d.some(e => clean(e.innerText).includes(clean(c.value))));
        return {ok, detail: d.length + ' dialog(s)'};
      }
      case 'dialog_closed': return {ok: dialogs().length === 0, detail: dialogs().length + ' dialog(s)'};
      case 'element_visible': {
        const name = e => clean(e.getAttribute('aria-label') || e.innerText);
        const byLabel = e => (!c.label || name(e) === clean(c.label)) &&
            (!c.label_contains || name(e).includes(clean(c.label_contains)));
        // the explicit role, else the implicit ARIA role of the tag (a[href] = link, ...), as jev's actions report it
        const implicit = e => { const t = e.tagName.toLowerCase(), ty = (e.getAttribute('type') || '').toLowerCase();
          if (t === 'a' || t === 'area') return e.hasAttribute('href') ? 'link' : '';
          if (t === 'button' || (t === 'input' && ['button','submit','reset','image'].includes(ty))) return 'button';
          if (t === 'input') return {checkbox:'checkbox', radio:'radio', range:'slider', search:'searchbox'}[ty] || 'textbox';
          return {textarea:'textbox', select:'combobox', tr:'row', table:'table', dialog:'dialog', nav:'navigation',
                  h1:'heading', h2:'heading', h3:'heading', h4:'heading', h5:'heading', h6:'heading',
                  li:'listitem', ul:'list', ol:'list', main:'main', img:'img'}[t] || ''; };
        const roleOf = e => e.getAttribute('role') || implicit(e);
        let els = [...document.querySelectorAll(c.selector || '*')].filter(e =>
            (!c.role || roleOf(e) === c.role || e.tagName.toLowerCase() === c.tag) && byLabel(e));
        if (c.label || c.label_contains) {   // one element per match: drop ancestors of a deeper match
          const set = new Set(els);
          els = els.filter(e => ![...set].some(d => d !== e && e.contains(d)));
        }
        const n = els.filter(vis).length;
        return {ok: c.count == null ? n > 0 : n === c.count, detail: n + ' visible'};
      }
      case 'count': {
        const n = [...document.querySelectorAll(c.selector)].filter(vis).length;
        return {ok: n === c.value, detail: n + ' visible'};
      }
      case 'title_contains': return {ok: document.title.includes(c.value), detail: document.title};
      default: return {ok: false, detail: 'unknown check type ' + c.type};
    }
  };
  const all = (Array.isArray(check) ? check : [check]).map(c => ({type: c.type, ...one(c)}));
  return {ok: all.every(r => r.ok), results: all};
})"""


def call(fn, arg):
    return f"({fn})({json.dumps(arg)})"
