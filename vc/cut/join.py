"""The joiner (Q-72, Q-82): clips in play order -> one full video.

Same site: hard cut. Site switch (hosts differ, both known): a linear 0.2 s (6 frame; knob crossfade_s) opacity cross-fade between the
last 6 frames of clip N and the first 6 frames of clip N+1; frame j of the fade = (1 - j/6) * A + j/6 * B, so the
fade starts on a pure A frame, is 50/50 at its middle (j = 3) and is followed by B's 7th frame. Every frame outside
a fade is a stream copy of the clip (bit-identical), so only the 6 fade frames are encoded; a join without a fade
is a pure stream copy.
"""
import os
import shutil
import subprocess

from .render import FADE_FRAMES
from .tools import FPS, probe, run, tool, x264_args


def boundary_kind(site_a, site_b):
    if site_a and site_b and site_a != site_b:
        return "fade"
    return "cut"


def _split(clip, frames, workdir, tag, head, tail):
    """Stream-copy parts of `clip`: [head, frames - tail). Relies on the key frames the cutter forces at 6 and K-6."""
    if head == 0 and tail == 0:
        return clip
    out = os.path.join(workdir, f"{tag}-body.mp4")
    pat = os.path.join(workdir, f"{tag}-part%d.mp4")
    points = []
    if head:
        points.append(str(head))
    if tail:
        points.append(str(frames - tail))
    run([tool("ffmpeg"), "-v", "error", "-y", "-i", clip, "-map", "0:v", "-c", "copy", "-f", "segment",
         "-segment_frames", ",".join(points), "-reset_timestamps", "1", pat])
    idx = 1 if head else 0
    shutil.move(pat % idx, out)
    return out


def _fade(clip_a, frames_a, clip_b, workdir, tag, preset, n=FADE_FRAMES):
    out = os.path.join(workdir, f"{tag}-fade.mp4")
    fg = (f"[0:v]select='gte(n\\,{frames_a - n})',setpts=N/{FPS}/TB[a];"
          f"[1:v]select='lt(n\\,{n})',setpts=N/{FPS}/TB[b];"
          f"[a][b]blend=all_expr='A*(1-T*{FPS}/{n})+B*(T*{FPS}/{n})',format=yuv420p[o]")
    run([tool("ffmpeg"), "-v", "error", "-y", "-i", clip_a, "-i", clip_b, "-filter_complex", fg, "-map", "[o]"]
        + x264_args(preset) + ["-frames:v", str(n), "-force_key_frames:v", "expr:1", out])
    return out


def join(clips, out_path, workdir, preset="veryfast", fade_n=FADE_FRAMES):
    """clips: list of {"path", "frames", "site_start", "site_end", "index"} in play order. fade_n: frames of a
    site-switch cross-fade (the crossfade_s knob, see render.fade_frames). Returns the join record."""
    os.makedirs(workdir, exist_ok=True)
    kinds = [boundary_kind(clips[i]["site_end"], clips[i + 1]["site_start"]) for i in range(len(clips) - 1)]
    parts = []
    joins = []
    t = 0.0
    for i, c in enumerate(clips):
        head = fade_n if i > 0 and kinds[i - 1] == "fade" else 0
        tail = fade_n if i < len(kinds) and kinds[i] == "fade" else 0
        parts.append(_split(c["path"], c["frames"], workdir, f"c{i:02d}", head, tail))
        t += (c["frames"] - head - tail) / FPS
        if i < len(kinds):
            j = {"between": [c["index"], clips[i + 1]["index"]], "kind": kinds[i],
                 "sites": [c["site_end"], clips[i + 1]["site_start"]]}
            if kinds[i] == "fade":
                parts.append(_fade(c["path"], c["frames"], clips[i + 1]["path"], workdir, f"f{i:02d}", preset, fade_n))
                j.update({"full_start": round(t, 4), "full_mid": round(t + fade_n / 2 / FPS, 4),
                          "full_end": round(t + fade_n / FPS, 4), "duration": fade_n / FPS,
                          "frames": fade_n, "curve": "linear"})
                t += fade_n / FPS
            else:
                j.update({"full_t": round(t, 4)})
            joins.append(j)
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))
    tmp = out_path + ".part.mp4"
    # parts have no B-frames (tools.x264_args), so decode order = display order and the timestamps can be rewritten
    # to exact 30 fps (the segment muxer of some ffmpeg versions stretches a part's last packet by a few ticks)
    run([tool("ffmpeg"), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v", "-c", "copy",
         "-bsf:v", f"setts=pts=N/({FPS}*TB):dts=N/({FPS}*TB):duration=1/({FPS}*TB)", "-movflags", "+faststart", "-video_track_timescale", "15360", tmp])
    os.replace(tmp, out_path)
    info = probe(out_path)
    expected = sum(c["frames"] for c in clips) - fade_n * kinds.count("fade")
    return {"joins": joins, "full_frames": info["frames"], "expected_frames": expected,
            "full_duration": round(info["frames"] / FPS, 4), "fades": kinds.count("fade")}
