#!/usr/bin/env python3
"""Optional scripted login (F-26 b, C-14, C-31). Runs inside the container, under the browser lock, off camera.

    python3 /opt/vc/env/login/scripted_login.py --profile /opt/vc/profiles/<app>/login.json [--check-only]

Exit codes: 0 logged in | 20 human needed (2FA, captcha, form not understood, or login failed: use the remote view)
            | 21 no credentials configured (human path) | 22 recording in progress or recorder status unknown
            (refused) | 1 error.
The credentials are read from the secrets file only here, typed by this deterministic code only, and never printed,
logged, sent to a model or captured (it refuses to run while the recorder is recording). App specifics (URLs,
selectors, env variable names) come from the app profile, never from this file (C-40).
"""
import argparse, json, os, sys, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from vccdp import CDP  # noqa: E402

SECRETS = os.environ.get("VC_SECRETS", "/run/secrets/vc.env")
REC = os.environ.get("VC_RECORDER_URL", "http://127.0.0.1:7777")
# generic human-only markers (not app specific): captchas and one-time-code fields
HUMAN_MARKERS = ["iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='turnstile']",
                 "iframe[src*='captcha']", "[class*='captcha' i]", "input[autocomplete='one-time-code']",
                 "input[name*='otp' i]", "input[name*='totp' i]"]


def out(status, code, **kw):
    print(json.dumps({"status": status, **kw}))
    sys.exit(code)


def read_secret(names):
    vals = {}
    try:
        with open(SECRETS) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in names:
                        vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return vals


def js_str(s):
    return json.dumps(s)


def logged_in(c, prof):
    chk = prof["logged_in"]
    url = c.evaluate("location.href")
    ok = True
    if chk.get("url_prefix"):
        ok = ok and url.startswith(chk["url_prefix"])
    if chk.get("url_not_contains"):
        ok = ok and not any(x in url for x in chk["url_not_contains"])
    if chk.get("selector"):
        ok = ok and bool(c.evaluate("!!document.querySelector(%s)" % js_str(chk["selector"])))
    if ok and c.evaluate("!!document.querySelector('input[type=password]')"):
        ok = False
    return ok


def human_marker(c, prof):
    sels = HUMAN_MARKERS + prof.get("human_markers", [])
    for s in sels:
        try:
            if c.evaluate("(()=>{const e=document.querySelector(%s); return !!(e && e.offsetParent !== null)})()" % js_str(s)):
                return s
        except Exception:
            pass
    return None


def wait_until(fn, timeout, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if r:
            return r
        time.sleep(step)
    return None


def type_into(c, selector, value):
    c.call("DOM.enable")
    root = c.call("DOM.getDocument", depth=0)["root"]["nodeId"]
    nid = c.call("DOM.querySelector", nodeId=root, selector=selector)["nodeId"]
    if not nid:
        return False
    c.call("DOM.focus", nodeId=nid)
    c.evaluate("(()=>{const e=document.activeElement; if (e && 'value' in e) { e.select && e.select(); }})()")
    c.call("Input.insertText", text=value)  # value is never echoed
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    prof = json.load(open(a.profile))
    try:
        with urllib.request.urlopen(REC + "/record/status", timeout=3) as r:
            if json.loads(r.read().decode()).get("recording"):
                out("recording in progress: login never runs on camera", 22)
    except (OSError, ValueError):
        # fail closed: without the recorder's answer nobody knows whether this would run on camera
        out("recorder status unknown: login refused (it never runs on camera)", 22)
    with CDP.page() as c:
        c.navigate(prof["start_url"], timeout=30)
        wait_until(lambda: c.evaluate("document.readyState") == "complete", 15)
        time.sleep(prof.get("settle_s", 1.5))  # redirects to the login page
        if logged_in(c, prof):
            out("logged in", 0, url_host=c.evaluate("location.host"))
        if a.check_only:
            out("login required", 20, url_host=c.evaluate("location.host"))
        names = [prof["credentials"]["user_var"], prof["credentials"]["pass_var"]]
        cred = read_secret(names)
        if len(cred) < 2 or not all(cred.values()):
            out("no credentials configured: log in through the remote view", 21)
        m = human_marker(c, prof)
        if m:
            out("human needed (2FA or captcha)", 20, marker=m)
        f = prof["form"]
        if not wait_until(lambda: c.evaluate("!!document.querySelector(%s)" % js_str(f["password"])), 15):
            out("login form not found: log in through the remote view", 20)
        if f.get("two_step") and not c.evaluate("!!document.querySelector(%s)" % js_str(f["user"])):
            out("login form not understood", 20)
        if not type_into(c, f["user"], cred[names[0]]):
            out("user field not found", 20)
        if f.get("two_step"):
            c.evaluate("document.querySelector(%s).click()" % js_str(f["submit"]))
            if not wait_until(lambda: c.evaluate("!!document.querySelector(%s)" % js_str(f["password"])), 15):
                out("password step not found", 20)
        if not type_into(c, f["password"], cred[names[1]]):
            out("password field not found", 20)
        cred.clear()
        c.evaluate("document.querySelector(%s).click()" % js_str(f["submit"]))
        def done():
            try:
                if logged_in(c, prof):
                    return "ok"
                return "human" if human_marker(c, prof) else None
            except Exception:
                return None  # page navigating
        r = wait_until(done, prof.get("timeout_s", 30))
        if r == "ok":
            out("logged in", 0, url_host=c.evaluate("location.host"))
        out("scripted login did not finish (2FA, captcha or wrong credentials): use the remote view", 20)


if __name__ == "__main__":
    main()
