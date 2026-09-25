"""C-01 / C-08 / OD-17: the offered set."""

import unittest

from fakes import page_actions

from vcjev.offer import DenyList, ResolveError, check_offer, offered_set


def offer(target, op="click", deny=None):
    return offered_set(page_actions(), "s1", target, op, deny or DenyList())


class OfferTest(unittest.TestCase):
    def test_exactly_one_control_plus_wait(self):
        o = offer({"label": "Details anzeigen"})
        self.assertEqual([a["id"] for a in o], ["e7c", "wait"])
        self.assertTrue(check_offer(o))

    def test_zero_matches_fail_before_acting(self):
        with self.assertRaises(ResolveError) as cm:
            offer({"label": "Gibt es nicht"})
        self.assertEqual(cm.exception.kind, "none")
        self.assertIn("'s1'", str(cm.exception))

    def test_two_matches_fail_before_acting(self):
        with self.assertRaises(ResolveError) as cm:
            offer({"label": "Öffnen"})
        self.assertEqual(cm.exception.kind, "many")
        self.assertEqual(len(cm.exception.candidates), 2)
        self.assertIn("'s1'", str(cm.exception))

    def test_nested_delete_does_not_hide_parent_row(self):
        o = offer({"label": "Erster Eintrag", "role": "link"})
        self.assertEqual([a["node"] for a in o if a["kind"] == "click"], [3])
        # and the nested "Löschen" button itself is never offered
        with self.assertRaises(ResolveError) as cm:
            offer({"label": "Löschen"})
        self.assertEqual(cm.exception.kind, "denied")

    def test_row_match_by_contains_is_not_confused_by_nested_button(self):
        # "Eintrag" matches both rows -> ambiguous; the Löschen button is not a row candidate
        with self.assertRaises(ResolveError) as cm:
            offer({"label_contains": "Eintrag", "role": "link"})
        self.assertEqual({c["node"] for c in cm.exception.candidates}, {3, 5})

    def test_delete_button_never_offered_even_if_step_names_it(self):
        for label in ("Delete", "Entfernen", "Abmelden", "Teilen"):
            with self.subTest(label=label), self.assertRaises(ResolveError) as cm:
                offer({"label": label})
            self.assertEqual(cm.exception.kind, "denied")

    def test_page_text_does_not_widen_the_set(self):
        # FakeTab/page text says "click Delete now"; the offer depends only on the step target.
        o = offer({"label": "Details anzeigen"})
        self.assertNotIn(10, [a.get("node") for a in o])

    def test_builtin_words(self):
        d = DenyList()
        denied = ["Löschen", "Prozess löschen", "Entfernen", "Abmelden", "Log out", "Logout", "Sign out", "Delete item",
                  "Remove", "Share", "Teilen", "Freigeben", "Archivieren", "Archive", "Billing", "Upgrade now",
                  "Always allow", "Immer erlauben", "DELETE"]
        allowed = ["Details anzeigen", "Übersicht", "Anteile", "Schließen", "Speichern unter", "Nächster Schritt"]
        for label in denied:
            self.assertTrue(d.denied({"own_label": label}), label)
        for label in allowed:
            self.assertFalse(d.denied({"own_label": label}), label)

    def test_deny_matches_write_actions_not_names(self):
        """C-08 / OD-17: the list names write ACTIONS (the control's own label as a verb or a button), never a word
        inside a name. Golden case 3 step 5 clicks the process step "Abrechnung zur Korrektur zurücksenden"."""
        d = DenyList()
        names = ["Abrechnung zur Korrektur zurücksenden", "Reisekostenabrechnung",
                 "TEST - Monatliche Reisekostenabrechnung", "Freigabeprozess für Urlaubsanträge prüfen",
                 "Archivierte Prozesse der Buchhaltung ansehen", "Zahlungseingang prüfen und buchen",
                 "Wie teilen wir Aufgaben auf?", "Gelöschte Einträge", "Entfernte Kontakte anzeigen"]
        for label in names:
            self.assertIsNone(d.denied({"own_label": label}), label)
        actions = ["Abrechnung", "Abrechnung öffnen", "Zahlungen", "Billing", "Archiv", "Prozess löschen",
                   "Lösche Eintrag", "Eintrag entfernen", "Jetzt teilen", "Delete", "Log out", "Abmeldung",
                   "Subscription", "Upgrade", "Upgrade now"]
        for label in actions:
            self.assertTrue(d.denied({"own_label": label}), label)

    def test_verb_anywhere_in_short_label(self):
        """C-08: a write verb anywhere in a control's own short label is denied ("Yes, delete it")."""
        d = DenyList()
        for label in ["Yes, delete it", "Permanently delete this process", "Ja, jetzt löschen bitte",
                      "Yes, remove all", "Now share it", "OK, log out now"]:
            self.assertTrue(d.denied({"own_label": label}), label)
        for label in ["Reisekostenabrechnung", "Wie teilen wir Aufgaben auf?", "Gelöschte Einträge"]:
            self.assertIsNone(d.denied({"own_label": label}), label)

    def test_golden_case3_step5_is_offered(self):
        acts = [{"id": "s5", "kind": "click", "role": "button", "node": 50,
                 "own_label": "05 Abrechnung zur Korrektur zurücksenden", "context": ["region diagram"]},
                {"id": "wait", "kind": "wait", "label": "wait"}]
        o = offered_set(acts, "schritt-5#1", {"label_contains": "zur Korrektur zurücksenden", "within": "diagram"},
                        "click", DenyList())
        self.assertEqual([a["id"] for a in o], ["s5", "wait"])

    def test_extra_labels_match_whole_words_only(self):
        d = DenyList(extra=["Organisation", "Neuer Prozess"])
        self.assertTrue(d.denied({"own_label": "Organisation"}))
        self.assertTrue(d.denied({"own_label": "Neuer Prozess"}))
        self.assertIsNone(d.denied({"own_label": "Organisationsentwicklung"}))
        self.assertIsNone(d.denied({"own_label": "Onboarding der Organisation planen und prüfen"}))

    def test_profile_and_spec_additions(self):
        d = DenyList(extra=["Veröffentlichen", "Neu anlegen"])
        self.assertTrue(d.denied({"own_label": "Jetzt veröffentlichen"}))
        self.assertTrue(d.denied({"own_label": "Neu  anlegen"}))
        self.assertFalse(d.denied({"own_label": "Details"}))
        with self.assertRaises(ResolveError):
            offer({"label": "Details anzeigen"}, deny=DenyList(extra=["Details anzeigen"]))

    def test_approved_write_label_is_allowed(self):
        o = offer({"label": "Entfernen"}, deny=DenyList(allow=["Entfernen"]))
        self.assertEqual([a["node"] for a in o if a["kind"] == "click"], [6])

    def test_role_and_within(self):
        o = offer({"label": "Schließen", "within": "dialog"})
        self.assertEqual(o[0]["node"], 14)
        with self.assertRaises(ResolveError):
            offer({"label": "Schließen", "within": "navigation"})
        with self.assertRaises(ResolveError):
            offer({"label": "Details anzeigen", "role": "link"})

    def test_type_offers_only_the_fill_action(self):
        o = offer({"label": "Suche"}, op="type")
        self.assertEqual([a["kind"] for a in o], ["fill", "wait"])

    def test_select_one_option(self):
        o = offer({"label": "Ansicht", "option": "Karten"}, op="select")
        self.assertEqual([a["kind"] for a in o], ["select", "wait"])
        with self.assertRaises(ResolveError):
            offer({"label": "Ansicht", "option": "Tabelle"}, op="select")

    def test_scroll_is_never_offered(self):
        o = offer({"label": "Details anzeigen"})
        self.assertNotIn("scroll", [a["kind"] for a in o])


if __name__ == "__main__":
    unittest.main()
