"""Render the planned clips from the raw recording in one decoding pass (Q-80, Q-81, Q-77 badge)."""
import math
import os
import subprocess

from .badge import badge_alpha_expr, make_badge
from .tools import FPS, tool, x264_args

FADE_FRAMES = 6  # Q-72: 0.2 s at 30 fps (the default of the F-23 knob crossfade_s)
CROSSFADE_DEFAULT_S = 0.2       # = loop/vcloop/knobs.py crossfade_s default (a test keeps them equal)
CROSSFADE_RANGE_S = (0.15, 0.25)  # Q-72 tolerance (the owner's range); outside it the cutter refuses


def fade_frames(seconds):
    """Frames of the site-switch cross-fade for the crossfade_s knob (Q-72: 0.15-0.25 s, whole frames)."""
    s = float(seconds)
    if not CROSSFADE_RANGE_S[0] - 1e-9 <= s <= CROSSFADE_RANGE_S[1] + 1e-9:
        raise ValueError(f"crossfade {s} s is outside Q-72's range {CROSSFADE_RANGE_S[0]}-{CROSSFADE_RANGE_S[1]} s")
    lo, hi = math.ceil(CROSSFADE_RANGE_S[0] * FPS - 1e-9), math.floor(CROSSFADE_RANGE_S[1] * FPS + 1e-9)
    return min(hi, max(lo, int(round(s * FPS))))      # whole frames, never outside the range (0.25 s -> 7)


def select_expr(fmap):
    """ffmpeg select expression that keeps exactly the raw frames of `fmap` (strictly increasing)."""
    terms = []
    i = 0
    while i < len(fmap):
        j = i
        while j + 1 < len(fmap) and fmap[j + 1] == fmap[j] + 1:
            j += 1
        terms.append(f"between(n\\,{fmap[i]}\\,{fmap[j]})" if j > i else f"eq(n\\,{fmap[i]})")
        i = j + 1
    return "+".join(terms)


def render(raw, clips, width, height, workdir, factor, preset="veryfast", fade_n=FADE_FRAMES):
    """clips: list of dicts with 'fmap', 'badge' (per-frame opacity) and 'path'. One ffmpeg process decodes the raw
    once and encodes every clip. Each clip gets forced key frames at frame 6 and at frame K-6, so the joiner can
    split off the 0.2 s fade overlap by stream copy."""
    os.makedirs(workdir, exist_ok=True)
    inputs = ["-i", raw]
    graph = [f"[0:v]split={len(clips)}" + "".join(f"[r{i}]" for i in range(len(clips))) if len(clips) > 1
             else "[0:v]null[r0]"]
    badge_png = None
    outs = []
    for i, c in enumerate(clips):
        if any(q <= p_ for p_, q in zip(c["fmap"], c["fmap"][1:])) or min(c["fmap"]) < 0:
            raise ValueError("frame map must be strictly increasing and non-negative")
        sel = select_expr(c["fmap"])
        chain = f"[r{i}]select='{sel}',setpts=N/{FPS}/TB"
        # Q-23 skeleton covers: the pre-click frame over the covered delivered frames, and on it the drawn ripple
        # (one small patch per frame from k0 while the ripple shows)
        for j, cv in enumerate(c.get("covers") or []):
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", "%.3f" % (len(c["fmap"]) / FPS + 1), "-i",
                       cv["png"]]
            ci = sum(1 for x in inputs if x == "-i") - 1
            graph.append(chain + f"[c{i}_{j}]")
            graph.append(f"[{ci}:v]format=rgba[ci{i}_{j}]")
            chain = (f"[c{i}_{j}][ci{i}_{j}]overlay=x=0:y=0:eof_action=pass:format=auto:"
                     f"enable='between(n\\,{cv['k0']}\\,{cv['k1'] - 1})'")
            pt = cv.get("patch")
            if pt:
                inputs += ["-framerate", str(FPS), "-start_number", "0", "-i", pt["pattern"]]
                pi = sum(1 for x in inputs if x == "-i") - 1
                graph.append(chain + f"[d{i}_{j}]")
                graph.append(f"[{pi}:v]format=rgba,tpad=start={cv['k0']}:start_mode=clone,"
                             f"setpts=N/{FPS}/TB[pi{i}_{j}]")
                chain = (f"[d{i}_{j}][pi{i}_{j}]overlay=x={pt['x']}:y={pt['y']}:eof_action=pass:format=auto:"
                         f"enable='between(n\\,{cv['k0']}\\,{cv['k0'] + pt['n'] - 1})'")
        if any(a > 0 for a in c["badge"]):
            if badge_png is None:
                badge_png, g = make_badge(os.path.join(workdir, "badge.png"), factor, width)
                c_geom = g
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", "%.3f" % (len(c["fmap"]) / FPS + 1), "-i",
                       badge_png]
            bi = sum(1 for x in inputs if x == "-i") - 1
            expr = badge_alpha_expr(c["badge"])
            graph.append(chain + f"[m{i}]")
            graph.append(f"[{bi}:v]format=rgba,geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
                         f"a='alpha(X\\,Y)*({expr})'[b{i}]")
            x = width - c_geom["margin"] - c_geom["w"]
            y = height - c_geom["margin"] - c_geom["h"]
            graph.append(f"[m{i}][b{i}]overlay=x={x}:y={y}:eof_action=pass:format=auto,format=yuv420p[o{i}]")
        else:
            graph.append(chain + f",format=yuv420p[o{i}]")
        K = len(c["fmap"])
        kf = f"expr:eq(n,{fade_n})+eq(n,{max(fade_n, K - fade_n)})"
        outs += ["-map", f"[o{i}]"] + x264_args(preset) + ["-frames:v", str(K), "-force_key_frames:v", kf,
                                                            c["path"]]
    script = os.path.join(workdir, "render.filtergraph")
    with open(script, "w") as f:
        f.write(";\n".join(graph))
    cmd = [tool("ffmpeg"), "-v", "error", "-y"] + inputs + ["-filter_complex_script", script] + outs
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("render failed: " + p.stderr[-2500:])
    return cmd
