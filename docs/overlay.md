# On-camera actions and visual style (M3): interfaces

Status: v0.1 (M3). Requirement IDs refer to `vc-v1-spec`. Code: `vc/overlay/`, tests `tests/test_overlay.py` (unit,
stdlib) and `tests/visual/` (filmed measurement). Standard library only; runs wherever the CDP client runs (inside the
M1 container).

## What it is

| File | Content |
|---|---|
| `vc/overlay/overlay.js` | The in-page overlay: the one demo cursor (Q-73), sine glide (Q-74), press spring (Q-75), ripple (Q-76), slow-click pressed state (Q-48). One `<vc-overlay>` element, `position:fixed`, 0×0, `pointer-events:none`, closed shadow root, drawn in the top layer (manual popover, re-raised when the page opens a modal/popover). All motion is compositor animation (transform/opacity). |
| `vc/overlay/controller.py` | `Overlay(call)`: timing and state; `call(method, **params)` is any sync CDP call on the filmed tab. |
| `vc/overlay/jev_hooks.py` | `OnCamera(tab)`: adapter to M2's `vcjev.runner.Hooks` (`before_input`, `after_input`, `type_text`). |
| `vc/overlay/timing.py` | Q-74 Fitts formula, Q-75/Q-76 constants, Q-79 typing plan, Q-55 paste delay. |
| `vc/overlay/pacing.py` | Q-60 readability pauses (first match wins), Q-62 hold defaults, Q-51 timeline-hold threshold. |

**Speed badge (Q-77): not here.** M4's cutter renders it (`vc-v1-m4-cut/docs/cut-interfaces.md`); M3 ships no badge
asset or renderer.

## Use from the jev wrapper (M2)

In the recorder image `VC_HOOKS=vc.overlay.vc_hooks`: the take runner builds the hooks with `make_hooks(recorder,
filming)`, binds them to the filmed tab, installs the overlay before recording starts, and uses its `camera` for pans
and reveals (`vc/overlay/camera.py`). Direct use:

```python
from vc.overlay.jev_hooks import OnCamera
cam = OnCamera(tab, emit=post_event, stale=StalePage)   # tab = vcjev FilmedTab; emit(dict) -> POST /event (M1)
cam.install()                          # once per take after the start URL loaded, before recording starts
hooks = Hooks(**cam.hooks(), on_decision=...)
cam.mark_copied(value)                 # a value copied earlier in the take: typing it becomes a paste (Q-55)
```

What each hook does, in M2's order (every input is still sent by M2's executor, C-14):

1. `before_input(action, point)`: glide from the last position to `point` (Fitts T, sine, straight), re-read the
   point via `tab.point(node)` and follow if it moved > 2 px (C-09). No press yet. A second call for the same
   action (M2's own re-check saw a move) glides again.
2. M2 re-reads the point until it is stable and sends one hover `mouseMoved` at it (decision below).
3. `before_click(action, point)`: rest until 120 ms after arriving, re-read the point one last time (target gone:
   raise M2's stale-page error; nothing has been drawn, no input is sent, M2 retries), press + ripple together (the
   logged click time), wait until 120 ms after the ring's first frame, return. M2's executor clicks at once.
4. `after_input(action, point, info)`: arms the Q-48 pressed state and emits the click event; then, once the ripple
   has ended (ring start + 520 ms, about 0.4 s after the real click), parks the real input pointer (below).
5. `input_aborted(action, point, reason)`: the executor failed after the press (the target vanished in the last
   120 ms): the ring has shown without a click, so the overlay logs an `orphan_ripple` event (the gate's Q-44 sees
   the ripple; the fixer sees why). This is the only remaining orphan window and it is 120 ms long by design (Q-76).
6. `type_text(tab, action, point, text, submit)` (fill): clicks the field at the pressed point, select-all, then
   types at a flat 37 ms/char (whitespace runs → one space), Enter 200 ms after the last char only if `submit`,
   whole value set (and logged) if the field does not end up equal; or pastes a copied value in one insert 150 ms
   after the click. The real pointer is parked right after the field's click, before the first character (the
   field keeps focus; no typing delay). Returns M2's info dict (`chars, t_first, t_last, fallback_whole_value, paste`).

**Hover before the press: yes (decided at integration).** M2 sends one `mouseMoved` at the stable click point after
the glide and before the 120 ms rest ends. Reasons, against the spec: the visual baseline `cursor.png` is "the demo
cursor at rest over the view button, with the hover state visible", so the control's hover state belongs on screen
while the cursor rests; Q-76 wants the ring painted before the page repaints, and a hover repaint that lands in the
rest cannot hide the ring's first frame (without the hover, the hover repaint happens in the same frame as the
click). It is not mimicry (Q-61): one move to the exact click point, no path. Q-71's link-hover URL bubble (S2) is
not made worse: the real click leaves the pointer on the same point anyway, and the pointer is parked after the
click (below).

**Real pointer parked after each click (Q-71; overlay default since 2026-09-25).** The page and Chrome hover wherever
the real input pointer (CDP mouse) is, not where the demo cursor is drawn. Left on the clicked control, Chrome showed
the control's native `title` tooltip ~0.57 s after the click and kept it through the hold (c2 of 2026-09-25:
"Prozesse" mid-card 2.87–5.17 s; Chrome places native tooltips at the X pointer, which the recorder leaves at the
screen centre, so they appear far from the demo cursor). `OnCamera` therefore sends one invisible `mouseMoved` to the
park point after every click (after the ripple), right after a field's click, and at `install()` (off camera, so an
approved setup click does not leave the pointer on its control). The demo cursor sprite does not move. Before the
next press M2's hover move puts the real pointer back on the next click point (re-sync), so hover states during the
rest and the Q-76 ordering are unchanged. Knob: `OnCamera(park=...)` or env `VC_POINTER_PARK`:
`outside` (default, (-1, -1): just outside the viewport, hovers nothing; measured on the recorder: `:hover` chain
empty, no tooltip 2 s after the "Prozesse" click, vs. the tooltip with `off`), `x,y` (a point on plain background),
`off`. A park that fails (page navigating) is logged as `pointer_park_failed`; the next hover re-syncs anyway. Caveat:
a menu that opens on hover (not on click) closes when the pointer leaves; set `off` for such a spec.
The gate's Q-71 tooltip check (`gate/vcgate/checks/tooltip.py`) fails any such box that stays ≥ 0.5 s.

Direct use without M2: `Overlay(call).click(x, y, do_click, size=(w,h), recheck=fn)` (= `arrive()` + `press()`),
`.type_text(text, insert, submit=, press_enter=, read_value=, set_value=)`, `.paste(value, insert_all)`,
`.place(x, y)` (off camera only), `.release()`, `.abort_press(reason)`.

## Camera (pans and reveals, Q-31 / Q-37)

`vc/overlay/camera.py` `move(tab, action, emit=, check=)`: finds the `to` target (text or selector), the nearest
scroll container on the pan's axis, and scrolls it in the page, once per display frame (requestAnimationFrame), in
whole pixels on a sine ease-in-out: a pan centres the target, a reveal brings it just inside the edge. Duration: the
spec's `seconds`, else 400 px/s mean, never faster than 40 px per frame at the peak, at least 0.5 s. Then 1.0 s still
(Q-37) unless the action has its own hold, then the `to` check. `pause_animations = true` pauses the page's own
animations during the move (Q-31 "nothing animates by itself"). A view that pans only by drag has no scroll
container: the step fails with that reason.

## View start positions (D-33 item 3): `vc/overlay/viewstart.py`, `viewstart.js`

Some views mount at a position that makes a later pan look wrong (example: a swimlane diagram mounts at
`translate(24px, 56px)` and its sticky lane headers sit at `max(translation, 0)`, so they ride along for the first
56 px of a pan). An app profile declares the start position; the view then paints its first frame there.

```json
"view_start": [{"id": "sticky-lane-headers",          // = the quirk record id (specs/quirks/<host>.md, D-31)
                "url_regex": "/processes/<uuid>",     // JS RegExp on location.href
                "selector": ".react-flow__viewport",  // the element holding the position
                "when": ".react-flow__node-swimlaneLane",   // optional: only while this exists too
                "by": "wheel",                        // "wheel": transform canvas panned by wheel; "scroll": scroll box
                "x": 0, "y": 0,                       // start translation / scroll offset; omit an axis to keep it
                "wheel_px_per_unit": 0.5,             // optional first guess, refined in the page
                "settle_ms": 1500}]                   // optional: re-apply window after the view appears
```

- The take runner installs the rules of the spec's profiles right after the jev session opens, before the warm routes
  and the start URL load (dry runs, takes and `vcloop.explore spec` alike, so the dry run is the proof, D-40). A bad
  rule stops the run with `error_class = "profile"`.
- The page script is a document-start script, so it also covers single-page-app navigation within that document.
  Its MutationObserver callback runs right after the app's DOM commit, before the next rendering step. A
  `scroll` rule sets scrollLeft/scrollTop there. A `wheel` rule dispatches one synthetic wheel event and reads the app's
  microtask re-render, at most 4 rounds. Either way the first painted frame already has the start position.
- The rule stays armed for `settle_ms` and undoes moves the app makes during mount. A wheel or pointer-down that is not
  its own, inside the element, ends it: that is the camera's pan or a person. A new element (a remount) arms again.
- Log: `result.json` → `view_start`: per correction `id`, `from`, `got`, `rounds`, `before_first_paint`.
- Specs need no field: the profile applies the rule to every matching view (D-35).
- `install` calls `Page.enable`. Without it Chrome does not run `Page.addScriptToEvaluateOnNewDocument` scripts in new
  documents (measured on the recorder's Chrome). The same applies to the cursor script in "Navigation and first
  frame" below.
- Tests: `tests/test_viewstart.py` (rules, install order, runner wiring) and `tests/viewstart_page.test.js` (the page
  script against a fake DOM in node; run by the Python test when node is on PATH).

## Events (epoch seconds; the fields M4's cutter and the gate read)

| `type` | fields |
|---|---|
| `click` | `t` (press/ripple start = the logged click, Q-76), `glide_t` (glide start), `click_t` (real input), `x`, `y`, `label`, `kind` |
| `typing` | `t` (first char), `end` (last char or Enter), `chars`, `submit`, `value_set_whole` |
| `paste` | `t`, `end`, `chars` |
| `glide` (log only) | `t`, `end`, `x`, `y`, `distance`, `ms` |

`step` is added by the take runner before posting; marks carry `url` (take runner). The one stream and its schema:
`docs/events.md`. Also posted: `orphan_ripple` (`t`, `x`, `y`, `reason`).

## Navigation and first frame (Q-22, Q-43)

The controller keeps a `Page.addScriptToEvaluateOnNewDocument` script in step with the cursor (new one added before
the old one is removed). It runs before any page script and attaches the cursor the moment `<html>` exists, so the
first painted frame of every document already shows the cursor at its previous position; a ripple or press still
running at a navigation continues in the new document at the right phase. Nothing ever fades in.

## Pacing (Q-60 to Q-62) for the loop (M5)

`readability_pause(card_chars=, text_words=, same_control=, new_view=, before_write=) -> (seconds, rule)`;
`remaining_pause(required, elapsed_deciding)`; `HOLDS` = landing 1.0, step 1.0, dialog 3.0, final 12.0 s;
`is_timeline_hold(s)` (> 1.2 s → mark as a hold, Q-51). Deterministic (Q-61).

## Measured (tests/visual/run.sh: throwaway Chrome 154 + Xvfb + x11grab `-draw_mouse 0` with wall-clock frame
times, local test page, cpuset 8-15; last run all 36 checks PASS)

| Req | Measured | Tolerance |
|---|---|---|
| Q-74 glide 300 / 600 / 1000 px | 267 / 400 / 533 ms (formula 259 / 412 / 529) | ±10 % or ±1 frame |
| Q-74 path, profile | ≤ 0.7 px off the line; steps e.g. 3 23 44 56 60 54 40 19 px; first step ≤ 19 px | ≤ 3 px; WARN rules |
| Q-74 rest before press | 120–138 ms (from the last frame moving > 2 px) | 120 ms |
| Q-76 real click after ripple start | 121–127 ms (page `mousedown` − ring frame, n = 8) | 120 ms ±1 frame |
| Q-76 ring | colour (76,162,255) at full opacity; 92–96 px across at 68 %; visible 399–433 ms; ring painted 3–4 frames before the page's reaction | #4da3ff ±10; 92 ±10 %; 0.2–0.9 s |
| Q-76 vs `ripple-peak.png` | dark-page band median (52,104,159) vs baseline (78,140,210), same hue band | Look |
| Q-45 | ripple 433 ms with the main thread blocked 1.5 s | ≤ 0.9 s |
| Q-75 press | arrow 21 → 18 px (0.857), smallest at 63–113 ms, overshoot to 22 px at ~160–200 ms, back to 21 px by ~230 ms; tip moves ≤ 1 px | 0.85 at 110 ms ±1 frame; ≤ 1 px |
| Q-73 look | outline box 16×22 px; white fill 12×18 px = `cursor.png`'s 12×18 | ±2 px |
| Q-79 typing | 25 chars in 900 ms on video (plan 888); page input intervals mean 37.1 ms, 32–41 ms | 37 ±3; each ±1 frame |
| Q-55 paste | ink 0 → full in one frame | empty → full at once |
| Q-48 | slow button darkened + busy spinner from ~0.5 s to the reaction at 2.0 s; none after fast clicks | Look |
| Q-40 / Q-43 / Q-22 | one cursor in every frame; 0 frames without it; 0 px jump in the first frame of 2 cross-site full loads; above a modal dialog | FAIL checks |
| layout | element rects, scroll size and `elementFromPoint` identical with and without the overlay | unchanged |

Rig artefacts (not the overlay, M1 owns the environment): the rig's kiosk window was 1919×1079 with a 1 px black edge
(no window manager); Chrome under Xvfb occasionally presents a frame late (one 37 px step in a 75 px glide); Chrome's
spell-check squiggle appears in typed fields (Q-71 stray interface, S2).

## Decisions and open points

- **Ripple size:** the ring itself is 96 px at 100 %; with its 2 px halo and 18 px glow the ripple spans ~110 px
  (max radius 55). That makes the "visible peak ~92 px at 68 %" of Q-76 and the 90–96 px of `ripple-peak.png` true;
  a 110 px ring would read 106–108 px at 68 %.
- **Pressed state (Q-48, S2, built):** only for controls that can hang (not fields); shown from 0.5 s after the click
  until any DOM/attribute change, focus moving elsewhere, URL change or page hide; drawn as an overlay over the
  control (never touches the app's DOM) plus a small busy spinner at the cursor.
- **Hover / orphan ripple:** resolved at integration (see "Use from the jev wrapper"); the pointer is parked after
  each click (Q-71, same section).
- **OS pointer (Q-40):** the recorder must capture without the pointer (`x11grab -draw_mouse 0`), and the real
  X pointer should be parked outside the filmed area (D-33 item 7) so Chrome's own hover never follows it.
