"""App profiles (F-36, C-40). The core knows no app: everything app-specific is in `profiles/<app>/profile.json`,
selected by the host of the spec's start URL (and of every site switch).

profile.json:
  {"name": "...", "hosts": ["example.org", "*.example.org"],
   "deny_extra": ["..."],                        # C-08 additions (never removals)
   "login": {"url": "...", "check": "login.json"},  # F-26; `check` is M1's scripted-login/check profile file
   "quirk_record": "specs/quirks/<host>.md",     # D-31 location (OD-12: with the specs), relative to the repo root
   "data_snapshot": null | {...},                 # C-34 read-only proof, optional
   "cleanup": {"safe_delete": true|false, ...}}   # C-34/OD-26 whether this app has a clear, safe delete path
"""
import fnmatch
import json
import os
from urllib.parse import urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def profiles_dir():
    return os.environ.get("VC_PROFILES_DIR") or os.path.join(ROOT, "profiles")


class ProfileError(ValueError):
    pass


def host_of(url):
    p = urlparse(url if "//" in url else "//" + url)
    if not p.hostname:
        return None
    return p.hostname.lower() + (f":{p.port}" if p.port else "")


def load_all(pdir=None):
    pdir = pdir or profiles_dir()
    out = []
    if not os.path.isdir(pdir):
        return out
    for app in sorted(os.listdir(pdir)):
        f = os.path.join(pdir, app, "profile.json")
        if os.path.isfile(f):
            with open(f) as fh:
                p = json.load(fh)
            p.setdefault("name", app)
            p["_dir"] = os.path.join(pdir, app)
            out.append(p)
    return out


def match(url, pdir=None):
    host = host_of(url or "")
    if not host:
        raise ProfileError(f"no host in URL {url!r}")
    bare = host.split(":")[0]
    for p in load_all(pdir):
        for pat in p.get("hosts", []):
            if fnmatch.fnmatch(host, pat) or fnmatch.fnmatch(bare, pat):
                return p
    raise ProfileError(f"no app profile for host {host!r}: add profiles/<app>/profile.json (F-36)")


def for_spec(spec, pdir=None, forced=None):
    """All profiles a spec needs: the start site and every site switch (OD-25). `forced` = the app_profile knob."""
    urls = [spec["start"]["url"]] + [s.get("site", "") for s in spec.get("site_switches", [])]
    out = []
    if forced and forced != "auto":
        cand = [p for p in load_all(pdir) if p["name"] == forced]
        if not cand:
            raise ProfileError(f"app_profile knob names an unknown profile {forced!r}")
        out.append(cand[0])
        urls = urls[1:]
    for u in urls:
        p = match(u, pdir)
        if p["name"] not in [q["name"] for q in out]:
            out.append(p)
    return out


def deny_extra(profiles):
    out = []
    for p in profiles:
        for w in p.get("deny_extra", []):
            if w not in out:
                out.append(w)
    return out


def public(p):
    return {k: v for k, v in p.items() if not k.startswith("_")}
