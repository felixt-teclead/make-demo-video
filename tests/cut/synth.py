"""Synthesise a raw take in M1's run-directory layout (docs/interfaces.md section 5) for cutter tests.

Scenario (video seconds, 1920x1080, 30 fps), built from the spec's baseline stills:
  0.0- 1.0  off-camera loading view (before the first mark; must never reach a clip, Q-15)
  step 1 (mark 1.0, site app.example.com)
  1.0- 2.0  landing hold (logged)            2.0- 6.0  dead air (not logged: shorten, Q-50)
  6.0- 6.4  cursor glide                      6.5       click (logged, glide_t 6.0) -> view B at 6.8
  7.5- 8.5  step hold    8.5-11.5 reading hold (logged, Q-51)     11.5-14.0 dead air
  step 2 (mark 14.0, same site)
  14.0-14.4 glide, 14.5 click that navigates  14.8-15.4 white blank (cursor still drawn)  15.4-15.5 half-painted
  15.5-23.5 readiness wait with a spinner (logged: speed-up + badge, Q-56)   23.5 content arrives (view D)
  24.0-25.0 step hold    25.0-26.0 caret blinks   26.0-26.2 a real 0.2 s scroll (must survive, Q-50)
  26.2-29.0 caret blinks (near-still)
  step 3 (mark 29.0, site claude.ai: a site switch -> cross-fade, Q-72)
  30.0-31.0 typing (logged)   31.0-33.0 dead air   33.0-45.0 final hold (logged, 12 s)
"""
import json
import os
import subprocess
import sys

# the private acceptance data (visual baselines); tests/cut/verify.py skips when VC_ACCEPTANCE is unset
ACCEPTANCE = os.environ.get("VC_ACCEPTANCE")
VB = os.path.join(ACCEPTANCE, "visual-baselines") if ACCEPTANCE else None
T0 = 1727200000.0
FPS = 30


def ffmpeg():
    for p in (os.environ.get("VC_FFMPEG"), os.path.expanduser("~/.local/bin/ffmpeg"), "ffmpeg"):
        if p and (os.path.exists(p) or p == "ffmpeg"):
            return p


def sine(t0, t1, a, b):
    return f"({a}+({b}-({a}))*(1-cos(PI*(t-{t0})/{t1 - t0}))/2)"


def build(run_dir, duration=45.0):
    if not VB or not os.path.isdir(VB):
        raise RuntimeError("set VC_ACCEPTANCE to the acceptance data dir (visual-baselines/, fixtures/)")
    os.makedirs(run_dir, exist_ok=True)
    scenes = [  # (start, end, source)
        (0.0, 1.0, "offcam"),
        (1.0, 6.8, f"{VB}/cursor-full.png"),
        (6.8, 14.8, f"{VB}/final-hold.png"),
        (14.8, 15.4, "white"),
        (15.4, 15.5, "half"),
        (15.5, 23.5, f"{VB}/fade-start.png"),
        (23.5, 29.0, "scrollD"),
        (29.0, duration, f"{VB}/fade-end.png"),
    ]
    inputs, chains, labels = [], [], []
    for i, (a, b, src) in enumerate(scenes):
        d = b - a
        if src == "offcam":  # off-camera setup: a moving test pattern that must never reach a clip
            chains.append(f"testsrc2=s=1920x1080:r={FPS}:d={d},format=yuv420p[s{i}]")
        elif src == "white":
            chains.append(f"color=c=white:s=1920x1080:r={FPS}:d={d}[s{i}]")
        elif src == "half":
            chains.append(f"color=c=white:s=1920x1080:r={FPS}:d={d},drawbox=x=0:y=0:w=1920:h=56:c=0xF2F2F2:t=fill,"
                          f"drawbox=x=0:y=56:w=240:h=1024:c=0xF6F6F6:t=fill[s{i}]")
        elif src == "scrollD":
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", str(d), "-i", f"{VB}/badge-full.png"]
            k = inputs.count("-i") - 1
            # view D, 1400 px tall canvas; a 200 px scroll at 26.0-26.2 (clip-local 2.5-2.7)
            chains.append(f"[{k}:v]scale=1920:1400,setsar=1[d{i}];color=c=white:s=1920x1080:r={FPS}:d={d}[bg{i}];"
                          f"[bg{i}][d{i}]overlay=x=0:y='if(lt(t,2.5),0,if(lt(t,2.7),-200*(t-2.5)/0.2,-200))'"
                          f":shortest=1[s{i}]")
        else:
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", str(d), "-i", src]
            k = inputs.count("-i") - 1
            chains.append(f"[{k}:v]scale=1920:1080,setsar=1,format=yuv420p[s{i}]")
        labels.append(f"[s{i}]")
    # cursor position over time (tip at x,y): rest (1500,100), glide to (800,500) 6.0-6.4, glide to (400,300)
    # 14.0-14.4, glide to (960,640) 29.3-29.6 (the typing field)
    cx = (f"if(lt(t,6),1500,if(lt(t,6.4),{sine(6, 6.4, 1500, 800)},if(lt(t,14),800,if(lt(t,14.4),"
          f"{sine(14, 14.4, 800, 400)},if(lt(t,29.3),400,if(lt(t,29.6),{sine(29.3, 29.6, 400, 960)},960))))))")
    cy = (f"if(lt(t,6),100,if(lt(t,6.4),{sine(6, 6.4, 100, 500)},if(lt(t,14),500,if(lt(t,14.4),"
          f"{sine(14, 14.4, 500, 300)},if(lt(t,29.3),300,if(lt(t,29.6),{sine(29.3, 29.6, 300, 640)},640))))))")
    post = (
        f"{''.join(labels)}concat=n={len(scenes)}:v=1:a=0,format=yuv420p[v0];"
        # spinner during the wait: a 24 px box that hops around a small circle every 2 frames
        f"[v0]drawbox=x='940+14*cos(2*PI*floor(t*15)/8)':y='520+14*sin(2*PI*floor(t*15)/8)':w=24:h=24:c=0x3070E0:t=fill:"
        f"enable='between(t,15.5,23.5)'[v1];"
        # caret blink in view D
        f"[v1]drawbox=x=700:y=600:w=3:h=26:c=black:t=fill:enable='between(t,25,29)*lt(mod(t,1),0.5)'[v2];"
        # typing: a text bar that grows 30.0-31.0 (field at 960,640)
        f"[v2]drawbox=x=900:y=620:w='max(1,min(400,400*(t-30)))':h=22:c=0x333333:t=fill:enable='gte(t,30)'[v3];"
        # the demo cursor (outline + white fill), present from the first frame
        f"color=c=0x111111:s=18x24:r={FPS}[co];color=c=white:s=14x20:r={FPS}[ci];[co][ci]overlay=2:2[cur];"
        f"[v3][cur]overlay=x='{cx}':y='{cy}':shortest=1:eval=frame[v4];"
        # a blue ring (ripple) around each click, 0.5 s
        f"[v4]drawbox=x=800-46:y=500-46:w=92:h=92:c=0x4DA3FF:t=5:enable='between(t,6.5,7.0)',"
        f"drawbox=x=400-46:y=300-46:w=92:h=92:c=0x4DA3FF:t=5:enable='between(t,14.5,14.79)'[out]"
    )
    fg = ";".join(chains) + ";" + post
    raw = os.path.join(run_dir, "raw.mp4")
    cmd = [ffmpeg(), "-v", "error", "-y"] + inputs + ["-filter_complex", fg, "-map", "[out]", "-t", str(duration),
                                                      "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
                                                      "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(FPS), "-an",
                                                      "-movflags", "+faststart", raw]
    subprocess.run(cmd, check=True)

    app, claude = "https://app.example.com/prozesse", "https://claude.ai/new"
    ev = []

    def e(typ, vt, **kw):
        d = {"type": typ, "t": T0 + vt, "video_t": vt, "frame": int(round(vt * FPS))}
        d.update(kw)
        ev.append(d)
        return d

    e("start", 0.0)
    marks = [e("mark", 1.0, name="landing", step=1, url=app),
             e("mark", 14.0, name="open-details", step=2, url=app),
             e("mark", 29.0, name="ask-claude", step=3, url=claude)]
    holds = [e("hold", 1.0, kind="landing", seconds=1.0, step=1),
             e("hold", 7.5, kind="step", seconds=1.0, step=1),
             e("hold", 8.5, kind="reading", seconds=3.0, step=1),
             e("hold", 24.0, kind="step", seconds=1.0, step=2),
             e("hold", 33.0, kind="final", seconds=12.0, step=3)]
    e("click", 6.5, step=1, label="Prozess", x=800, y=500, glide_t=T0 + 6.0)
    e("click", 14.5, step=2, label="Details", x=400, y=300, glide_t=T0 + 14.0, navigates=True)
    e("readiness_wait", 15.5, step=2, end=T0 + 23.5, ok=True)
    e("typing", 30.0, step=3, end=T0 + 31.0, text="x" * 27)
    ev.sort(key=lambda d: d["t"])
    with open(os.path.join(run_dir, "events.jsonl"), "w") as f:
        for d in ev:
            f.write(json.dumps(d) + "\n")
    n = int(duration * FPS)
    segs = []
    for i, m in enumerate(marks):
        end = marks[i + 1]["video_t"] if i + 1 < len(marks) else duration
        segs.append({"index": i, "step": m["step"], "name": m["name"], "start_t": m["video_t"], "end_t": end,
                     "start_frame": m["frame"], "end_frame": int(round(end * FPS)),
                     "holds": [h for h in holds if m["video_t"] <= h["video_t"] < end]})
    man = {"run_id": os.path.basename(run_dir.rstrip("/")), "raw": "raw.mp4", "t0": T0, "fps": FPS, "width": 1920,
           "height": 1080, "duration": duration, "frames": n, "marks": marks, "holds": holds, "events": ev,
           "segments": segs}
    with open(os.path.join(run_dir, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1)
    return run_dir


if __name__ == "__main__":
    build(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 45.0)
