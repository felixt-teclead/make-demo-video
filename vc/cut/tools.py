"""ffmpeg / ffprobe discovery and small helpers. The cutter uses only the video tools and the stdlib."""
import json
import os
import shutil
import subprocess

FPS = 30


def tool(name):
    env = os.environ.get("VC_" + name.upper())
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    local = os.path.expanduser("~/.local/bin/" + name)
    if os.path.exists(local):
        return local
    raise RuntimeError(f"{name} not found (set VC_{name.upper()})")


def threads():
    """Threads for the encoders: the CPUs this process may use (cpuset aware)."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return os.cpu_count() or 1


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd))[:400]}\n{p.stderr[-2000:]}")
    return p


def probe(path):
    """width, height, fps, frames, duration, codec info of the first video stream (+ audio stream count)."""
    p = run([tool("ffprobe"), "-v", "error", "-count_packets", "-show_entries",
             "stream=codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,nb_read_packets"
             ":format=duration", "-of", "json", path])
    info = json.loads(p.stdout)
    v = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not v:
        raise RuntimeError(f"{path}: no video stream")
    v = v[0]
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    frames = int(v.get("nb_read_packets") or 0)
    return {
        "width": v["width"], "height": v["height"], "fps": num / den, "frames": frames,
        "duration": float(info.get("format", {}).get("duration") or frames / FPS),
        "codec": v.get("codec_name"), "profile": v.get("profile"), "pix_fmt": v.get("pix_fmt"),
        "audio_streams": sum(1 for s in info["streams"] if s.get("codec_type") == "audio"),
    }


# One encoder setting for every file the cutter and the joiner write (Q-81). Identical settings keep the
# parts of a join stream-copy compatible. No B-frames: then no part carries a reorder delay / edit list, so parts
# split at forced key frames and concatenated by stream copy keep exact 30 fps timestamps on every ffmpeg version
# (screen content gains little from B-frames).
def x264_args(preset="veryfast"):
    return ["-c:v", "libx264", "-preset", preset, "-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "18",
            "-x264-params", "open-gop=0:scenecut=0:keyint=300:min-keyint=1:bframes=0",
            "-threads", str(threads()), "-r", str(FPS), "-fps_mode", "cfr", "-an",
            "-movflags", "+faststart", "-video_track_timescale", "15360"]
