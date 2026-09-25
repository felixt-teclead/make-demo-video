"""Viewer review (owner decision 2026-09-25): the "by eye" check of the delivered video, after a gate PASS and before
delivery. It replaces the old last-frame check (F-12) and extends it:

1. sample(): event-driven frames from the cut record (clicks, covers / speed-ups, pans, holds, clip joins, first and
   last frame, 1 fps in between) -> contact sheets with burned-in timestamps, plus zoom sheets around every click;
2. build_request(): the sheets, the steps' expected states and the checklist prompt for the reviewing model;
3. judge(): a blocker fails the take (like a gate FAIL), minors go into the report; the review never passes a take
   the gate failed; each blocker becomes a candidate FIXES-LEDGER entry.

Host python3, stdlib only (C-19); ffmpeg does the decoding and the tiling. `run_headless()` runs the review with
`claude -p` (tests, unattended runs); the default model `orchestrator` is a handoff (docs/steps/review.md).
"""
import json
import os
import re
import shutil
import subprocess
import time

FPS_DEFAULT = 30.0
TILE_W, TILE_H, LABEL_H = 960, 540, 34          # timeline tiles: 2x2 per sheet, half resolution
ZOOM_W, ZOOM_H = 640, 360                        # zoom tiles: native pixels around the click, 3x2 per sheet
CLICK_OFFSETS = (0.0, 0.2, 0.5, 1.0)
PAN_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
SEVERITIES = ("blocker", "minor")
CATEGORIES = ("expected_missing", "hidden_content", "stray_ui", "flicker_hole", "pace", "second_pointer",
              "private_data", "other")
COMMON_CATEGORIES = ("stray_ui", "flicker_hole", "second_pointer", "pace")   # scope guess for a ledger candidate


def ffmpeg():
    for p in (os.environ.get("VC_FFMPEG"), os.path.expanduser("~/.local/bin/ffmpeg"), shutil.which("ffmpeg")):
        if p and os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------------------------------------------- sampling
def _clip_offsets(rec):
    """Start of every clip on the full-video timeline (the joins' full_t, else the running sum)."""
    offs, t = [], 0.0
    joins = {tuple(j.get("between", [])): j.get("full_t") for j in rec.get("joins", []) or []}
    for i, c in enumerate(rec.get("clips", [])):
        ft = joins.get((i - 1, i))
        offs.append(float(ft) if ft is not None else t)
        t = offs[-1] + float(c.get("duration") or 0)
    return offs


def sample_times(rec):
    """Every sample as {t, why, step, zoom?: [x, y]} on the full-video timeline, sorted, one per frame."""
    fps = float(rec.get("fps") or FPS_DEFAULT)
    dur = float(rec.get("full_duration") or 0)
    last = max(0.0, dur - 1.0 / fps)
    out = []

    def add(t, why, step, zoom=None, prio=1):
        t = min(max(0.0, t), last)
        out.append({"t": round(t, 3), "why": why, "step": step, "zoom": zoom, "prio": prio})

    add(0.0, "first frame", rec["clips"][0]["name"] if rec.get("clips") else None)
    for off, c in zip(_clip_offsets(rec), rec.get("clips", [])):
        name, cd = c["name"], float(c.get("duration") or 0)
        if off > 0:
            add(off, "cut in", name)
            add(off - 1.0 / fps, "cut out", name)
        add(off + cd - 0.2, "step end", name)
        for e in c.get("events", []):
            if e.get("type") == "click" and not e.get("dropped"):
                lab = e.get("label") or "click"
                for d in CLICK_OFFSETS:
                    add(off + e["clip_t"] + d, f"click +{d:g} {lab}", name, zoom=[e.get("x"), e.get("y")])
                add(off + e["clip_t"] - 0.3, f"click -0.3 {lab}", name, zoom=[e.get("x"), e.get("y")], prio=2)
            elif e.get("type") in ("pan", "scroll", "camera", "reveal") and e.get("clip_end") is not None:
                a, b = off + e["clip_t"], off + e["clip_end"]
                for fr in PAN_FRACTIONS:
                    add(a + fr * (b - a), f"pan {int(fr * 100)}%", name)
                add(b + 0.5, "pan end +0.5", name)
            elif e.get("clip_end") is not None:
                add(off + e["clip_t"], f"{e.get('type')} start", name, prio=2)
                add(off + e["clip_end"], f"{e.get('type')} end", name, prio=2)
        windows = [(w, "cover") for w in c.get("covers", []) if w.get("status") in ("covered", "bridged")]
        windows += [(w, "speed-up") for w in c.get("speedups", [])]
        windows += [(w, "sped-up") for w in c.get("kept", []) if float(w.get("factor") or 1) > 1.0
                    and not c.get("speedups")]
        windows += [(w, "freeze") for w in c.get("freezes", [])] + [(w, "bridge") for w in c.get("bridges", [])]
        for w, kind in windows:
            a, b = w.get("clip_start"), w.get("clip_end")
            if a is None or b is None:
                continue
            z = [w["x"], w["y"]] if w.get("x") is not None else None
            for lab, tt in (("start", a), ("mid", (a + b) / 2), ("end", b), ("end +2f", b + 2.0 / fps),
                            ("end +0.2", b + 0.2)):
                add(off + tt, f"{kind} {lab}", name, zoom=z)
        for s in c.get("splices", []):
            if s.get("clip_t") is not None:
                add(off + s["clip_t"], "splice", name, prio=2)
        for h in c.get("holds", []):
            add(off + h["clip_t"], f"{h['kind']} hold start", name, prio=2)
            if h.get("clip_end") is not None:
                add(off + h["clip_end"], f"{h['kind']} hold end", name, prio=2)
    add(last, "last frame", rec["clips"][-1]["name"] if rec.get("clips") else None)
    # 1 fps in between: fill every gap longer than 1 s evenly, so no two samples are more than 1 s apart
    offs = _clip_offsets(rec)

    def step_at(t):
        step = None
        for off, c in zip(offs, rec.get("clips", [])):
            if off <= t + 1e-6:
                step = c["name"]
        return step
    ev_ts = sorted({s["t"] for s in out})
    for a, b in zip(ev_ts, ev_ts[1:]):
        k = int((b - a - 1e-6) // 1.0)
        for i in range(1, k + 1):
            t = a + (b - a) * i / (k + 1)
            add(t, "1 fps", step_at(t), prio=3)
    # one sample per frame: merge reasons, keep the zoom point
    byf = {}
    for s in out:
        n = int(round(s["t"] * fps))
        cur = byf.get(n)
        if cur is None:
            byf[n] = dict(s, frame=n, t=round(n / fps, 3), why=[s["why"]])
        else:
            if s["why"] not in cur["why"]:
                cur["why"].append(s["why"])
            cur["zoom"] = cur["zoom"] or s["zoom"]
            cur["prio"] = min(cur["prio"], s["prio"])
    res = [byf[n] for n in sorted(byf)]
    for s in res:
        s["why"] = "; ".join(s["why"])
    return res


# ---------------------------------------------------------------------------------------------- label bitmaps
_FONT = {  # 5x7, rows top to bottom
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    ".": "00000 00000 00000 00000 00000 01100 01100", ",": "00000 00000 00000 00000 01100 00100 01000",
    ":": "00000 01100 01100 00000 01100 01100 00000", "+": "00000 00100 00100 11111 00100 00100 00000",
    "-": "00000 00000 00000 11111 00000 00000 00000", "%": "11000 11001 00010 00100 01000 10011 00011",
    "#": "01010 01010 11111 01010 11111 01010 01010", "/": "00001 00001 00010 00100 01000 10000 10000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    ";": "00000 01100 01100 00000 01100 00100 01000", " ": "00000 00000 00000 00000 00000 00000 00000",
    "?": "01110 10001 00001 00010 00100 00000 00100", "=": "00000 00000 11111 00000 11111 00000 00000",
}
_TRANS = str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS", "…": "...", "_": "-", "×": "X"})


def _label_ppm(path, text, width, height=LABEL_H, scale=3):
    """A white-on-black label strip (P6 PPM), text in the 5x7 bitmap font above."""
    text = text.upper().translate(_TRANS)
    text = "".join(ch if ch in _FONT else "?" for ch in text)
    maxch = max(1, (width - 8) // (6 * scale))
    text = text[:maxch]
    row_bg, fg = b"\x10\x10\x14", b"\xff\xff\xff"
    y0 = (height - 7 * scale) // 2
    pix = bytearray(row_bg * width * height)
    for i, ch in enumerate(text):
        rows = _FONT[ch].split()
        for ry, bits in enumerate(rows):
            for rx, b in enumerate(bits):
                if b != "1":
                    continue
                for dy in range(scale):
                    y = y0 + ry * scale + dy
                    x0 = 6 + (i * 6 + rx) * scale
                    for dx in range(scale):
                        o = (y * width + x0 + dx) * 3
                        pix[o:o + 3] = fg
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (width, height) + bytes(pix))


# ------------------------------------------------------------------------------------------------ extraction
def _extract(ff, video, frames, out_dir):
    """Decode the video once and write each wanted frame number as <out_dir>/f<n>.jpg."""
    os.makedirs(out_dir, exist_ok=True)
    want = [n for n in sorted(set(frames)) if not os.path.exists(os.path.join(out_dir, f"f{n:05d}.jpg"))]
    if not want:
        return
    tmp = os.path.join(out_dir, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    for i in range(0, len(want), 60):                      # keep the select expression short
        chunk = want[i:i + 60]
        expr = "+".join(f"eq(n\\,{n})" for n in chunk)
        subprocess.run([ff, "-v", "error", "-y", "-i", video, "-vf", f"select={expr}", "-fps_mode", "passthrough",
                        "-q:v", "2", os.path.join(tmp, "o%04d.jpg")], check=True)
        got = sorted(os.listdir(tmp))
        for n, name in zip(chunk, got):
            os.replace(os.path.join(tmp, name), os.path.join(out_dir, f"f{n:05d}.jpg"))
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
    os.rmdir(tmp)


def _sheet(ff, tiles, cols, tw, th, out, width, height):
    """tiles: [(frame_jpg, label, crop_xy_or_None)] -> one JPEG sheet with a label strip over every tile."""
    args, parts, lay = [ff, "-v", "error", "-y"], [], []
    lab_dir = os.path.dirname(out)
    for i, (img, label, crop) in enumerate(tiles):
        lp = os.path.join(lab_dir, f"_lab{i}.ppm")
        _label_ppm(lp, label, tw)
        args += ["-i", img, "-i", lp]
        if crop:
            x = int(min(max(0, crop[0] - tw / 2), width - tw))
            y = int(min(max(0, crop[1] - th / 2), height - th))
            parts.append(f"[{2 * i}:v]crop={tw}:{th}:{x}:{y}[f{i}]")
        else:
            parts.append(f"[{2 * i}:v]scale={tw}:{th}[f{i}]")
        parts.append(f"[{2 * i + 1}:v][f{i}]vstack[t{i}]")
        lay.append(f"{(i % cols) * tw}_{(i // cols) * (th + LABEL_H)}")
    n = len(tiles)
    if n == 1:
        parts.append("[t0]null[out]")
    else:
        parts.append("".join(f"[t{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={'|'.join(lay)}:fill=black[out]")
    args += ["-filter_complex", ";".join(parts), "-map", "[out]", "-frames:v", "1", "-q:v", "3", out]
    subprocess.run(args, check=True)
    for i in range(n):
        os.remove(os.path.join(lab_dir, f"_lab{i}.ppm"))


def _lab(s):
    return f"#{s['n']} T={s['t']:.2f} {s['why']}"


def sample(run_dir, out_dir=None, video=None):
    """Sample the delivered video of a cut run and write the contact sheets. Returns (samples, sheets)."""
    ff = ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found (VC_FFMPEG, ~/.local/bin/ffmpeg or PATH)")
    with open(os.path.join(run_dir, "cut", "record.json")) as f:
        rec = json.load(f)
    video = video or os.path.join(run_dir, "full.mp4")
    if not os.path.exists(video):
        raise FileNotFoundError(f"no video {video}")
    out_dir = out_dir or os.path.join(run_dir, "review")
    fr_dir = os.path.join(out_dir, "frames")
    os.makedirs(out_dir, exist_ok=True)
    samples = sample_times(rec)
    for i, s in enumerate(samples):
        s["n"] = i + 1
    _extract(ff, video, [s["frame"] for s in samples], fr_dir)
    W, H = int(rec.get("width") or 1920), int(rec.get("height") or 1080)
    sheets = []
    for k in range(0, len(samples), 4):
        grp = samples[k:k + 4]
        p = os.path.join(out_dir, f"sheet-{k // 4 + 1:02d}.jpg")
        _sheet(ff, [(os.path.join(fr_dir, f"f{s['frame']:05d}.jpg"), _lab(s), None)
                    for s in grp], 2, TILE_W, TILE_H, p, W, H)
        sheets.append({"path": p, "kind": "timeline", "tiles": [{"n": s["n"], "t": s["t"], "why": s["why"],
                                                                 "step": s["step"]} for s in grp]})
    # zoom sheets: native pixels around each click / cover point, before -> after
    groups = {}
    for s in samples:
        if s.get("zoom") and s["zoom"][0] is not None:
            key = (s["step"], round(s["zoom"][0]), round(s["zoom"][1]))
            groups.setdefault(key, []).append(s)
    for j, ((step, x, y), grp) in enumerate(sorted(groups.items(), key=lambda kv: kv[1][0]["t"])):
        for k in range(0, len(grp), 6):
            g = grp[k:k + 6]
            p = os.path.join(out_dir, f"zoom-{j + 1:02d}{'abcdef'[k // 6]}.jpg")
            _sheet(ff, [(os.path.join(fr_dir, f"f{s['frame']:05d}.jpg"), _lab(s), (x, y)) for s in g], 3, ZOOM_W, ZOOM_H, p, W, H)
            sheets.append({"path": p, "kind": "zoom", "center": [x, y], "step": step,
                           "tiles": [{"n": s["n"], "t": s["t"], "why": s["why"], "step": s["step"]} for s in g]})
    return samples, sheets


# ------------------------------------------------------------------------------------------------- request
CHECKLIST = """You are the viewer review of a silent product demo video (1920x1080, 30 fps). A QA gate has already
passed it on numbers; you judge it the way a viewer sees it. You get contact sheets: `timeline` sheets (2x2 tiles,
half resolution) walk the whole video in order; `zoom` sheets (3x2 tiles, native pixels) show a 640x360 region
around each click before and after it. Every tile has a label: `#<n> T=<seconds> <why>`. Open EVERY sheet.

Intended, never a finding: one demo pointer (an arrow) that glides between targets; a blue ring (ripple) around it
at a click; a small "4x" badge at the bottom right during a speed-up; hard cuts between steps; the page's own
design (dark theme, hover highlight of the item under the demo pointer).
{house_style}
Check, for every step (the request lists each step's time window and expected states):
1. Expected state: its `step end` tile visibly shows every expected item. Missing = blocker.
2. Key content not hidden: the content the step is about (the card, dialog, label or text named in its expected
   states or its title) is fully readable, not clipped at a frame edge, not under a sticky/fixed header, lane-header
   column, sidebar or toolbar, not off-screen. Especially check the ends of pans. Hidden or cut = blocker.
Across the whole video, compare consecutive tiles and look for:
3. Stray UI: a native tooltip or title bubble, a hover card or popover nobody asked for, a toast/banner/cookie or
   update notice, browser UI (address bar, download bar, dev tools, scrollbars flashing), a debug overlay. A stray
   element readable in two or more tiles (about 0.3 s or longer) = blocker; a single-tile flash = minor.
4. Flicker, holes, seams: around clicks and inside `cover`/`speed-up` windows look in the zoom sheets for a disc or
   patch that shows a different page, text that is erased or half dimmed, a blank, grey or half-painted frame, a
   jump of the layout, text that vanishes and comes back. Judge how long it lasts from the neighbouring tiles
   (`end +2f` is two frames = 0.07 s after `end`): a large glitch (a big part of the view: a blank, skeleton or
   half-painted page) seen in two or more tiles at least 0.1 s apart = blocker; the same seen in one tile only while
   the tiles right after it are clean = a one-frame flash = minor; a hole or patch confined to the ripple area
   (within about 150 px of the click point) = minor.
5. Pace: text that the viewer should read is on screen too briefly to read, or motion so fast it cannot be
   followed = blocker; merely slow or slightly stepped motion = minor. Typing speed and the final hold are house
   style (above), never a pace finding.
6. A second pointer (any second arrow or hand cursor next to the demo pointer) = blocker.
{privacy}
Report only what you see in the tiles. Each finding: the time (seconds, from the tile labels; `t_end` if it spans
tiles), the region (where in the 1920x1080 frame, words plus an approximate box [x, y, w, h]), what it is, the
category, and the severity. A blocker is what an attentive viewer notices at normal speed or what hides or misleads
content; a minor is only visible when paused or looked for.

Answer with ONLY this JSON (no prose around it):
{"steps": [{"step": "<name>", "expected_visible": true, "note": ""}],
 "findings": [{"t": 0.0, "t_end": 0.0, "step": "<name>", "region": "<words>", "box": [0, 0, 0, 0],
               "category": "expected_missing|hidden_content|stray_ui|flicker_hole|pace|second_pointer|private_data|other",
               "severity": "blocker|minor", "what": "<what a viewer sees>", "tiles": [0]}]}
"""
HOUSE_STYLE = """House style (owner decisions), NEVER a finding at any severity, not even a minor:
- Fast typing: typed text appears within a fraction of a second, all at once or in a few frames. That is intended;
  do not call it too fast, unreadable, a jump, a flicker or a missing typing animation. Judge only whether the typed
  text is correct and readable once it stands.
- The final hold: the last view stays still for about 12 s at the end. That is intended; do not call it too long,
  slow, frozen, a stall, dead time or a pace problem.
"""
HOUSE_STYLE_MARKERS = ("Fast typing", "The final hold", "NEVER a finding")
PRIVACY_ON = """7. Private data (privacy review is on): anything on screen beyond what the spec says may be shown
   ({shown}; account {account}): other people's names, e-mail addresses, tokens, private documents = blocker.
"""
PRIVACY_OFF = "(Privacy review is off: do not judge private data.)\n"


def checklist(privacy_text=PRIVACY_OFF):
    return CHECKLIST.replace("{house_style}", HOUSE_STYLE).replace("{privacy}", privacy_text)


def build_request(run_dir, steps, *, model, privacy=None, out_dir=None, video=None):
    """steps: [{step, expected: [human-readable]}] in clip order. Samples, writes sheets, returns the request dict and
    writes it to <out_dir>/request.json. `privacy`: None (off) or the spec's [privacy] table."""
    out_dir = out_dir or os.path.join(run_dir, "review")
    t0 = time.time()
    samples, sheets = sample(run_dir, out_dir, video)
    with open(os.path.join(run_dir, "cut", "record.json")) as f:
        rec = json.load(f)
    offs = _clip_offsets(rec)
    exp = {s["step"]: s.get("expected", []) for s in steps}
    st = [{"step": c["name"], "start": round(o, 2), "end": round(o + float(c.get("duration") or 0), 2),
           "expected": exp.get(c["name"], [])} for o, c in zip(offs, rec.get("clips", []))]
    pv = (PRIVACY_ON.format(shown=privacy.get("shown", "?"), account=privacy.get("account", "?"))
          if privacy else PRIVACY_OFF)
    req = {"run_dir": run_dir, "video": video or os.path.join(run_dir, "full.mp4"), "model": model,
           "duration_s": rec.get("full_duration"), "privacy": bool(privacy), "steps": st,
           "sheets": sheets, "samples": len(samples), "sample_s": round(time.time() - t0, 2),
           "prompt": checklist(pv),
           "out": os.path.join(out_dir, "result.json")}
    with open(os.path.join(out_dir, "request.json"), "w") as f:
        json.dump(req, f, indent=1, ensure_ascii=False)
    return req


def _sheet_line(sh):
    extra = f" around ({sh['center'][0]}, {sh['center'][1]})" if sh["kind"] == "zoom" else ""
    return (f"{os.path.basename(sh['path'])} [{sh['kind']}{extra}] tiles "
            + ", ".join(f"#{t['n']} {t['t']:.2f}s" for t in sh["tiles"]))


def prompt_text(req, inline=False):
    """The full prompt for a reviewer: checklist + steps + the sheet list (paths unless the sheets are inline)."""
    lines = [req["prompt"], "", f"Video: {req['duration_s']} s. Steps (full-video seconds):"]
    for s in req["steps"]:
        lines.append(f"- {s['step']} {s['start']}-{s['end']} s; expected: " + ("; ".join(s["expected"]) or "-"))
    if inline:
        lines += ["", f"The {len(req['sheets'])} sheets follow, each after a line naming it."]
    else:
        lines += ["", "Sheets (open each with the Read tool; several per turn):"]
        lines += [f"- {os.path.dirname(req['sheets'][0]['path'])}/{_sheet_line(sh)}" for sh in req["sheets"]]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------- headless
def run_headless(req, model=None, timeout=1200):
    """Run the review with `claude -p` in ONE turn: the prompt and every sheet go in as one user message (stream-json
    input, no tools), so the sheets are paid once. Writes req['out'] and returns it.
    model: None / 'orchestrator' = the CLI's default model (the orchestrating frontier model)."""
    import base64
    cli = os.environ.get("VC_CLAUDE_CLI") or shutil.which("claude")
    if not cli:
        raise RuntimeError("claude CLI not found (VC_CLAUDE_CLI)")
    model = model or req.get("model")
    content = [{"type": "text", "text": prompt_text(req, inline=True)}]
    for sh in req["sheets"]:
        with open(sh["path"], "rb") as f:
            data = base64.b64encode(f.read()).decode()
        content += [{"type": "text", "text": _sheet_line(sh)},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}]
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
    cmd = [cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--tools", ""]
    if model and model != "orchestrator":
        cmd += ["--model", model]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    t0 = time.time()
    p = subprocess.run(cmd, input=msg + "\n", capture_output=True, text=True, env=env, timeout=timeout,
                       cwd=os.path.dirname(req["out"]))
    secs = round(time.time() - t0, 1)
    meta = {"result": "", "is_error": True}
    for line in reversed(p.stdout.splitlines()):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            meta = ev
            break
    res = parse_answer(meta.get("result") or "")
    u = meta.get("usage") or {}
    mu = meta.get("modelUsage") or {}
    res.update({"model": ",".join(mu) or (model or "cli-default"),
                "tokens": {"input": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                           + u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)},
                "seconds": secs, "agent_cost_usd": meta.get("total_cost_usd"), "runner": "claude -p (one turn)"})
    if meta.get("is_error") or p.returncode:
        res.setdefault("error", f"claude exit {p.returncode}: {(p.stderr or '')[-300:]}")
    with open(req["out"], "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    return res


def parse_answer(text):
    """The reviewer's JSON answer (tolerates a code fence or prose around it)."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return {"error": "no JSON in the answer", "raw": text[-2000:]}
    try:
        return json.loads(text[a:b + 1])
    except ValueError as e:
        return {"error": f"bad JSON: {e}", "raw": text[-2000:]}


# ---------------------------------------------------------------------------------------------------- judge
_TYPING = re.compile(r"typ|keystroke|tipp|eingabe", re.I)
_FINAL_HOLD = re.compile(r"(final|last|end(ing)?|closing)\s+(hold|view|frame|screen|still)|12\s*s|hold at the end", re.I)


def is_house_style(f):
    """A pace finding about typing speed or the final hold: the owner's house style, dropped even if the model
    ignores the prompt. Only `pace` findings, so a stray tooltip seen during the hold still counts."""
    if f.get("category") != "pace":
        return False
    text = " ".join(str(f.get(k) or "") for k in ("what", "region"))
    return bool(_TYPING.search(text) or _FINAL_HOLD.search(text))


def judge(result, gate_verdict):
    """Apply the semantics. Returns {verdict: PASS|FAIL, clean, blockers, minors, problems}.
    - the review never passes a take the gate failed;
    - a missing or invalid answer, a step whose expected state is not visible, or any blocker fails the take;
    - a finding without a time, a region or a valid severity is a problem and counts as a blocker;
    - a pace finding about fast typing or the final hold is house style: listed under `house_style`, never counted."""
    out = {"blockers": [], "minors": [], "problems": [], "house_style": []}
    if gate_verdict != "PASS":
        out.update(verdict="FAIL", clean=False, note=f"skipped (gate {gate_verdict})")
        return out
    if not result or result.get("error"):
        out["problems"].append(f"the viewer review wrote no valid result ({(result or {}).get('error', 'missing')})")
    if result and "issues" in result and "findings" not in result:          # the old frame-check answer
        result = dict(result, findings=[{"t": None, "step": i.get("step"), "region": "step end", "severity": "blocker",
                                         "category": "expected_missing", "what": i.get("what")}
                                        for i in result.get("issues", [])])
        for f in result["findings"]:
            out["blockers"].append(f)
        result["findings"] = []
    for s in (result or {}).get("steps", []) or []:
        if s.get("expected_visible") is False:
            out["blockers"].append({"t": None, "step": s.get("step"), "region": "step end", "category":
                                    "expected_missing", "severity": "blocker",
                                    "what": s.get("note") or "expected state not visible"})
    for f in (result or {}).get("findings", []) or []:
        if is_house_style(f):
            out["house_style"].append(f)
            continue
        sev = f.get("severity")
        bad = [k for k in ("t", "region") if f.get(k) in (None, "")] + ([] if sev in SEVERITIES else ["severity"])
        if bad:
            out["problems"].append(f"finding without {', '.join(bad)}: {f.get('what')}")
            f = dict(f, severity="blocker")
        (out["blockers"] if f["severity"] == "blocker" else out["minors"]).append(f)
    if out["problems"] and not out["blockers"]:
        out["blockers"].append({"t": None, "region": "-", "category": "other", "severity": "blocker",
                                "what": "; ".join(out["problems"])})
    out["clean"] = not out["blockers"]
    out["verdict"] = "PASS" if out["clean"] else "FAIL"
    return out


def summary(j):
    """One line for the take log: `step: what (time, region)` per blocker (the fixer reads the step name first)."""
    if j.get("note"):
        return j["note"]
    b = "; ".join(f"{f.get('step') or '-'}: {f.get('what')} ({_ts(f)}, {f.get('region')})" for f in j["blockers"])
    return (b or "clean") + (f" (+{len(j['minors'])} minor)" if j["minors"] else "")


def _ts(f):
    t = f.get("t")
    if t is None:
        return f"[{f.get('step') or '-'}]"
    te = f.get("t_end")
    return f"{float(t):.2f}-{float(te):.2f}s" if te not in (None, "", t) else f"{float(t):.2f}s"


def ledger_candidates(j, *, case, run_id, date, review_dir):
    """Markdown entries in the FIXES-LEDGER format, one per blocker (owner rule: symptom -> fix -> evidence)."""
    out = []
    for i, f in enumerate(j.get("blockers", []), 1):
        scope = "COMMON" if f.get("category") in COMMON_CATEGORIES else "SPECIFIC"
        out += [f"### FX-?? (candidate {run_id} #{i}) {f.get('what', '')[:80]}",
                f"- date: {date}", f"- case/spec: {case}",
                f"- symptom: viewer review, {_ts(f)}, {f.get('region')}: {f.get('what')} "
                f"[{f.get('category', 'other')}]",
                "- root cause: (fixer fills in)", "- change: (fixer fills in: files, commit)",
                f"- evidence: {review_dir} (sheets; tiles {f.get('tiles', '-')}); the retake's viewer review "
                "must no longer show it",
                f"- scope guess: {scope}", "- lives in: (fixer fills in)", "- owner decision:", ""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ spec + CLI
def steps_from_spec(spec_res):
    """[{step, expected}] from a resolved spec (vcloop.spec.resolve)."""
    from . import spec as S
    return [{"step": st["name"], "expected": [S.human_check(x) for x in st.get("expect", [])]}
            for st in spec_res.get("steps", [])]


def main(argv=None):
    """vc-review request RUN_DIR SPEC [--model M] [--privacy] [--out-dir D]   sample + write the request
    vc-review headless REQUEST [--model M]                                   run the review with `claude -p`
    vc-review judge RESULT                                                   apply the semantics (gate PASS assumed)"""
    import argparse
    ap = argparse.ArgumentParser(prog="vc-review")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("request")
    a.add_argument("run_dir")
    a.add_argument("spec")
    a.add_argument("--model", default="orchestrator")
    a.add_argument("--privacy", action="store_true")
    a.add_argument("--out-dir")
    b = sub.add_parser("headless")
    b.add_argument("request")
    b.add_argument("--model")
    c = sub.add_parser("judge")
    c.add_argument("result")
    ns = ap.parse_args(argv)
    if ns.cmd == "request":
        from . import spec as S
        sp = S.load(ns.spec)
        res, _ = S.resolve(sp, {"unique": "-"})
        req = build_request(ns.run_dir, steps_from_spec(res), model=ns.model,
                            privacy=sp.get("privacy") if ns.privacy else None, out_dir=ns.out_dir)
        print(os.path.join(ns.out_dir or os.path.join(ns.run_dir, "review"), "request.json"))
        print(f"{req['samples']} frames, {len(req['sheets'])} sheets, {req['sample_s']} s")
        return 0
    if ns.cmd == "headless":
        with open(ns.request) as f:
            req = json.load(f)
        r = run_headless(req, ns.model)
        j = judge(r, "PASS")
        print(json.dumps({"verdict": j["verdict"], "summary": summary(j), "tokens": r.get("tokens"),
                          "seconds": r.get("seconds"), "cost_usd": r.get("agent_cost_usd")}, ensure_ascii=False))
        return 0 if j["clean"] else 1
    with open(ns.result) as f:
        j = judge(json.load(f), "PASS")
    print(json.dumps(j, indent=1, ensure_ascii=False))
    return 0 if j["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
