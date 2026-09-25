"""Create the repo's host .venv for the selftest (the gate needs numpy; host python3 may lack pip/ensurepip).

python3 tests/tools/host_venv.py [--check]
Builds `.venv` with `python3 -m venv --without-pip` and installs the numpy wheel pinned by env/requirements.txt's
range from PyPI with the standard library only (download + unzip). The recorder image does not use this: it installs
env/requirements.txt with pip.
"""
import json
import os
import re
import subprocess
import sys
import sysconfig
import urllib.request
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VENV = os.path.join(ROOT, ".venv")
PY = os.path.join(VENV, "bin", "python")


def has_numpy():
    return os.path.exists(PY) and subprocess.run([PY, "-c", "import numpy"], capture_output=True).returncode == 0


def numpy_range():
    with open(os.path.join(ROOT, "env", "requirements.txt")) as f:
        for line in f:
            m = re.match(r"numpy\s*>=\s*([\d.]+)\s*,\s*<\s*(\d+)", line.strip())
            if m:
                return tuple(int(x) for x in m.group(1).split(".")), int(m.group(2))
    raise SystemExit("numpy line missing in env/requirements.txt")


def main():
    if "--check" in sys.argv:
        return 0 if has_numpy() else 1
    if has_numpy():
        print(f"{VENV}: numpy present")
        return 0
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--clear", VENV], check=True)
    lo, hi = numpy_range()
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    with urllib.request.urlopen("https://pypi.org/pypi/numpy/json", timeout=60) as r:
        releases = json.load(r)["releases"]

    def vkey(v):
        return tuple(int(x) for x in v.split(".")) if re.fullmatch(r"\d+(\.\d+)*", v) else None

    versions = sorted((vkey(v), v) for v in releases if vkey(v) and lo <= vkey(v) and vkey(v)[0] < hi)
    arch = sysconfig.get_platform().split("-")[-1]
    for _, v in reversed(versions):
        wheels = [f for f in releases[v] if f["filename"].endswith(".whl") and f"-{tag}-{tag}-" in f["filename"]
                  and "manylinux" in f["filename"] and arch in f["filename"] and not f.get("yanked")]
        if wheels:
            w = wheels[0]
            break
    else:
        raise SystemExit(f"no numpy wheel for {tag} {arch} in range")
    site = subprocess.run([PY, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    dst = os.path.join(VENV, w["filename"])
    urllib.request.urlretrieve(w["url"], dst)
    with zipfile.ZipFile(dst) as z:
        z.extractall(site)
    os.remove(dst)
    print(f"{VENV}: installed {w['filename']}")
    return 0 if has_numpy() else 1


if __name__ == "__main__":
    sys.exit(main())
