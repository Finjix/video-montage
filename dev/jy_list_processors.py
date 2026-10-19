import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jy_client import call
st, o = call("GET", "/api/processors")
print(st, len(o["processors"]), "processors")
for p in o["processors"]:
    print(f'{p["processorId"]:55} {p["kind"]:8} {p["implementation"]["mode"]:8} {p["name"]} | {p["description"][:60]}')
