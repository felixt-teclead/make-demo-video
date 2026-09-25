# Step: deliver

The loop wrote `<job>/report.md` and, if a take exists, `<job>/deliver/<spec>[-NOT-A-HIT].mp4`. Relay; do not
re-judge the take and do not re-open its frames.

The message to the user holds, taken from the report and logs (never from memory):
- the video path and its length, labelled HIT or NOT A HIT (never call a NOT A HIT a pass);
- the verdict line and the path of the full QA report;
- takes used, "k of cap";
- the paths of the take log (`take-log.jsonl`) and the timing summary (`bin/vc-loop summary JOB_DIR`);
- jev decisions and cost; agent cost only if the harness reported it;
- approved writes that ran, and created items deleted or left;
- any fix-promotion proposal (`docs/steps/promotion.md`), or "none";
- every warning that was kept;
- unattended: the assumptions from approval and, if the run stopped, the stop report (stop point, what was needed,
  the options, the saved job folder for a later resume).

Cap reached without a hit, attended: ask the human to raise the cap, change the spec, or accept the take, then
`bin/vc-loop resume JOB_DIR --answer raise-cap=N|change-spec|accept`. Unattended: end with the report.
