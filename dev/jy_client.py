"""Minimal client for JianYing's local agent server. Tokens stay in memory and are never printed.

Usage: python -I dev/jy_client.py GET /api/processors
       python -I dev/jy_client.py POST /api/xxx '{"json": "body"}'
"""
import json, os, sys, urllib.request, urllib.error

SENSITIVE = ("token", "credential", "cookie", "auth", "secret", "password")


def redact(o):
    if isinstance(o, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in SENSITIVE) else redact(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(x) for x in o]
    return o


def _http(method, url, headers, body=None, timeout=15):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={**headers, **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def connect():
    d = json.load(open(os.path.join(os.environ["TEMP"], "infinite-canvas-native-bridge.json"), encoding="utf-8"))
    st, body = _http("GET", f"http://127.0.0.1:{d['port']}/.infinite-canvas-native-bridge/health", {"x-infinite-canvas-native-token": d["token"]})
    srv = json.loads(body)["local_agent_server"]
    if not srv.get("running"):
        raise RuntimeError("local agent server is not running")
    return f"http://127.0.0.1:{srv['port']}", {"x-custom-agent-token": srv["localServerToken"]}


def call(method, path, body=None):
    base, h = connect()
    st, text = _http(method, base + path, h, body)
    try:
        return st, redact(json.loads(text))
    except ValueError:
        return st, "<non-json body, %d bytes>" % len(text)


if __name__ == "__main__":
    method, path = sys.argv[1], sys.argv[2]
    body = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    st, out = call(method, path, body)
    print(st)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])
