python - <<'PY'
import json, sys
from pathlib import Path
p=Path("data/collected_pieces.json")
if not p.exists():
    print("ERROR: data file not found:", p)
    sys.exit(1)
d=json.load(open(p,'r',encoding='utf-8'))
puz="full_puzzle1"
pieces=d.get("pieces",{}).get(puz,{})
print("pieces keys:", list(pieces.keys()))
for k,v in pieces.items():
    q=Path(v)
    if not q.is_absolute():
        q=Path.cwd().joinpath(v)
    print(k, "->", v, "exists:", q.exists(), "resolved:", q.resolve() if q.exists() else "N/A")
PY