"""vc-gate: the S1 quality gate.

usage: vc-gate TAKE_DIR [--events FILE | --no-events] [--record FILE] [--raw | --layers] [--out DIR] [--json]
               [--workers N]

--layers (Q-03): the cut verdict plus every pixel check on the run's raw.mp4; each defect is placed in the layer
that made it (recording, cutter, raw only). The raw run is a diagnostic and never changes the verdict.

Exit status: 0 PASS, 1 FAIL, 3 ABORT (could not measure, Q-02; no count is printed), 2 usage/input error.
"""
import argparse
import json
import os
import sys

from . import gate, layers, report
from .inputs import InputError, load_take


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vc-gate", description="quality gate for a cut take")
    ap.add_argument("take", help="take directory (clips/index.json, cut/record.json, clips/events.json)")
    ap.add_argument("--events", help="event log in clip time (default: <take>/clips/events.json if present)")
    ap.add_argument("--no-events", action="store_true", help="run without an event log (event checks are skipped)")
    ap.add_argument("--record", help="cut record (default: <take>/cut/record.json if present)")
    ap.add_argument("--raw", action="store_true", help="raw mode: check the run's raw.mp4 (diagnostic, Q-03)")
    ap.add_argument("--layers", action="store_true",
                    help="cut verdict + raw run: say which layer (recording or cutter) each defect came from (Q-03)")
    ap.add_argument("--out", help="write report.txt and report.json here (default: <take>/qa when writable)")
    ap.add_argument("--json", action="store_true", help="print the JSON result instead of the text report")
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args(argv)
    if a.raw and a.layers:
        print("vc-gate: --raw and --layers exclude each other", file=sys.stderr)
        return gate.EXIT_USAGE

    try:
        take = load_take(a.take, a.events, a.record, a.no_events, "raw" if a.raw else "cut")
    except (InputError, OSError, ValueError) as e:
        print(f"vc-gate: input error: {e}", file=sys.stderr)
        return gate.EXIT_USAGE
    except Exception as e:   # noqa: BLE001  malformed inputs the loader did not foresee: no verdict (C3)
        print(f"vc-gate: {gate._error_reason(e)}", file=sys.stderr)
        return gate.EXIT_ABORT
    try:
        res, status = gate.run(take, a.workers)
    except Exception as e:   # noqa: BLE001  any unexpected error: ABORT with a report, never the FAIL status (C3)
        res, status = gate.abort_result(take, "(take)", gate._error_reason(e)), gate.EXIT_ABORT
    text = report.render(res)
    raw_res = None
    if a.layers and status != gate.EXIT_ABORT:
        try:
            raw_take = load_take(a.take, None, None, a.no_events, "raw")
        except (InputError, OSError, ValueError) as e:
            print(f"vc-gate: --layers: {e}", file=sys.stderr)
            return gate.EXIT_USAGE
        except Exception as e:   # noqa: BLE001  the raw diagnostic could not be loaded: noted, verdict unchanged (C3)
            raw_take, raw_res = None, gate.abort_result(take, "(raw)", gate._error_reason(e))
        if raw_take is not None:
            try:
                raw_res, _ = gate.run(raw_take, a.workers)
            except Exception as e:   # noqa: BLE001  never a traceback (exit 1 would read as FAIL); raw is diagnostic
                raw_res = gate.abort_result(raw_take, "(raw)", gate._error_reason(e))
        fps = _record_fps(a.record or os.path.join(take.root, "cut", "record.json"))
        diag = layers.diagnose(res, raw_res, take.record, {n: fps for n in take.record})
        res["layers"] = diag
        res["raw"] = {k: raw_res.get(k) for k in ("violations", "warnings", "elapsed", "abort", "clips")
                      if k in raw_res}
        text += layers.render(diag, raw_res)
    out = a.out
    if out is None and os.access(take.root, os.W_OK) and not a.raw:
        out = os.path.join(take.root, "qa")
    if out:
        try:
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "report.txt"), "w") as fh:
                fh.write(text)
            with open(os.path.join(out, "report.json"), "w") as fh:
                json.dump(res, fh, indent=1)
        except OSError:
            pass
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        sys.stdout.write(text)
    return status


def _record_fps(path):
    try:
        with open(path) as fh:
            return float(json.load(fh).get("fps") or 30.0)
    except (OSError, ValueError, TypeError):
        return 30.0


if __name__ == "__main__":
    sys.exit(main())
