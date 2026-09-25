# Throwaway test browser + jev runtime for the vcjev integration tests.
# Not the filming environment (that is built by the env milestone); it only
# gives the tests a Chrome with a DevTools port and the jev Python deps.
FROM python:3.12-slim
ARG JEV_ULTRAFAST_SHA=1231850a0bf1a0c0341fe408ef1668dbbfdfac46
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
COPY env/requirements.txt /tmp/requirements.txt
RUN grep -v 'no-deps' /tmp/requirements.txt > /tmp/req-deps.txt && pip install --no-cache-dir -r /tmp/req-deps.txt \
 && pip install --no-cache-dir --no-deps \
      "jev-ultrafast @ https://github.com/browser-use/jev-ultrafast/archive/${JEV_ULTRAFAST_SHA}.tar.gz" \
 && python - <<'PY'
import importlib.util, pathlib, shutil
root = pathlib.Path(importlib.util.find_spec("jev_ultrafast").submodule_search_locations[0])
# C-29: the upstream demo web app is not shipped.
shutil.rmtree(root / "static", ignore_errors=True)
(root / "demo.py").unlink(missing_ok=True)
PY
WORKDIR /work
