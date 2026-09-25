#!/usr/bin/env python3
"""Fake recorder: a synthetic raw take in M1's run-directory layout (vc-v1-m1-env docs/interfaces.md sections 4-5).

The take is built for the deterministic retake-loop tests: the real cutter (M4, bin/vc-cut) and the real QA gate
(M6a, bin/vc-gate) accept it, and optional defects make the gate fail in a known way.

    build_take(run_dir, steps, *, landing_hold=1.2, defects=None, t0=1727200000.0,
               url="https://app.example.test/") -> manifest dict

steps: [{"name": str, "actions": [{"kind": "click"|"type", "label": str, "hold": float,
                                   "hold_kind": "step"|"dialog"|"reading"|"final"}]}]

Timeline (video seconds, 30 fps, every event on a frame boundary):
  0.0-0.5  off camera (before the first mark, never part of a clip)
  mark 1 + landing hold (landing_hold s, kind "landing")
  per action: sine-eased straight glide (Fitts' law of Q-74 clamped to 0.4-0.6 s) to a deterministic target, 0.12 s
  rest, then
    click: press (ripple, Q-76) -> view change 0.2 s later -> hold starts after the ripple (press + 0.567 s)
    type:  0.8 s of typing into a field at the target (no ripple) -> view change 0.2 s later -> hold at end + 0.3 s
  then the hold (screen completely still, cursor resting at the target). Each step starts with a mark named exactly
  the step name, at the start of its first glide (step 1: after the off-camera part).

Frames are rendered in pure Python (rgb24) and piped to ffmpeg (libx264 High, yuv420p, CRF 18, preset veryfast,
+faststart). Identical consecutive frames are rendered once.

Defects (dict, optional):
  {"black": "<step>"}       1.5 s of black inserted at that step's mark, before its first glide: no click within
                            2.4 s before it, so it is no skeleton after a click and the cutter's Q-23 cover may not
                            hide it (a black right after a click is covered with the pre-click frame, legitimately,
                            and the viewer never sees it; a longer one is first shrunk by the standstill cut). The
                            black carries 24 sparse 4x4 px specks (0.03 % of the picture) aligned to the cutter's
                            160x90 thumbnail grid: the gate (Q-20: luma range < 12 at 480 px, 0.1 % extremes
                            ignored) sees solid black, but the cutter's blank test (160x90 thumbnail) does not, so
                            the stretch survives the cut and the gate FAILs Q-20 on that clip.
  {"black_pure": "<step>"}  the same stretch perfectly black; the real cutter drops it as a blank (Q-53).
  {"no_ripple": "<step>"}   the ripple of that step's first click is not drawn (gate FAIL Q-44 with an event log).
  {"slow_black": "<step>"}  the step's first click loads for 3.4 s (SLOW_F) before its new view: black (solid for the
                            gate's Q-20, with the `black` specks so the cutter does not drop it as blank) that
                            flickers between luma 0 and 8 each frame, so the standstill cut does not shorten it.
                            The cutter bridges it (hold 2.0 s + fade); the gate PASSes.
  {"slow_skeleton": "<step>"} the same 3.4 s load as grey placeholder bars with a soft moving shimmer band.

CLI: python3 synth_video.py RUN_DIR STEPS_JSON [--defects JSON] [--landing-hold S] [--t0 EPOCH] [--url URL]
     (STEPS_JSON / JSON: a file path or an inline JSON string)
"""
import datetime
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys

W, H, FPS = 1920, 1080, 30
OFFCAM_F = 15            # 0.5 s before the first mark
REST_F = 4               # 0.12 s rest between the glide and the press (Q-74)
VIEW_AFTER_F = 6         # view change 0.2 s after the press / the end of typing
RIPPLE_F = 16            # ripple frames after the press (520 ms)
HOLD_AFTER_CLICK_F = 17  # the hold starts once the ripple is gone
TYPE_F = 24              # 0.8 s of typing
HOLD_AFTER_TYPE_F = 9
BLACK_F = 45             # 1.5 s black defect
SLOW_F = 102             # 3.4 s slow load after a click (slow_black / slow_skeleton)

# The demo cursor's arrow fill (vc-v1-m6a-gate gate/vcgate/checks/cursor.py ARROW, the fill of the spec's
# cursor.png at 1:1); tip = top left.
ARROW = [
    "##..........",
    "###.........",
    "####........",
    "#####.......",
    "######......",
    "#######.....",
    "#########...",
    "##########..",
    "###########.",
    "############",
    "########....",
    "#######.....",
    "###...##....",
    "##.....#....",
    ".......##...",
    ".......##...",
    "........#...",
]
RIPPLE_RGB = (77, 163, 255)   # #4da3ff (Q-76)


# ----------------------------------------------------------------------------- sprites

def _cursor_sprite():
    """List of (dx, dy, r, g, b, a256) relative to the tip: white fill, #111 outline (2 px, the gate template's
    band), soft black shadow (1 px down, ~3 px blur, 60 %)."""
    fill = {(x, y) for y, row in enumerate(ARROW) for x, c in enumerate(row) if c == "#"}
    shape = set(fill)
    for _ in range(2):   # 4-neighbour dilation twice = the gate template's outline band
        shape |= {(x + dx, y + dy) for x, y in shape for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
    outline = shape - fill
    # shadow: the silhouette 1 px down, box-blurred (radius 1, twice ~ 3 px blur)
    sh = {(x, y + 1): 1.0 for x, y in shape}
    for _ in range(2):
        nb = {}
        keys = {(x + dx, y + dy) for x, y in sh for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        for x, y in keys:
            nb[(x, y)] = sum(sh.get((x + dx, y + dy), 0.0) for dx in (-1, 0, 1) for dy in (-1, 0, 1)) / 9.0
        sh = nb
    out = []
    for (x, y), v in sorted(sh.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if (x, y) in shape:
            continue
        a = int(round(0.6 * v * 256))
        if a > 0:
            out.append((x, y, 0, 0, 0, a))
    for x, y in sorted(outline, key=lambda p: (p[1], p[0])):
        out.append((x, y, 17, 17, 17, 256))
    for x, y in sorted(fill, key=lambda p: (p[1], p[0])):
        out.append((x, y, 255, 255, 255, 256))
    return out


def _bezier_ease(p, x1=0.2, y1=0.7, x2=0.3, y2=1.0):
    """cubic-bezier(x1, y1, x2, y2) at progress p (Q-76 easing)."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0

    def bx(u):
        return 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3

    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if bx(mid) < p:
            lo = mid
        else:
            hi = mid
    u = (lo + hi) / 2
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3


def _ripple_opacity(p):
    """0 -> 1 by 16 %, 1 to 42 %, 0.9 at 68 %, 0 at the end (Q-76)."""
    if p <= 0.16:
        return p / 0.16
    if p <= 0.42:
        return 1.0
    if p <= 0.68:
        return 1.0 - 0.1 * (p - 0.42) / 0.26
    return max(0.0, 0.9 * (1.0 - p) / 0.32)


def _ripple_sprites():
    """Ripple frame k (k frames after the press, k = 0..RIPPLE_F-1): list of (dx, dy, r, g, b, a256) around the
    click point. A #4da3ff ring, 5 px stroke, 38 % fill, growing from ~15 px to ~92 px across over 520 ms."""
    sprites = []
    stroke = 5.0
    for k in range(RIPPLE_F):
        p = k / FPS / 0.52
        op = _ripple_opacity(p)
        d = 15.0 + (96.0 - 15.0) * _bezier_ease(p)
        R = d / 2.0
        ent = []
        if op > 0:
            n = int(math.ceil(R)) + 1
            for dy in range(-n, n + 1):
                for dx in range(-n, n + 1):
                    r = math.hypot(dx, dy)
                    cov_s = max(0.0, min(1.0, min(r - (R - stroke), R - r) + 0.5))
                    cov_f = max(0.0, min(1.0, (R - stroke) - r + 0.5)) * 0.38
                    a = op * (cov_s + cov_f * (1.0 - cov_s))
                    a256 = int(round(a * 256))
                    if a256 > 0:
                        ent.append((dx, dy) + RIPPLE_RGB + (a256,))
        sprites.append(ent)
    return sprites


# ----------------------------------------------------------------------------- views

def _fill(buf, x, y, w, h, col):
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return
    row = bytes(col) * (x1 - x0)
    for yy in range(y0, y1):
        o = (yy * W + x0) * 3
        buf[o:o + len(row)] = row


def _box(buf, x, y, w, h, fill, border, t=2):
    _fill(buf, x, y, w, h, border)
    _fill(buf, x + t, y + t, w - 2 * t, h - 2 * t, fill)


TINTS = [(226, 232, 240), (230, 238, 226), (242, 232, 222), (236, 228, 238), (228, 236, 236), (240, 238, 222)]
ACCENTS = [(214, 226, 208), (238, 222, 204), (226, 214, 230), (212, 228, 226), (232, 228, 206)]


def _view(seed, target=None, kind=None):
    """A light synthetic page: header, sidebar, panels with grey text lines, one large block; a button (click) or a
    text field (type) under the next target. Low-contrast greys only, no blue: nothing looks like a pointer or a
    ripple."""
    rnd = random.Random(seed)
    bgc = (246 - rnd.randrange(4), 247 - rnd.randrange(4), 249 - rnd.randrange(4))
    buf = bytearray(bytes(bgc) * (W * H))
    tint = TINTS[rnd.randrange(len(TINTS))]
    hh = 56 + rnd.randrange(24)
    _fill(buf, 0, 0, W, hh, tint)
    _fill(buf, 28, hh // 2 - 9, 140 + rnd.randrange(200), 18, (168, 170, 176))
    for i in range(rnd.randrange(3, 6)):
        _fill(buf, W - 140 - i * 130, hh // 2 - 7, 96, 14, (196, 198, 204))
    sw = 200 + rnd.randrange(100) if rnd.random() < 0.75 else 0
    if sw:
        _fill(buf, 0, hh, sw, H - hh, (236, 238, 242))
        for i in range(rnd.randrange(6, 11)):
            _fill(buf, 28, hh + 36 + i * 46, 90 + rnd.randrange(90), 12, (196, 198, 204))
    x_lo = sw + 40
    # a large block (makes views clearly distinct)
    bw, bh = 500 + rnd.randrange(600), 160 + rnd.randrange(260)
    bx, by = x_lo + rnd.randrange(max(1, W - 60 - x_lo - bw)), hh + 30 + rnd.randrange(max(1, H - hh - 90 - bh))
    _box(buf, bx, by, bw, bh, ACCENTS[rnd.randrange(len(ACCENTS))], (200, 202, 206))
    # panels with text lines
    for _ in range(rnd.randrange(3, 6)):
        pw, ph = 320 + rnd.randrange(420), 150 + rnd.randrange(220)
        px = x_lo + rnd.randrange(max(1, W - 40 - x_lo - pw))
        py = hh + 24 + rnd.randrange(max(1, H - hh - 60 - ph))
        _box(buf, px, py, pw, ph, (255, 255, 255), (208, 210, 214))
        _fill(buf, px + 20, py + 20, min(pw - 40, 120 + rnd.randrange(160)), 14, (150, 152, 158))
        yy = py + 52
        while yy + 10 < py + ph - 16:
            _fill(buf, px + 20, yy, min(pw - 40, 80 + rnd.randrange(pw)), 9, (198, 200, 206))
            yy += 24
    if target is not None:
        tx, ty = target
        if kind == "type":
            _box(buf, tx - 360, ty - 24, 400, 48, (255, 255, 255), (170, 172, 178))
        else:
            _box(buf, tx - 80, ty - 22, 160, 44, (222, 230, 218), (176, 186, 172))
            _fill(buf, tx - 44, ty - 6, 88, 12, (150, 160, 146))
    return buf


def _typed(view, target, n):
    """The field at `target` holding n typed characters (dark grey glyph blocks)."""
    buf = bytearray(view)
    tx, ty = target
    for i in range(n):
        x = tx - 344 + i * 12
        if x + 9 > tx - 24:
            break
        _fill(buf, x, ty - 8, 9, 16, (70, 72, 78))
    return buf


def _black(pure, level=0):
    buf = bytearray(bytes([level]) * (W * H * 3))
    if not pure:
        # 24 specks of 4x4 px, each fully inside one 12x12 cell of the cutter's 160x90 thumbnail
        for j in range(4):
            for i in range(6):
                _fill(buf, 12 * (20 + 24 * i) + 4, 12 * (15 + 18 * j) + 4, 4, 4, (255, 255, 255))
    return buf


def _slow(kind, k):
    """Frame k of a slow load: flickering black (specks as in _black) or a skeleton with a moving shimmer band."""
    if kind == "slow_black":
        return _black(False, 8 if k % 2 else 0)
    buf = bytearray(bytes((226, 227, 229)) * (W * H))
    _fill(buf, 0, 0, W, 64, (214, 215, 218))
    for i in range(8):
        _fill(buf, 360, 120 + i * 110, 900 - (i % 3) * 180, 40, (212, 213, 216))
    x0 = (k * 60) % (W + 240) - 240
    for i, v in enumerate((230, 234, 238, 242, 242, 238, 234, 230)):     # soft steps: no sharp edge (< 24 luma)
        xa, xb = max(0, x0 + 30 * i), min(W, x0 + 30 * (i + 1))
        if xb > xa:
            _fill(buf, xa, 0, xb - xa, H, (v, v, v + 2))
    return buf


def _blend(buf, x0, y0, sprite):
    for dx, dy, r, g, b, a in sprite:
        x, y = x0 + dx, y0 + dy
        if 0 <= x < W and 0 <= y < H:
            o = (y * W + x) * 3
            if a >= 256:
                buf[o] = r
                buf[o + 1] = g
                buf[o + 2] = b
            else:
                ia = 256 - a
                buf[o] = (buf[o] * ia + r * a) >> 8
                buf[o + 1] = (buf[o + 1] * ia + g * a) >> 8
                buf[o + 2] = (buf[o + 2] * ia + b * a) >> 8


# ----------------------------------------------------------------------------- plan

def _target(step_i, act_i, label, prev):
    salt = 0
    while True:
        h = int(hashlib.sha1(f"{step_i}|{act_i}|{label}|{salt}".encode()).hexdigest(), 16)
        x = 420 + h % 1140           # well inside the frame (and clear of a typing field's left end)
        y = 190 + (h >> 20) % 700
        if prev is None or 220 <= math.hypot(x - prev[0], y - prev[1]) <= 800:
            return (x, y)
        salt += 1


def _glide_frames(a, b):
    """Q-74 Fitts' law (W 40 px), clamped to 0.4-0.6 s for the fake."""
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    ms = 0.67 * (-385 + 250 * math.log2(d / 40.0 + 1))
    return int(round(min(0.6, max(0.4, ms / 1000.0)) * FPS))


def _ffmpeg():
    for p in (os.environ.get("VC_FFMPEG"), os.path.expanduser("~/.local/bin/ffmpeg"), shutil.which("ffmpeg")):
        if p and os.path.exists(p):
            return p
    raise RuntimeError("ffmpeg not found (set VC_FFMPEG)")


def build_take(run_dir, steps, *, landing_hold=1.2, defects=None, t0=1727200000.0,
               url="https://app.example.test/"):
    defects = dict(defects or {})
    unknown = set(defects) - {"black", "black_pure", "no_ripple", "slow_black", "slow_skeleton"}
    if unknown:
        raise ValueError(f"unknown defects: {sorted(unknown)}")
    names = [s["name"] for s in steps]
    for k, v in defects.items():
        if v not in names:
            raise ValueError(f"defect {k}: no step named {v!r}")
    if not steps or not all(s.get("actions") for s in steps):
        raise ValueError("every step needs at least one action")
    if "no_ripple" in defects:
        s = steps[names.index(defects["no_ripple"])]
        if s["actions"][0].get("kind", "click") != "click":
            raise ValueError("defect no_ripple: the step's first action is not a click")
    os.makedirs(run_dir, exist_ok=True)

    # ---- actions and their targets
    acts = []
    prev = (960, 560)
    start_pos = prev
    for si, s in enumerate(steps):
        for ai, a in enumerate(s["actions"]):
            kind = a.get("kind", "click")
            if kind not in ("click", "type"):
                raise ValueError(f"action kind {kind!r}")
            tgt = _target(si + 1, ai, a.get("label", ""), prev)
            acts.append({"step": si + 1, "name": s["name"], "ai": ai, "kind": kind, "label": a.get("label", ""),
                         "hold": float(a["hold"]), "hold_kind": a.get("hold_kind", "step"), "target": tgt})
            prev = tgt

    # ---- per-frame states: (view key, cursor (x, y), ripple (k) or None)
    frames = []
    events = []

    def ev(typ, f, **kw):
        vt = f / FPS
        d = {"type": typ, "t": round(t0 + vt, 6), "video_t": round(vt, 6), "frame": f}
        d.update(kw)
        events.append(d)
        return d

    marks, holds = [], []
    view = ("v", 0, 0)
    cur = start_pos
    ev("start", 0, run_id=os.path.basename(os.path.normpath(run_dir)), synthetic=True)

    def emit(n, vkey=None, pos=None, rip=None):
        for _ in range(n):
            frames.append((vkey or view, pos or cur, rip))

    emit(OFFCAM_F)
    marks.append(ev("mark", len(frames), name=steps[0]["name"], step=1, url=url))
    hf = int(round(landing_hold * FPS))
    if hf > 0:
        holds.append(ev("hold", len(frames), kind="landing", seconds=float(landing_hold), step=1))
        emit(hf)
    step_seen = {1}
    for i, a in enumerate(acts):
        if a["step"] not in step_seen:
            step_seen.add(a["step"])
            marks.append(ev("mark", len(frames), name=a["name"], step=a["step"], url=url))
        black = a["ai"] == 0 and a["name"] in (defects.get("black"), defects.get("black_pure"))
        if black:
            emit(BLACK_F, vkey=("black", a["name"] == defects.get("black_pure")))
        # glide
        g = len(frames)
        tgt = a["target"]
        n = _glide_frames(cur, tgt)
        for k in range(1, n + 1):
            p = (1 - math.cos(math.pi * k / n)) / 2
            emit(1, pos=(int(round(cur[0] + (tgt[0] - cur[0]) * p)), int(round(cur[1] + (tgt[1] - cur[1]) * p))))
        cur = tgt
        emit(REST_F)
        c = len(frames)
        first = a["ai"] == 0
        nxt = ("v", i + 1, 0)
        if a["kind"] == "click":
            ripple = not (first and defects.get("no_ripple") == a["name"])
            ev("click", c, step=a["step"], label=a["label"], x=tgt[0], y=tgt[1], glide_t=round(t0 + g / FPS, 6),
               click_t=round(t0 + c / FPS + 0.12, 6))
            slow = next((k for k in ("slow_black", "slow_skeleton") if first and defects.get(k) == a["name"]), None)
            seq = [view] * VIEW_AFTER_F + ([("load", slow, j) for j in range(SLOW_F)] if slow else []) + [nxt]
            n_pre = max(HOLD_AFTER_CLICK_F, len(seq))
            for k in range(n_pre):
                vk = seq[min(k, len(seq) - 1)]
                emit(1, vkey=vk, rip=(k if ripple and k < RIPPLE_F else None))
        else:
            ev("typing", c, step=a["step"], label=a["label"], end=round(t0 + (c + TYPE_F) / FPS, 6),
               x=tgt[0], y=tgt[1], chars=27)
            for k in range(1, TYPE_F + 1):
                emit(1, vkey=("v", i, int(math.ceil(27 * k / TYPE_F))))
            typed = ("v", i, 27)
            seq = [typed] * VIEW_AFTER_F + [nxt]
            for k in range(max(HOLD_AFTER_TYPE_F, len(seq))):
                emit(1, vkey=seq[min(k, len(seq) - 1)])
        view = nxt
        kind = a["hold_kind"]
        holds.append(ev("hold", len(frames), kind=kind, seconds=a["hold"], step=a["step"]))
        emit(int(round(a["hold"] * FPS)))
    nfr = len(frames)
    events.sort(key=lambda e: (e["frame"], e["type"] != "start", e["type"] != "mark"))

    # ---- render
    views = {}
    targets = {i: (a["target"], a["kind"]) for i, a in enumerate(acts)}

    def bg(key):
        if key not in views:
            if key[0] == "black":
                views[key] = _black(key[1])
            elif key[0] == "load":
                return _slow(key[1], key[2])
            elif key[2] == 0:
                t, k = targets.get(key[1], (None, None))
                views[key] = _view(f"{key[1]}|{steps[0]['name']}|view", t, k)
            else:
                views[key] = _typed(bg(("v", key[1], 0)), targets[key[1]][0], key[2])
        return views[key]

    csprite = _cursor_sprite()
    rsprites = _ripple_sprites()
    raw = os.path.join(run_dir, "raw.mp4")
    tmp = raw + ".part.mp4"
    cmd = [_ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
           "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(FPS), "-movflags", "+faststart", tmp]
    log = open(os.path.join(run_dir, "recorder.log"), "wb")
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
    last_state, last_buf = None, None
    try:
        for i, st in enumerate(frames):
            if st != last_state:
                vkey, pos, rip = st
                buf = bytearray(bg(vkey))
                # the ripple is centred on the press point: the last click's target
                if rip is not None:
                    _blend(buf, pos[0], pos[1], rsprites[rip])
                _blend(buf, pos[0], pos[1], csprite)
                last_state, last_buf = st, buf
                # drop typed variants of past views to keep memory flat
                for k in [k for k in views if k[0] == "v" and k[1] < vkey[1] - 1] if vkey[0] == "v" else []:
                    del views[k]
            p.stdin.write(last_buf)
        p.stdin.close()
    except BrokenPipeError:
        pass
    rc = p.wait()
    log.close()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc}); see {os.path.join(run_dir, 'recorder.log')}")
    os.replace(tmp, raw)

    # ---- event log + manifest (M1 docs/interfaces.md section 5)
    with open(os.path.join(run_dir, "events.jsonl"), "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    dur = nfr / FPS
    segs = []
    for k, m in enumerate(marks):
        ef = marks[k + 1]["frame"] if k + 1 < len(marks) else nfr
        segs.append({"index": k, "step": m["step"], "name": m["name"], "start_t": m["video_t"],
                     "end_t": round(ef / FPS, 6), "start_frame": m["frame"], "end_frame": ef,
                     "holds": [h for h in holds if m["frame"] <= h["frame"] < ef]})

    def iso(t):
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()

    man = {"run_id": os.path.basename(os.path.normpath(run_dir)), "raw": "raw.mp4", "t0": t0, "fps": FPS,
           "width": W, "height": H, "duration": round(dur, 6), "frames": nfr, "started": iso(t0),
           "stopped": iso(t0 + dur), "synthetic": {"defects": defects, "landing_hold": landing_hold},
           "marks": marks, "holds": holds, "events": events, "segments": segs}
    with open(os.path.join(run_dir, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1)
    return man


def _load(s):
    if os.path.isfile(s):
        with open(s) as fh:
            return json.load(fh)
    return json.loads(s)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Build a synthetic raw take (fake recorder).")
    ap.add_argument("run_dir")
    ap.add_argument("steps", help="steps JSON: a file path or an inline JSON string")
    ap.add_argument("--defects", help="defects JSON: a file path or an inline JSON string")
    ap.add_argument("--landing-hold", type=float, default=1.2)
    ap.add_argument("--t0", type=float, default=1727200000.0)
    ap.add_argument("--url", default="https://app.example.test/")
    a = ap.parse_args(argv)
    man = build_take(a.run_dir, _load(a.steps), landing_hold=a.landing_hold,
                     defects=_load(a.defects) if a.defects else None, t0=a.t0, url=a.url)
    print(json.dumps({"run_dir": os.path.abspath(a.run_dir), "duration": man["duration"], "frames": man["frames"],
                      "marks": [m["name"] for m in man["marks"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
