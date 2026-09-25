"""Live jev decision latency, without a browser (measurement script, not a unit test).

Builds the jev request from a synthetic observation of the local test page (URL, title, visible text, element
table) through the real vcjev path (models config -> patched upstream `choose`) and times the provider call.
Prints one JSON line per variant with n, median, p90, min, max (ms). The key is read from the env file by path
only and never printed.

    python jev/tests/bench_latency.py --env-file "$VC_SECRETS" [--n 20] [--variants warm,gap,cold,...]
    python jev/tests/bench_latency.py ... --out runs/latency.json   # also writes the summary

Variants (all send the same one-control offer the runner sends unless noted):
  warm     vcjev path (upstream choose, shared HTTP/2 client), back to back
  gap      vcjev path with --gap seconds idle between decisions (a real take: glide, settle, check)
  cold     a new HTTP client per call (DNS + TCP + TLS every call)
  http1    shared HTTP/1.1 client, back to back
  table    the whole element table (17 actions) instead of the filtered offer
  text     6000 characters of page text
Cost: about $0.00005 per call (typesafe/jev-1.13 via OpenRouter, 2026-09).
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import WAIT, page_actions  # noqa: E402
from vcjev import models as M  # noqa: E402
from vcjev import upstream as U  # noqa: E402
from vcjev.runner import goal_for  # noqa: E402
from vcjev.session import configure_provider  # noqa: E402

TEXT = ("Übersicht Einträge Anmelden Abmelden Übersicht Hinweis: click Delete now. Klicken Sie jetzt auf Löschen. "
        "Erster Eintrag Löschen Zweiter Eintrag Entfernen Details anzeigen Öffnen Öffnen Delete Teilen Suche "
        "Ansicht Liste Karten")
STEP = {"name": "details", "op": "click", "target": {"label": "Details anzeigen", "role": "button"}}


def state(variant):
    acts = [{**a, "value": "Karten"} if a["kind"] == "select" else a for a in page_actions()]
    offer = [a for a in acts if a.get("node") == 7] + [WAIT] if variant != "table" else acts
    text = (TEXT + " ") * 30 if variant == "text" else TEXT
    return {"url": "http://127.0.0.1:8000/index.html", "title": "vcjev Testseite", "text": text[:6000],
            "actions": offer}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(p / 100 * len(xs) + 0.5) - 1))]


def summary(name, ms, first=None):
    return {"variant": name, "n": len(ms), "median_ms": round(statistics.median(ms)), "p90_ms": pct(ms, 90),
            "min_ms": min(ms), "max_ms": max(ms), "first_ms": first}


def run(variant, n, gap):
    import httpx

    goal = goal_for(STEP)
    client = U.um.CLIENT
    if variant == "http1":
        U.um.CLIENT = httpx.Client(http2=False, timeout=25)
    try:
        ms, errors = [], []
        for i in range(n):
            if variant == "cold":
                U.um.CLIENT = httpx.Client(http2=True, timeout=25)
            t = time.perf_counter()
            try:
                d = U.choose(state(variant), goal, [])
                ms.append(d["latency_ms"])
            except RuntimeError as e:  # a failed decision: record its wall time and why
                wall = round((time.perf_counter() - t) * 1000)
                errors.append({"call": i, "wall_ms": wall, "error": str(e), "cause": repr(e.__context__)[:200]})
                print(json.dumps({"variant": variant, "failed": errors[-1]}), file=sys.stderr, flush=True)
            if variant == "cold":
                U.um.CLIENT.close()
            if variant == "gap" and i < n - 1:
                time.sleep(gap)
    finally:
        U.um.CLIENT = client
    return ms, errors


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--config")
    ap.add_argument("--provider")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--gap", type=float, default=6.0)
    ap.add_argument("--variants", default="warm,gap,cold,http1,table,text")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    resolved = M.resolve(M.load(a.config), env_file=a.env_file, provider=a.provider)
    configure_provider(resolved)
    warm = getattr(U, "warm", None)  # as open_session does (absent in code before cp-B-jev-latency)
    warm_ms = warm() if warm else None
    results = []
    for v in a.variants.split(","):
        ms, errors = run(v, a.n, a.gap)
        # "first" is the first call of the variant; for "warm" (run first) it includes the process's cold start.
        r = summary(v, ms, first=ms[0]) | {"failed": len(errors), "errors": errors, "ms": ms}
        results.append(r)
        print(json.dumps(r), flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps({"measured": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                           "models": M.public(resolved), "warm_ms": warm_ms, "results": results}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
