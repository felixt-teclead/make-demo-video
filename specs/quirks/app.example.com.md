# Quirk record: app.example.com (example app)

One record per quirk (D-31: id, views, symptom, detection, mitigation). Records come only from measurements (D-32).
The mitigations are applied by the app profile (`profiles/example/profile.json`), never by a spec (D-35). Copy this
file to `specs/quirks/<your-host>.md` for your app, point the profile's `quirk_record` at it and replace the entry
below with the quirks you measure on your app.

## sticky-lane-headers

- **id:** `sticky-lane-headers`
- **views:** a record's swimlane diagram (`/processes/<process-id>`), a React Flow canvas without a scroll container,
  in both layouts (vertical, and horizontal).
- **symptom:** in the first pan the lane headers move with the diagram for about 56 px, then stop while the diagram
  keeps moving: a visible jerk. In the horizontal layout the same happens sideways for 24 px.
- **cause (measured with read-only CDP on the live page):** the viewport element `.react-flow__viewport` mounts at
  `translate(24px, 56px)` and keeps that until the first pan. The lane-header layer (a sibling of `.react-flow`)
  sits at `max(translation, 0)` on each axis, so the headers stay pinned only once the translation is ≤ 0.
- **detection:** the Q-31 rigid-shift check on the pan: the header band shifts with the content in the first frames
  and then stands still. D-21 diagnostic: the viewport translation at load is > 0 on a view that has a sticky header
  layer. Threshold: any header-band shift during a pan (> 1 px).
- **mitigation (D-33 item 3, start position in the same frame the view first paints):** profile rule `view_start` →
  `sticky-lane-headers`. The view starts at translation (0, 0), with the headers already where they will stay
  (`vc/overlay/viewstart.py`, `vc/overlay/viewstart.js`):
  - the take runner installs a document-start script (dry runs and takes);
  - the script's MutationObserver sees `.react-flow__viewport` on a matching URL while lane nodes exist, and
    dispatches one synthetic wheel event (0.5 px per wheel unit, refined in the page), before the first paint;
  - the script re-applies the position if the app moves the view within 1.5 s; the camera's own pan ends the rule for
    that view;
  - a remount applies the rule again.
