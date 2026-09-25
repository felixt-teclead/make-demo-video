# Fixture test page (Q-91, OD-20)

A small static page served by the environment's static server (`/opt/vc/env/www` at `http://127.0.0.1:8099/`):

- `http://127.0.0.1:8099/fixture/index.html` – the page; client-side routes `#/uebersicht` (default), `#/eintraege`,
  `#/ansicht`, `#/protokoll`, `#/werkzeuge`.
- `http://127.0.0.1:8099/fixture/page2.html` – a second real document for full-load navigation (nav link "Seite 2").

No build step, no external resources, no network calls, no GIFs. The script never clicks, types or navigates by
itself (C-14): every state change comes from jev's input. Animations are page behaviour only. App profile:
`profiles/fixture/profile.json` (F-36); no plugin-core code knows this page (C-40).

## Targets

| Target | Where | Labels (own label, role, container) |
|---|---|---|
| Navigation | left bar, `nav aria-label="Hauptmenü"` | links "Übersicht", "Einträge", "Ansicht", "Protokoll", "Werkzeuge", "Seite 2" |
| Create (case W, C-34) | `#/eintraege` | form "Neuer Eintrag", textbox "Name des Eintrags", button "Erstellen" |
| Item list | `#/eintraege`, tab "Liste" | one `role=row` per item, row name `Eintrag <name>`; the item name is its own button; a "Löschen" button per row |
| Item dialog | click an item name | `role=dialog`, named by a heading with the item name; button "Schließen" |
| Tabs | `#/eintraege` | `role=tab` "Liste", "Details"; the "Details" panel shows "Anzahl Einträge" |
| Copy-then-paste (Q-55) | `#/werkzeuge` | value `VC-4711-FIX`, button "Kopieren" (shows "In die Zwischenablage kopiert."), textbox "Wert einfügen" (shows "Wert stimmt überein." when equal) |
| Late content (Q-30) | `#/werkzeuge` | button "Antwort anfordern": "Anfrage gesendet." at once, "Antwort erhalten: …" after `late` ms |
| Scripted list scroll | `#/protokoll` | button "Ans Ende scrollen" scrolls the region "Protokolleinträge" (2.4 s, ease in/out) |
| Pan view | `#/ansicht` | scrollable region "Diagramm", 4200 × 2400 px (room for a clean 2036 px pan) |

Item store: `localStorage["vc-fixture-items"]` (JSON list of `{id, name, created}`), seeded once with "Beispiel Alpha"
and "Beispiel Beta" when the key is absent. It persists across reloads, so a hand-created item survives a job's
cleanup, which deletes only rows named by the job's `{item}` (C-34, OD-26). Names must be unique (the form refuses a
duplicate). Status texts never repeat the item name, so `text_absent {item}` holds after a delete.

## Planted defects (all off by default)

Switch on with URL query parameters, e.g. `index.html?black=1&skeleton=11`. The settings panel "Defekte" appears only
with `?panel=1` (bottom right, never on camera in a take); a change there reloads the page with the new query. The
query is carried over to `page2.html` and back.

| Param | Defect | Serves |
|---|---|---|
| `whiteout=1` | every route change shows a full white screen for 300 ms; on `page2.html` the new document stays white for 400 ms | Q-20 (WHITE), Q-22; D-20 navigation probe |
| `black=1` | every route change shows a solid black screen for 1.5 s | Q-20 (BLACK); D-20 navigation probe |
| `orphan=1` | closing the item dialog leaves the backdrop without the dialog for one capture frame (33 ms) | Q-21; D-20 dialog-close probe |
| `nesteddim=1` | closing the item dialog first adds a second dim layer (double dim) for 3 frames | Q-21; D-20 dialog-close probe |
| `skeleton=N` | after a route change, N capture frames (N × 33 ms) of grey placeholder bars; use `11` (FAIL) and `1` (WARN) | Q-23 [S2]; D-T03, D-21 readiness |
| `sticky=1` | pan view: a 56 px toolbar above the sticky lane header, so the header rides along 56 px on a vertical pan, then pins | Q-31; D-T01, D-21 sticky elements |
| `dash=1` | pan view: edges have marching dashes (infinite CSS animation, 1 s) | Q-31; D-T02, D-21 running animations |
| `halfpaint=1` | pan view: every 2nd animation frame holds the previous picture (counter-translate by the scroll since the last paint), so a moving pan updates every 2nd frame | Q-31 step/cadence, Q-34; D-T07, D-21 frame interval |
| `jitter=1` | "Ans Ende scrollen": the scripted scroll reverses by up to 90 px mid-way | Q-31 (no change of direction) |
| `pointer=1` | a second arrow-shaped sprite at (1260, 420) | Q-40 |
| `banner=1` | 2.5 s after load a banner "Hinweis: Es gibt neue Prüfergebnisse." is inserted and pushes the view down | Q-30 layout shift; D-21 readiness |
| `late=MS` | answer delay of "Antwort anfordern" in ms (default 3000; 1000 passes Q-30, 3000 fails) | Q-30 late content |
| `panel=1` | shows the "Defekte" settings panel (not a defect) | – |

Without any switch the page is the clean reference: dialogs open and close in one frame, routes switch in one
frame, no animation runs on its own.

`halfpaint` is an emulation on the main thread; if the compositor scrolls ahead of it, the held frames can differ by
a frame of scroll. Calibrate against the gate before using it as a Q-90 fixture.

## Tests

`python3 -m unittest discover -s tests/fixture -t .` (from the repo root; no Chrome needed): labels and roles that
the fixture specs target, every switch documented and wired, no external URLs, JS syntax (`node --check`, skipped
without node), and the pages served with HTTP 200.
