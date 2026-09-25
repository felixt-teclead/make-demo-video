"""VC_HOOKS entry point for the take runner (loop/vcloop/take_runner.py): the on-camera hooks and the camera.

The image sets VC_HOOKS=vc.overlay.vc_hooks. The runner calls, in order:

    h = make_hooks(recorder, filming)   # before the jev session exists
    runner = open_session(hooks=Hooks(**{k: h[k] for k in HOOK_NAMES if k in h}), ...)
    h["bind"](runner.tab)               # the one filmed tab
    h["install"]()                      # after the start URL loaded, before recording starts (overlay drawn)
    h["camera"](runner, action, rec)    # a pan or reveal
    h["mark_copied"](value)             # a value the demo copied: typing it becomes a paste (Q-55)

Filming (a take): cursor glide, hover, press + ripple, typing and paste are drawn by the overlay (M3) and every click,
typing, paste and orphan ripple is posted to the recorder as one event (docs/events.md). Not filming (dry run, cleanup):
no overlay and no glides (fast), the jev wrapper's default typing; the camera still moves the view, so the dry run
proves every pan reaches its target.
"""

HOOK_NAMES = ("before_input", "before_click", "after_input", "input_aborted", "type_text")


def make_hooks(recorder, filming):
    state = {"cam": None, "tab": None, "copied": set()}

    def emit(ev):
        if recorder is not None and filming:
            recorder.event(ev)

    def bind(tab):
        state["tab"] = tab
        if filming:
            from .jev_hooks import OnCamera
            try:
                from vcjev.upstream import StalePage as stale
            except Exception:  # noqa: BLE001 - unit tests without the wrapper
                stale = RuntimeError
            state["cam"] = OnCamera(tab, emit=emit, stale=stale, copied=state["copied"])

    def install():
        if state["cam"] is not None:
            return state["cam"].install()

    def mark_copied(value):
        state["copied"].add(value)
        if state["cam"] is not None:
            state["cam"].mark_copied(value)

    def camera(runner, action, rec=None):
        from . import camera as cam_mod
        tab = state["tab"] or runner.tab

        def check(to):
            fn = getattr(runner, "check", None)
            if fn is not None:
                return bool(fn(to, 2.0))
            return bool(tab.check(to).get("ok"))

        return cam_mod.move(tab, action, emit=emit, check=check)

    def _delegate(name):
        def hook(*args):
            cam = state["cam"]
            if cam is None:
                raise RuntimeError(f"on-camera hook {name} called before bind(tab)")
            return getattr(cam, name)(*args)
        return hook

    out = {"bind": bind, "install": install, "camera": camera, "mark_copied": mark_copied}
    if filming:
        out.update({name: _delegate(name) for name in HOOK_NAMES})
    return out
