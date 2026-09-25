"""Checks of the cutter's [S1] requirements, each following its Verify line in section Q.

    python3 tests/cut/verify.py all [WORKDIR]        synthetic take + recut + fixtures (one command)

Every check prints PASS/FAIL with the numbers it measured; the exit status is the number of failures.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from vc.cut import analyze as an_mod  # noqa: E402
from vc.cut.cutter import cut, dir_digest  # noqa: E402
from vc.cut.join import join  # noqa: E402
from vc.cut.tools import FPS, probe, tool, x264_args  # noqa: E402
import synth  # noqa: E402
from badge_measure import measure  # noqa: E402

SPEC = os.environ.get("VC_ACCEPTANCE")
FAILS = []
RESULTS = []


def check(rid, ok, msg):
    RESULTS.append((rid, ok, msg))
    print(("PASS " if ok else "FAIL ") + f"{rid}: {msg}", flush=True)
    if not ok:
        FAILS.append(rid)


def gray_frames(path, w, h, vf=""):
    """All frames of `path` as w x h grey byte strings."""
    chain = (vf + "," if vf else "") + f"scale={w}:{h}:flags=area,format=gray"
    out = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", path, "-vf", chain, "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    n = w * h
    return [out[i:i + n] for i in range(0, len(out), n)]


def mad(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def windows_for(clip):
    """Clip-time windows where stillness is intended or an action runs (holds, clicks with glide and ripple,
    typing): Q-50 counts still stretches outside them."""
    w = [(h["clip_t"], h["clip_end"]) for h in clip["holds"] if h["clip_t"] is not None]
    for e in clip["events"]:
        if e["type"] == "click" and e["clip_t"] is not None:
            s = e["clip_t"] - 1.0
            if e.get("glide_clip_t") is not None:
                s = min(s, e["glide_clip_t"] - 0.2)
            w.append((s, e["clip_t"] + (0.65 if e.get("navigates") else 1.2)))
        elif e.get("clip_t") is not None and e.get("clip_end") is not None:
            w.append((e["clip_t"] - 1.0, e["clip_end"] + 0.3))
    return w


def in_windows(t, wins, pad=0.0):
    return any(a - pad <= t < b + pad for a, b in wins)


# ---------------------------------------------------------------------------------------------------------- format
def faststart(path):
    with open(path, "rb") as f:
        head = f.read(1 << 16)
    i_moov, i_mdat = head.find(b"moov"), head.find(b"mdat")
    return i_moov != -1 and (i_mdat == -1 or i_moov < i_mdat)


def crf_of(path):
    with open(path, "rb") as f:
        data = f.read(4 << 20)
    k = data.find(b"crf=")
    return data[k + 4:k + 12].split(b" ")[0].decode() if k >= 0 else None


def check_format(files):
    bad = []
    for p in files:
        i = probe(p)
        rate = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=r_frame_rate,avg_frame_rate", "-of", "csv=p=0", p],
                              capture_output=True, text=True).stdout.strip()
        ok = (i["width"], i["height"]) == (1920, 1080) and i["codec"] == "h264" and i["profile"] == "High" \
            and i["pix_fmt"] == "yuv420p" and i["audio_streams"] == 0 and rate == "30/1,30/1" and faststart(p) \
            and crf_of(p) == "18.0"
        if not ok:
            bad.append((os.path.basename(p), i, rate, faststart(p), crf_of(p)))
    check("Q-80/Q-81", not bad, f"{len(files)} files: 1920x1080, 30/1 CFR, h264 High, yuv420p, crf=18.0, no audio, "
                                f"moov before mdat" + (f"; bad: {bad}" if bad else ""))


# ---------------------------------------------------------------------------------------------------- synthetic
def verify_take(src, out, rec):
    clips = rec["clips"]
    man = json.load(open(os.path.join(src, "manifest.json")))
    raw = os.path.join(src, "raw.mp4")
    check_format([os.path.join(out, c["file"]) for c in clips] + [os.path.join(out, "full.mp4")])

    # Q-10 / one clip per step, in order; each clip ends where its step ends
    marks = man["marks"]
    ok = len(clips) == len(marks) and [c["step"] for c in clips] == [m["step"] for m in marks]
    ends = [(c["source"]["start_t"], c["source"]["end_t"]) for c in clips]
    exp = [(m["video_t"], marks[i + 1]["video_t"] if i + 1 < len(marks) else man["duration"])
           for i, m in enumerate(marks)]
    ok2 = all(abs(a - x) < 0.034 and abs(b - y) < 0.034 for (a, b), (x, y) in zip(ends, exp))
    idx = json.load(open(os.path.join(out, "clips", "index.json")))
    check("one clip per step", ok and ok2 and len(idx["clips"]) == len(marks),
          f"{len(clips)} clips for {len(marks)} marks; spans {ends} = mark-to-next-mark {exp}")

    # per clip: analyse the delivered video
    rawg = gray_frames(raw, 160, 90)
    for c in clips:
        path = os.path.join(out, c["file"])
        an = an_mod.analyze(path, os.path.join(out, "verify-work", str(c["index"])))
        K = an.n
        # the clip's last frame is the raw frame just before the next mark (the step's end)
        last_src = c["frame_map"][-1]
        check(f"clip ends at step end [{c['name']}]", last_src == c["source"]["end_frame"] - 1 and K == c["frames"],
              f"last delivered frame shows raw frame {last_src}, step ends at {c['source']['end_frame']}; "
              f"{K} frames decoded, record {c['frames']}")
        # Q-15: first frame is the step's first view (never anything before the mark)
        if c["index"] == 0:
            f0 = an.thumb(0)
            mk = c["source"]["start_frame"]
            d_mark, d_pre = mad(f0, rawg[mk]), mad(f0, rawg[max(0, mk - 5)])
            check("Q-15 first frame is the landing view", d_mark < 2 and d_pre > 5,
                  f"MAD to the raw frame at the mark {d_mark:.2f}, to the off-camera view {d_pre:.2f}")
        # Q-53 / Q-20: no blank frame reaches the clip
        blanks = [k for k in range(K) if an.blank(k)]
        check(f"Q-53 no blank frames [{c['name']}]", not blanks,
              f"{len(blanks)} blank frames; drops listed: "
              + (", ".join(f"{d['src_start']}-{d['src_end']} {d['reason']}" for d in c["drops"]) or "none"))
        # Q-50: no kept still stretch outside a hold (or an action) longer than 1.5 s (2.0 s near still)
        wins = windows_for(c)
        longest, run, runs = 0, 0, []
        for k in range(1, K + 1):
            t = k / FPS
            if k < K and an.mad[k] <= 0.5 and not in_windows(t, wins):
                run += 1
            else:
                if run:
                    runs.append(round((run + 1) / FPS, 2))
                run = 0
        longest = max(runs) if runs else 0
        check(f"Q-50 still stretches [{c['name']}]", longest <= 2.0 + 1 / FPS,
              f"longest still stretch outside holds/actions {longest:.2f} s (limit 1.5 s, 2.0 near-still); "
              f"drops: {sum(1 for d in c['drops'] if d['reason'] in ('standstill', 'near-still'))}")
        # Q-51: holds kept to the frame, clip at least as long as its holds, frames still during the hold
        hold_ok, notes = True, []
        for h in c["holds"]:
            s, e = int(round(h["clip_t"] * FPS)), int(round(h["clip_end"] * FPS))
            moving = [k for k in range(s + 1, min(e, K)) if an.mad[k] > 0.5]
            ok_h = abs(h["kept_seconds"] - h["seconds"]) <= 0.25 and not moving
            hold_ok &= ok_h
            notes.append(f"{h['kind']} {h['seconds']}s->{h['kept_seconds']}s")
        hold_ok &= c["duration"] + 1e-6 >= sum(h["seconds"] for h in c["holds"])
        check(f"Q-51 holds [{c['name']}]", hold_ok, "; ".join(notes) or "no holds")
        # Q-52: no splice inside a glide, a click or its ripple
        prot = []
        for e in c["events"]:
            if e["type"] == "click" and e["clip_t"] is not None:
                s = e["clip_t"] - 1.0 if e.get("glide_clip_t") is None else min(e["clip_t"] - 1.0,
                                                                                  e["glide_clip_t"] - 0.2)
                prot.append((s, e["clip_t"] + (0.65 if e.get("navigates") else 1.2), e["clip_t"],
                             bool(e.get("navigates"))))
        # Q-52 hint: after a click that navigates, a blank is dropped from 0.3 s on
        bad = [sp["clip_t"] for sp in c["splices"] if any(
            a < sp["clip_t"] < b and not (nav and sp["reason"].startswith("blank") and sp["clip_t"] >= ct + 0.3 - 1e-6)
            for a, b, ct, nav in prot)]
        check(f"Q-52 no splice through a click [{c['name']}]", not bad,
              f"{len(c['splices'])} splices at {[sp['clip_t'] for sp in c['splices']]}, click windows {prot}")
        # Q-56 / Q-77: speed-ups only from logged waits, no click inside, badge exactly over them
        for s in c["speedups"]:
            w = s.get("wait")
            clicks_in = [e["clip_t"] for e in c["events"] if e["type"] == "click"
                         and s["clip_start"] <= e["clip_t"] < s["clip_end"]]
            check(f"Q-56 speed-up [{c['name']} {s['clip_start']}-{s['clip_end']}]",
                  w is not None and s["factor"] == 4.0 and not clicks_in,
                  f"factor {s['factor']}, raw {s['src_start']}-{s['src_end']}, wait {w}, clicks inside {clicks_in}")
        check_badge(raw, path, c, rec["width"], rec["height"])
        # Q-14: the cut record flags all-standstill clips
        check(f"Q-14 flag [{c['name']}]", c["all_standstill"] is False and "all_standstill" in c,
              f"all_standstill={c['all_standstill']}, events {[(e['type'], e['clip_t']) for e in c['events']]}")
    # Q-50 "real motion is never bridged away": the 0.2 s scroll at raw 26.0-26.2 is in clip 2
    c2 = clips[1]
    scroll = [f for f in range(780, 786) if f in set(c2["frame_map"])]
    check("Q-50 real motion kept", len(scroll) == 6, f"scroll raw frames kept: {scroll}")

    # Q-72 joins
    check_joins(out, rec)
    shutil.rmtree(os.path.join(out, "verify-work"), ignore_errors=True)


def check_badge(raw, clip_path, c, W, H):
    """Badge present exactly over the sped-up stretches (Q-77 Verify), at full opacity inside them."""
    x, y = W - 28 - 84, H - 28 - 44
    vf = f"crop=84:44:{x}:{y}"
    cl = gray_frames(clip_path, 84, 44, vf)
    rw = gray_frames(raw, 84, 44, vf)
    diffs = [mad(cl[k], rw[f]) for k, f in enumerate(c["frame_map"])]
    inside = set()
    for s in c["speedups"]:
        inside |= set(range(s["frames"][0], s["frames"][1]))
    on = [k for k, d in enumerate(diffs) if d > 3]
    ok = set(on) == inside if inside else not on
    full = [k for k in inside if diffs[k] > 30]
    check(f"Q-77 badge exactly over speed-ups [{c['name']}]", ok,
          f"badge visible in {len(on)} frames, sped-up frames {len(inside)}, at full strength {len(full)}"
          + (f"; mismatch {sorted(set(on) ^ inside)[:12]}" if not ok else ""))


def frame_rgb(path, n, w, h):
    return subprocess.run([tool("ffmpeg"), "-v", "error", "-i", path, "-vf", f"select='eq(n\\,{n})'", "-frames:v",
                           "1", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-f", "rawvideo", "-"],
                          capture_output=True, check=True).stdout


def psnr(a, a_rng, b, b_rng):
    fg = (f"[0:v]trim=start_frame={a_rng[0]}:end_frame={a_rng[1]},setpts=PTS-STARTPTS[a];"
          f"[1:v]trim=start_frame={b_rng[0]}:end_frame={b_rng[1]},setpts=PTS-STARTPTS[b];[a][b]psnr")
    p = subprocess.run([tool("ffmpeg"), "-v", "info", "-i", a, "-i", b, "-filter_complex", fg, "-f", "null", "-"],
                       capture_output=True, text=True)
    line = [ln for ln in p.stderr.splitlines() if "PSNR" in ln and "average" in ln]
    v = line[-1].split("average:")[1].split()[0] if line else "nan"
    return float("inf") if v == "inf" else float(v)


def check_joins(out, rec):
    clips = rec["clips"]
    full = os.path.join(out, "full.mp4")
    fi = probe(full)
    kinds = [j["kind"] for j in rec["joins"]]
    exp_kinds = ["fade" if (c["site_end"] and n["site_start"] and c["site_end"] != n["site_start"]) else "cut"
                 for c, n in zip(clips, clips[1:])]
    check("Q-72 fade only at site switch", kinds == exp_kinds,
          f"joins {kinds}, sites {[(c['site_start'], c['site_end']) for c in clips]}")
    nf = rec["params"].get("fade_frames", 6)
    exp = sum(c["frames"] for c in clips) - nf * kinds.count("fade")
    check("Q-72/Q-82 full duration", abs(fi["frames"] - exp) <= 1,
          f"full {fi['frames']} frames ({fi['frames'] / FPS:.3f} s) = sum of clips {sum(c['frames'] for c in clips)}"
          f" - {nf} x {kinds.count('fade')} fades = {exp}")
    # frames outside fades are copies of the clips (PSNR >= 50 dB); the fade midpoint is a 50/50 blend
    pos = 0
    for i, c in enumerate(clips):
        head = nf if i > 0 and kinds[i - 1] == "fade" else 0
        tail = nf if i < len(kinds) and kinds[i] == "fade" else 0
        n = c["frames"] - head - tail
        v = psnr(full, (pos, pos + n), os.path.join(out, c["file"]), (head, head + n))
        check(f"Q-72 frames outside fades match [{c['name']}]", v >= 50, f"PSNR {v} dB over {n} frames")
        pos += n
        if tail:
            W, H = rec["width"], rec["height"]
            if nf % 2:                   # an odd fade has no exact 50/50 frame; the midpoint check needs an even one
                pos += nf
                continue
            h = nf // 2
            a = frame_rgb(os.path.join(out, c["file"]), c["frames"] - h, W, H)
            b = frame_rgb(os.path.join(out, clips[i + 1]["file"]), h, W, H)
            m = frame_rgb(full, pos + h, W, H)
            dev = [abs(mm - (aa + bb) / 2) for aa, bb, mm in zip(a, b, m)]
            worst = max(dev)
            over = sum(1 for d in dev if d > 6)
            check("Q-72 fade midpoint 50/50", over <= len(dev) * 0.005,
                  f"frame {pos + h}: max |mid - mean| {worst:.1f} luma, {over} of {len(dev)} px over 6 "
                  f"(limit 0.5 % of pixels: the baseline fade-mid.png itself has 0.34 % over 6 luma)")
            pos += nf


# ---------------------------------------------------------------------------------------------------- knob crossfade_s
def verify_crossfade_knob(src, work):
    """F-23 knob crossfade_s drives the fade length (Q-72 range 0.15-0.25 s); the cutter default equals the knob's."""
    sys.path.insert(0, os.path.join(ROOT, "loop"))
    from vcloop.knobs import TABLE
    from vc.cut.render import CROSSFADE_DEFAULT_S
    check("F-23 crossfade default = knob table", CROSSFADE_DEFAULT_S == TABLE["crossfade_s"][0],
          f"cutter {CROSSFADE_DEFAULT_S} s, knob {TABLE['crossfade_s'][0]} s")
    out, rec = cut(src, out=os.path.join(work, "runs", "recut-xfade-7f"), crossfade_s=0.25)
    fades = [j for j in rec["joins"] if j["kind"] == "fade"]
    check("Q-72 crossfade knob 0.25 s -> 7-frame fades", fades and all(j["frames"] == 7 for j in fades)
          and rec["params"]["fade_frames"] == 7, f"fades {[(j['frames'], j['duration']) for j in fades]}")
    check_joins(out, rec)
    env = dict(os.environ, PYTHONPATH=ROOT)
    p = subprocess.run([sys.executable, "-m", "vc.cut", "cut", src, "--out", os.path.join(work, "runs", "x"),
                        "--crossfade", "0.5"], env=env, capture_output=True, text=True)
    check("Q-72 crossfade outside 0.15-0.25 s refused", p.returncode == 2 and "outside" in p.stderr,
          p.stderr.strip().splitlines()[-1] if p.stderr.strip() else f"exit {p.returncode}")


# ---------------------------------------------------------------------------------------------------- Q-53 via Q-20/Q-21
def gate_frames_check(out, label):
    """Q-53's Verify line is "Q-20, Q-21 and Q-22 pass": run the gate's own Q-20/Q-21 checks (the gate owns their
    definition: luma range < 12 at 480 px with 0.1 % extremes ignored, > 3 frames) on the delivered clips. The
    cutter's own per-frame `blank()` test above is stricter (not even one blank frame) and stays as well."""
    gate = os.path.join(ROOT, "bin", "vc-gate")
    tmp = os.path.join(out, "cut", "verify-gate")
    p = subprocess.run([gate, out, "--no-events", "--json", "--out", tmp, "--workers", "4"],
                       capture_output=True, text=True)
    try:
        res = json.loads(p.stdout)
    except ValueError:
        check(f"Q-53 via gate Q-20/Q-21 [{label}]", False, f"gate exit {p.returncode}: {p.stderr[-300:]}")
        return
    bad = [v for v in res.get("violations", []) if v["check"] in ("Q-20", "Q-21")]
    check(f"Q-53 via gate Q-20/Q-21 [{label}]", not bad and "abort" not in res,
          f"{len(bad)} Q-20/Q-21 violations" + (f": {bad[0]['reason']}" if bad else ""))
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------- recut (Q-58)
def verify_recut(src):
    before = dir_digest(src)
    t = time.time()
    out, rec = cut(src)
    dt = time.time() - t
    after = dir_digest(src)
    check("Q-58 recut writes a new run dir", out != src and os.path.dirname(out) == os.path.dirname(src)
          and rec["recut"] and os.path.exists(os.path.join(out, "source.json")),
          f"recut -> {os.path.basename(out)} in {dt:.1f} s")
    check("Q-58 source unchanged", before == after, f"sha256 of the source tree before/after: {before[:12]} / "
                                                     f"{after[:12]}")
    refused = False
    try:
        cut(src, out=src)
    except SystemExit:
        refused = True
    check("Q-58 cut in place refused", refused and dir_digest(src) == before, "cut(src, out=src) refused")
    return out


# ---------------------------------------------------------------------------------------------------- fixtures
def fixture_run(work, name, clip, marks, events=(), holds=()):
    """A run directory around a real fixture clip used as the raw recording."""
    d = os.path.join(work, name)
    os.makedirs(d, exist_ok=True)
    shutil.copy(clip, os.path.join(d, "raw.mp4"))
    i = probe(clip)
    ev = [dict(e) for e in events]
    mk = [{"type": "mark", "name": m[0], "step": k + 1, "video_t": m[1], "t": 1e9 + m[1], "url": m[2]}
          for k, m in enumerate(marks)]
    hl = [{"type": "hold", "kind": h[0], "seconds": h[2], "video_t": h[1], "t": 1e9 + h[1]} for h in holds]
    man = {"run_id": name, "raw": "raw.mp4", "t0": 1e9, "fps": 30, "width": i["width"], "height": i["height"],
           "duration": i["frames"] / 30, "frames": i["frames"], "marks": mk, "holds": hl, "events": ev + mk + hl}
    json.dump(man, open(os.path.join(d, "manifest.json"), "w"))
    return d


def verify_fixtures(work):
    fx = f"{SPEC}/fixtures"
    # (1) badge look on a real clip with the baseline's light page (251,251,248): the claude.ai part of fade.mp4
    # (reference B, 1910x986) after its fade, with a logged wait 1.95-3.45 s and no click
    d = fixture_run(work, "fx-badge", f"{SPEC}/visual-baselines/fade.mp4", [("page", 0.0, "https://x.de")],
                    [{"type": "readiness_wait", "video_t": 1.95, "t": 1e9 + 1.95, "end": 1e9 + 3.45}])
    out, rec = cut(d)
    c = rec["clips"][0]
    sp = c["speedups"]
    check("Q-56 speed-up on a real clip", len(sp) == 1 and sp[0]["wait"] is not None, f"speed-ups {sp}")
    if sp:
        mid = (sp[0]["frames"][0] + sp[0]["frames"][1]) // 2
        W, H = rec["width"], rec["height"]
        crop = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", os.path.join(out, c["file"]), "-vf",
                               f"select='eq(n\\,{mid})',crop=240:120:{W - 240}:{H - 120},format=rgb24",
                               "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "240x120",
                        "-i", "-", os.path.join(work, "badge-real.png")], input=crop, check=True)
        base = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", f"{SPEC}/visual-baselines/badge.png", "-f",
                               "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        m, b = measure(crop), measure(base)
        ok = (abs(m["pill_wh"][0] - 84) <= 4 and abs(m["pill_wh"][1] - 44) <= 4 and m["right_margin_in_crop"] == 28
              and m["bottom_margin_in_crop"] == 28 and abs(m["text_wh"][0] - 26) <= 4 and abs(m["text_wh"][1] - 18) <= 4
              and max(abs(x) for x in m["text_centre_offset"]) <= 1.5)
        check("Q-77 badge look vs badge.png", ok, f"ours {m}; baseline {b}")
        # opacity: the fill over the page matches rgb(24,24,28) at 80 +- 5 %
        bg = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", os.path.join(d, "raw.mp4"), "-vf",
                             f"select='eq(n\\,{c['frame_map'][mid]})',crop=240:120:{W - 240}:{H - 120},format=rgb24",
                             "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        px = m["pill"][0] + 6, (m["pill"][1] + m["pill"][3]) // 2
        k = (px[1] * 240 + px[0]) * 3
        alphas = [(bg[k + j] - crop[k + j]) / max(1, bg[k + j] - v) for j, v in enumerate((24, 24, 28))]
        check("Q-77 badge opacity", all(0.75 <= a <= 0.85 for a in alphas),
              f"page {tuple(bg[k:k + 3])} -> badge {tuple(crop[k:k + 3])}: opacity {[round(a, 3) for a in alphas]}")
        check_badge(os.path.join(d, "raw.mp4"), os.path.join(out, c["file"]), c, W, H)
    # (2) fade look on real clips: q43 clip A (the app) -> clip B (other site), plus the clean take's hard cuts
    parts = []
    for name in ("q43-boundary-jump-428px-clipA", "q43-boundary-jump-428px-clipB"):
        p = os.path.join(work, name + ".mp4")
        i = probe(f"{fx}/{name}.mp4")
        subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-i", f"{fx}/{name}.mp4"] + x264_args() +
                       ["-force_key_frames:v", f"expr:eq(n,6)+eq(n,{i['frames'] - 6})", p], check=True)
        parts.append({"path": p, "frames": i["frames"], "index": len(parts)})
    parts[0].update(site_start="app.example.com", site_end="app.example.com")
    parts[1].update(site_start="claude.example", site_end="claude.example")
    full = os.path.join(work, "fade-full.mp4")
    jr = join(parts, full, os.path.join(work, "fadework"))
    j = jr["joins"][0]
    fs = int(round(j["full_start"] * FPS))
    i = probe(full)
    W, H = i["width"], i["height"]
    a = frame_rgb(parts[0]["path"], parts[0]["frames"] - 3, W, H)
    b = frame_rgb(parts[1]["path"], 3, W, H)
    m = frame_rgb(full, fs + 3, W, H)
    dev = [abs(mm - (aa + bb) / 2) for aa, bb, mm in zip(a, b, m)]
    over = sum(1 for x in dev if x > 6)
    check("Q-72 fade on real clips", j["kind"] == "fade" and jr["full_frames"] == 120 - 6 and over <= len(dev) * 0.005,
          f"{jr['full_frames']} frames (120 - 6); midpoint max dev {max(dev):.1f} luma, {over} px over 6")
    # contact sheet: our fade start/mid/end next to the baseline's
    sheet = os.path.join(work, "fade-compare.png")
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-i", full, "-i", f"{SPEC}/visual-baselines/fade.mp4",
                    "-filter_complex",
                    f"[0:v]select='between(n\\,{fs}\\,{fs + 6})',scale=384:-2,tile=7x1[a];"
                    f"[1:v]select='between(n\\,44\\,50)',scale=384:-2,tile=7x1[b];[a][b]vstack",
                    "-frames:v", "1", sheet], check=True)
    # baseline fade: linear ramp check on fade.mp4 itself for reference (frame 51..57 = fade 536..542 of B)
    # (3) clean reference take joined with hard cuts: a pure stream copy
    clean = f"{fx}/clean-reference-take"
    parts = []
    for k, name in enumerate(["prozesse", "suche", "prozess", "schritt", "details"]):
        p = os.path.join(work, f"clean-{name}.mp4")
        i = probe(f"{clean}/{name}.mp4")
        subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-i", f"{clean}/{name}.mp4"] + x264_args() +
                       ["-force_key_frames:v", f"expr:eq(n,6)+eq(n,{i['frames'] - 6})", p], check=True)
        parts.append({"path": p, "frames": i["frames"], "index": k, "site_start": "app", "site_end": "app"})
    t = time.time()
    jr = join(parts, os.path.join(work, "clean-full.mp4"), os.path.join(work, "cleanwork"))
    dt = time.time() - t
    check("Q-72 hard cuts on one site (join time C-26)", all(x["kind"] == "cut" for x in jr["joins"])
          and jr["full_frames"] == sum(p["frames"] for p in parts),
          f"{len(jr['joins'])} hard cuts, {jr['full_frames']} frames = sum; join by stream copy in {dt:.2f} s")
    # (4) all-standstill fixture: the cut record flags it (Q-14)
    d = fixture_run(work, "fx-q14", f"{fx}/synthetic-q14-all-standstill.mp4", [("prozesse", 0.0, "https://x.de")],
                    [{"type": "click", "video_t": 1.5, "t": 1e9 + 1.5, "x": 300, "y": 400}])
    out, rec = cut(d)
    check("Q-14 all-standstill clip flagged", rec["clips"][0]["all_standstill"] is True,
          f"all_standstill={rec['clips'][0]['all_standstill']}, click at clip {rec['clips'][0]['events'][0]['clip_t']}")
    # (5) white-out at a navigation (real): the cutter removes the blank and the half-painted frames
    d = fixture_run(work, "fx-whiteout", f"{fx}/q20-whiteout-at-navigation.mp4", [("oauth", 0.0, "https://x.de")],
                    [{"type": "click", "video_t": 0.27, "t": 1e9 + 0.27, "x": 962, "y": 655, "navigates": True}])
    out, rec = cut(d)
    c = rec["clips"][0]
    an = an_mod.analyze(os.path.join(out, c["file"]), os.path.join(work, "wo-an"))
    blanks = [k for k in range(an.n) if an.blank(k)]
    solid = [k for k in range(an.n) if an.lrange[k] < 12]
    check("Q-53 real white-out removed", not blanks and not solid,
          f"drops {[(x['src_start'], x['src_end'], x['reason']) for x in c['drops']]}; "
          f"blank frames left {len(blanks)}")
    gate_frames_check(out, "real white-out")


# ------------------------------------------------------------------------------ recorder frame stamps (review)
def recorder_run(work, name, clip, events):
    """A run directory stamped exactly like env/recorder/recorder.py: every event gets frame = ceil(video_t * fps)
    (the first frame that shows it), and the manifest's segments start at the mark's frame."""
    import math
    d = os.path.join(work, name)
    os.makedirs(d, exist_ok=True)
    shutil.copy(clip, os.path.join(d, "raw.mp4"))
    i = probe(clip)
    frames = i["frames"]
    evs = []
    for e in events:
        e = dict(e, t=1e9 + e["video_t"])
        e["frame"] = max(0, math.ceil(e["video_t"] * FPS - 1e-6))
        if e.get("end_video_t") is not None:
            e["video_end_t"] = e.pop("end_video_t")
            e["end"] = 1e9 + e["video_end_t"]
        evs.append(e)
    marks = [e for e in evs if e["type"] == "mark"]
    holds = [e for e in evs if e["type"] == "hold"]
    segs = []
    for k, mk in enumerate(marks):
        end_t = marks[k + 1]["video_t"] if k + 1 < len(marks) else frames / FPS
        segs.append({"index": k, "step": mk.get("step"), "name": mk.get("name"), "start_t": mk["video_t"],
                     "end_t": end_t, "start_frame": mk["frame"],
                     "end_frame": marks[k + 1]["frame"] if k + 1 < len(marks) else frames})
    man = {"run_id": name, "raw": "raw.mp4", "t0": 1e9, "fps": 30, "width": i["width"], "height": i["height"],
           "duration": frames / FPS, "frames": frames, "marks": marks, "holds": holds, "events": evs,
           "segments": segs}
    json.dump(man, open(os.path.join(d, "manifest.json"), "w"))
    return d


def verify_frame_assignment(work):
    """Review Major 3: the recorder stamps a mark at frame ceil(t*fps) and the segment starts there. A hold or an
    action logged a few ms after the mark belongs to that mark's clip, even when the mark falls in the first half of
    a frame (round() put it one frame earlier, into no clip or the previous one)."""
    clip = f"{SPEC}/fixtures/clean-reference-take/suche.mp4"
    evs = [{"type": "mark", "name": "one", "step": 1, "video_t": 0.51, "url": "https://x.de"},
           {"type": "hold", "kind": "landing", "seconds": 0.5, "step": 1, "video_t": 0.514},
           {"type": "mark", "name": "two", "step": 2, "video_t": 1.61, "url": "https://x.de"},
           {"type": "typing", "step": 2, "video_t": 1.612, "end_video_t": 1.9},
           {"type": "hold", "kind": "step", "seconds": 0.5, "step": 2, "video_t": 2.2}]
    d = recorder_run(work, "fx-frames", clip, evs)
    out, rec = cut(d)
    c1, c2 = rec["clips"]
    h1 = [h["kind"] for h in c1["holds"]]
    t2 = [e["type"] for e in c2["events"]]
    t1 = [e["type"] for e in c1["events"]]
    check("Q-51/Q-62 hold logged just after its mark stays in that clip", h1 == ["landing"],
          f"clip one holds {h1} (source frames {c1['source']['start_frame']}-{c1['source']['end_frame']})")
    check("Q-14 action logged just after its mark stays in that clip", t2 == ["typing"] and "typing" not in t1,
          f"clip one events {t1}, clip two events {t2}")


def verify_empty_segment(work):
    """Review minor: two marks in the same frame give an empty segment. The cutter must not crash (render's
    min([])) but deliver the step as a 1-frame clip flagged all-standstill, so the gate fails it on Q-14."""
    clip = f"{SPEC}/fixtures/clean-reference-take/suche.mp4"
    evs = [{"type": "mark", "name": "one", "step": 1, "video_t": 0.51, "url": "https://x.de"},
           {"type": "mark", "name": "two", "step": 2, "video_t": 0.52, "url": "https://x.de"}]
    d = recorder_run(work, "fx-empty", clip, evs)
    try:
        out, rec = cut(d)
    except Exception as e:   # noqa: BLE001
        check("Q-14 empty segment (two marks in one frame) is flagged, not a crash", False,
              f"cutter raised {type(e).__name__}: {str(e)[:200]}")
        return
    c1 = rec["clips"][0]
    gate = os.path.join(ROOT, "bin", "vc-gate")
    p = subprocess.run([gate, out, "--no-events", "--json", "--out", os.path.join(out, "qa")], capture_output=True,
                       text=True)
    try:
        q14 = [v for v in json.loads(p.stdout)["violations"] if v["check"] == "Q-14" and v["clip"] == "one"]
    except ValueError:
        q14 = []
    check("Q-14 empty segment (two marks in one frame) is flagged, not a crash",
          c1["all_standstill"] and c1.get("collapsed") and c1["frames"] == 1 and bool(q14),
          f"clip one: {c1['frames']} frame(s), all_standstill={c1['all_standstill']}, collapsed={c1.get('collapsed')}; "
          f"gate Q-14 on it: {len(q14)} (exit {p.returncode})")


def verify_cover_units():
    """Q-23 skeleton cover (S2, owner-approved): the unit tests of vc/cut/cover.py (synthetic + real c1/c2 frames +
    a rendered cover with the drawn ripple), run as one check."""
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.cut.test_cover"], cwd=ROOT, capture_output=True,
                       text=True)
    last = (p.stderr.strip().splitlines() or ["?"])
    check("Q-23 skeleton cover unit tests (detect, settle, drawn ripple, render)", p.returncode == 0,
          " / ".join(x for x in last[-3:] if x.strip()) if p.returncode == 0 else p.stderr[-1500:])


def verify_bridge_units():
    """Long loading after a click (FX-31): hold 2.0 s + cut + house fade, at plan level and on synthetic takes through
    the real cutter and gate (skeleton and black after a click PASS; black without a click FAILs Q-20)."""
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.cut.test_bridge"], cwd=ROOT, capture_output=True,
                       text=True)
    last = (p.stderr.strip().splitlines() or ["?"])
    check("long loading bridged: cut + fade, gate PASS; black without a click FAILs (FX-31)", p.returncode == 0,
          " / ".join(x for x in last[-3:] if x.strip()) if p.returncode == 0 else p.stderr[-1500:])


def main():
    if not SPEC or not os.path.isdir(os.path.join(SPEC, "visual-baselines")):
        print("SKIP cut verify: needs the acceptance data (visual baselines, fixture clips), which are private and not "
              "in this repo; set VC_ACCEPTANCE to that directory to run it")
        return 0
    work = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, ".verify")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    runs = os.path.join(work, "runs")
    src = synth.build(os.path.join(runs, "synth-take"))
    t = time.time()
    out, rec = cut(src)
    dt = time.time() - t
    print(f"cut of a {rec['timing']['raw_duration_s']} s raw take: {dt:.1f} s wall, {rec['timing']}")
    check("C-26 cut time (soft, 35 s for ~50 s raw)", dt <= 35, f"{dt:.1f} s for {rec['timing']['raw_duration_s']} s")
    verify_take(src, out, rec)
    gate_frames_check(out, "synthetic take")
    verify_recut(src)
    verify_crossfade_knob(src, work)
    verify_fixtures(work)
    verify_frame_assignment(work)
    verify_empty_segment(work)
    verify_cover_units()
    verify_bridge_units()
    print(f"\n{len(RESULTS) - len(FAILS)} passed, {len(FAILS)} failed" + (f": {FAILS}" if FAILS else ""))
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
