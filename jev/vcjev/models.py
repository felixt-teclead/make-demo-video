"""Models config (C-04): versions, provider, prices, upgrade and rollback.

Pure standard library. The key is read from the env file named by path and is
only ever handed to the HTTP client; it is never returned in a log record.
"""

import json
import os
from pathlib import Path

ROLES = ("jev", "text_helper")


class ConfigError(ValueError):
    pass


def default_config_path():
    return Path(os.environ.get("VC_MODELS_CONFIG") or Path(__file__).resolve().parents[2] / "config" / "models.json")


def load(path=None):
    path = Path(path or default_config_path())
    cfg = json.loads(path.read_text())
    validate(cfg)
    return cfg


def save(cfg, path=None):
    validate(cfg)
    path = Path(path or default_config_path())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def validate(cfg):
    if cfg.get("provider") not in cfg.get("providers", {}):
        raise ConfigError(f"provider {cfg.get('provider')!r} is not listed under providers")
    for name, p in cfg["providers"].items():
        for k in ("jev_url", "jev_format", "text_base_url", "text_format", "key_var"):
            if not p.get(k):
                raise ConfigError(f"provider {name}: missing {k}")
    for role in ROLES:
        r = cfg.get(role) or {}
        versions = r.get("versions") or {}
        if r.get("default") not in versions:
            raise ConfigError(f"{role}: default {r.get('default')!r} is not in versions")
        if r.get("candidate") and r["candidate"] not in versions:
            raise ConfigError(f"{role}: candidate {r['candidate']!r} is not in versions")
        for vid, v in versions.items():
            price = v.get("price_per_mtok") or {}
            if not all(isinstance(price.get(k), (int, float)) for k in ("input", "output")):
                raise ConfigError(f"{role} {vid}: price_per_mtok.input/output required")


def selected_version(cfg, role, override=None, use_candidate=False):
    r = cfg[role]
    vid = override or (r["candidate"] if use_candidate and r.get("candidate") else r["default"])
    if vid not in r["versions"]:
        raise ConfigError(f"{role}: version {vid!r} is not listed in the models config")
    return vid


def price(cfg, role, version):
    p = cfg[role]["versions"][version]["price_per_mtok"]
    return p["input"], p["output"]


def read_env_value(env_file, name):
    """Read one variable from the env file (KEY=VALUE lines). Never logged."""
    env_file = Path(env_file)
    if not env_file.is_file():
        raise ConfigError(f"env file not found: {env_file}")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().removeprefix("export ").strip() == name:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            return v
    raise ConfigError(f"{name} is not set in the env file {env_file}")


def resolve(cfg, *, env_file, provider=None, jev_version=None, text_version=None, use_candidate=False):
    """Everything a run needs to talk to jev and the text helper."""
    pname = provider or cfg["provider"]
    if pname not in cfg["providers"]:
        raise ConfigError(f"provider {pname!r} is not listed")
    p = cfg["providers"][pname]
    key = read_env_value(env_file, p["key_var"])
    jv = selected_version(cfg, "jev", jev_version, use_candidate)
    tv = selected_version(cfg, "text_helper", text_version, use_candidate)
    return {
        "provider": pname,
        "jev": {"url": p["jev_url"], "format": p["jev_format"], "key": key, "model": jv, "price": price(cfg, "jev", jv)},
        "text": {
            "base_url": p["text_base_url"],
            "format": p["text_format"],
            "key": key,
            "model": tv,
            "reasoning": cfg["text_helper"].get("reasoning", "none"),
            "price": price(cfg, "text_helper", tv),
        },
    }


def public(resolved):
    """The resolved settings without the key, for logs and the plan."""
    return {
        "provider": resolved["provider"],
        "jev": {k: v for k, v in resolved["jev"].items() if k != "key"},
        "text": {k: v for k, v in resolved["text"].items() if k != "key"},
    }


# ---- upgrade path -------------------------------------------------------


def add_candidate(cfg, role, version, price_in, price_out):
    r = cfg[role]
    r["versions"].setdefault(version, {"verified": None, "price_per_mtok": {"input": price_in, "output": price_out}})
    r["candidate"] = version
    return cfg


def promote(cfg, role, acceptance, today):
    """Make the candidate the default, only on a passing acceptance record.

    `acceptance` is the acceptance run's summary: {"version", "passed", "job_cost_usd"}.
    The previous default stays listed and selectable (rollback).
    """
    r = cfg[role]
    cand = r.get("candidate")
    if not cand:
        raise ConfigError(f"{role}: no candidate to promote")
    if acceptance.get("version") != cand:
        raise ConfigError(f"{role}: acceptance record is for {acceptance.get('version')!r}, not {cand!r}")
    if not acceptance.get("passed"):
        raise ConfigError(f"{role}: candidate {cand} did not pass the acceptance cases")
    if not isinstance(acceptance.get("job_cost_usd"), (int, float)) or acceptance["job_cost_usd"] > 1.0:
        raise ConfigError(f"{role}: candidate {cand} job cost {acceptance.get('job_cost_usd')} exceeds $1 (C-12)")
    r["versions"][cand]["verified"] = today
    r["history"] = [v for v in r.get("history", []) if v != cand] + [cand]
    r["default"], r["candidate"] = cand, None
    return cfg


def rollback(cfg, role):
    """Select the previous default again (no code change)."""
    r = cfg[role]
    hist = r.get("history") or [r["default"]]
    if len(hist) < 2:
        raise ConfigError(f"{role}: no previous version to roll back to")
    hist.pop()
    r["default"] = hist[-1]
    r["history"] = hist
    return cfg
