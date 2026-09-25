# Unattended runs

An instruction for a run without a human (`human_reachable=no`) answers up front:
1. the app and start URL, and that its profile exists (`profiles/<app>/profile.json`);
2. the one thing the viewer should learn, and the steps or the feature to show;
3. the account and data to use, and why they are fine to show;
4. read-only, or each write named and approved (`approve write <id>`), with its cleanup;
5. the login path: a live stored session or credentials in the env file;
6. the target length, the final hold and any knob away from its default, including the take cap;
7. where to deliver the video and the report.

A missing answer with a documented default takes the default and is recorded as an assumption
(`vc-spec approve --unattended --assume …`, listed in the delivery). A missing answer without a default (items 1, 2
and 4) ends the run before filming with a stop report.
