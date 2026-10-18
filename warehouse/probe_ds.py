import sys
sys.path.insert(0, r"D:\Projects\DWM-Project")
from fastapi.testclient import TestClient
from dwm.api.app import app
import json
c = TestClient(app)
for p in ("/ui/schema", "/ui/etl"):
    r = c.get(p)
    print(f"=== {p} -> {r.status_code}")
    d = r.json()["data"]
    if isinstance(d, dict):
        for k, v in d.items():
            n = f"[{len(v)}]" if isinstance(v, (list, dict)) else ""
            s = json.dumps(v[0])[:180] if isinstance(v, list) and v else json.dumps(v)[:180]
            print(f"  {k:22} {n:6} {s}")
    else:
        print("  list:", json.dumps(d)[:300])
    print("  note:", r.json()["meta"].get("note", "")[:200])
