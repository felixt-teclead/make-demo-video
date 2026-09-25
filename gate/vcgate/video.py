"""Video access: probing and decoding through ffmpeg/ffprobe (the only video tools the gate uses, Q-95)."""
import json
import os
import shutil
import subprocess

import numpy as np

LOW_W, LOW_H = 480, 240  # the MAD scale of section Q ("after scaling to 480x240")


def _tool(name):
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


FFMPEG = None
FFPROBE = None


def ffmpeg():
    global FFMPEG
    if FFMPEG is None:
        FFMPEG = _tool("ffmpeg")
    return FFMPEG


def ffprobe():
    global FFPROBE
    if FFPROBE is None:
        FFPROBE = _tool("ffprobe")
    return FFPROBE


class Unmeasurable(Exception):
    """The gate cannot measure this clip (Q-02): abort, no verdict."""

    def __init__(self, clip, reason):
        super().__init__(f"{clip}: {reason}")
        self.clip = clip
        self.reason = reason


def probe(path, name):
    """Return dict(width, height, fps, duration, frames). Raises Unmeasurable (Q-02)."""
    if not os.path.isfile(path):
        raise Unmeasurable(name, "file missing")
    try:
        out = subprocess.run(
            [ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate,nb_frames,duration:format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise Unmeasurable(name, "ffprobe timed out")
    if out.returncode != 0:
        raise Unmeasurable(name, "unreadable (" + (out.stderr.strip().splitlines() or ["ffprobe failed"])[-1] + ")")
    info = json.loads(out.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise Unmeasurable(name, "no video stream")
    s = streams[0]
    try:
        num, den = s.get("r_frame_rate", "0/1").split("/")
        fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    dur = s.get("duration") or (info.get("format") or {}).get("duration")
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        dur = None
    try:
        frames = int(s.get("nb_frames"))
    except (TypeError, ValueError):
        frames = None
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    if not w or not h:
        raise Unmeasurable(name, "no frame size")
    if dur is None or dur <= 0:
        raise Unmeasurable(name, "no readable duration")
    if fps <= 0:
        raise Unmeasurable(name, "no readable frame rate")
    if frames is not None and frames <= 0:
        raise Unmeasurable(name, "decodes to 0 frames")
    return {"width": w, "height": h, "fps": fps, "duration": dur, "frames": frames}


def _read_frames(cmd, frame_bytes, name):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_bytes * 4)
    data = p.stdout.read()
    err = p.stderr.read()
    p.wait()
    if p.returncode != 0:
        raise Unmeasurable(name, "decode failed (" + (err.decode(errors="replace").strip().splitlines() or ["?"])[-1] + ")")
    n = len(data) // frame_bytes
    return data, n


def decode_low(path, name):
    """All frames as 8-bit luma at 480x240 (area scaling). Returns uint8 array (n, 240, 480)."""
    cmd = [ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0", "-vf",
           f"scale={LOW_W}:{LOW_H}:flags=area,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    data, n = _read_frames(cmd, LOW_W * LOW_H, name)
    if n == 0:
        raise Unmeasurable(name, "decodes to 0 frames")
    return np.frombuffer(data, np.uint8, n * LOW_W * LOW_H).reshape(n, LOW_H, LOW_W)


def decode_full(path, name, width, height, start_frame=0, count=None, fps=30.0, pix_fmt="rgb24"):
    """Frames [start_frame, start_frame+count) at native resolution. rgb24 -> (n,h,w,3); gray -> (n,h,w)."""
    ch = 3 if pix_fmt == "rgb24" else 1
    cmd = [ffmpeg(), "-v", "error", "-nostdin"]
    if start_frame > 0:
        # accurate input seek: decodes from the previous key frame and drops frames before the timestamp
        cmd += ["-ss", "%.6f" % ((start_frame - 0.5) / fps)]
    cmd += ["-i", path, "-map", "0:v:0", "-fps_mode", "passthrough"]   # see decode_scaled
    if count is not None:
        cmd += ["-frames:v", str(int(count))]
    cmd += ["-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
    fb = width * height * ch
    data, n = _read_frames(cmd, fb, name)
    shape = (n, height, width, 3) if ch == 3 else (n, height, width)
    return np.frombuffer(data, np.uint8, n * fb).reshape(shape)


def iter_full(path, name, width, height, pix_fmt="rgb24", chunk=32):
    """Stream native-resolution frames in chunks (bounded memory). Yields (first_index, array)."""
    ch = 3 if pix_fmt == "rgb24" else 1
    fb = width * height * ch
    cmd = [ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0",
           "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fb * 2)
    idx = 0
    eof = False
    try:
        while True:
            buf = p.stdout.read(fb * chunk)
            n = len(buf) // fb
            if n == 0:
                eof = True
                break
            shape = (n, height, width, 3) if ch == 3 else (n, height, width)
            yield idx, np.frombuffer(buf, np.uint8, n * fb).reshape(shape)
            idx += n
            if n < chunk:
                eof = True
                break
    finally:
        p.stdout.close()
        p.wait()
    if eof and p.returncode != 0:     # read to the end but ffmpeg failed: a partial decode (Q-02)
        raise Unmeasurable(name, f"decode failed (ffmpeg exit {p.returncode})")


def decode_scaled(path, name, width, height, start_frame=0, count=None, fps=30.0):
    """Gray frames [start_frame, start_frame+count) scaled to width x height (area scaling). (n, h, w) uint8.

    -fps_mode passthrough: frame k of the output is decoded frame start_frame + k. Without it the rawvideo output is
    constant-rate: after a seek into a variable-rate recording (raw.mp4) ffmpeg repeated the first frame and shifted
    the rest by one (measured on the c2/c3 raw takes of 2026-09-25: 15 of 345 seek starts)."""
    cmd = [ffmpeg(), "-v", "error", "-nostdin"]
    if start_frame > 0:
        cmd += ["-ss", "%.6f" % ((start_frame - 0.5) / fps)]
    cmd += ["-i", path, "-map", "0:v:0", "-fps_mode", "passthrough"]
    if count is not None:
        cmd += ["-frames:v", str(int(count))]
    cmd += ["-vf", f"scale={width}:{height}:flags=area,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    data, n = _read_frames(cmd, width * height, name)
    return np.frombuffer(data, np.uint8, n * width * height).reshape(n, height, width)
