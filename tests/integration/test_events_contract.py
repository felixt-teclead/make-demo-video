"""One event stream, two readers (docs/events.md): the cutter's Timeline and the gate's raw-mode reader parse the same
recorder events.jsonl; the schema check (vc/events.py) accepts what the runner, overlay and camera post."""
import json
import math
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "gate")]

from vc import events as E  # noqa: E402
from vc.cut.timeline import Timeline  # noqa: E402

T0 = 1790000000.0


def stamp(ev):
    """Stamp like env/recorder/recorder.py: frame = ceil((t - t0) * fps), the first frame that shows the event."""
    ev = dict(ev)
    ev["video_t"] = round(ev["t"] - T0, 4)
    ev["frame"] = max(0, math.ceil((ev["t"] - T0) * 30 - 1e-6))
    if ev.get("end"):
        ev["video_end_t"] = round(ev["end"] - T0, 4)
    return ev


STREAM = [
    {"type": "start", "t": T0, "label": "x"},
    {"type": "mark", "t": T0 + 0.5, "name": "liste", "step": 1, "url": "https://app.example/list"},
    {"type": "hold", "t": T0 + 0.5, "kind": "landing", "seconds": 1.0, "step": 1},
    {"type": "readiness_wait", "t": T0 + 1.6, "end": T0 + 3.4, "step": 1, "ok": True, "jev_step": "liste#1"},
    {"type": "click", "t": T0 + 4.0, "glide_t": T0 + 3.6, "click_t": T0 + 4.12, "x": 300, "y": 200, "step": 1,
     "label": "Liste", "kind": "click"},
    {"type": "hold", "t": T0 + 5.0, "kind": "step", "seconds": 1.0, "step": 1},
    {"type": "mark", "t": T0 + 6.0, "name": "suche", "step": 2, "url": "https://other.example/search"},
    {"type": "typing", "t": T0 + 6.5, "end": T0 + 7.4, "step": 2, "chars": 25, "submit": False},
    {"type": "pan", "t": T0 + 8.0, "end": T0 + 10.5, "step": 2, "distance": 1000, "axis": "y", "frames": 75},
    {"type": "hold", "t": T0 + 11.5, "kind": "final", "seconds": 12.0, "step": 2},
]


class EventsContract(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        evs = [stamp(e) for e in STREAM]
        with open(os.path.join(self.dir, "events.jsonl"), "w") as f:
            for e in evs:
                f.write(json.dumps(e) + "\n")
        man = {"run_id": "x", "raw": "raw.mp4", "t0": T0, "fps": 30, "events": evs,
               "marks": [e for e in evs if e["type"] == "mark"], "holds": [e for e in evs if e["type"] == "hold"]}
        with open(os.path.join(self.dir, "manifest.json"), "w") as f:
            json.dump(man, f)

    def test_schema_accepts_the_stream(self):
        self.assertEqual(E.check_stream(STREAM), [])

    def test_schema_rejects_missing_fields(self):
        bad = dict(STREAM[4])
        del bad["click_t"]
        self.assertTrue(E.problems(bad))
        self.assertTrue(E.problems({"type": "typing", "t": T0, "step": 1}))              # no end
        self.assertTrue(E.problems({"type": "click", "t": T0 + 1, "glide_t": T0 + 2, "click_t": T0 + 3,
                                    "x": 1, "y": 1, "step": 1}))                            # glide after press

    def test_cutter_reads_every_kind(self):
        tl = Timeline(self.dir, 700)
        self.assertEqual(len(tl.clicks), 1)
        self.assertAlmostEqual(tl.clicks[0]["glide"], 3.6, places=3)
        self.assertEqual(len(tl.typing), 1)
        self.assertAlmostEqual(tl.typing[0]["end"], 7.4, places=3)
        self.assertEqual([(round(w["start"], 2), round(w["end"], 2)) for w in tl.waits], [(1.6, 3.4)])
        self.assertEqual([(round(s["start"], 2), round(s["end"], 2), s["type"]) for s in tl.spans],
                         [(8.0, 10.5, "pan")])
        self.assertEqual([h for _f, h in tl.urls], ["app.example", "other.example"])

    def test_cutter_does_not_double_count_a_wait_also_in_take_log(self):
        with open(os.path.join(self.dir, "take.log.jsonl"), "w") as f:
            f.write(json.dumps({"type": "readiness_wait", "start": T0 + 1.6, "end": T0 + 3.4, "step": "liste#1"}) + "\n")
        self.assertEqual(len(Timeline(self.dir, 700).waits), 1)

    def test_events_just_after_a_mark_belong_to_its_segment(self):
        """Review Major 3: the recorder starts a segment at the mark's frame ceil(t*fps). The landing hold and an
        action posted a few ms after a mark in the first half of a frame belong to that segment (round() put them
        one frame earlier)."""
        evs = [stamp(e) for e in [
            {"type": "mark", "t": T0 + 0.51, "name": "a", "step": 1},
            {"type": "hold", "t": T0 + 0.514, "kind": "landing", "seconds": 1.0, "step": 1},
            {"type": "mark", "t": T0 + 2.01, "name": "b", "step": 2},
            {"type": "click", "t": T0 + 2.013, "glide_t": T0 + 2.012, "click_t": T0 + 2.013, "x": 1, "y": 1,
             "step": 2},
            {"type": "typing", "t": T0 + 2.012, "end": T0 + 2.5, "step": 2}]]
        marks = [e for e in evs if e["type"] == "mark"]
        segs = [{"index": 0, "step": 1, "name": "a", "start_t": 0.51, "end_t": 2.01, "start_frame": marks[0]["frame"],
                 "end_frame": marks[1]["frame"]},
                {"index": 1, "step": 2, "name": "b", "start_t": 2.01, "end_t": 3.0, "start_frame": marks[1]["frame"],
                 "end_frame": 90}]
        with open(os.path.join(self.dir, "manifest.json"), "w") as f:
            json.dump({"run_id": "x", "t0": T0, "fps": 30, "events": evs, "marks": marks, "segments": segs,
                       "holds": [e for e in evs if e["type"] == "hold"]}, f)
        tl = Timeline(self.dir, 90)
        s1, s2 = tl.segments()
        self.assertTrue(s1["start"] <= tl.holds[0]["start"] < s1["end"], (tl.holds[0], s1))
        self.assertTrue(s2["start"] <= tl.clicks[0]["frame"] < s2["end"], (tl.clicks[0], s2))
        self.assertTrue(s2["start"] <= tl.frame(tl.typing[0]["start"]) < s2["end"], (tl.typing[0], s2))

    def test_gate_raw_reader_reads_the_same_stream(self):
        from vcgate.inputs import raw_events
        ev = raw_events(self.dir)["clips"]["raw"]
        self.assertEqual(len(ev["clicks"]), 1)
        self.assertAlmostEqual(ev["clicks"][0]["glide_t"], 3.6, places=3)
        self.assertEqual(ev["marks"], [0.5, 6.0])
        acts = {a["type"]: a for a in ev["actions"]}
        self.assertAlmostEqual(acts["pan"]["end"], 10.5, places=3)
        self.assertAlmostEqual(acts["typing"]["end"], 7.4, places=3)
        self.assertAlmostEqual(acts["readiness_wait"]["end"], 3.4, places=3)
        self.assertEqual(len(ev["holds"]), 3)


if __name__ == "__main__":
    unittest.main()
