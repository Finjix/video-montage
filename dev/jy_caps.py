import sys, os, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jy_client as c
base, h = c.connect()
h = {**h, "x-agent-runtime-instance-id": "bridge-probe-instance", "x-agent-runtime-session-id": "bridge-probe-session"}
st, text = c._http("GET", base + "/api/custom-agent/host-capabilities/manifest", h)
print(st)
try: print(json.dumps(c.redact(json.loads(text)), ensure_ascii=False, indent=1)[:7000])
except ValueError: print(text[:500])
