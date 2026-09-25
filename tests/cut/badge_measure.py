"""Measure a badge crop (240x120 rgb24 raw): pill box, fill colour, text box. Used to compare with badge.png."""
import sys


def measure(buf, w=240, h=120):
    px = lambda x, y: buf[(y * w + x) * 3:(y * w + x) * 3 + 3]  # noqa: E731
    dark = [(x, y) for y in range(h) for x in range(w) if sum(px(x, y)) / 3 < 200]
    xs, ys = [p[0] for p in dark], [p[1] for p in dark]
    box = (min(xs), min(ys), max(xs), max(ys))
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    # text: bright pixels inside the pill box (away from the rim)
    txt = [(x, y) for y in range(box[1] + 4, box[3] - 3) for x in range(box[0] + 12, box[2] - 11)
           if min(px(x, y)) > 200]
    tx, ty = [p[0] for p in txt], [p[1] for p in txt]
    tbox = (min(tx), min(ty), max(tx), max(ty))
    fill = px(box[0] + 6, cy)
    return {"pill": box, "pill_wh": (box[2] - box[0] + 1, box[3] - box[1] + 1),
            "right_margin_in_crop": w - 1 - box[2], "bottom_margin_in_crop": h - 1 - box[3],
            "fill": tuple(fill), "text": tbox, "text_wh": (tbox[2] - tbox[0] + 1, tbox[3] - tbox[1] + 1),
            "text_centre_offset": ((tbox[0] + tbox[2]) / 2 - (box[0] + box[2]) / 2,
                                   (tbox[1] + tbox[3]) / 2 - (box[1] + box[3]) / 2)}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p.rsplit("/", 1)[-1], measure(open(p, "rb").read()))
