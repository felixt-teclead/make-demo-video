import argparse
import json
import sys

from .cutter import cut
from .render import CROSSFADE_DEFAULT_S, fade_frames


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m vc.cut", description="Cut a raw take into clips and join them.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut", help="cut RUN_DIR (a recut always goes to a new run directory, Q-58)")
    c.add_argument("run_dir")
    c.add_argument("--out", help="new run directory for a recut (default: <runs>/<stamp>-recut-<run_id>)")
    c.add_argument("--speed", type=float, default=4.0, help="speed-up factor (F-23 knob, default 4)")
    c.add_argument("--crossfade", type=float, default=CROSSFADE_DEFAULT_S,
                   help="site-switch cross-fade in s (F-23 knob crossfade_s, default 0.2; Q-72 allows 0.15-0.25)")
    c.add_argument("--keep-work", action="store_true", help="keep cut/work (analysis files) for debugging")
    a = ap.parse_args(argv)
    try:
        fade_frames(a.crossfade)
    except ValueError as e:
        ap.error(str(e))
    out, rec = cut(a.run_dir, a.out, a.speed, a.keep_work, crossfade_s=a.crossfade)
    summary = {"out": out, "recut": rec["recut"], "clips": len(rec["clips"]), "full_duration": rec["full_duration"],
               "fades": sum(1 for j in rec["joins"] if j["kind"] == "fade"), "timing": rec["timing"]}
    print(json.dumps(summary))
    return 0 if rec["full_frames"] == rec["full_expected_frames"] else 3


if __name__ == "__main__":
    sys.exit(main())
