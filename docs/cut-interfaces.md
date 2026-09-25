# Cutter and joiner (M4): interfaces

Status: v0.1 (M4). Requirement IDs refer to `vc-v1-spec`. Code: `vc/cut/`, CLI `bin/vc-cut`, tests `tests/cut/`.
Tools: the stdlib of Python ≥ 3.11 plus ffmpeg/ffprobe (needs the filters `tblend signalstats blend maskedmerge ass`,
libx264; Debian bookworm's ffmpeg 5.1 in the M1 image and ffmpeg 7 both work). No model call, no numpy.

## Command

```sh
bin/vc-cut RUN_DIR [--out NEW_RUN_DIR] [--speed 4] [--crossfade 0.2] [--keep-work]      # = python3 -m vc.cut cut ...
```

- First cut of a take: writes `clips/`, `cut/record.json` and `full.mp4` into `RUN_DIR` (M1 layout, section 5).
  Output is staged and published only on success, so a failed cut leaves nothing half-written.
- Any later cut (RUN_DIR already has `clips/`, `cut/` or `full.mp4`, or `--out` is given) is a **recut**: it
  always goes to a new run directory (default `<runs>/<YYYYmmdd-HHMMSS>-recut-<run_id>/`) holding `clips/`, `cut/`,
  `full.mp4` and `source.json`; the source directory is never written (Q-58). `--out` equal to or inside the
  source is refused.
- Prints one JSON line (`out, recut, clips, full_duration, fades, timing`). Exit 0; 3 if the joined video's frame
  count differs from the plan.
- `--speed` is the F-23 speed-up knob (default 4). `--crossfade` is the F-23 knob `crossfade_s` (default 0.2 s =
  6 frames); Q-72 allows 0.15–0.25 s, so the value is rounded to whole frames inside 5–7 frames and anything outside
  the range is refused (exit 2). The record's `params` carry `fade_s` and `fade_frames`.

## Input (what the cutter reads from RUN_DIR)

`manifest.json` (M1): `t0`, `raw`, `marks`, `holds`, `events`, `segments` (or marks only: segments are then mark to
next mark). Times: `video_t`, or epoch `t` minus `t0`. Extra event fields the cutter uses:

| event `type` | fields | effect |
|---|---|---|
| `mark` | `url` or `site` (optional) | the clip's site (host) for the cross-fade rule; unknown → hard cut |
| `click` | `t`, `glide_t` (epoch), `x`, `y`, `navigates` (optional) | protected from `min(glide−0.2, click−1.0)` to click+1.2 s (+0.65 s if it navigates, also auto-detected from a blank within 1 s); `x,y` guard splices against a moved cursor |
| `typing` / `type` / `paste` | `t`, `end` (epoch) or `duration` | protected from start−1.0 s to end+0.3 s |
| `pan` / `scroll` / `camera` / `glide` / `reveal` | `t`, `end` | protected ±0.25 s |
| `readiness_wait` / `wait` / `late_content` | `t` (start), `end` (epoch) or `duration`; or a `wait_start`/`wait_end` pair | the **only** trigger of a speed-up (Q-56) |
| `navigation` | `t` (commit), `ready` (epoch: fonts and cursor in place) | frames between are dropped as half-painted (Q-22) |
| any | `url` | tracks the current host (clip end site) |
| `hold` | `kind`, `seconds` | kept to the frame, never sped up (Q-51) |

Frames: a time maps to raw frame `ceil(t * fps)`, the first frame that shows it; this is the recorder's own `frame`
stamp and every segment's `start_frame`, so an event or hold logged after a mark always falls into that mark's clip
(a clip spans `start_frame <= f < end_frame`). Span ends are read from `video_end_t` (recorder), else `end`.

`take.log.jsonl` `readiness_wait` records (M2: `start`, `end` epochs) are read too, so M2 need not post waits
separately. **M2/loop:** please post clicks with `glide_t`, typing with `end`, and set `url` on marks.

## Output: `cut/record.json` (the cut record the gate reads; Q-14, Q-50, Q-52, Q-53, Q-56, Q-72)

Top level: `version, run_id, source_run, source_dir, recut, raw, width, height, fps, raw_frames, params, clips[],
joins[], full, full_duration, full_frames, full_expected_frames, timing{analyze_s, plan_s, render_s, join_s,
total_s, raw_duration_s}`.

Per clip (all `clip_*` times are delivered clip seconds, `src_*` are raw seconds):
- `index, step, name, file, site_start, site_end, duration, frames, source{start_t,end_t,start_frame,end_frame}`
- `kept[]` `{clip_start, clip_end, src_start, src_end, factor}`; `frame_map[]` = raw frame of every delivered frame
- `drops[]` `{src_start, src_end, frames, reason}`; reasons: `blank`, `transitional after blank`,
  `half-painted document`, `standstill`, `near-still` (+ `join_mad`)
- `declined[]` removals or speed-ups the cutter refused, with the reason
- `splices[]` `{clip_t, frame, src_from, src_to, removed_frames, reason, in_speedup, join_mad}` — every gap in the
  kept raw frames, including those inside a sped-up stretch
- `speedups[]` `{clip_start, clip_end, frames[k0,k1), src_start, src_end, factor, ease_frames, badge, wait{kind,
  start_t, end_t, step, source}}`; `badge[]` the same stretches (the badge is shown exactly there)
- `holds[]` `{kind, seconds, clip_t, clip_end, kept_seconds, src_t}`
- `events[]` clicks `{clip_t, glide_clip_t, x, y, navigates, dropped}`, typing and spans `{clip_t, clip_end}`
- `all_standstill` (Q-14 flag: every raw frame of the step has MAD ≤ 0.5), `kept_motion_frames`
- `collapsed`: the step's segment was empty (two marks in the same frame); it is delivered as its one mark frame
  with `all_standstill: true`, so the gate fails it on Q-14
- `freezes[]`, `bridges[]`: always empty in v0.1 (no freeze-frames, no synthetic cursor bridge)
- `covers[]` (Q-23 skeleton cover, below): one entry per click with skeleton frames `{click_t, src_t, label, x, y,
  status: "covered"|"bridged"|"skipped", reason, skeleton_frames, skeleton_run[k0,k_end), seam, ripple, change_area,
  pre_frame, pre_src_frame}`; `ripple` = `{found, delta_s, start_s, fit_mismatch, blue_px, refine_seam, box,
  drawn_frames}` (the drawn ripple's fit; null when skipped before drawing); covered: `frames[k0,k1)` (delivered,
  k1 = the settled frame), `clip_start, clip_end, covered_frames, src_frames`; skipped: `frames: null,
  covered_frames: 0, skeleton_window`. `params.skeleton_cover` = `{max_s, seam_max, ripple: "drawn", ripple_s,
  ripple_d}`;
  `timing.cover_s`.

`joins[]` `{between[i,j], kind: "cut"|"fade", sites, full_t}` or for a fade `{full_start, full_mid, full_end,
duration 0.2, frames 6, curve "linear"}`.

`clips/index.json` follows M1's contract (`run_id, source_run, clips[{index, step, name, file, duration, frames,
holds[{kind, seconds, clip_t}], source{start_t,end_t}}], full, full_duration`) plus `record` and each clip's `site`.

## Rules implemented (constants in `vc/cut/plan.py`; quality rules, not knobs)

- Standstill (Q-50): still = MAD ≤ 0.5 at 480×240; runs split at holds and speed changes; near-still neighbours
  < 0.6 s apart merge if every gap frame has MAD < 2.0; cap 1.5 s (2.0 s merged; ×factor inside a speed-up);
  the last 0.5 s before the following motion is kept; only unprotected frames are removed; a removal is placed only
  where the two joined frames match (160×90 MAD ≤ 1.0) and nothing changed within 40 px of the known cursor tip.
- Blanks (Q-53, Q-22): a frame is blank when its luma range is < 12 at 480 wide, or < 12 on the 160×90 thumbnail
  with a 2 px inset and the 0.1 % extreme pixels at each end ignored (so a cursor does not rescue it). After a blank,
  frames up to the last hard change (MAD ≥ 1.5) within 0.3 s are dropped as transitional.
  Tests: Q-53's Verify line is "Q-20, Q-21 and Q-22 pass", and the gate owns the Q-20/Q-21 definition (luma range
  < 12 at 480 px with the 0.1 % extremes ignored, more than 3 frames). So `tests/cut/verify.py` runs the gate's own
  Q-20/Q-21 checks on the delivered clips (synthetic take and the real white-out fixture) and additionally keeps the
  cutter's stricter per-frame test (not a single blank frame left). The cutter never re-implements the gate's rule.
- Speed-ups (Q-56): only on logged waits, minus clicks, holds, typing and dropped frames; at least 1.0 s; speed
  eased linearly over 3 delivered frames (the 0.4 s ease at 4×), duration-neutral; frames are picked, never blended.
- Badge (Q-77, implemented here; M3 had none): capsule 84×44, r 22, rgb(24,24,28) at 80 %, white bold "4×" fitted
  to the 26×18 glyph box, 28 px from right and bottom, all scaled by width/1920; opacity follows the speed ease.
  Font: Noto Sans Bold (M1 image) or DejaVu Sans Bold, `VC_BADGE_FONT` overrides.
- Output (Q-80, Q-81): the raw's size (1920×1080 from M1), 30 fps CFR, libx264 High, yuv420p, CRF 18, preset
  veryfast, no B-frames, no audio, `+faststart`. Clips carry forced key frames at frame 6 and K−6.
- Join (Q-72): hard cut = stream copy; site switch (both hosts known and different) = 6-frame (knob) linear cross-fade,
  frame j = (1−j/6)·A + j/6·B; only those 6 frames are encoded, every other frame is a bit copy.

## Q-23 skeleton cover (S2, pulled forward; cutter change approved by the owner 2026-09-25)

Code `vc/cut/cover.py`, tests `tests/cut/test_cover.py` (also run by `verify.py all`). Per click, on the delivered
frame sequence (the clip's frame map, so the window is the one the gate judges):
- window: the click's delivered frame to +2.4 s, stopped at the next click / glide start / glide-move span and at the
  clip end; pre-click frame = the delivered frame before the click, settled view = the window's last frame;
- skeleton frame = Q-23's detection with its default thresholds, at half resolution (960x540, area scaling): change
  area |settled - pre| >= 16 outside 110 px of the click, >= 2 % of the frame; old view gone (mean >= 10 luma there);
  <= 25 % of the settled view's sharp edges (neighbour difference >= 24); a plain old/new blend is no skeleton.
  A 480x270 pre-check (change area >= 1 %) skips clicks that change nothing;
- cover = delivered frames from the first skeleton frame until the view is settled: k1 = the first frame from which
  on every frame of the window carries the settled view (> 25 % of its sharp edges in the change area and closer to
  it than to the pre-click frame), so intermittent half-painted or not-quite-gone frames between and after the
  skeletons are covered too (fix 2026-09-25: c2 070742 "Prozesse" left 9 empty-shell frames at 9.8 luma that the
  gate counted on the encode). The pre-click frame (opaque) is overlaid in the render; the first frame after the
  cover is live, so the cut goes old view -> settled view;
- the ripple on covered frames is drawn, not live (fix 2026-09-25, parity viewer: the old fixed 72 px live hole
  showed the loading page through the ripple's .38 fill and, after the ripple, plain: card text vanishing, a dark
  arc, the next view's orange highlight). It is the overlay's own geometry (overlay.js: 96 px ring, 5 px border
  #4da3ff, fill .38, 2 px white halo .55, 18 px glow .5; scale 0.14->1 on cubic-bezier(0.2,0.7,0.3,1), opacity
  0/1/1/.9/0 at 0/.16/.42/.68/1 of 520 ms) drawn onto the pre-click frame under its pointer, so the drawn area
  shrinks with the ripple and is nothing once it is gone. Its start is fitted per click on the opaque ring's blue
  pixels in the live frames (page-independent), -0.25..+0.10 s around the logged click, then refined (+-3/120 s)
  on the live frames between the click and the cover. Rendered as small PNG patches (one per frame while the
  ripple shows) overlaid on the pre-click frame. No ring found: no ripple drawn (there is none to keep);
- skipped (frames left, reason logged) when: the seam, i.e. the mean |live - drawn| luma over the ripple's disc on
  the last live frame before the cover, exceeds 16; the cover until the settled view is longer than 2.0 s; the view
  already changed before the skeleton run; or the click has no logged position.
- long loading (owner decision 2026-09-25, FX-31): when the view settles more than 2.0 s after the first skeleton
  frame, the cover holds 2.0 s (ripple drawn as above), the rest of the wait is cut out (delivered frames removed,
  drop reason `loading bridged`, a splice at the fade) and the held pre-click frame fades out over the live settled
  view in the site-switch fade's shape: crossfade_s frames (6 at 0.2 s), frame j at (1 - j/n) held + j/n live, j = 0
  being the last held frame, so 5 blend frames then live. A load still running when the 2.4 s window ends (the
  window's last frame is a skeleton of the view at the next action, checked on the 480x270 analysis frames) is
  followed up to the next click/glide or the clip end, at most 10 s; a load that never settles before that is left
  alone (the gate fails it on Q-23). Not cut when a hold, typing or a span lies in the frames to remove (skipped,
  reason logged). Record: `status: "bridged"`, `frames[k0, k0+60)` (held), `fade_frames[a, b)` (blend frames),
  `loading_s, hold_s, removed_frames, removed_s, fade_s, summary` ("loading 3.4 s: cut to 2.0 s + fade"),
  `window_extended_to` when the window was followed on; `params.skeleton_cover.long_load`. The gate prints the
  summary as a Q-23 info line.
- The gate judges the covered clip as any other (no special case). Real takes (re-cut 2026-09-25, drawn ripple):
  c1 064809 t1 "Urlaubsantrag" frames 67-78 covered (seam 8.1); c2 064810 t1 "Prozesse" 73-85 (7.0) and
  "Bestellanforderung" 21-35 (7.4); c2 070742 t1 "Prozesse" 72-103 (skeleton run 72-94 + 9 shell frames; 7.6; was
  Q-23 FAIL 9 frames) and "Bestellanforderung" 23-37 (7.9); c3 064809 t3 "Reisekostenabrechnung" 23-34 (5.8):
  gate Q-23 0 frames on every click, Q-44 ripple still detected (0.37-0.47 s), nothing else changed; crop sheets of
  every covered frame show the old view with the ripple growing and fading, no loading page.
  Cost: 4-8 s per 40 s take (stdlib, no numpy).

## Measured (tests/cut/verify.py, one command: `python3 tests/cut/verify.py all [WORKDIR]`)

44 checks pass on the host (ffmpeg 7) and in the M1 image on cpuset 8-15 (ffmpeg 5.1). C-26: a 45 s raw take
(3 steps, 1 speed-up, 1 fade) cuts and joins in 10–15 s (target about 35 s for 50 s); joining 5 clips on one site
by stream copy takes 0.2 s. Fade midpoint: 0.07 % (synthetic) and 0.42 % (real clips) of pixels deviate more than
6 luma from the 50/50 mean; the baseline `fade-mid.png` itself has 0.34 %, so the check allows 0.5 %.

## Not done / open

- Cursor continuity bridge (Q-42/Q-43 fix direction, optional): not implemented; instead the cutter refuses any
  splice that would move the cursor (join guard above), and never splices through a glide. `bridges[]` stays empty.
- Raw-mode diagnostics (Q-03, S2) are not built. The Q-23 skeleton cover is (section above).
