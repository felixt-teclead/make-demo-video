"""Measure the filmed overlay take against the spec tolerances (test rig only; numpy inside the container).

Reads OUT/take.mkv and OUT/log.json (from drive.py), writes OUT/measure.json, OUT/verdicts.txt and a few crops.
"""

import json
import math
import subprocess
import sys

import numpy as np

sys.path.insert(0, "/repo")
from vc.overlay import timing as T  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "/out"
W, H, FPS = 1920, 1080, 30.0
FR = 1000.0 / FPS
log = json.load(open(f"{OUT}/log.json"))
marks = log["marks"]

TYP = (100, 100, 500, 138)      # text part of the typing field (its centre, the click point, lies right of it)
PST = (100, 200, 500, 238)
PANEL = (1480, 80, 1900, 420)   # dark panel (like the baselines' dark app): the cursor is tracked by its white fill


def hue_chroma(a):
    a = a.astype(np.int16)
    mx, mn = a.max(-1), a.min(-1)
    ch = mx - mn
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    c = np.maximum(ch, 1)
    h = np.where(mx == r, ((g - b) / c) % 6, np.where(mx == g, (b - r) / c + 2, (r - g) / c + 4)) * 60
    return h, ch


def cursor_mask(f, luma):
    m = luma < 70                               # the dark outline on light pages
    m[:, W - 2:] = False                        # rig artefact: the kiosk window leaves a 1 px black edge
    m[H - 2:, :] = False
    for (x0, y0, x1, y1) in (TYP, PST):
        m[y0 - 2:y1 + 2, x0 - 2:x1 + 2] = False
        # the page's own focus ring around the whole field (not a pointer)
        fx1 = 1020
        m[y0 - 4:y0 + 4, x0 - 4:fx1 + 4] = False
        m[y1 - 4:y1 + 5, x0 - 4:fx1 + 4] = False
        m[y0 - 4:y1 + 5, fx1 - 6:fx1 + 4] = False
    x0, y0, x1, y1 = PANEL
    if np.median(luma[y0:y1:8, x0:x1:8]) < 50:  # the panel exists on this page (p1): track the white fill there
        m[y0:y1, x0:x1] = luma[y0:y1, x0:x1] > 200
    return m


PTS = [float(x.strip(",")) for x in subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "frame=pts_time", "-of", "csv=p=0",
     f"{OUT}/take.mkv"], capture_output=True, text=True).stdout.split()]   # x11grab + -copyts: wall-clock capture times
proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", f"{OUT}/take.mkv", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                        stdout=subprocess.PIPE)
frames = []
i = 0
while True:
    buf = proc.stdout.read(W * H * 3)
    if len(buf) < W * H * 3:
        break
    f = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    luma = f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114
    cm = cursor_mask(f, luma)
    ys, xs = np.nonzero(cm)
    rec = {"i": i}
    if len(ys):
        ymin = ys.min()
        tx, ty = float(xs[ys == ymin].min()), float(ymin)
        rec["tip"] = (tx, ty)
        near = (np.abs(xs - tx) < 40) & (ys - ty < 40)
        rec["bbox"] = (int(xs[near].min()), int(ys[near].min()), int(xs[near].max()), int(ys[near].max()))
        rec["stray"] = int((~near).sum())     # cursor-like pixels away from the one cursor (Q-40)
        if rec["stray"]:
            rec["stray_box"] = (int(xs[~near].min()), int(ys[~near].min()), int(xs[~near].max()), int(ys[~near].max()))
        rec["white_below_tip"] = float(luma[min(H - 1, int(ty) + 9), min(W - 1, int(tx) + 3)])
        if PANEL[0] + 40 < tx < PANEL[2] - 40 and PANEL[1] + 40 < ty < PANEL[3] - 40:
            wy, wx = np.nonzero(luma[int(ty) - 2:int(ty) + 30, int(tx) - 4:int(tx) + 30] > 120)
            if len(wy):
                rec["white_box"] = (int(wx.max() - wx.min() + 1), int(wy.max() - wy.min() + 1), int(len(wy)))
    h, ch = hue_chroma(f)
    band = (h >= 200) & (h <= 225) & (ch >= 60)
    by, bx = np.nonzero(band)
    rec["band_n"] = int(len(by))
    if len(by) >= 20:
        rec["band_bbox"] = (int(bx.min()), int(by.min()), int(bx.max()), int(by.max()))
        cols = f[by, bx].astype(int)
        rec["band_median"] = [int(v) for v in np.median(cols, 0)]
        strong = cols[ch[by, bx] >= 150]
        if len(strong) >= 10:
            rec["ring_median"] = [int(v) for v in np.median(strong, 0)]
    rec["bg"] = [int(v) for v in f[5, 5]]
    for name, (x, y) in {"t1": (1070, 830), "t2": (470, 830), "t3": (1270, 230)}.items():
        p = f[y, x].astype(int)
        rec[name] = int(p[0] - p[1])
    rec["slow_luma"] = float(luma[404:436, 704:738].mean())
    grey_ink = (luma < 128) & (ch < 40)
    rec["typ_ink"] = int(grey_ink[TYP[1] + 6:TYP[3] - 6, TYP[0] + 6:TYP[2]].sum())
    rec["pst_ink"] = int(grey_ink[PST[1] + 6:PST[3] - 6, PST[0] + 6:PST[2]].sum())
    frames.append(rec)
    i += 1
N = len(frames)
res = {"frames": N}
V = []   # (check, measured, tolerance, pass)


def tipd(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---- ripple episodes -------------------------------------------------------------------------------------------
eps, cur = [], None
for r in frames:
    on = r["band_n"] >= 5
    if on and cur is None:
        cur = [r["i"], r["i"]]
    elif on:
        cur[1] = r["i"]
    elif cur is not None:
        eps.append(cur)
        cur = None
if cur:
    eps.append(cur)
clicks = sorted([k for k in marks if isinstance(marks[k], dict) and marks[k].get("type") == "click"],
                key=lambda k: marks[k]["t"])
ripples = []
for (a, b) in eps:
    k68 = min(b, a + round(0.68 * T.RIPPLE_MS / FR))
    k30 = min(b, a + round(0.30 * T.RIPPLE_MS / FR))
    ws = {j: frames[j]["band_bbox"][2] - frames[j]["band_bbox"][0] + 1 for j in range(a, b + 1) if "band_bbox" in frames[j]}
    ripples.append({"first": a, "last": b, "visible_ms": round((PTS[b] - PTS[a]) * 1000 + FR), "max_w": max(ws.values()),
                    "w_at_68pct": ws.get(k68), "frame_68pct": k68, "ring_rgb_at_30pct": frames[k30].get("ring_median"),
                    "band_median_at_68pct": frames[k68].get("band_median")})
res["ripples"] = ripples
anchor_wall = marks[clicks[0]]["t"]
anchor_f = ripples[0]["first"]


def w2f(wall):
    """The first frame captured at or after `wall` (exact: frames carry their wall-clock capture time)."""
    import bisect
    return min(len(PTS) - 1, bisect.bisect_left(PTS, wall))


def f2w(k):
    return PTS[k]


def ripple_of(name):
    return min(ripples, key=lambda r: abs(r["first"] - w2f(marks[name]["t"])))


nclicks = len([e for e in log["events"] if e.get("type") == "click"])
V.append(("Q-44 one ripple per click", f"{len(ripples)} ripples / {nclicks} clicks", "equal",
          len(ripples) == nclicks))
vis = [r["visible_ms"] for r in ripples]
V.append(("Q-76/Q-45 ripple visible", f"{min(vis)}-{max(vis)} ms", "200-900 ms", all(200 <= v <= 900 for v in vis)))
rl = ripple_of("#t1")
V.append(("Q-76 ring diameter at 68% (light page)", f"{rl['w_at_68pct']} px", "92 px +-10% (83-101)",
          83 <= (rl["w_at_68pct"] or 0) <= 101))
rd = ripple_of("#t4")
V.append(("Q-76 ring diameter at 68% (dark panel)", f"{rd['w_at_68pct']} px", "92 px +-10% (83-101)",
          83 <= (rd["w_at_68pct"] or 0) <= 101))
ring = rl["ring_rgb_at_30pct"] or [0, 0, 0]
V.append(("Q-76 ring colour at full opacity", str(ring), "#4da3ff (77,163,255) +-10/channel",
          all(abs(a - b) <= 10 for a, b in zip(ring, (77, 163, 255)))))
V.append(("Q-76 dark-panel band median at 68% vs ripple-peak.png median (78,140,210)",
          str(rd["band_median_at_68pct"]), "same band: hue 200-225, chroma>=60", rd["band_median_at_68pct"] is not None))

def glide_of(name):
    return next(e for e in log["events"] if e.get("type") == "glide" and abs(e["t"] - marks[name]["glide_t"]) < 1e-6)


# ---- glides (Q-74) ---------------------------------------------------------------------------------------------
glides = []
for name in ("#t1", "#t2", "#t3"):
    g = glide_of(name)
    fs, fe = int(round(w2f(g["t"]))), int(round(w2f(g["end"])))
    lo, hi = max(1, fs - 10), min(N - 1, fe + 10)
    tips = {j: frames[j].get("tip") for j in range(lo - 1, hi + 1)}
    step = {j: tipd(tips[j], tips[j - 1]) for j in range(lo, hi + 1) if tips[j] and tips[j - 1]}
    moving = [j for j, s in step.items() if s > 2.0]          # Q-74: steps of 2 px or less are noise
    s, e = moving[0] - 1, moving[-1]
    # sub-frame edges: the ease tails move < 2 px, so count from the frame before the first real step to the last
    dur = (PTS[e] - PTS[s]) * 1000
    exp = g["ms"]
    tol = max(0.10 * exp, FR)
    pts = [tips[j] for j in range(s, e + 1)]
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    L = math.hypot(x1 - x0, y1 - y0)
    dev = max(abs((x1 - x0) * (y0 - py) - (x0 - px) * (y1 - y0)) / L for px, py in pts)
    steps = [round(step[j], 1) for j in range(s + 1, e + 1)]
    big = [x for x in steps if x > 40]
    even = steps[0] <= 60 and all(max(a, b) / min(a, b) <= 2.5 for a, b in zip(big, big[1:]))
    glides.append({"target": name, "distance": g["distance"], "formula_ms": exp, "measured_ms": round(dur, 1),
                   "tolerance_ms": round(tol, 1), "max_path_dev_px": round(dev, 2), "steps_px": steps})
    V.append((f"Q-74 glide D={g['distance']:.0f}", f"{dur:.0f} ms", f"{exp:.0f} +-{tol:.0f} ms", abs(dur - exp) <= tol))
    V.append((f"Q-74 straight D={g['distance']:.0f}", f"{dev:.1f} px", "<= 3 px", dev <= 3))
    V.append((f"Q-74 evenness D={g['distance']:.0f} (WARN)", f"first {steps[0]} px", "first<=60, neighbours<=2.5x", even))
    cen = sum(k * x for k, x in enumerate(steps)) / sum(steps)
    sym = abs(cen - (len(steps) - 1) / 2) <= 1.0
    V.append((f"Q-74 symmetric speed D={g['distance']:.0f}", f"speed centroid at step {cen:.1f} of 0..{len(steps) - 1}",
              "in the middle +-1 step", sym))
res["glides"] = glides

# ---- press -> click, reaction, press spring --------------------------------------------------------------------
downs = [e for e in log["page_log"] if e["k"] == "down"]
delays = []
for name in clicks:
    d = [e for e in downs if e["id"] == name.lstrip("#") and e["t"] >= marks[name]["t"] * 1000 - 5]
    if d:
        delays.append(d[0]["t"] - round(marks[name]["t"] * 1000))
res["press_to_mousedown_ms"] = delays
V.append(("Q-76 real click after ripple start", f"{min(delays)}-{max(delays)} ms (n={len(delays)})", "120 ms +-1 frame",
          all(abs(x - 120) <= FR for x in delays)))
reacts = []
for name, key in (("#t1", "t1"), ("#t2", "t2"), ("#t3", "t3")):
    rp = ripple_of(name)
    fr = next(j for j in range(rp["first"] - 5, N) if frames[j][key] > 10)
    reacts.append(fr - rp["first"])
V.append(("Q-76 ring painted before the page reacts", f"reaction {min(reacts)}-{max(reacts)} frames after ring",
          ">= 1 frame", min(reacts) >= 1))
def bez_y(x, p1=(0.2, 0.7), p2=(0.3, 1.0)):
    lo, hi = 0.0, 1.0
    for _ in range(40):
        m = (lo + hi) / 2
        xm = 3 * (1 - m) ** 2 * m * p1[0] + 3 * (1 - m) * m ** 2 * p2[0] + m ** 3
        lo, hi = (m, hi) if xm < x else (lo, m)
    m = (lo + hi) / 2
    return 3 * (1 - m) ** 2 * m * p1[1] + 3 * (1 - m) * m ** 2 * p2[1] + m ** 3


def ring_w(t_ms):   # band width of the ring (96 px ring + ~3 px glow in the band) at animation time t
    return 96 * (0.14 + 0.86 * bez_y(min(1.0, t_ms / T.RIPPLE_MS))) + 3


def ring_t0(rp):
    """Screen time at which a ripple started, from its growth (compositor clock, same pipeline as the press)."""
    ests = []
    for k in range(rp["first"], rp["first"] + 4):
        bb = frames[k].get("band_bbox")
        if not bb:
            continue
        w = bb[2] - bb[0] + 1
        if w >= 95:
            break
        lo, hi = 0.0, T.RIPPLE_MS
        for _ in range(30):
            m = (lo + hi) / 2
            lo, hi = (m, hi) if ring_w(m) < w else (lo, m)
        ests.append(PTS[k] - lo / 1000.0)
    ests.sort()
    return ests[len(ests) // 2] if ests else PTS[rp["first"]]


press = []
for name in ("#t1", "#t2", "#t3"):
    pw = ring_t0(ripple_of(name))               # press start as the screen shows it (ring and press start together)
    g_end = max(j for j in range(w2f(glide_of(name)["t"]), ripple_of(name)["first"])
                if frames[j].get("tip") and frames[j - 1].get("tip") and tipd(frames[j]["tip"], frames[j - 1]["tip"]) > 2)
    ks = [k for k in range(N) if pw - 0.1 <= PTS[k] <= pw + 0.4]
    hs = [(k, frames[k]["bbox"][3] - frames[k]["bbox"][1]) for k in ks]
    h0 = max(set(h for k, h in hs[:3]), key=[h for k, h in hs[:3]].count)
    smalls = [k for k, h in hs if h < h0]
    mins = [k for k, h in hs if h == min(x for _, x in hs)]
    base_tip = frames[ks[0]]["tip"]
    tipmove = max(tipd(frames[k]["tip"], base_tip) for k in ks)
    press.append({"target": name, "h_series": [h for k, h in hs], "t_ms": [round((PTS[k] - pw) * 1000) for k in ks],
                  "rest_h": h0, "min_h": min(h for k, h in hs), "scale": round(min(h for k, h in hs) / h0, 3),
                  "smallest_ms": [round((PTS[mins[0]] - pw) * 1000), round((PTS[mins[-1]] - pw) * 1000)],
                  "smaller_ms": [round((PTS[smalls[0]] - pw) * 1000), round((PTS[smalls[-1]] - pw) * 1000)],
                  "tip_move_px": round(tipmove, 1),
                  "rest_before_press_ms": round((pw - PTS[g_end]) * 1000),
                  "ripple_first_frame_ms": round((PTS[ripple_of(name)["first"]] - pw) * 1000)})
res["press"] = press
sc = [p["scale"] for p in press]
V.append(("Q-75 press scale", f"{min(sc)}-{max(sc)}", "0.85 (+-1 px of 22)", all(abs(s * 21 - 0.85 * 21) <= 1.2 for s in sc)))
# theory (scale curve sampled on the display): below 20.5/21 from ~35 ms to ~150 ms, smallest (<= 18.5/21) ~75-135 ms
ok = all(p["smallest_ms"][0] <= 110 + FR and p["smallest_ms"][1] >= 110 - FR and p["smaller_ms"][1] <= 250 for p in press)
V.append(("Q-75 smallest arrow around 110 ms, back by 250 ms", "; ".join(
    f"smallest {p['smallest_ms']} ms, smaller {p['smaller_ms']} ms" for p in press), "110 ms +-1 frame; <= 250 ms", ok))
rb = [p["rest_before_press_ms"] for p in press]
V.append(("Q-74 rest between glide end and press", f"{min(rb)}-{max(rb)} ms", "120 ms +-1 frame (from the last moving frame)",
          all(120 - FR <= x <= 120 + 2 * FR for x in rb)))
tm = [p["tip_move_px"] for p in press]
V.append(("Q-75 tip still during press", f"<= {max(tm)} px", "<= 1 px", max(tm) <= 1.0))

# ---- typing, paste ---------------------------------------------------------------------------------------------
ft = int(round(w2f(marks["type"]["t"])))
ink = [frames[j]["typ_ink"] for j in range(ft - 3, min(N, ft + 45))]
res["typing_ink"] = ink
chg = [k for k in range(1, len(ink)) if ink[k] != ink[k - 1]]
n = marks["type"]["chars"]
tv = (PTS[ft - 3 + chg[-1]] - PTS[ft - 3 + chg[0]]) * 1000
V.append(("Q-79 typing first->last char (video)", f"{tv:.0f} ms for {n} chars", f"{T.typing_duration_ms(n):.0f} ms +-1 frame",
          abs(tv - T.typing_duration_ms(n)) <= FR + 1))
inputs = [e["t"] for e in log["page_log"] if e["k"] == "input" and e["id"] == "typ"]
iv = np.diff(inputs)
res["typing_intervals_ms"] = [int(x) for x in iv]
V.append(("Q-79 mean interval (page events)", f"{iv.mean():.2f} ms (min {iv.min()}, max {iv.max()})", "37 +-3 ms; each +-1 frame",
          abs(iv.mean() - 37) <= 3 and all(abs(x - 37) <= FR for x in iv)))
fp = int(round(w2f(marks["paste"]["t"])))
pink = [frames[j]["pst_ink"] for j in range(fp - 8, min(N, fp + 20))]
pch = [k for k in range(1, len(pink)) if abs(pink[k] - pink[k - 1]) > 40]   # the caret is ~26 px of ink
res["paste_ink"] = pink
one = len(pch) == 1 and pink[pch[0] - 1] < 40 and pink[pch[0]] >= 0.9 * max(pink)
V.append(("Q-55 paste appears at once", f"{len(pch)} ink change(s): {[(pink[k-1], pink[k]) for k in pch]}",
          "empty -> full in one frame", one))

# ---- pressed state on a slow click; ripple on a blocked main thread ----------------------------------------------
fs = int(round(w2f(marks["slow"]["click_t"])))
sl = [round(frames[j]["slow_luma"]) for j in range(fs - 3, min(N, fs + 75), 3)]
res["slow_click_button_luma_every_100ms"] = sl
base = sl[0]
darker = [k for k, v in enumerate(sl) if v < base - 20 and k >= 6]   # the ripple tints the button for ~0.4 s
V.append(("Q-48 pressed state while the page is slow", f"darker from ~{darker[0] * 100 - 100} to ~{darker[-1] * 100 - 100} ms",
          "shown from ~0.5 s until the reaction at 2.0 s", 500 <= darker[0] * 100 <= 800 and 1900 <= darker[-1] * 100 <= 2300))
fast = []
for name in ("#t1", "#t2", "#t3"):
    k = w2f(marks[name]["click_t"] + 0.8)
    fast.append(frames[k]["bbox"][3] - frames[k]["bbox"][1])
V.append(("Q-48 no pressed state after a fast reaction", f"cursor box heights 0.8 s after fast clicks: {fast}",
          "rest size (no busy pointer)", max(fast) <= 22))
bz = ripple_of("busy")["visible_ms"]
V.append(("Q-45 ripple with a 1.5 s blocked main thread", f"{bz} ms", "<= 900 ms", bz <= 900))

# ---- top layer, navigation continuity, one cursor ----------------------------------------------------------------
fd = int(round(w2f(marks["dlg"]["click_t"]))) + 15
wl = frames[fd].get("white_below_tip")
V.append(("cursor above a modal dialog", f"fill luma {wl:.0f}", "> 240 (not under the backdrop)", wl > 240))
navs = []
for j in range(1, N):
    a, b = frames[j - 1]["bg"], frames[j]["bg"]
    if sum(abs(x - y) for x, y in zip(a, b)) > 20:
        ta, tb = frames[j - 1].get("tip"), frames[j].get("tip")
        navs.append({"frame": j, "bg": b, "jump_px": round(tipd(ta, tb), 1) if ta and tb else None})
res["background_changes"] = navs
docs = [x for k, x in enumerate(navs) if abs(x["bg"][0] - 237) < 5 or (k > 0 and abs(navs[k - 1]["bg"][0] - 237) < 5)]
V.append(("Q-22/Q-43 cursor in the first frame of each new document",
          f"{len(docs)} loads, jumps {[x['jump_px'] for x in docs]} px", "present, 0-1 px", len(docs) >= 2 and
          all(x["jump_px"] is not None and x["jump_px"] <= 1 for x in docs)))
missing = sum(1 for r in frames if "tip" not in r)
V.append(("Q-43 cursor present in every frame", f"{missing} frames without cursor", "0", missing == 0))
stray = max(r.get("stray", 0) for r in frames)
res["stray_frames"] = [(r["i"], r["stray"], r["stray_box"]) for r in frames if r.get("stray")][:20]
V.append(("Q-40 exactly one cursor", f"max {stray} cursor-like px away from the cursor", "0", stray == 0))
bb = frames[ripple_of("#t1")["first"] - 3]["bbox"]
ow, oh = bb[2] - bb[0] + 1, bb[3] - bb[1] + 1
V.append(("Q-73 arrow size (dark outline box, light page)", f"{ow}x{oh} px", "about 16x22 +-2 px",
          14 <= ow <= 18 and 20 <= oh <= 24))
wb = frames[ripple_of("#t4")["first"] - 3].get("white_box")
V.append(("Q-73 white fill vs cursor.png (12x18 px, 105 px > luma 120)", f"{wb[0]}x{wb[1]} px, {wb[2]} px",
          "+-2 px", wb is not None and abs(wb[0] - 12) <= 2 and abs(wb[1] - 18) <= 2))

# ---- crops for the visual comparison with the baselines --------------------------------------------------------
rdk = ripple_of("#t4")
tx, ty = marks["#t4"]["x"], marks["#t4"]["y"]
crops = {"ripple-peak-dark": (rdk["frame_68pct"], 480, 270, int(tx - 349), int(ty - 139)),
         "cursor-dark": (rdk["first"] - 3, 480, 270, int(tx - 349), int(ty - 139)),
         "ripple-peak-light": (ripple_of("#t1")["frame_68pct"], 480, 270, int(marks["#t1"]["x"] - 240),
                               int(marks["#t1"]["y"] - 135)),
         "pressed-slow": (fs + 30, 480, 270, 520, 285)}
for nm, (fr, w, h, x, y) in crops.items():
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"{OUT}/take.mkv", "-vf",
                    f"select=eq(n\\,{fr}),crop={w}:{h}:{max(0, x)}:{max(0, y)}", "-frames:v", "1", f"{OUT}/{nm}.png"])
res["verdicts"] = [{"check": c, "measured": m, "tolerance": t, "pass": bool(p)} for c, m, t, p in V]
json.dump(res, open(f"{OUT}/measure.json", "w"), indent=1)
lines = [f"{'PASS' if p else 'FAIL'}  {c}: {m}  [{t}]" for c, m, t, p in V]
open(f"{OUT}/verdicts.txt", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
print("checks:", json.dumps(log["checks"]))
