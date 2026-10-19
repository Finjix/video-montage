import sys, json, glob, os
D = "c:/Bytedance/JianyingPro/JianyingPro Drafts"
for f in sorted(glob.glob(D + "/*/draft_content.json") + glob.glob(D + "/*/Timelines/*/draft_content.json")):
    b = open(f, "rb").read()
    try:
        j = json.loads(b); kind = "plain JSON, top keys: " + ",".join(list(j)[:8])
    except Exception:
        kind = "NOT JSON, first bytes: %r" % b[:12]
    print(os.path.relpath(f, D), len(b), kind)
