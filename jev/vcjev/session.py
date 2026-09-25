"""Wire models config, provider route, filmed tab, decision log and runner."""

import os

from . import models as M
from .accounting import DecisionLog


def configure_provider(resolved):
    """Point the patched upstream post_json at the configured provider (C-04)."""
    from . import upstream

    upstream.ROUTE.jev = {"url": resolved["jev"]["url"], "key": resolved["jev"]["key"], "model": resolved["jev"]["model"]}
    upstream.ROUTE.text = {"base_url": resolved["text"]["base_url"], "key": resolved["text"]["key"],
                           "model": resolved["text"]["model"]}
    # Upstream reads these names when building requests; the real key/URL/model are
    # substituted by the patched post_json, so no secret is placed in the environment.
    os.environ["TYPESAFE_API_KEY"] = "routed-by-vcjev"
    os.environ["TEXT_MODEL_API_KEY"] = "routed-by-vcjev"
    os.environ["TEXT_MODEL_BASE_URL"] = resolved["text"]["base_url"]
    os.environ["TEXT_MODEL"] = resolved["text"]["model"]
    if resolved["text"].get("reasoning") == "none":
        os.environ["TEXT_MODEL_REASONING"] = "none"
    return upstream.ROUTE


def open_session(*, cdp_url, env_file, log_path, job, run_id=None, phase=None, take=None, models_config=None,
                 provider=None, jev_version=None, text_version=None, use_candidate=False, expect_viewport=None,
                 deny_extra=(), settings=None, hooks=None, budget_usd=1.0):
    from .browser import FilmedTab
    from .runner import StepRunner

    cfg = M.load(models_config)
    resolved = M.resolve(cfg, env_file=env_file, provider=provider, jev_version=jev_version,
                         text_version=text_version, use_candidate=use_candidate)
    route = configure_provider(resolved)
    from .upstream import warm
    warm_ms = warm(route.jev["url"])  # open the provider connection now, not in the first decision
    log = DecisionLog(log_path, job=job, run_id=run_id, phase=phase, take=take, budget_usd=budget_usd)
    log.event("models", **M.public(resolved))
    log.event("provider_warm", ms=warm_ms)
    tab = FilmedTab(cdp_url, expect_viewport=expect_viewport)
    runner = StepRunner(tab, log, M.public(resolved), deny_extra=deny_extra, settings=settings, hooks=hooks)
    return runner
