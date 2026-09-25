#!/usr/bin/env python3
"""C-17 port check from the host (stdlib): the environment's published ports bind to loopback only, no port of the
environment answers on any non-loopback host address, and the DevTools port does not answer from the host."""
import json, os, socket, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
env = {}
for line in open(os.environ.get("VC_SETTINGS", os.path.join(here, "..", "..", "settings.env"))):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k] = v
name = env["VC_CONTAINER_NAME"]
ports = json.loads(subprocess.run(["docker", "inspect", "-f", "{{json .NetworkSettings.Ports}}", name],
                                  capture_output=True, text=True, check=True).stdout)
published = [(k, b["HostIp"], int(b["HostPort"])) for k, bl in ports.items() if bl for b in bl]
ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
def open_(ip, port):
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    s = socket.socket(fam); s.settimeout(0.5)
    try:
        return s.connect_ex((ip, port)) == 0
    finally:
        s.close()
host_ports = sorted({p for _, _, p in published} | {9222})
scan = {ip: [p for p in host_ports if open_(ip, p)] for ip in ips if not ip.startswith("127.")}
cdp_from_host = any(open_(ip, 9222) for ip in ["127.0.0.1"] + ips)
res = {"published": published, "non_loopback_open": scan, "devtools_answers_from_host": cdp_from_host}
ok = all(h in ("127.0.0.1", "::1") for _, h, _ in published) and not any(scan.values()) and not cdp_from_host
res["verdict"] = "PASS" if ok else "FAIL"
print(json.dumps(res, indent=1)); sys.exit(0 if ok else 1)
