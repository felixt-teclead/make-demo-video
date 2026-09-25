"""One decoding pass over the raw recording: per-frame numbers the cut plan needs.

- mad[i]:   mean absolute luma difference between frame i-1 and frame i at 480x240 (section Q conventions).
            mad[0] = 0.
- lrange[i]: luma max - min of frame i at 480x240 (Q-20 "solid").
- thumbs:   160x90 grey thumbnails (robust blank test, splice join test).
- mids:     480x270 grey frames kept in a file (splice cursor test; read only at candidate splices).
"""
import array
import os
import subprocess

from .tools import tool

TW, TH = 160, 90
MW, MH = 480, 270


def _parse_meta(path, keys):
    out = {}
    cur = None
    with open(path) as f:
        for line in f:
            if line.startswith("frame:"):
                parts = line.split()
                pts_time = [p for p in parts if p.startswith("pts_time:")][0].split(":", 1)[1]
                cur = {"t": float(pts_time)}
                out[int(parts[0].split(":")[1])] = cur
            elif "=" in line and cur is not None:
                k, v = line.strip().split("=", 1)
                k = k.rsplit(".", 1)[-1]
                if k in keys:
                    cur[k] = float(v)
    return out


class Analysis:
    def __init__(self, n, mad, lrange, lmean, thumbs, mids_path):
        self.n = n
        self.mad = mad
        self.lrange = lrange
        self.lmean = lmean
        self._thumbs = thumbs
        self.mids_path = mids_path

    def thumb(self, i):
        return self._thumbs[i * TW * TH:(i + 1) * TW * TH]

    def mid(self, i):
        with open(self.mids_path, "rb") as f:
            f.seek(i * MW * MH)
            return f.read(MW * MH)

    def blank(self, i):
        """Solid / blank frame: luma range < 12 over the frame, ignoring the few most extreme pixels, so a cursor or a
        thin loading bar does not rescue a white-out (Q-20, Q-21 confirmation rule, applied on the thumbnail)."""
        if self.lrange[i] < 12:
            return True
        t = self.thumb(i)
        # 2 px inset on the 160x90 thumbnail (the 8 px inset at full size); the most extreme 0.2 % of the pixels
        # are ignored (0.1 % at each end)
        inner = b"".join(t[y * TW + 2:(y + 1) * TW - 2] for y in range(2, TH - 2))
        s = sorted(inner)
        k = max(1, len(s) // 1000)
        return s[-k - 1] - s[k] < 12

    def thumb_mad(self, i, j):
        a, b = self.thumb(i), self.thumb(j)
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    def mid_changes(self, i, j, thresh=24):
        """Pixels (480x270) whose luma changes by more than `thresh` between frames i and j, as (x, y) list."""
        a, b = self.mid(i), self.mid(j)
        out = []
        if a == b:
            return out
        for k, (x, y) in enumerate(zip(a, b)):
            if x - y > thresh or y - x > thresh:
                out.append((k % MW, k // MW))
        return out


def analyze(raw, workdir, frames_hint=None):
    os.makedirs(workdir, exist_ok=True)
    meta_a = os.path.join(workdir, "stats.txt")
    meta_b = os.path.join(workdir, "mad.txt")
    thumbs = os.path.join(workdir, "thumbs.gray")
    mids = os.path.join(workdir, "mids.gray")
    fg = (
        "[0:v]split=3[s1][s2][s3];"
        "[s1]scale=480:240:flags=area,format=yuv420p,split[m1][m2];"
        f"[m1]signalstats,metadata=mode=print:file={meta_a}[oa];"
        f"[m2]tblend=all_mode=difference,signalstats,metadata=mode=print:file={meta_b}[ob];"
        f"[s2]scale={TW}:{TH}:flags=area,format=gray[ot];"
        f"[s3]scale={MW}:{MH}:flags=area,format=gray[om]"
    )
    cmd = [tool("ffmpeg"), "-v", "error", "-y", "-i", raw, "-filter_complex", fg,
           "-map", "[oa]", "-f", "null", "-",
           "-map", "[ob]", "-f", "null", "-",
           "-map", "[ot]", "-f", "rawvideo", thumbs,
           "-map", "[om]", "-f", "rawvideo", mids]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"analysis failed: {p.stderr[-1500:]}")
    a = _parse_meta(meta_a, {"YMIN", "YMAX", "YAVG"})
    b = _parse_meta(meta_b, {"YAVG"})
    with open(thumbs, "rb") as f:
        tb = f.read()
    n = len(tb) // (TW * TH)
    mad = array.array("d", [0.0] * n)
    lrange = array.array("d", [255.0] * n)
    lmean = array.array("d", [0.0] * n)
    for i in range(n):
        s = a.get(i)
        if s:
            lrange[i] = s.get("YMAX", 255) - s.get("YMIN", 0)
            lmean[i] = s.get("YAVG", 0)
    # tblend emits one frame per pair (i-1, i), numbered from 0; its first output is the pair (0, 1).
    for k, s in b.items():
        if k + 1 < n:
            mad[k + 1] = s.get("YAVG", 0.0)
    os.remove(thumbs)
    return Analysis(n, mad, lrange, lmean, tb, mids)
