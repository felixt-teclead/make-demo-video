#!/usr/bin/env python3
"""C-15 environment check (runs inside the container): font and locale check on a fixed test page, plus
no dialogs/bars/extra tabs, kiosk full screen at 1920x1080, DPR 1, 24-bit colour. Prints JSON, exits 1 on FAIL.
Only reads browser state and loads the test page off camera (C-14)."""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from vccdp import CDP, page_targets  # noqa: E402

LANG = os.environ.get("VC_LANG", "de-DE")
TZ = os.environ.get("TZ", "Europe/Berlin")
W, H = (int(x) for x in os.environ.get("VC_SCREEN", "1920x1080").split("x"))
PAGE = "http://127.0.0.1:8099/fontcheck.html"
IDS = ["umlauts", "symbols", "emoji", "serif", "mono", "arial", "numbers"]
res = {"checks": {}, "fail": []}

def check(name, ok, detail):
    res["checks"][name] = {"ok": bool(ok), "detail": detail}
    if not ok:
        res["fail"].append(name)

# fresh state first: tabs and window, before we load anything
pages = page_targets()
check("one_page_tab", len(pages) == 1, [p.get("url") for p in pages])
with CDP.page() as c:
    v = c.evaluate("({w: innerWidth, h: innerHeight, ow: outerWidth, oh: outerHeight, sw: screen.width,"
                   " sh: screen.height, dpr: devicePixelRatio, depth: screen.colorDepth})")
    check("viewport_is_whole_screen_no_bars", (v["w"], v["h"], v["sw"], v["sh"]) == (W, H, W, H), v)
    check("device_scale_1", v["dpr"] == 1, v["dpr"])
    check("colour_24bit", v["depth"] == 24, v["depth"])
    tid = c.call("Target.getTargetInfo")["targetInfo"]["targetId"]
    win = c.call("Browser.getWindowForTarget", targetId=tid)["bounds"]
    pos = c.evaluate("[screenX, screenY]")
    # the page area, not the window, must be the screen: without a window manager the bars sit above the screen
    # (env/lib/window_fit.py), so the page's top edge is screenY + the bar height
    top = pos[1] + (v["oh"] - v["h"])
    check("window_covers_screen", pos[0] == 0 and top == 0 and win.get("width", 0) >= W and win.get("height", 0) >= H,
          {"bounds": win, "screen_xy": pos, "page_top_on_screen": top})
    sc = c.evaluate("(() => { const t = document.createElement('textarea'); return t.spellcheck; })()")
    check("spellcheck_off_policy", os.path.exists("/etc/opt/chrome/policies/managed/vc.json") and
          json.load(open("/etc/opt/chrome/policies/managed/vc.json")).get("SpellcheckEnabled") is False, sc)

    c.call("Network.enable")
    # a cached load sends no request, so no Accept-Language header would be seen on a second run
    c.call("Network.setCacheDisabled", cacheDisabled=True)
    c.events.clear()
    c.navigate(PAGE)
    accept = None
    for e in c.events:
        if e["method"] == "Network.requestWillBeSentExtraInfo" or e["method"] == "Network.requestWillBeSent":
            hdrs = e["params"].get("headers") or e["params"].get("request", {}).get("headers", {})
            for k, val in hdrs.items():
                if k.lower() == "accept-language":
                    accept = val
    c.call("Network.setCacheDisabled", cacheDisabled=False)
    c.call("Network.disable")
    check("accept_language", bool(accept) and accept.split(",")[0].strip() == LANG, accept)
    loc = c.evaluate("({lang: navigator.language, langs: navigator.languages,"
                     " tz: Intl.DateTimeFormat().resolvedOptions().timeZone,"
                     " locale: Intl.DateTimeFormat().resolvedOptions().locale,"
                     " num: (1234.56).toLocaleString(), date: new Date(Date.UTC(2026,8,24,12)).toLocaleString()})")
    check("navigator_language", loc["lang"] == LANG, loc)
    check("timezone", loc["tz"] == TZ, loc["tz"])
    # Chrome sets the ICU default locale from its UI locale, and its de-DE UI ships as "de" (locales/de.pak), so
    # Intl reports "de" (CLDR de = de-DE formats). The formatted number and date prove the German formats.
    check("intl_locale", loc["locale"] in (LANG, LANG.split("-")[0]) and loc["num"] == "1.234,56"
          and loc["date"].startswith("24.9.2026"), {k: loc[k] for k in ("locale", "num", "date")})

    c.call("DOM.enable"); c.call("CSS.enable")
    root = c.call("DOM.getDocument", depth=-1)["root"]["nodeId"]
    fonts = {}
    for i in IDS:
        nid = c.call("DOM.querySelector", nodeId=root, selector="#" + i)["nodeId"]
        pf = c.call("CSS.getPlatformFontsForNode", nodeId=nid)["fonts"]
        fonts[i] = [{"family": f["familyName"], "ps": f.get("postScriptName"), "glyphs": f["glyphCount"]} for f in pf]
    bad = {i: [f["family"] for f in fl if not f["family"].startswith("Noto")] for i, fl in fonts.items()}
    bad = {k: v for k, v in bad.items() if v}
    check("only_noto_fonts_no_fallback", not bad, bad or fonts)
    check("emoji_uses_noto_color_emoji", any(f["family"] == "Noto Color Emoji" for f in fonts["emoji"]), fonts["emoji"])
    check("text_uses_noto_sans", fonts["umlauts"] and fonts["umlauts"][0]["family"] == "Noto Sans", fonts["umlauts"])
    check("serif_uses_noto_serif", fonts["serif"] and fonts["serif"][0]["family"] == "Noto Serif", fonts["serif"])
    check("mono_uses_noto_sans_mono", fonts["mono"] and fonts["mono"][0]["family"] == "Noto Sans Mono", fonts["mono"])
    c.call("CSS.disable"); c.call("DOM.disable")
    c.navigate("about:blank")

# Chrome's own sandbox is on (owner decision 2026-09-25, option a; env/seccomp-chrome.json): no --no-sandbox on any
# Chrome process, and the renderers live in their own user namespace (the namespace sandbox), not the browser's.
def _chrome_procs():
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            # child processes rewrite their title, so the cmdline can be one space-joined string
            args = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").split()
            if not args or not args[0].endswith(b"/opt/google/chrome/chrome"):
                continue
            typ = next((a.decode().split("=", 1)[1] for a in args if a.startswith(b"--type=")), "browser")
            out.append({"pid": int(pid), "type": typ, "no_sandbox": b"--no-sandbox" in args,
                        "userns": os.readlink(f"/proc/{pid}/ns/user")})
        except OSError:
            pass
    return out
procs = _chrome_procs()
browser_ns = {p["userns"] for p in procs if p["type"] == "browser"}
renderers = [p for p in procs if p["type"] == "renderer"]
check("chrome_sandbox_on", procs and not any(p["no_sandbox"] for p in procs) and renderers
      and all(p["userns"] not in browser_ns for p in renderers),
      {"processes": len(procs), "no_sandbox_flag": sum(p["no_sandbox"] for p in procs),
       "renderers_in_own_userns": sum(p["userns"] not in browser_ns for p in renderers), "renderers": len(renderers)})

with urllib.request.urlopen("http://127.0.0.1:7777/browser", timeout=10) as r:
    res["browser"] = json.loads(r.read().decode())
res["verdict"] = "PASS" if not res["fail"] else "FAIL"
print(json.dumps(res, indent=1, ensure_ascii=False))
sys.exit(0 if not res["fail"] else 1)
