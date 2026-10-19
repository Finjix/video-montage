"""Dump a decoded window of a JianYing runtime .mjs around an anchor string. usage: jy_dump.py FILE ANCHOR [before] [after]"""
import re, sys
sys.stdout.reconfigure(encoding="utf-8")
f, anchor = sys.argv[1], sys.argv[2]
b = int(sys.argv[3]) if len(sys.argv) > 3 else 200
a = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
s = open(f, encoding="utf-8").read()
i = s.find(anchor)
if i < 0:
    sys.exit("anchor not found")
seg = s[max(0, i - b): i + a]
print(re.sub(r"[\\]u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), seg))
