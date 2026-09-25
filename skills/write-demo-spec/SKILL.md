---
name: write-demo-spec
description: >-
  Writes or adjusts the use-case spec of a demo video without filming it: draft the spec from an instruction, feature
  text or diff, validate it, show the approval view, record the approval, and diff it against the approved version.
  Use when the user says "write / draft a demo spec for <feature>", "adjust / change / fix the spec <name>", "show me
  the approval view", "approve the spec", "what changed since approval" or "diff the spec", i.e. when only the spec
  should change. Do NOT use for: making, filming, recording, running, resuming or retaking a demo video (use
  make-demo-video, which aligns the spec itself), claude.ai or other chat-app scenes, editing QA checks, thresholds or
  cutter rules, a single screenshot, or general browser automation.
---

# Write or adjust a demo spec

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Run commands from there. No filming: never start `bin/vc-loop`.

1. Read `docs/house-rules.md` once. It applies to everything below.
2. Follow `docs/steps/align.md`. It is the one source for the spec rules (format and commands in
   `docs/spec-format.md`). Stop after approval, or after the diff when adjusting an approved spec.
3. Report the spec path, the validation result and the approval view (or the diff). If the user then wants the video,
   hand over to the `make-demo-video` skill with that spec.
