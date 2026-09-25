#!/usr/bin/env python3
"""vc-v1 recorder control API (C-16). Stdlib only. Runs inside the recorder container.

Records the whole virtual screen (x11grab, no system pointer) at a fixed frame rate and marks named points on the
video clock. API and run layout: docs/interfaces.md sections 4 and 5.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import vccdp  # noqa: E402

RUNS = os.environ.get("VC_RUNS", "/runs")
DISPLAY = os.environ.get("DISPLAY", ":99")
SCREEN = os.environ.get("VC_SCREEN", "1920x1080")
FPS = int(os.environ.get("VC_FPS", "30"))
CRF = int(os.environ.get("VC_CRF", "18"))
PORT = int(os.environ.get("VC_RECORDER_PORT", "7777"))
PRESET = "veryfast"  # real-time 1080p30 on a few cores; the raw file is re-encoded by the cutter anyway
HOLD_KINDS = {"landing", "step", "dialog", "reading", "final"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
STOP_WAIT_S = float(os.environ.get("VC_REC_STOP_WAIT_S", "20"))   # ffmpeg's time to finish after "q" before signals
# A run folder may already exist when the loop created it for its request.json (docs/loop.md); it must not hold a
# recording yet (a recut always gets a new run folder, Q-58).
RECORDING_FILES = ("raw.mkv", "raw.mp4", "manifest.json", "events.jsonl", "recorder.log", "browser.json")
# C-31: page URLs are stored with the values of credential-like parameters removed (query and fragment).
SECRET_PARAMS = {"code", "token", "access_token", "id_token", "refresh_token", "auth", "authorization", "state",
                 "session", "sessionid", "session_id", "sid", "password", "passwd", "pwd", "secret", "client_secret",
                 "key", "api_key", "apikey", "signature", "sig", "jwt", "ticket", "samlresponse", "samlrequest",
                 "assertion", "otp", "nonce", "x-amz-signature", "x-amz-credential", "x-amz-security-token"}
URL_KEYS = ("url", "href", "page_url", "location")


def _redact_params(q):
    if "=" not in q:
        return q
    out = []
    for part in q.split("&"):
        k, sep, v = part.partition("=")
        name = urllib.parse.unquote_plus(k).lower()
        out.append(f"{k}=REDACTED" if sep and (name in SECRET_PARAMS or name.endswith(("_token", "token"))) else part)
    return "&".join(out)


def redact_url(u):
    """The URL without credential values: userinfo dropped, secret query/fragment parameters set to REDACTED."""
    if not isinstance(u, str) or not u:
        return u
    try:
        sp = urllib.parse.urlsplit(u)
    except ValueError:
        return "REDACTED-URL"
    netloc = sp.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit((sp.scheme, netloc, sp.path, _redact_params(sp.query), _redact_params(sp.fragment)))


def now():
    return time.time()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds")


class Recording:
    def __init__(self, run_id, label):
        self.run_id = run_id
        self.label = label
        self.dir = os.path.join(RUNS, run_id)
        self.t0 = None
        self.started = now()
        self.events = []
        self.proc = None
        self.t0_ready = threading.Event()
        self.lock = threading.Lock()
        self.log = None

    # frame n is captured at t0 + n/FPS; an event at t is first visible in frame ceil((t - t0) * FPS)
    def stamp(self, ev):
        t = float(ev.get("t") or now())
        ev["t"] = round(t, 6)
        ev["video_t"] = round(t - self.t0, 4)
        ev["frame"] = max(0, math.ceil((t - self.t0) * FPS - 1e-6))
        if isinstance(ev.get("end"), (int, float)) and ev["end"] > 1e8:     # docs/events.md: spans carry `end`
            ev["video_end_t"] = round(float(ev["end"]) - self.t0, 4)
        return ev

    def add(self, ev):
        for k in URL_KEYS:
            if k in ev:
                ev[k] = redact_url(ev[k])
        with self.lock:
            self.stamp(ev)
            self.events.append(ev)
            with open(os.path.join(self.dir, "events.jsonl"), "a") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    def start(self):
        os.makedirs(self.dir, exist_ok=True)       # the caller checked it holds no recording (RECORDING_FILES)
        w, h = SCREEN.split("x")
        mkv = os.path.join(self.dir, "raw.mkv")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats",
               "-f", "x11grab", "-draw_mouse", "0", "-framerate", str(FPS), "-video_size", f"{w}x{h}",
               "-thread_queue_size", "1024", "-i", f"{DISPLAY}.0+0,0",
               "-an", "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-pix_fmt", "yuv420p",
               "-profile:v", "high", "-g", str(FPS * 2), "-fps_mode", "cfr", "-r", str(FPS), mkv]
        self.log = open(os.path.join(self.dir, "recorder.log"), "w")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL)
        threading.Thread(target=self._read_stderr, daemon=True).start()
        if not self.t0_ready.wait(10):
            self.proc.kill()
            raise RuntimeError("recorder did not start (no first frame within 10 s); see recorder.log")
        self.add({"type": "start", "t": self.t0, "label": self.label})
        threading.Thread(target=self._snapshot_browser, daemon=True).start()

    def _read_stderr(self):
        pat = re.compile(r"Duration: N/A, start: (\d+\.\d+)")
        for raw in self.proc.stderr:
            line = raw.decode(errors="replace")
            self.log.write(line)
            self.log.flush()
            if self.t0 is None:
                m = pat.search(line)
                if m:
                    self.t0 = float(m.group(1))
                    self.t0_ready.set()

    def _snapshot_browser(self):
        try:
            info = browser_info()
        except Exception as e:  # never fails a recording
            info = {"error": str(e)}
        with open(os.path.join(self.dir, "browser.json"), "w") as f:
            json.dump(info, f, indent=1)

    def stop(self):
        t_stop = now()
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except OSError:
            pass
        killed = False
        try:
            self.proc.wait(STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(min(10.0, STOP_WAIT_S))
            except subprocess.TimeoutExpired:
                # never leave ffmpeg grabbing the screen and filling the disk after a stop
                self.proc.kill()
                self.proc.wait()
                killed = True
        if killed:
            self.log.write("recorder: ffmpeg did not finish after q and SIGINT; killed\n")
            self.log.close()
            raise RuntimeError("ffmpeg did not finish and was killed; the recording is incomplete (raw.mkv kept)")
        mkv = os.path.join(self.dir, "raw.mkv")
        mp4 = os.path.join(self.dir, "raw.mp4")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", mkv, "-c", "copy",
                        "-movflags", "+faststart", mp4], check=True)
        os.remove(mkv)
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
             "stream=nb_read_packets,width,height,r_frame_rate,pix_fmt,profile:format=duration", "-of", "json", mp4],
            capture_output=True, text=True, check=True).stdout)
        st = probe["streams"][0]
        frames = int(st["nb_read_packets"])
        duration = frames / FPS
        m = self.manifest(frames, duration, t_stop, st)
        with open(os.path.join(self.dir, "manifest.json"), "w") as f:
            json.dump(m, f, indent=1, ensure_ascii=False)
        self.log.close()
        return m

    def manifest(self, frames, duration, t_stop, st):
        with self.lock:
            events = list(self.events)
        marks = [e for e in events if e["type"] == "mark"]
        holds = [e for e in events if e["type"] == "hold"]
        segs = []
        for i, mk in enumerate(marks):
            end_t = marks[i + 1]["video_t"] if i + 1 < len(marks) else duration
            end_f = marks[i + 1]["frame"] if i + 1 < len(marks) else frames
            segs.append({"index": i, "step": mk.get("step"), "name": mk.get("name"),
                         "start_t": mk["video_t"], "end_t": round(end_t, 4),
                         "start_frame": mk["frame"], "end_frame": end_f,
                         "holds": [h for h in holds if mk["video_t"] <= h["video_t"] < end_t]})
        return {"run_id": self.run_id, "label": self.label, "raw": "raw.mp4", "t0": self.t0, "fps": FPS,
                "width": st.get("width"), "height": st.get("height"), "pix_fmt": st.get("pix_fmt"),
                "profile": st.get("profile"), "duration": round(duration, 4), "frames": frames,
                "started": iso(self.t0), "stopped": iso(t_stop),
                "marks": marks, "holds": holds, "events": events, "segments": segs}


def browser_info():
    """Browser settings snapshot: identical for discovery and takes (C-15)."""
    ver = vccdp.http_json("/json/version")
    pages = vccdp.page_targets()
    info = {"version": ver.get("Browser"), "user_agent": ver.get("User-Agent"), "page_tabs": len(pages)}
    h = hashlib.sha256()
    for p in ("/usr/local/bin/vc-chrome", "/etc/opt/chrome/policies/managed/vc.json", "/etc/fonts/local.conf"):
        with open(p, "rb") as f:
            h.update(f.read())
    info["settings_sha256"] = h.hexdigest()
    with vccdp.CDP.page(timeout=5) as c:
        info["page"] = c.evaluate(
            "({w: innerWidth, h: innerHeight, sw: screen.width, sh: screen.height, dpr: devicePixelRatio,"
            " depth: screen.colorDepth, lang: navigator.language, langs: navigator.languages,"
            " tz: Intl.DateTimeFormat().resolvedOptions().timeZone, locale: Intl.DateTimeFormat().resolvedOptions().locale})")
    return info


STATE = {"rec": None, "runs": {}}
GLOCK = threading.Lock()


def default_run_id(label):
    s = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    lab = re.sub(r"[^A-Za-z0-9._-]+", "-", label or "").strip("-")[:40]
    return f"{s}-{lab}" if lab else s


class Handler(BaseHTTPRequestHandler):
    server_version = "vc-recorder/1"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (iso(now()), fmt % a))

    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def err(self, code, msg):
        self.reply(code, {"ok": False, "error": msg})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        d = json.loads(self.rfile.read(n).decode() or "{}")
        if not isinstance(d, dict):
            raise ValueError("body must be a JSON object")
        return d

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/")
        rec = STATE["rec"]
        if p == "/health":
            h = {"ok": True, "xvfb": os.path.exists("/tmp/.X11-unix/X" + DISPLAY.strip(":").split(".")[0]),
                 "recording": rec.run_id if rec else None}
            try:
                vccdp.http_json("/json/version", timeout=2)
                h["chrome"] = h["cdp"] = True
            except Exception:
                h["chrome"] = h["cdp"] = False
            h["ok"] = h["xvfb"] and h["cdp"]
            return self.reply(200 if h["ok"] else 503, h)
        if p == "/browser":
            try:
                return self.reply(200, browser_info())
            except Exception as e:
                return self.err(503, str(e))
        if p == "/record/status":
            if not rec:
                return self.reply(200, {"recording": None})
            return self.reply(200, {"recording": rec.run_id, "t0": rec.t0, "elapsed": round(now() - rec.t0, 3),
                                    "events": len(rec.events), "dir": rec.dir})
        m = re.match(r"^/runs/([^/]+)$", p)
        if m:
            f = os.path.join(RUNS, m.group(1), "manifest.json")
            if not RUN_ID_RE.match(m.group(1)) or not os.path.exists(f):
                return self.err(404, "no manifest for run")
            with open(f) as fh:
                return self.reply(200, json.load(fh))
        return self.err(404, "unknown path")

    def do_POST(self):
        p = self.path.split("?")[0].rstrip("/")
        try:
            b = self.body()
        except (ValueError, json.JSONDecodeError) as e:
            return self.err(400, "bad JSON: %s" % e)
        try:
            if p == "/record/start":
                return self.start(b)
            if p == "/record/stop":
                return self.stop()
            rec = STATE["rec"]
            if p in ("/mark", "/hold", "/event"):
                if not rec:
                    return self.err(409, "not recording")
                ev = dict(b)
                if p == "/mark":
                    if not b.get("name"):
                        return self.err(400, "mark needs a name")
                    ev["type"] = "mark"
                elif p == "/hold":
                    if b.get("kind") not in HOLD_KINDS:
                        return self.err(400, "hold kind must be one of %s" % sorted(HOLD_KINDS))
                    try:
                        ev["seconds"] = float(b.get("seconds"))
                    except (TypeError, ValueError):
                        return self.err(400, "hold needs seconds")
                    ev["type"] = "hold"
                    ev.pop("wait", None)
                else:
                    if not b.get("type"):
                        return self.err(400, "event needs a type")
                    if b["type"] in ("start", "mark", "hold"):
                        return self.err(400, "use /mark or /hold")
                ev = rec.add(ev)
                if p == "/hold" and b.get("wait"):
                    time.sleep(max(0.0, ev["t"] + ev["seconds"] - now()))
                return self.reply(200, ev)
            return self.err(404, "unknown path")
        except Exception as e:
            return self.err(500, "%s: %s" % (type(e).__name__, e))

    def start(self, b):
        with GLOCK:
            if STATE["rec"]:
                return self.err(409, "already recording: " + STATE["rec"].run_id)
            run_id = b.get("run_id") or default_run_id(b.get("label"))
            if not RUN_ID_RE.match(run_id):
                return self.err(400, "bad run_id")
            d = os.path.join(RUNS, run_id)
            if os.path.exists(d) and (not os.path.isdir(d) or any(os.path.exists(os.path.join(d, f))
                                                                  for f in RECORDING_FILES)):
                return self.err(409, "run folder holds a recording (a recut needs a new run id, Q-58)")
            rec = Recording(run_id, b.get("label"))
            rec.start()
            STATE["rec"] = rec
        return self.reply(200, {"run_id": rec.run_id, "dir": rec.dir, "t0": rec.t0})

    def stop(self):
        try:
            m = stop_recording()
        except NotRecording:
            return self.err(409, "not recording")
        return self.reply(200, m)


class NotRecording(Exception):
    pass


def stop_recording():
    """Stop and finalise the running recording (the API's stop and the shutdown path share it). The state is cleared
    only once ffmpeg has exited, so a recorder never forgets an ffmpeg that still runs."""
    with GLOCK:
        rec = STATE["rec"]
        if not rec:
            raise NotRecording()
        try:
            return rec.stop()
        finally:
            if rec.proc is None or rec.proc.poll() is not None:
                STATE["rec"] = None


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True

    def term(*_):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)      # a second TERM must not interrupt the finalising
        try:
            stop_recording()
            sys.stderr.write("%s recording finalised on shutdown\n" % iso(now()))
        except NotRecording:
            pass
        except Exception as e:
            sys.stderr.write("stop on shutdown failed: %s\n" % e)
        os._exit(0)

    signal.signal(signal.SIGTERM, term)
    sys.stderr.write("%s recorder listening on %d\n" % (iso(now()), PORT))
    srv.serve_forever()


if __name__ == "__main__":
    main()
