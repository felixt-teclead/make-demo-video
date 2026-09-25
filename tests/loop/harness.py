"""Test harness: a throw-away workspace (spec copy + exploration + scenario + components) and a vc-loop runner."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FAKES = os.path.join(REPO, "tests", "fakes")
sys.path.insert(0, os.path.join(REPO, "loop"))
sys.path.insert(0, FAKES)


def sibling(name):
    """The component under test: M2 jev (matching), M4 cutter, M6a gate. In the merged repo they all live in REPO;
    VC_JEV_PATH / VC_M4_DIR / VC_M6A_DIR override (e.g. to test another checkout)."""
    env = {"jev": "VC_JEV_PATH", "cut": "VC_M4_DIR", "gate": "VC_M6A_DIR"}[name]
    if os.environ.get(env):
        return os.environ[env]
    p = {"jev": os.path.join(REPO, "jev"), "cut": REPO, "gate": REPO}[name]
    return p if os.path.exists(p) else None


class Workspace:
    def __init__(self, spec_rel, scenario, *, root=None, components="stub", keep=False):
        self.root = root or tempfile.mkdtemp(prefix="vcloop-", dir=os.environ.get("VC_TEST_TMP"))
        self.keep = keep
        os.makedirs(self.root, exist_ok=True)
        sdir = os.path.join(self.root, "specs")
        os.makedirs(sdir, exist_ok=True)
        src = os.path.join(REPO, spec_rel)
        self.spec = os.path.join(sdir, os.path.basename(src))
        shutil.copyfile(src, self.spec)
        name = os.path.splitext(os.path.basename(src))[0]
        ex = os.path.join(os.path.dirname(src), "explore", name)
        if os.path.isdir(ex):
            shutil.copytree(ex, os.path.join(sdir, "explore", name))
        self.scenario = os.path.join(self.root, "scenario.json")
        if scenario.get("store") == "auto":
            scenario["store"] = os.path.join(self.root, "store.json")
            with open(scenario["store"], "w") as f:
                json.dump({"items": [{"id": "hand-1", "name": "Handeintrag", "by": "human"}]}, f)
        with open(self.scenario, "w") as f:
            json.dump(scenario, f, indent=1)
        self.components = os.path.join(self.root, "components.json")
        self._write_components(components)
        self.env = dict(os.environ, VC_RUNS_DIR=os.path.join(self.root, "runs"),
                        VC_STATE_DIR=os.path.join(self.root, "state"), VC_FAKE_SCENARIO=self.scenario,
                        VC_LEDGER=os.path.join(self.root, "FIXES-LEDGER.md"))
        self.ledger = self.env["VC_LEDGER"]
        if sibling("jev"):
            self.env["VC_JEV_PATH"] = sibling("jev")

    def _write_components(self, kind):
        with open(os.path.join(FAKES, "components.stub.json")) as f:
            cfg = json.loads(f.read().replace("{fakes}", FAKES))
        if kind == "real-cut-gate":
            cut, gate = sibling("cut"), sibling("gate")
            cfg["cut"] = [os.path.join(cut, "bin", "vc-cut"), "{run_dir}", "--speed", "{speedup_factor}",
                          "--crossfade", "{crossfade_s}"]
            cfg["gate"] = [os.path.join(gate, "bin", "vc-gate"), "{run_dir}", "--events", "{events}", "--workers", "4"]
            cfg["protected"] = [os.path.join(gate, "gate", "vcgate", "**", "*.py"), os.path.join(cut, "vc", "cut", "*.py")]
        with open(self.components, "w") as f:
            json.dump(cfg, f, indent=1)

    def approve(self, by="owner", writes=(), unattended_instruction=None, assume=()):
        args = ["approve", self.spec]
        if unattended_instruction is not None:
            ip = os.path.join(self.root, "instruction.txt")
            with open(ip, "w") as f:
                f.write(unattended_instruction)
            args += ["--unattended", "--instruction", ip]
        else:
            args += ["--by", by]
        for w in writes:
            args += ["--approve-write", w]
        for a in assume:
            args += ["--assume", a]
        return self.cli("vc-spec", *args)

    def cli(self, tool, *args, extra_env=None):
        env = dict(self.env, **(extra_env or {}))
        env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "")
        p = subprocess.run([os.path.join(REPO, "bin", tool)] + [str(a) for a in args], env=env,
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def run(self, *knobs, job="job", extra_env=None):
        args = ["run", self.spec, "--components", self.components, "--job", job]
        for k in knobs:
            args += ["--knob", k]
        return self.cli("vc-loop", *args, extra_env=extra_env)

    def resume(self, job="job", answer=None, extra_env=None):
        args = ["resume", self.job_dir(job)] + (["--answer", answer] if answer else [])
        return self.cli("vc-loop", *args, extra_env=extra_env)

    def job_dir(self, job="job"):
        return os.path.join(self.root, "runs", "jobs", job)

    def state(self, job="job"):
        with open(os.path.join(self.job_dir(job), "state.json")) as f:
            return json.load(f)

    def take_log(self, job="job"):
        p = os.path.join(self.job_dir(job), "take-log.jsonl")
        return [json.loads(x) for x in open(p)] if os.path.exists(p) else []

    def timing(self, job="job"):
        p = os.path.join(self.job_dir(job), "timing.jsonl")
        return [json.loads(x) for x in open(p)] if os.path.exists(p) else []

    def report(self, job="job"):
        p = os.path.join(self.job_dir(job), "report.md")
        return open(p).read() if os.path.exists(p) else ""

    def cleanup(self):
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)
