# vc-v1 recorder: one Google Chrome (stable) in kiosk mode on Xvfb, recorded with ffmpeg, remote view via noVNC.
# Rebuild from source: bin/vc-env build. Every package below is mapped to its run phase in docs/build-notes.md (C-29).
# Public base: Debian bookworm with CPython 3.12 in /usr/local (jev-ultrafast requires Python >= 3.12; bookworm's own
# python3 is 3.11). The venv below is built from this Python.
FROM python:3.12-slim-bookworm

ARG VC_UID=1000
ARG VC_GID=1000
ARG VC_LOCALE=de_DE.UTF-8

ENV DEBIAN_FRONTEND=noninteractive

# Base runtime + Chrome repository in one layer; apt lists and docs removed in the same layer (C-22, C-29).
RUN set -eux; \
    printf 'path-exclude /usr/share/doc/*\npath-exclude /usr/share/man/*\npath-exclude /usr/share/info/*\npath-exclude /usr/share/locale/*\npath-include /usr/share/locale/de/*\npath-include /usr/share/locale/locale.alias\n' > /etc/dpkg/dpkg.cfg.d/01-slim; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg; \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        google-chrome-stable \
        xvfb x11vnc novnc python3-websockify \
        ffmpeg \
        fonts-noto-core fonts-noto-mono fonts-noto-color-emoji fontconfig \
        locales tzdata \
        util-linux procps; \
    apt-get purge -y --auto-remove gnupg; \
    sed -i "s/^# *\(${VC_LOCALE}\)/\1/" /etc/locale.gen; locale-gen; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc /usr/share/man; \
    rm -f /etc/apt/sources.list.d/google-chrome.list; \
    groupadd -g "${VC_GID}" vc; useradd -m -u "${VC_UID}" -g "${VC_GID}" -s /bin/sh vc; \
    mkdir -p /profile /runs /state /opt/vc; chown vc:vc /profile /runs /state; chmod 700 /profile

# Python venv for in-container tools: the ONE requirements file (env/requirements.txt). The jev-ultrafast line is
# installed with --no-deps (docs/jev-interface.md) and its demo web app is dropped (C-29).
COPY requirements.txt /tmp/requirements.txt
RUN set -eux; python3 -m venv /opt/venv; \
    grep -v 'no-deps' /tmp/requirements.txt > /tmp/req-deps.txt; \
    grep 'no-deps' /tmp/requirements.txt | sed 's/#.*//' > /tmp/req-nodeps.txt; \
    /opt/venv/bin/pip install --no-cache-dir -r /tmp/req-deps.txt; \
    /opt/venv/bin/pip install --no-cache-dir --no-deps -r /tmp/req-nodeps.txt; \
    /opt/venv/bin/python -c 'import importlib.util, pathlib, shutil; r = pathlib.Path(importlib.util.find_spec("jev_ultrafast").submodule_search_locations[0]); shutil.rmtree(r / "static", ignore_errors=True); (r / "demo.py").unlink(missing_ok=True)'; \
    /opt/venv/bin/pip uninstall -y pip setuptools >/dev/null 2>&1 || true; \
    rm -rf /tmp/req*.txt /root/.cache

COPY rootfs/ /
RUN chmod 755 /usr/local/bin/vc-* && fc-cache -f >/dev/null

ENV LANG=${VC_LOCALE} LC_ALL=${VC_LOCALE} DISPLAY=:99 HOME=/home/vc \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    VC_CDP_URL=http://127.0.0.1:9222 VC_RECORDER_URL=http://127.0.0.1:7777 \
    VC_RUNS=/runs VC_STATE=/state VC_SECRETS=/run/secrets/vc.env PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/vc:/opt/vc/jev:/opt/vc/loop:/opt/vc/gate VC_HOOKS=vc.overlay.vc_hooks VC_PYTHON=/opt/venv/bin/python

USER vc
WORKDIR /opt/vc
EXPOSE 7777 6080
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7777/health >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/vc-supervisor"]
