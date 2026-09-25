# QA gate (S1 + S2 video-only checks) — interfaces and behaviour

Owner: milestone M6a (S1); S2-GATE added Q-23 and Q-03. Spec: `vc-v1-spec/sections/10-quality.md` (Q-01 to Q-95). The gate is deterministic, reads only
the listed clips, and runs no model (Q-95): its dependencies are ffmpeg/ffprobe and Python 3 with numpy.

## Commands

```sh
bin/vc-gate TAKE_DIR [--events FILE | --no-events] [--record FILE] [--raw | --layers] [--out DIR] [--json] [--workers N]
bin/vc-gate-fixtures [--stage all|S1|S2] [--only ID ...] [-v] [--keep] [--fixtures DIR]   # Q-90 suite
```

- Exit status: **0** PASS, **1** FAIL, **3** ABORT (could not measure, Q-02: no violation count is printed, the clip is
  named), **2** usage or input error.
- Output: the text report on stdout (or the JSON result with `--json`); both are also written to `TAKE_DIR/qa/`
  (`report.txt`, `report.json`) unless `--out` says otherwise.
- Python: `$VC_PYTHON`, else the repo's `.venv`, else `python3` (in the recorder container: `/opt/venv`, which
  contains `numpy` from the one requirements file `env/requirements.txt`). ffmpeg/ffprobe from PATH, `$VC_FFMPEG`/
  `$VC_FFPROBE`, or `~/.local/bin`.
- `--workers` (default min(8, cores)): clips are analysed in parallel processes.

## Inputs (cut mode)

`TAKE_DIR` is a run directory as in M1 `docs/interfaces.md` section 5.

1. **`clips/index.json`** (written by the cutter, M1 contract): `{"clips":[{"index","step","name","file","duration",
   "frames","holds":[{"kind","seconds","clip_t","spec_seconds"?}],"source":{...}}],"full":"full.mp4"}`. The gate
   checks exactly these clips, in this order (play order). Every other `.mp4` under `TAKE_DIR` (the joined full video,
   stray copies, `raw.mp4`) is listed as *ignored* and never analysed (Q-10). No index = 0 clips = Q-10 FAIL.
2. **`cut/record.json`** (the cut record, written by the cutter, M4). The gate reads, per clip (`clips[].name`):
   - `splices`: `[t, ...]` or `[{"t": ...}]`, delivered clip seconds where two kept pieces meet (the first frame after
     the splice is at `ceil(t*fps)`). Used by Q-30 (rule 1), Q-42 and Q-51.
   - `speedups`: `[{"start","end","factor","wait"?}]` clip seconds; Q-45's limit is divided by the factor and the
     paler ripple band is accepted inside.
   - `all_standstill`: `true` if the cutter found the clip all standstill (Q-14).
   - `map`: `[{"src_start","src_end","clip_start","speed"}]` raw seconds → clip seconds; used to map the recorder's
     raw-clock events into clip time (`vcgate.inputs.map_raw_events`). Events that fall in no kept piece are listed
     as `dropped` in the event log and fail Q-14.
   The gate never re-detects cuts from pixels. No record = no splices, no speed-ups.
3. **Event log in clip time** (required for a verdict): `clips/events.json` or `--events FILE`. Without one the gate
   ABORTs (exit 3): Q-10, Q-11, Q-13, Q-44, Q-51 and Q-62 cannot be measured, and the cut record is no substitute.
   `--no-events` is the explicit pixel-only diagnostic (the report says which checks were not run).
   ```json
   {"steps": ["prozesse", "suche"],            // spec step names in spec order (Q-10)
    "length": {"min": 30, "max": 60},          // spec length range in s (Q-13)
    "dry_run": false,                          // Q-11
    "final_hold": true,                        // the spec has a final hold on the last clip (Q-62)
    "dropped": [{"type": "click", "video_t": 12.3}],   // raw events that no clip kept (Q-14)
    "clips": {
      "prozesse": {
        "clicks": [{"t": 2.99, "x": 144, "y": 201, "label": "Prozesse", "glide_t": 2.55}],
        "marks": [0.0],                        // step mark(s); the clip start is the step's mark
        "holds": [{"kind": "step", "t": 3.8, "seconds": 1.0, "spec_seconds": 1.0}],
        "spec_holds": [{"kind": "step", "seconds": 1.0}],  // the spec's holds of this step, in runner order (Q-51):
                                               // logged holds are aligned to it by kind and order; a spec hold with
                                               // no logged hold is a "missing" Q-51 FAIL; the rest get spec_seconds
        "actions": [{"type": "pan", "t": 0.0, "end": 3.6}, {"type": "type", "t": 1.2}],
        "result": {"verified": true, "error": null}      // Q-11
      }}}
   ```
   Everything is in delivered clip seconds. The loop builds this file from the recorder's `events.jsonl`, the spec
   and the cut record (`map`). With `--no-events` the gate still runs every pixel check; the event-dependent parts
   are skipped and the report says so: Q-11, Q-13, Q-44 click matching, Q-37, Q-51/Q-62 holds from the log, and the
   click/mark/late-content explanations of Q-30 (unexplained jumps are then listed as "not judged").

## Raw mode (diagnostic)

`bin/vc-gate RUN_DIR --raw` runs every pixel check on `RUN_DIR/raw.mp4` as one clip, with the recorder's
`events.jsonl` (raw clock) as its event log. It never writes into the run dir unless `--out` is given. Raw mode does
not count toward the C-27 budget.

## Layers: raw versus cut (Q-03, S2)

`bin/vc-gate RUN_DIR --layers` (RUN_DIR = a cut run directory: `raw.mp4`, `events.jsonl`, `clips/`, `cut/record.json`)
runs the normal cut-mode gate (its verdict and exit status are unchanged), then every pixel check on `raw.mp4` (raw
mode), and appends a `layers` section to the report (`layers` and `raw` keys in the JSON). Each defect gets a layer:

| layer | meaning | what to fix |
|---|---|---|
| `recording` | found in the clips and, of the same check within 0.25 s of the mapped raw time, in the raw | the step or the page handling |
| `cutter` | found only in the clips (e.g. a teleport across a splice, a jump at a clip boundary) | the cutter |
| `raw only` | found only in the raw: "removed by the cutter" when none of its raw frames reached a clip, else "not seen in the clips" | nothing (the cut hid it); the recording still has it |
| `structure` | clip list, results, length, holds (Q-10, Q-11, Q-13, Q-14, Q-37, Q-51, Q-62): no raw counterpart | – |

Clip time maps to raw time through the cut record: per clip `frame_map` (raw frame of every delivered frame), else
`kept` (`clip_start, clip_end, src_start, src_end, factor`, half-open), else `map`. Raw-only structure findings (the
raw is one long clip) are not listed. The raw run's time is printed separately and is not part of C-27.
`--raw` and `--layers` exclude each other (exit 2). Code: `gate/vcgate/layers.py`.

## Checks

| Check | What the gate measures (default thresholds from the spec unless noted) | Needs events |
|---|---|---|
| Q-02 | clip missing, unreadable, no duration/frame rate, or 0 frames → ABORT (exit 3) | no |
| Q-10 | 0 clips always FAIL; clip names vs `steps` (missing, extra, order); unlisted mp4s ignored | step list only |
| Q-11 | `dry_run`; per-step `result.verified` and `error` | yes |
| Q-13 | sum of clip durations within [min, max + 1 s] | yes (range) |
| Q-14 | all-standstill clip (every MAD ≤ 0.5 and no low-res pixel changes > 12) or `all_standstill` in the record; logged actions outside their clip; dropped events | partly |
| Q-20 | solid frames: luma range < 12 at 480 px (0.1 % extremes ignored) for > 3 frames; WHITE / BLACK / flat colour | no |
| Q-21 | screens by 16×9 thumbnails (same screen within 6 luma); runs < 0.3 s fail unless a plain blend ≤ 0.5 s (d(A,x)+d(x,B) ≤ 1.25·d(A,B)); blank candidates confirmed at full resolution (8 px inset); a run < 0.3 s touching the clip's first or last frame always fails (no screen on that side, so no blend) | no |
| Q-23 | skeleton frames per logged click (S2), see below | yes (else "not judged") |
| Q-30 | scdet-style scene score > 0.45 at 480 px; groups < 0.1 s; explained by splice / click ±1.0 s / mark or hold start ±1.0 s / late content ≤ 2.5 s after the last click with no glide between and a clean switch / a passing pan | yes (else "not judged") |
| Q-31, Q-34 | see `gate/vcgate/checks/motion.py` | no |
| Q-37 | ≥ 0.9 s of stillness after each pan before the next logged action | yes |
| Q-40, Q-42, Q-43 | see `gate/vcgate/checks/cursor.py` | no |
| Q-44, Q-45 | see `gate/vcgate/checks/ripple.py` | Q-44 matching yes |
| Q-51 | each logged hold: logged vs spec length ±0.25 s; hold ends inside the clip; no splice inside a hold | yes (or index holds) |
| Q-71 | stray tooltip-like box (S2): a small framed, text-bearing box that appears or disappears as a unit and stays ≥ 0.5 s, see below | no (logged clicks exempt the clicked control) |
| Q-62 | final hold: last clip ≥ the hold and its last (hold − 0.25 s) all still (MAD ≤ 0.5); `final_hold` requires one | yes |

One defect is one violation (Q-06): a failing pan episode, a run of solid frames, a group of tiny screens and a
jump group are each reported once. Warnings are counted separately and never fail a take (Q-07).

## Fixture suite (Q-90)

`gate/fixtures/cases.json` lists every fixture of `vc-v1-spec/acceptance/fixtures/README.md`, the expected verdict
and check, the minimal event log (clip time) and cut-record context. The runner builds a take directory per case
(symlinks + `clips/index.json`), runs `bin/vc-gate` on it, and prints a per-fixture table and a confusion table.
Since S2-GATE every case is scored by default, including the two `q23-*` fixtures (Q-90 item 3); `--stage S1`
scores the S1 suite only. The fixture web page (Q-91) is not built here.

Confusion table (25 scored cases; rows expected, columns gate verdict):

```
expected      FAIL    WARN    PASS   ABORT   other
FAIL            20       0       0       0       0
WARN             0       1       0       0       0
PASS             0       0       2       0       0
ABORT            0       0       0       2       0
```

## Build notes: thresholds changed or added, and spec conflicts

All of the spec's default numbers are kept unless listed here. Every change was checked against the whole fixture suite
(23/23 scored fixtures as expected).

- **Q-34 duplicates (OD-15, owner's decision, revised 2026-09-25).** Duplicated frames are fine when the pan looks
  fine; only a visible hitch fails. The PASS fixture `q31-clean-pan-2036px-PASS` repeats 98 of 252 frames, at the
  same cadence as the q34 FAIL fixture, and stays a PASS. The gate applies the rule as follows:
  - a duplicate followed by a double update (a step ≥ 1.8× the local median right after a painted frame; this is the
    timer failure named in Q-34's Why) is a **FAIL**;
  - duplicates inside an evenly stepping pan are a **WARN** ("dropped paints").
  
  `Q34_STRICT` in `motion.py` stays `False` by default, as the owner decided. `True` restores the superseded
  2026-09-24 rule (any duplicate fails); the clean-pan fixture then fails.
- **Q-34 neighbour steps** are enforced: two consecutive frame intervals that both show a new, shifted picture may
  differ by at most 2 px (scaled by width/1920, plus 0.25 px measurement resolution); FAIL, one violation per pan,
  frames Q-31 already reports (jitter) not counted again. Steps next to a duplicate span two intervals and belong to
  the duplicate rule, so in a pan with dropped paints the excess is part of that WARN (the clean-pan fixture has two
  3 px neighbour steps), per the owner's OD-15 rule; with `Q34_STRICT = True` it fails as well.
- **Q-31 ride-along and animated edges** in the clean pan look like the FAIL fixtures in pixels: the headers ride
  55 px and then pin, and the dashes march. The gate separates them on secondary features:
  - a partial-pin frame (a header moving against the step);
  - coherent independent motion of ≥ 4 blocks of 32 px, spread ≥ 400 px, offset ≥ max(3 px, 0.5 × step), which in
    practice only happens during the ease-in.
  
  Each of these thresholds is fitted to one fixture.
- **Q-31 jitter.** The "−46 px reversal" given for `q31-list-scroll-jitter` is an alias at 480 px: the list repeats
  every ~202 px. At 1 px accuracy that scroll changes pace (16 → 23 → 13 px). The gate reports a "pace reversal":
  consecutive steps that change by ≥ max(5 px, 30 %) and reverse direction. Sticky bands are excluded per frame pair,
  not per episode. A motion counts as a pan only if at least ⅓ of its moving frames are shifted. Ease is not judged
  on a side where the clip cuts into the motion.
- **Q-44 ripple.**
  - A ring pixel must have chroma ≥ 0.8 × the blob's 98th-percentile chroma, on top of the spec's floor of 60,
    because the 38 % fill reaches chroma 59–81 on bluish pages.
  - The ring must have ≥ 0.5·π·d ring pixels spread over ≥ 7 of 8 sectors.
  - A box within 16 px of the frame edge counts as clipped.
  - New invariant (Q-76): a clean ripple is seen growing, so its first ring must be ≤ 0.75 × its peak diameter. This
    implements the owner's rule that a single faint frame is no ripple (`q44-click-ring-overpainted`).
- **Q-40.** A pointer is found by arrow-template correlation (from `cursor.png`). The demo cursor is recognised by the
  sprite's own look: a white fill and a dark outline (outline luma ≤ 140; the demo cursor measures ≤ 114, the Q-90 coral
  pointer ≥ 172), so a coloured button under the parked cursor no longer counts as a glow. Any other candidate is a
  pointer only if it has a tail (tail ratio ≥ 0.14 for the demo sprite, ≤ 0.04 for connector arrowheads), so static
  ▶ arrowheads in a diagram are not pointers. Frames are sampled every 0.5 s (OD-21). Owner-approved change
  2026-09-25 (FIXES-LEDGER FX-13, FX-14).
- **Q-42.** A cursor that is found before the splice and not after, where the area around its old tip is plain page
  in every frame after, fails as "teleported or vanished". Any other missing cursor is a WARN.
- **Q-30** uses an scdet-style scene score: the frame's mean difference, reduced by the previous frame's. With no
  event log, jump groups that no splice, pan or clip-start mark explains are listed as "not judged".
- **Q-14.** A clip is all standstill when every MAD ≤ 0.5 and no low-res pixel changes by more than 12. Because of
  this, `q43-...-clipB` (2 s, nothing moves) also reports Q-14.
- **Co-occurring findings on real fixtures:**
  - q20-black and q45 carry the R01 coral pointer (Q-40), as the README says.
  - q20-black at 0.43 s and q45 at 7.13 s show a real ±16–27 px wobble of the consent dialog, which fires Q-31.

- **Q-23 (S2)** follows the spec's detection with these choices (all fitted on the q23 fixtures and checked on every
  fixture with clicks, the clean take and synthetic stand-ins in `gate/tests/test_s2.py`):
  - analysed on gray frames at half resolution; the view changes where |settled − pre-click| ≥ 16 luma; the settled
    view is the window's last frame (2.4 s, or the frame before the next glide/click, or the clip end);
  - "25 % of the settled view's sharp edges" is an **edge pixel count** (neighbour step ≥ 24 luma) inside the change
    area, not a position match: `synthetic-q30-late-content-3s` has a dialog that settles a few px off, which a
    position match (1 px) scored as 22–38 % and falsely called 15 skeleton frames; the count gives 0.56–0.64 there,
    against 0.08–0.13 (11f) and 0.02 (1f) on the skeletons;
  - a plain blend of old and new is exempt: least-squares fit `(1−a)·pre + a·settled` with RMS residual ≤ 15 % of
    |settled − pre|. Real cross-fades fit to ~2 %; the skeletons sit at 38–52 %; a blurred stand-in at 33 % (so the
    limit is not 35 %);
  - the click position (110 px exclusion) is the logged `x, y`, else the click's own ripple (within 0.5 s), else none;
  - skeleton frames per click need not be contiguous; one violation per click (Q-06).
  - a long load the cutter bridged (FX-31: held frame 2.0 s, wait cut out, house fade into the settled view) is
    judged like any other clip, no special case: the held frames equal the pre-click frame (not gone), the fade frames
    are plain blends. The record's summary is printed as an info line ("cutter: click "X" at 0.53 s: loading 3.4 s:
    cut to 2.0 s + fade"). The fade's first frame is a Q-30 hard jump, explained by the splice there.
  Measured: q23-11f → 11 frames at 1.30–1.67 s (FAIL); q23-1f → 1 frame at 1.33 s (WARN, take passes); 0 frames for all
  7 clicks of the clean take and for the clicks of q44/q14/q30 fixtures.

- **Q-71 (S2, partial: tooltip-like boxes only)**, `gate/vcgate/checks/tooltip.py`. QA definition change approved by
  the owner 2026-09-25 (self-improvement rule: detect what the parity viewer found: Chrome's native "Prozesse" title
  tooltip mid-card 2.87–5.17 s in the delivered c2 of 06:48, missed by every other check). Every 3rd frame at full
  resolution, gray. Between two samples, changed pixels (≥ 32 luma) are grouped on an 8 px block grid (a pair with
  > 4000 changed blocks is a view change: skipped). A group is a box when its bbox is 14–64 × 20–640 px (1920 scale),
  each side's outermost 2 px hold a line (≥ 75 % of its pixels changed and within 32 luma of the side's median), the
  line contrasts with the fill by ≥ 40, ≥ 40 % of the inside is fill and 2–60 % are glyphs (≥ 60 from the fill), and the
  region's shapes before/after differ (gradient correlation < 0.6; a framed button that only dims under a dialog
  backdrop scored 0.75+ and is exempt). A box containing a logged click point (±12 px) is the control's own state.
  Its time on screen is where the region stays within 8 luma (mean) of the found box; ≥ 0.5 s FAILs, one violation per
  box. A box that appears together with a view change and stays past the clip end is not seen in that clip; it is seen
  where it disappears. Measured: all c2 takes of 2026-09-25 FAIL on `suche` 0.00–1.0/1.2 s (the tooltip carried over
  from `prozesse`); 0 boxes in every other clip of the c1/c2/c3 takes of 2026-09-25 and in the fixture suite. Tests:
  `gate/tests/test_tooltip.py` (real crops in `gate/tests/data/tooltip/`).

## Timing (C-27)

The clean reference take has 5 clips, 31.8 s and 1003 frames at 1910×986. The gate ran pinned to cores 8–15
(`taskset -c 8-15`) on a shared host (load average ~3) and took 9.4–9.6 s wall over 3 runs. The soft target is
~15 s.

After S2-GATE (Q-23 added), same pinning, but the host was busy (load average 10–15 from other work): 13.5 s best,
20.9 s typical over 3 runs. Q-23 costs 0.3–1.1 s per clip (one half-resolution decode of ≤ 2.4 s per click); on the
critical path (schritt, 3 clicks) 0.8–1.05 s. Re-measure on an idle host. The critical path is schritt.mp4: 1.4 s decode, 3.5 s motion, 3.9 s ripple and pointer scan.

## Pending

- **The Q-91 fixture web page is not built here** (it needs the browser). Its planted cases are pending, including
  the Q-30 late-content target at 1 s and at 3 s.
- **Q-48 (pressed state) and Q-71 (stray browser interface)** are S2 but **Look** checks (sampled frames, frame-check
  model); the deterministic gate runs no model (Q-95). Since 2026-09-25 the gate covers one Q-71 case by pixels:
  tooltip-like boxes (see Build notes); URL bubbles, banners and toasts remain Look.
- **Q-23 cover** is built in the cutter (docs/cut-interfaces.md); the gate only measures the delivered clip.
