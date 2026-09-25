# Fixes ledger

Every fix the system makes (code, spec, quirk mitigation, gate) gets one entry here. At the end the owner reviews the
list and decides, per fix, whether it goes into the main plugin as a known fix (COMMON) or stays local to its
app/case (SPECIFIC). The "owner decision" field stays blank until the owner fills it in.

Fields: id · date · case/spec · symptom · root cause · change (files, commit) · evidence · scope guess · lives in ·
owner decision.

## First try (tag cp-B-try-done), backfilled

### FX-01 Preconditions are checked on the start view
- date: 2026-09-25
- case/spec: c1-four-angles (all specs with preconditions)
- symptom: the case 1 dry run stopped with "precondition not met" on the page that was open before
- root cause: the take runner checked preconditions before the start URL loaded
- change: `loop/vcloop/take_runner.py`, `tests/loop/test_take_runner.py`; commit 3dac0ed
- evidence: unit test (the start URL loads before any check); case 1 got past its preconditions afterwards
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-02 element_visible matches implicit ARIA roles
- date: 2026-09-25
- case/spec: c1-four-angles (the app's process-list links)
- symptom: case 1 precondition `{role: link}` never matched, although jev itself reports the cards as links
- root cause: the check compared only the explicit `role` attribute, not implicit roles (`a[href]` = link …)
- change: `jev/vcjev/page_js.py`, `jev/tests/test_integration.py`; commit bba7fa9
- evidence: integration test on a live Chrome test page, 2/2 OK
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-03 Exploration tool vcloop.explore (D-10)
- date: 2026-09-25
- case/spec: all (align.md step 4)
- symptom: align.md told the agent to explore with jev and write a record, but no tool existed (synthetic records only)
- root cause: missing tool
- change: `loop/vcloop/explore.py`, `docs/steps/align.md`; synthetic c1 record removed; commit 1198e77
- evidence: live on the app, case 1 walks 7/7 steps and validates
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-04 Exploration records the observation jev decides on
- date: 2026-09-25
- case/spec: c3-correction-loop ("Horizontal" radio)
- symptom: the exploration record said "matches none" although jev clicked the control
- root cause: the snapshot was taken right after the previous click, before the control existed
- change: `loop/vcloop/explore.py` (same readiness wait as the runner); commit 381fe39
- evidence: c3 exploration record resolves the control; spec validates
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-05 The run dir is the request's folder inside the container
- date: 2026-09-25
- case/spec: all (first real dry run)
- symptom: the first real dry run stopped as "recorder down"
- root cause: request.json holds host paths; the in-container runner tried to mkdir the host home path (PermissionError)
  and wrote no result
- change: `loop/vcloop/take_runner.py`, `tests/loop/test_take_runner.py`; commit c07e9eb
- evidence: unit test; the following dry runs wrote results
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-06 Loop tests use frozen copies of the golden specs
- date: 2026-09-25
- case/spec: tests (golden specs)
- symptom: 8 loop unit tests broke once the golden specs were explored and approved for real
- root cause: tests seeded defects by editing the LIVE golden specs and expected their synthetic-exploration warning
- change: `tests/loop/golden-frozen/*`, `tests/loop/test_loop.py`, `tests/loop/test_spec.py`, `tests/loop/e2e_real.py`;
  commit 64028fd
- evidence: loop unit suite 62 OK
- scope guess: COMMON (test hygiene)
- lives in: core code (tests)
- owner decision:

### FX-07 Camera pans a wheel-driven canvas
- date: 2026-09-25
- case/spec: c2-find-and-read, c3-correction-loop (the app's swimlane diagram)
- symptom: the pan step failed with "no scroll container on the y axis"
- root cause: the diagram moves by a CSS transform on wheel events; the camera only knew scroll containers
- change: `vc/overlay/camera.py` (fallback: nearest clipping ancestor, one wheel event per frame, px-per-unit
  calibration, sine curve), `tests/visual/wheel_pan.py`, `tests/visual/site/wheelpan.html`; commit ca8e794
- evidence: live wheel_pan test 7/7 PASS; on the app step 4 of Bestellanforderung is centred
- scope guess: COMMON (canvas/flow-chart libraries)
- lives in: core code
- owner decision:

### FX-08 Wheel pan steps once per painted frame
- date: 2026-09-25
- case/spec: c2-find-and-read
- symptom: c2 take 1 failed Q-34 with a double update during the pan
- root cause: the app paints ~20 fps during the pan; the clock-driven curve caught up with a double-size step
- change: `vc/overlay/camera.py`; commit 96940a9
- evidence: live wheel_pan test 7/7 PASS (2.55 s, max 40 px); c3 t1 pan shows even steps (parity c3pan-shift.txt)
- scope guess: COMMON (any slow-painting page)
- lives in: core code
- owner decision:

## Second try (tag cp-B-try2-done)

### FX-12 c2 opens on the empty start page (approved write step, off-camera setup)
- date: 2026-09-25
- case/spec: c2-find-and-read
- symptom: the c2 video opened on the latest existing chat, not the empty "Prozessaufnahme" start page the brief asks for
  (parity M-09, `frames/c2-start.png`)
- root cause: the app's `/de/chat` opens the latest chat; the spec format had no off-camera action to reach the empty page
- change: optional `[start.setup]` (runs after the start view is ready, before the cursor install and recording, on-camera
  hooks off): `loop/vcloop/take_runner.py` (`run_setup`), `loop/vcloop/spec.py`, `docs/spec-format.md`,
  `tests/loop/test_setup.py`; c2 spec: setup "neues-gespraech" clicks "Neues Gespräch", `[[writes]]` "new-chat"
  (`creates = false`), `writes_allowed = true`; re-explored and re-approved (contract sha256:5f1efec151a06b31,
  `runs/try2/approvals.log`); commit 29a034d
- evidence: live jev exploration: the click gives `/de/chat?new=1`, no chat id, the sidebar keeps the same 4 chats, and a
  reload shows no new entry, so it creates nothing and needs no cleanup; exploration record
  `specs/golden/explore/c2-find-and-read/20260925-081550.json`; `test_setup.py`; selftest --quick 8/8
- scope guess: COMMON (the off-camera setup mechanism) + SPECIFIC (the c2 setup step)
- lives in: core code (setup) + spec (c2)
- owner decision:

### FX-11 Sticky lane headers pinned before the first paint (view_start, D-33 item 3)
- date: 2026-09-25
- case/spec: c2-find-and-read (vertical swimlanes), c3-correction-loop (horizontal, 24 px sideways)
- symptom: during the c2 pan the lane headers rode along for the first ~56 px (0.13–0.53 s), then stopped (Q-31, case-2
  rule; parity `frames/c2-header-ride.png`)
- root cause: nothing scrolls. The React Flow canvas opens at `translate(24px, 56px)` and keeps it until the first pan; the
  header layer sits at max(translation, 0), so it follows the first 56 px of the pan
- change: generic profile-declared `view_start` rules (URL regex, element, condition, method `wheel`|`scroll`, x/y),
  injected as a document-start script by the take runner in dry runs and takes; one wheel event moves the view before
  its first paint, re-applied for 1.5 s, stops when the camera pan starts; log in `result.json` `view_start`:
  `vc/overlay/viewstart.py`, `vc/overlay/viewstart.js`, `loop/vcloop/take_runner.py`, `profiles/example/profile.json`
  (rule: swimlane views to (0,0)), quirk record `sticky-lane-headers` in `specs/quirks/app.example.com.md`,
  `docs/overlay.md`; commits 8cf6157, 15ece81
- evidence: live off-camera per-frame log, card click through the c2 pan: first painted frame at (24,56) before and at
  (0,0) after; no change in the 1.8 s before the pan; header movement over the 965 px pan 56 px before and 0 px after;
  `tests/test_viewstart.py` (10), `tests/viewstart_page.test.js` (9, node); selftest --quick 8/8
- scope guess: COMMON (the mechanism) + SPECIFIC (the app's rule and quirk record)
- lives in: core code + app profile/quirk record
- owner decision:
- note: Chrome runs start-of-page scripts only after `Page.enable`; the cursor overlay (`vc/overlay/controller.py`) never
  calls it. The view_start install does, so the example app is covered, but profiles without view_start rules are still exposed.

### FX-10 Q-23 skeleton cover in the cutter
- date: 2026-09-25
- case/spec: c1, c2, c3 (process open; c2 also the process list)
- symptom: 0.35–0.5 s of grey placeholder boxes / half-painted diagram after a navigating click (Q-23 FAIL in both
  gates: c1 10 f, c2 15 + 11 f, c3 14 f)
- root cause: the app paints skeleton placeholders before its data arrives; route warm-up did not remove it
- change: `vc/cut/cover.py` (new), `vc/cut/cutter.py`, `vc/cut/render.py`, `docs/cut-interfaces.md`: per click, in the
  gate's Q-23 window and thresholds (960×540), the first to last skeleton frame (≤ 2.0 s) is covered with the last
  pre-click frame, a 72 px round hole around the click keeps the ripple; skipped (reason logged) on a > 16 luma seam
  on a 4 px ring, > 2.0 s, a view change before the run, or an unknown click position; every decision in
  `cut/record.json` `clips[].covers[]`; gate unchanged; commit 19a035e (owner approval logged)
- evidence: re-cut of the real raw takes, gate Q-23: c1 t3 "Urlaubsantrag" 10→0 (seam 7.0), c2 t1 "Prozesse" 15→0
  (9.3), "Bestellanforderung" 11→0 (6.8), c3 t1 "Reisekostenabrechnung" 14→0 (7.9); Q-44 ripple still detected at
  each, no new Q-30/Q-42; `tests/cut/test_cover.py` (10, incl. real-frame fixture
  `tests/cut/data/c1t3-urlaubsantrag-480x270.gray.z` and the seam-skip path), `verify.py all` 58/58
- scope guess: COMMON
- lives in: core code (cutter)
- owner decision:

### FX-13 Q-40/Q-43: the demo cursor on a coloured button is the demo cursor
- date: 2026-09-25
- case/spec: c1-four-angles (cursor parked on the orange active "Swimlanes" button at (1608,169)); also the old passing
  c1 references
- symptom: new gate FAIL "second pointer … not the demo cursor look" and Q-43 "cursor not found at boundary
  swimlanes→schritt"; frames show only the demo cursor (parity section 3)
- root cause: the look test (`gate/vcgate/checks/cursor.py` `look`, `is_demo_look`) read the orange button around the
  sprite as a coloured glow
- change: the look is the sprite's own (white fill, dark outline luma ≤ 140); commit 8065c41; `docs/gate.md` updated
- evidence: `gate/tests/test_parity.py` Q40CursorOnOrangeButton on native c1 crops (`gate/tests/data/parity/`); the coral
  pointer, a coral sprite pasted on the page and the Q-90 second-pointer fixture still FAIL; fixtures 25/25; c1 t3
  re-gated: only Q-23 left
- scope guess: COMMON
- lives in: core code (gate)
- owner decision:

### FX-14 Q-40: static connector arrowheads are no pointer
- date: 2026-09-25
- case/spec: c3-correction-loop (horizontal swimlanes)
- symptom: new gate FAIL "second pointer" at the grey ▶ arrowheads between step cards ((974,491), (1334,491), …)
- root cause: triangles matched the arrow template's head
- change: a candidate that is not the demo cursor must have a tail to be a pointer (`is_pointer`; demo ≥ 0.14, arrowheads
  ≤ 0.04); commit 8065c41
- evidence: Q40ConnectorArrowheads on real c3 crops; two pasted cursors still FAIL; fixtures 25/25
- scope guess: COMMON
- lives in: core code (gate)
- owner decision:

### FX-15 Q-31: sparse diagrams are not sticky bands (no false "stall")
- date: 2026-09-25
- case/spec: c3-correction-loop (sideways pan)
- symptom: new gate FAIL "17-frame stall", "1110 px" on a smooth eased 1364 px pan (parity `c3pan-shift.txt`)
- root cause: one bright line set the largest row energy (`motion.py` `_frame_surface`), so most of the sparse diagram
  fell below 15 % and was excluded as sticky
- change: sticky bands measured by band energy over 16 px (`_band_energy`); commit 1891da4
- evidence: Q31EasedHorizontalPan on a clip cut from the c3 pan: 1364 px, only a Q-34 WARN (evenly dropped paints); the
  59 px step fixture still FAILs; fixtures 25/25
- scope guess: COMMON
- lives in: core code (gate)
- owner decision:

### FX-16 Q-31: sticky-header ride-along traced and reported in full
- date: 2026-09-25
- case/spec: c2-find-and-read
- symptom: the real 56 px ride-along (frames 4–16) was reported as "1 frame, −8 px"
- root cause: the gate did not follow the sticky band's offset over the episode
- change: `_ride_along` / `_band_offset` trace the band; report "13 frames, 0.13–0.53 s, 56 px", FAIL; commit 1891da4
- evidence: Q31StickyHeaderRideAlong on a real c2 clip + the Q-90 fixture (55 px); fixtures 25/25
- scope guess: COMMON
- lives in: core code (gate)
- owner decision:

### FX-17 Gate decodes are frame-exact after a seek
- date: 2026-09-25
- case/spec: all (found while testing the skeleton cover on raw takes)
- symptom: after some seeks on raw.mp4 a frame was repeated and the rest shifted by one
- root cause: `gate/vcgate/video.py` `decode_scaled`/`decode_full` let ffmpeg resample to a constant rate
- change: `-fps_mode passthrough`; commit 1524a71
- evidence: SeekDecodeIsFrameExact on a 3.2 s stream copy of the c3 raw take; delivered clips were not affected
- scope guess: COMMON
- lives in: core code (gate)
- owner decision:

### FX-18 c3 take 1: flake retake (CPU contention), no change
- date: 2026-09-25
- case/spec: c3-correction-loop (job 20260925-064809)
- symptom: Q-31 "not eased" (first step 10 px); the planned 3.0 s pan ran 8.57 s
- root cause: three cases filmed in parallel; the app painted only every ~2.9 capture frames, so the curve's ease-in was
  stretched over few paints (140 repeated frames)
- change: none (fixer kind "flake", unchanged retake)
- evidence: take 2 of the same spec painted every 1.8 frames, and the Q-31 FAIL was gone. c2's 965 px pan (planned 3 s)
  took 8.2 s with three cases filming (a new picture every 2.8 frames) and 5.0 s filmed alone (every 1.5 frames), so
  parallel filming slows the app's paints. The rest is the app's own ~20 fps paint rate, an even Q-34 WARN
- scope guess: COMMON (lesson: do not film several takes at once on shared cores, or give each ≥ 5 dedicated cores)
- lives in: nowhere (operational finding)
- owner decision:

### FX-19 c3 pan centres the decision card, not step 5
- date: 2026-09-25
- case/spec: c3-correction-loop
- symptom: frame check failed on take 2: at the pan end the step-4 card ("…nung korrekt?") and the "Rücksprung" label
  were behind the lane-header column
- root cause: the pan centred step 5 in the clip box (which includes the header column); since FX-11 the view also
  starts 24 px further left
- change: `specs/golden/c3-correction-loop.toml` pan `to` = {decision} (the how; contract unchanged); commit cb63131
- evidence: final job c3-correction-loop-20260925-074542: HIT on take 1; the frame check shows the full "Abrechnung
  korrekt?" card, the "Rücksprung" label, the diamond and step 5
- scope guess: SPECIFIC
- lives in: spec
- owner decision:

### FX-20 Page animations stay paused 10 frames around a pan
- date: 2026-09-25
- case/spec: c2-find-and-read (solo re-film, job 20260925-065951 take 1)
- symptom: Q-31 FAIL "1 frame that no single shift explains" at the pan start
- root cause: a marching-dash step of the app's animated connectors landed in the gate's 0.25 s episode pad, one frame
  before the first shift; `pause_animations` paused only during the move
- change: `vc/overlay/camera.py` keeps animations paused 10 frames (0.33 s) before the first and after the last step;
  `tests/visual/wheel_pan.py` + `tests/visual/site/wheelpan.html` (marching-dash animation); commit 0ec3411
- evidence: live wheel_pan 10/10 PASS; the next solo take (job 20260925-070742 t1) had no Q-31 finding on the pan
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-21 The loop accepts a code-only fix
- date: 2026-09-25
- case/spec: all (found on c2 solo job 20260925-065951)
- symptom: after the camera fix FX-20 the loop stopped with STOP 7 "the fixer returned a fix note but changed nothing"
- root cause: the loop compared only the spec text, although on-fail.md allows the smallest diff for a plain runner bug
- change: `loop/vcloop/loop.py` also hashes the fixable code outside the QA definition (vc/, jev/vcjev, loop/vcloop,
  profiles, quirk records; protected files excluded) and names the changed files in the change note; commit 04d85bf
- evidence: `tests/loop/test_loop.py` F15 test_code_only_fix_is_accepted; selftest --quick 8/8
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-22 Real pointer parked after each click (no native tooltip)
- date: 2026-09-25
- case/spec: c2-find-and-read (any click on an element with a title attribute)
- symptom: in the c2 HIT a native "Prozesse" tooltip sat mid-card for 2.3 s (2.87–5.17 s); no gate or frame check
  caught it (parity viewer, `v1-parity-evidence/second/frames/tip.png`)
- root cause: the real CDP pointer stayed on the clicked control; Chrome showed its title tooltip after ~0.57 s and kept
  it through the hold
- change: `vc/overlay/jev_hooks.py` `OnCamera.park`: once the ripple ends (also right after a field's click, before
  typing, and at `install()`), one invisible mouseMoved sends the real pointer to (-1,-1); the demo cursor stays; the
  runner's hover re-syncs before the next press; knob `VC_POINTER_PARK` (outside | x,y | off), `docs/overlay.md`;
  commit 057f517. Caveat: a hover-opened menu would close; set off for such a spec
- evidence: live off camera on the app (vc-v1-p2): park off shows the tooltip after 2 s; with park on the `:hover` chain is
  empty and no tooltip shows (`runs/tooltip-probe/`); selftest --quick 8/8
- scope guess: COMMON
- lives in: core code
- owner decision:

### FX-23 Gate check Q-71: a stray tooltip over a hold
- date: 2026-09-25
- case/spec: all (found on c2)
- symptom: see FX-22; the gate had no check for it
- root cause: no Q-71 detection for small overlays that appear after a click
- change: `gate/vcgate/checks/tooltip.py` `run_clip`: FAIL for a small framed text box that appears or disappears as a
  unit (a box that only dims does not count) and stays ≥ 0.5 s; commit 11c1deb
- evidence: `gate/tests/test_tooltip.py` on real crops of the delivered c2 (FAIL); bin/vc-gate: c2 HIT FAIL (suche
  0–1.2 s), c1 and c3 PASS; fires on no Q-90 fixture
- scope guess: COMMON
- lives in: core code (gate)
- owner decision:

### FX-24 Q-31: a pan episode includes its faint ease tails
- date: 2026-09-25
- case/spec: c3-correction-loop (take 3, job 20260925-064809)
- symptom: gate FAIL "right pan of 576 px … not eased: first step 12 px" on a clean sine pan of 804 px (steps 1→14→1,
  measured frame by frame)
- root cause: on the dark, sparse diagram the 480×240 MAD of the 1–11 px ease-in frames is 0.09–0.50, which counts as
  "still", so `_episodes` began ~50 frames into the motion; "widen once" missed it because every other frame is a
  duplicate
- change: `gate/vcgate/checks/motion.py` `_episodes` + `_extend_faint` (extend over neighbouring faint changes, MAD >
  0.05, stalls ≤ 3); `_judge` exempts a no-shift settling frame (< 1.5 grey levels) just before or after the pan; ease rule
  unchanged; commit 74689ac
- evidence: `gate/tests/test_parity.py` Q31EaseTailsOfASparsePan on real t3 frames 0–179: PASS (Q-34 WARN); the old
  rule reproduces the FAIL; the same pan with its ease-in cut off still FAILs; fixtures 25/25; gate tests 64 OK; re-gate
  c3 t2/t3 and c1 PASS
- scope guess: COMMON
- lives in: core code (gate)
- owner decision:

### FX-25 Skeleton cover: drawn ripple instead of a live hole, cover until the view is settled
- date: 2026-09-25
- case/spec: c1, c2 (found by the parity viewer on the HITs; c2 solo job 20260925-070742 t1)
- symptom: (a) the ~145 px live hole showed the loading page for ~0.4 s (c1: card text vanished, a dark arc; c2 sidebar:
  half an orange disc); (b) c2 solo t1: Q-23 FAIL, 9 skeleton frames right after the cover ended
- root cause: (a) a fixed live disc under a translucent ripple (38 % fill) lets the next view through; (b) the cover
  stopped at the last skeleton frame, but later frames at 9.8 luma (just under Q-23's 10) were still skeleton on the encode
- change: `vc/cut/cover.py`, `render.py`, `cutter.py`, `docs/cut-interfaces.md`: covered frames are the pre-click
  frame with the overlay's own ripple drawn on it (geometry and timing from overlay.js, start fitted on the live ring's
  blue border; the drawn area follows the ring and is nothing once the ripple is gone; seam check ≤ 16 luma kept); the
  cover runs until the view stays settled (≤ 2.0 s); commit 229547e
- evidence: `tests/cut/test_cover.py` (continues-until-settled, flickers-back, RealSettle, RealRipple ×5 incl. the drawn
  area shrinks to nothing and no loading page in the drawn frames); Q-23 0 on c1 064809 t1, c2 064810 t1, c2 070742 t1
  (was 9), c3 064809 t3; Q-44 still finds every ripple; all covered-frame crops checked by eye; verify.py 58/58, fixtures
  25/25, selftest 8/8
- scope guess: COMMON
- lives in: core code (cutter)
- owner decision:

## Viewer review merge (tag cp-B-review-merged)

### FX-26 Wall-clock total leaves out human waits
- date: 2026-09-25
- case/spec: all (found in c2-find-and-read job 20260925-074309, report.md)
- symptom: delivery report said "wall-clock total 5403.3 s" while the phases summed to 199.1 s and the job ran ~2 min
- root cause: the "approval (human)" record spans the owner's last `vc-spec view` to the approval; that approval was
  88 min before the job started (same fingerprint, approved earlier), and the wall clock ran from the first start of
  any record
- change: `loop/vcloop/logs.py` (human waits, flag `human` or "(human)" in the phase, are reported apart as
  "human waits … s, not included" and left out of wall clock and sum), `loop/vcloop/loop.py` (approval record flagged
  `human`); commit a2fefaa
- evidence: `tests/loop/test_loop.py` F28_WallClock (the c2 shape: 121.8 s wall, 77.4 s human; old logs without the
  flag); the c2 074309 timing log recomputes to "wall-clock total 121.8 s (sum of phases 121.7 s; … human waits 77.4 s,
  not included)"
- scope guess: COMMON
- lives in: core code (loop)
- owner decision:

### FX-27 Viewer review fallback request gets the filled-in checklist
- date: 2026-09-25
- case/spec: all (a take whose video or cut record cannot be sampled)
- symptom: the fallback request's prompt carried the raw template with `{privacy}` unreplaced
- root cause: `ph_framecheck` used `RV.CHECKLIST` directly instead of the formatted prompt
- change: `loop/vcloop/review.py` `checklist()`, `loop/vcloop/loop.py` fallback uses it; commits b9fa5c0, a2fefaa
- evidence: `tests/review/test_review.py` test_house_style_calibration_in_prompt (no placeholder left, privacy on
  and off); loop-unit 73 OK
- scope guess: COMMON
- lives in: core code (loop)
- owner decision:

### FX-28 Viewer review never reports the house style (fast typing, ~12 s final hold)
- date: 2026-09-25
- case/spec: all (owner calibration of the viewer review)
- symptom: risk of false blockers/minors: the checklist's pace rule ("text on screen too briefly", "merely slow")
  reads on fast typing and the long still final hold, which are owner decisions (house rules)
- root cause: the house style was one clause inside the "intended" list, not an explicit never-a-finding rule, and
  nothing enforced it after the answer
- change: `loop/vcloop/review.py` HOUSE_STYLE block in the prompt (fast typing and the final hold are never a
  finding, any severity) and the pace rule points to it; `judge()` drops a `pace` finding about typing or the final
  hold into `house_style` (other categories, e.g. a tooltip during the hold, still count); commit b9fa5c0
- evidence: `tests/review/test_review.py` test_house_style_calibration_in_prompt, test_house_style_findings_do_not_count;
  live review (claude-opus-5-5, one turn, 34 sheets, $0.81, 33 s) of the c3 delivery (job 20260925-074542 t1,
  `runs/c3-correction-loop-20260925-074542-t1/review-calibrated/`): PASS, 0 blockers, 2 minors (one-frame half-painted
  view at 5.2 s; hint banner under the NOTATION legend at 9.17 s), no finding about typing or the final hold, 0 dropped
  by the filter
- scope guess: COMMON
- lives in: core code (review)
- owner decision:
