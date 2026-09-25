# Step: propose a fix promotion (S2 stub: format only)

When a fix is proven (a green dry run or a hit after it), classify it:
- **SPECIFIC**: tied to this app or video. It stays in the spec or the app's quirk record (`specs/quirks/`). No
  proposal.
- **COMMON**: likely to recur on many apps or videos (skill text, a default mitigation, a gate check). Propose it at
  delivery. Nothing is applied: the plugin files stay unchanged until the owner confirms and makes the change; a gate
  check additionally needs the owner flag (house rule H5).

Append one line per proposal to `<job>/promotions.jsonl`, and one line per owner decision:

```json
{"type": "proposal", "id": "<job>-p1", "class": "COMMON", "target": "docs/steps/on-fail.md",
 "summary": "wait for the list to settle before typing into a search box",
 "evidence": {"failing": {"run": "<run_id>", "check": "Q-30", "measure": "unexplained jump at 4.2 s"},
              "change": {"what": "", "where": "", "old": "", "new": "", "why": ""},
              "passing": {"run": "<run_id>", "verdict": "PASS"}},
 "at": "<ISO time>"}
{"type": "decision", "id": "<job>-p1", "decision": "accepted|rejected|deferred", "by": "<owner>", "at": "<ISO time>",
 "note": ""}
```

Required in a proposal: `id`, `class` (COMMON or SPECIFIC; only COMMON is proposed), `target` (the plugin file it would
change), `evidence.failing`, `evidence.change`, `evidence.passing`. The delivery lists each proposal with its id,
class, target and evidence (attended: ask for the decision; unattended: list it, promote nothing).
