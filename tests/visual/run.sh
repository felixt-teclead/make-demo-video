#!/bin/sh
# Film the overlay test page in a throwaway container and measure it. Usage: tests/visual/run.sh OUTDIR
set -eu
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/../.." && pwd)
out=${1:?outdir}; mkdir -p "$out"
docker build -q -t vc-m3-testrig:local "$here" >/dev/null
docker run --rm --cpuset-cpus "${CPUS:-8-15}" --shm-size 1g -v "$repo:/repo:ro" -v "$out:/out" \
  vc-m3-testrig:local sh -c '
  Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
  cd /repo/tests/visual/site && (python3 -m http.server 8001 >/dev/null 2>&1 &) && (python3 -m http.server 8002 >/dev/null 2>&1 &)
  sleep 1
  LANGUAGE=de DISPLAY=:99 google-chrome --no-sandbox --kiosk --start-fullscreen --window-position=0,0 --window-size=1920,1080 \
    --user-data-dir=/tmp/prof --no-first-run --no-default-browser-check --disable-features=Translate,TranslateUI --lang=de-DE --accept-lang=de-DE \
    --force-device-scale-factor=1 --remote-debugging-port=9222 --hide-crash-restore-bubble about:blank >/tmp/chrome.log 2>&1 &
  for i in $(seq 50); do curl -s localhost:9222/json >/dev/null 2>&1 && break; sleep 0.2; done
  sleep 1
  DISPLAY=:99 python3 /repo/tests/visual/drive.py /out && python3 /repo/tests/visual/measure.py /out
'
