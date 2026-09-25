"""The speed-up badge (Q-77): a capsule pill 84x44 px, radius 22, rgb(24,24,28) at 80 % opacity, centred white bold
"4x" (the actual factor with the x sign) at 25 px, 28 px from the right and bottom edges. All sizes scale with
output width / 1920, rounded to whole pixels. The PNG holds the pill at full badge opacity; the per-frame opacity
(the 0.4 s speed ease) is applied when compositing (render.py).

Implemented here because the overlay milestone (M3) had no badge renderer yet; it is a pure cutter-side overlay.
"""
import os
import subprocess

from .tools import run, tool

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def font_file():
    env = os.environ.get("VC_BADGE_FONT")
    if env and os.path.exists(env):
        return env
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True, text=True).stdout
        if out and os.path.exists(out):
            return out
    except OSError:
        pass
    raise RuntimeError("no bold sans-serif font found for the speed badge (set VC_BADGE_FONT)")


def geometry(width):
    s = width / 1920.0
    r = lambda v: int(round(v * s))  # noqa: E731
    w, h, margin, font = r(84), r(44), r(28), r(25)
    return {"w": w, "h": h, "radius": h / 2.0, "margin": margin, "font": font}


def factor_label(factor):
    f = float(factor)
    return ("%d" % f if f == int(f) else ("%g" % f)) + "×"


FONT_FAMILIES = {"NotoSans-Bold.ttf": "Noto Sans", "DejaVuSans-Bold.ttf": "DejaVu Sans",
                 "LiberationSans-Bold.ttf": "Liberation Sans"}


def _ass(path, w, h, size, scale_x, family, label, x, y):
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nScaledBorderAndShadow: yes\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
                "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: B,%s,%.2f,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,%.1f,100,0,0,1,0,0,5,0,0,0,1"
                "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:10.00,B,,0,0,0,,{\\pos(%.2f,%.2f)}%s\n"
                % (w, h, family, size, scale_x, x, y, label))


def _text_mask(ass, w, h, fdir):
    out = subprocess.run([tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                          f"color=c=black:s={w}x{h}:r=1,format=rgb24", "-vf",
                          f"ass='{ass}':fontsdir='{fdir}',format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    pts = [(i % w, i // w) for i, v in enumerate(out) if v > 127]
    if not pts:
        return None
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


def make_badge(path, factor, width):
    """Write the badge PNG (RGBA, pill at 80 % opacity, text opaque white) for `factor` at output `width`.
    The text is calibrated to the measured glyph box of "4x" (26x18 px at 1920 wide, centred), whatever bold sans
    font the environment has."""
    g = geometry(width)
    w, h, rad = g["w"], g["h"], g["radius"]
    sc = width / 1920.0
    font = font_file()
    family = FONT_FAMILIES.get(os.path.basename(font), "Sans")
    fdir = os.path.dirname(font)
    ass = os.path.splitext(path)[0] + ".ass"
    label = factor_label(factor)
    # calibrate size, horizontal scale and centre on the "4x" glyph box, then apply to the actual label
    size, sx, x, y = float(g["font"]), 100.0, w / 2.0, h / 2.0
    for _ in range(3):
        _ass(ass, w, h, size, sx, family, "4\u00d7", x, y)
        bb = _text_mask(ass, w, h, fdir)
        if bb is None:
            break
        tw, th = bb[2] - bb[0] + 1, bb[3] - bb[1] + 1
        size *= (18 * sc) / th
        sx *= (26 * sc) / tw * (th / (18 * sc))
        x += w / 2.0 - (bb[0] + bb[2] + 1) / 2.0
        y += h / 2.0 - (bb[1] + bb[3] + 1) / 2.0
    _ass(ass, w, h, size, sx, family, label, x, y)
    # anti-aliased capsule coverage from the distance to the centre segment (rad, rad)-(w-rad, rad)
    cov = f"clip(0.5-(hypot(X+0.5-clip(X+0.5\\,{rad}\\,{w - rad})\\,Y+0.5-{rad})-{rad})\\,0\\,1)"
    fg = (f"color=c=black:s={w}x{h}:r=1,format=rgb24,ass='{ass}':fontsdir='{fdir}',format=gray,split[m1][m2];"
          f"color=c=0x18181C:s={w}x{h}:r=1,format=rgb24[base];"
          f"color=c=white:s={w}x{h}:r=1,format=rgb24[white];"
          f"[m1]format=rgb24[mrgb];"
          f"[base][white][mrgb]maskedmerge[rgb];"
          f"color=c=black:s={w}x{h}:r=1,format=gray,geq=lum='255*0.8*{cov}'[pa];"
          f"[pa][m2]blend=all_mode=lighten[alpha];"
          f"[rgb][alpha]alphamerge,format=rgba[out]")
    run([tool("ffmpeg"), "-v", "error", "-y", "-filter_complex", fg, "-map", "[out]", "-frames:v", "1", path])
    os.remove(ass)
    g.update({"font_file": font, "ass_size": round(size, 2), "scale_x": round(sx, 1)})
    return path, g


def badge_alpha_expr(alphas):
    """geq expression of the per-frame badge opacity (relative to the PNG, which is at full badge opacity)."""
    terms = []
    i, n = 0, len(alphas)
    rel = [round(a / 0.8, 3) if a > 0 else 0 for a in alphas]
    while i < n:
        if rel[i] == 0:
            i += 1
            continue
        j = i
        while j + 1 < n and rel[j + 1] == rel[i]:
            j += 1
        if j > i:
            terms.append(f"between(N\\,{i}\\,{j})*{rel[i]}")
        else:
            terms.append(f"eq(N\\,{i})*{rel[i]}")
        i = j + 1
    return "+".join(terms) if terms else "0"
