"""
consolidate.py -- bundle per-scenario solver output into results/<CC>.json.
Run after a re-solve: python consolidate.py
"""
import json
import glob
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RES = REPO / "results"


def consolidate(cc):
    scenarios = {}
    for path in sorted(glob.glob(str(RES / cc / "*" / "*.json"))):
        p = Path(path)
        if p.name.endswith(".meta.json") or p.parent.name == "benchmarks":
            continue
        record = json.loads(p.read_text())
        sidecar = p.parent / (p.stem + ".meta.json")
        if sidecar.exists():
            prov = json.loads(sidecar.read_text())
            prov.pop("git_commit", None)   # not meaningful for an archived release
            record["provenance"] = prov
        scenarios[p.stem] = record
    benchmarks = {}
    bpath = RES / cc / "benchmarks" / "benchmarks.json"
    if bpath.exists():
        benchmarks = json.loads(bpath.read_text())
    out = {"country": cc, "scenarios": scenarios, "benchmarks": benchmarks}
    (RES / f"{cc}.json").write_text(json.dumps(out, indent=2))
    print(f"{cc}: {len(scenarios)} scenarios + benchmarks -> results/{cc}.json")


if __name__ == "__main__":
    for cc in ("ETH", "ZWE"):
        consolidate(cc)
