"""Read-only probe of the JianYing native bridge. Redacts every credential-looking field.

The bridge's /custom-agent/context returns the logged-in account cookies, so only
health is queried by default and any key containing token/credential/cookie/auth is masked.
"""
import json, os, sys, urllib.request, urllib.error

SENSITIVE = ("token", "credential", "cookie", "auth", "secret", "value")


def redact(o):
    if isinstance(o, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in SENSITIVE) else redact(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(x) for x in o]
    return o


disc = os.path.join(os.environ["TEMP"], "infinite-canvas-native-bridge.json")
d = json.load(open(disc, encoding="utf-8"))
base = f"http://127.0.0.1:{d['port']}"
H = {"x-infinite-canvas-native-token": d["token"]}


def get(path, limit=3000):
    req = urllib.request.Request(base + path, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                body = json.dumps(redact(json.loads(body)), ensure_ascii=False)
            except ValueError:
                body = "<non-json body withheld>"
            print(f"GET {path} -> {r.status}\n{body[:limit]}\n")
    except urllib.error.HTTPError as e:
        print(f"GET {path} -> {e.code}")


for p in sys.argv[1:] or ["/.infinite-canvas-native-bridge/health"]:
    get(p)
