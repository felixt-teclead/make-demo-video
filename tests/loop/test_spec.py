"""Spec format, validator, approval view, fingerprint, approval and diff (F-05..F-10, F-06, C-34, F-37).
Run: python3 -m unittest discover -s tests/loop -t ."""
import os
import re
import shutil
import tempfile
import unittest

from harness import REPO, sibling  # noqa: F401  (sets sys.path)

if sibling("jev"):
    os.environ.setdefault("VC_JEV_PATH", sibling("jev"))

from vcloop import match, spec as S  # noqa: E402

GOLD = os.path.join(REPO, "tests", "loop", "golden-frozen")   # golden specs as built (cp-B-try-start)
EX = os.path.join(REPO, "specs", "examples")


def copy_spec(src, tmp):
    name = os.path.splitext(os.path.basename(src))[0]
    dst = os.path.join(tmp, os.path.basename(src))
    shutil.copyfile(src, dst)
    ex = os.path.join(os.path.dirname(src), "explore", name)
    if os.path.isdir(ex):
        shutil.copytree(ex, os.path.join(tmp, "explore", name))
    return dst


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def edit(path, old, new, count=1):
    t = read(path)
    assert old in t, (old, path)
    write(path, t.replace(old, new, count))


class GoldenCases(unittest.TestCase):
    def test_case1_and_case2_validate(self):
        for n in ("c1-four-angles", "c2-find-and-read"):
            sp = S.load(os.path.join(GOLD, n + ".toml"))
            self.assertEqual(S.errors(S.validate(sp)), [], n)

    def test_case3_validates_step5_is_not_a_denied_write(self):
        # The never-offer list matches write actions, not words inside names (integration fix): the step card
        # "Abrechnung zur Korrektur zurücksenden" is offered; the validator judges with vcjev.offer itself.
        self.assertEqual(match.SOURCE, "vcjev.offer")
        sp = S.load(os.path.join(GOLD, "c3-correction-loop.toml"))
        self.assertEqual(S.errors(S.validate(sp)), [])

    def test_every_step_is_one_row_and_length_is_shown(self):
        for n in ("c1-four-angles", "c2-find-and-read", "c3-correction-loop"):
            sp = S.load(os.path.join(GOLD, n + ".toml"))
            v = S.approval_view(sp)
            rows = [ln for ln in v.splitlines() if re.match(r"^\| \d+ +\|", ln)]
            self.assertEqual([int(r.split("|")[1]) for r in rows], list(range(1, len(sp["steps"]) + 1)))
            est, _ = S.estimate_length(sp)
            self.assertIn(f"Estimated length: {est:g} s", v)
            self.assertIn(f"range {sp['length'][0]}-{sp['length'][1]} s", v)

    def test_case2_view_has_5_to_8_rows_with_a_readable_control(self):
        sp = S.load(os.path.join(GOLD, "c2-find-and-read.toml"))
        rows = [ln for ln in S.approval_view(sp).splitlines() if re.match(r"^\| \d+ +\|", ln)]
        self.assertTrue(5 <= len(rows) <= 8)
        for r in rows:
            self.assertRegex(r.split("|")[3], r"(click|type|pan|close)")


class SeededBrokenSpecs(unittest.TestCase):
    """F-10 Verify: one seeded broken spec per rule, each rejected with a message naming the step and the rule."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get("VC_TEST_TMP"))
        self.p = copy_spec(os.path.join(GOLD, "c1-four-angles.toml"), self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def problems(self):
        return S.errors(S.validate(S.load(self.p)))

    def assertRejected(self, rule, step=None):
        errs = self.problems()
        hit = [e for e in errs if e.rule.startswith(rule) and (step is None or e.step == step)]
        self.assertTrue(hit, f"expected {rule} on {step}, got {[str(e) for e in errs]}")
        if step:
            self.assertIn(step, str(hit[0]))
        self.assertIn(rule.split(".")[0], str(hit[0]))

    def test_missing_groups(self):
        for key in ("title", "purpose", "context", "length", "deny", "site_switches", "writes"):
            shutil.copyfile(os.path.join(GOLD, "c1-four-angles.toml"), self.p)
            t = read(self.p)
            t = re.sub(rf"(?m)^{key} = .*\n", "", t, count=1)
            write(self.p, t)
            errs = self.problems()
            self.assertTrue(any(e.rule == "F-07.groups" and key in e.message for e in errs), key)
        for table in ("params", "start", "privacy", "approval"):
            shutil.copyfile(os.path.join(GOLD, "c1-four-angles.toml"), self.p)
            t = read(self.p)
            t = t.replace(f"[{table}]", "[x_removed_" + table + "]")
            write(self.p, t)
            self.assertTrue(any(e.rule == "F-07.groups" and table in e.message for e in self.problems()), table)

    def test_duplicate_step_name(self):
        edit(self.p, 'name = "raci"', 'name = "bpmn"')
        self.assertRejected("F-08.name", "bpmn")

    def test_step_without_expected_state(self):
        edit(self.p, 'expect = [{ type = "text_visible", value = "BPMN exportieren" }]', "expect = []")
        self.assertRejected("F-08.expect", "bpmn")

    def test_control_matching_zero_elements(self):
        edit(self.p, 'control = { label = "BPMN" }', 'control = { label = "BPMN-Ansicht" }')
        self.assertRejected("D-11", "bpmn")

    def test_control_matching_several_elements(self):
        edit(self.p, 'control = { label = "BPMN" }', 'control = { label_contains = "e" }')
        errs = [e for e in self.problems() if e.rule == "D-11" and e.step == "bpmn"]
        self.assertTrue(errs and "many" in errs[0].message and "candidates" in errs[0].message)

    def test_unfilled_placeholder(self):
        edit(self.p, 'control = { label = "BPMN" }', 'control = { label = "{view_name}" }')
        errs = self.problems()
        self.assertTrue(any(e.rule == "F-10.params" and "view_name" in e.message for e in errs))

    def test_final_hold_without_reason(self):
        edit(self.p, "hold = 12.0", "hold = 8.0")
        self.assertRejected("F-08.final", "metadaten")
        t = read(self.p).replace('length = [30, 60]',
                                                           'length = [30, 60]\nfinal_hold_reason = "owner asked"')
        write(self.p, t)
        self.assertFalse([e for e in self.problems() if e.rule == "F-08.final"])

    def test_write_looking_control_not_flagged(self):
        edit(self.p, 'control = { label = "BPMN" }', 'control = { label = "Version einfrieren" }')
        errs = [str(e) for e in self.problems() if e.step == "bpmn"]
        self.assertTrue(any("F-10.write" in e for e in errs), errs)
        self.assertTrue(any("C-08" in e for e in errs), errs)      # also on the profile's never-offer list

    def test_write_looking_typed_text(self):
        edit(self.p, 'control = { label = "BPMN" }\n', 'control = { label = "BPMN" }\n  text_note = "x"\n')
        t = read(self.p)
        t = t.replace('op = "click"\n  control = { label = "BPMN" }', 'op = "type"\n  control = { label = "BPMN" }\n  text = "Neuen Prozess speichern"')
        write(self.p, t)
        self.assertRejected("F-10.write", "bpmn")

    def test_unapproved_write(self):
        t = read(self.p).replace(
            "writes = []\n", 'writes = [{ id = "w1", step = "bpmn", changes = "exports a file" }]\n', 1)
        write(self.p, t)
        self.assertRejected("C-34", "bpmn")

    def test_length_out_of_range(self):
        edit(self.p, "length = [30, 60]", "length = [45, 60]")
        self.assertRejected("F-10.length")

    def test_hold_below_one_second_mid_video(self):
        edit(self.p, "hold = 2.5", "hold = 0.5")
        self.assertRejected("Q-62", "bpmn")


class Fingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get("VC_TEST_TMP"))
        self.p = copy_spec(os.path.join(GOLD, "c2-find-and-read.toml"), self.tmp)
        self.fp0 = S.fingerprint(S.load(self.p))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fp(self):
        return S.fingerprint(S.load(self.p))

    def test_how_changes_keep_the_contract(self):
        edit(self.p, 'where = "the \'Prozesse\' link in the left sidebar"', 'where = "the sidebar link"')
        edit(self.p, 'control = { label = "Prozesse", within = "navigation" }',
             'control = { label = "Prozesse", within = "navigation Hauptmenü" }\n  wait_before = [{ type = "dialog_closed" }]')
        edit(self.p, "hold = 2.5", "hold = 3.0")
        edit(self.p, "warm = []", 'warm = ["https://app.example.com/prozesse"]')
        self.assertEqual(self.fp(), self.fp0)

    def test_contract_changes_change_it(self):
        cases = [('value = "Nur bei Bedarf."', 'value = "Bei Bedarf."'),        # an expected state
                 ("hold = 12.0", "hold = 10.0"),                                # the final hold
                 ("length = [30, 60]", "length = [30, 50]"),                    # the length range
                 ('does = \'click the tab "Details"\'', 'does = "open the details"'),  # a step's intent
                 ('search = "Bestell"', 'search = "Bestellung"')]              # typed text (a parameter)
        for old, new in cases:
            shutil.copyfile(os.path.join(GOLD, "c2-find-and-read.toml"), self.p)
            edit(self.p, old, new)
            self.assertNotEqual(self.fp(), self.fp0, old)

    def test_diff_against_approval(self):
        S.approve(self.p, by="owner")
        ch, text = S.diff(self.p)
        self.assertFalse(ch)
        edit(self.p, 'value = "Nur bei Bedarf."', 'value = "Bei Bedarf."')
        ch, text = S.diff(self.p)
        self.assertTrue(ch)
        self.assertIn("CONTRACT steps[3].expect[2].value: 'Nur bei Bedarf.' -> 'Bei Bedarf.'", text)
        ok, why = S.approval_status(S.load(self.p))
        self.assertFalse(ok)
        self.assertIn("changed since approval", why)

    def test_approval_refused_on_invalid_spec(self):
        edit(self.p, 'control = { label = "Prozesse", within = "navigation" }', 'control = { label = "Nope" }')
        with self.assertRaises(S.SpecError):
            S.approve(self.p, by="owner")


class WriteCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get("VC_TEST_TMP"))
        self.p = copy_spec(os.path.join(EX, "w-fixture-write.toml"), self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refused_before_approval_accepted_after(self):
        self.assertTrue(any(e.rule == "C-34" for e in S.errors(S.validate(S.load(self.p)))))
        with self.assertRaises(S.SpecError):
            S.approve(self.p, by="owner")                           # write not approved -> refused
        S.approve(self.p, by="owner", write_ids=["create-item"])
        self.assertEqual(S.errors(S.validate(S.load(self.p))), [])
        self.assertTrue(S.approval_status(S.load(self.p))[0])

    def test_unattended_write_needs_the_instruction_to_name_it(self):
        with self.assertRaises(S.SpecError):
            S.approve(self.p, unattended_instruction="Film the fixture write case. Read-only otherwise.")
        ap = S.approve(self.p, unattended_instruction="Film case W. approve write create-item, clean it up after.",
                       assumptions=["target length 20-45 s from the spec"])
        self.assertTrue(ap["approver"].startswith("unattended, from instruction sha256:"))
        self.assertEqual([w["id"] for w in ap["writes"]], ["create-item"])


if __name__ == "__main__":
    unittest.main()
