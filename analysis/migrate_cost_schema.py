"""Bring consolidated result files onto the current cost-field schema.

Background
----------
Earlier production runs emitted a single field, ``net_resource_cost_BUSD``,
holding ``gross - salvage + VoLL + refurb``. That quantity is the manuscript's
*total expected cost*, not its *net resource cost*, which is ``gross - salvage``
and is the quantity printed in the net-resource-cost column of the Zimbabwe
policy table. ``model/gep_sddp.jl`` now writes both fields under the names the
manuscript uses. This script applies the same two definitions to consolidated
result files produced before that change, so ``results/<CC>.json`` matches what
the current solver would write without re-solving every scenario.

Definitions, identical to ``model/gep_sddp.jl``::

    net_resource_cost_BUSD   = gross_resource_BUSD - salvage_BUSD
    total_expected_cost_BUSD = gross_resource_BUSD + voll_cost_BUSD
                               - salvage_BUSD + refurb_cost_BUSD

The migration is idempotent and verifies, for every scenario, that any
pre-existing ``net_resource_cost_BUSD`` equals the recomputed
``total_expected_cost_BUSD`` before overwriting it. A scenario that fails that
check is left untouched and reported, because it would mean the stored value
came from some third definition.

Usage::

    python analysis/migrate_cost_schema.py            # rewrite results/{ETH,ZWE}.json
    python analysis/migrate_cost_schema.py --check    # report only, write nothing
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RES = ROOT / "results"
TOL = 5e-9
COUNTRIES = ("ETH", "ZWE")


def net_resource_cost(s: dict) -> float:
    return s["gross_resource_BUSD"] - s["salvage_BUSD"]


def total_expected_cost(s: dict) -> float:
    return (s["gross_resource_BUSD"] + s["voll_cost_BUSD"]
            - s["salvage_BUSD"] + s["refurb_cost_BUSD"])


def migrate(cc: str, write: bool) -> int:
    path = RES / f"{cc}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    scenarios = doc["scenarios"]
    changed = already = refused = 0

    for name, s in scenarios.items():
        need = ("gross_resource_BUSD", "salvage_BUSD", "voll_cost_BUSD", "refurb_cost_BUSD")
        if any(k not in s for k in need):
            print(f"  {cc}/{name}: missing cost components, skipped")
            refused += 1
            continue

        new_net = net_resource_cost(s)
        new_tot = total_expected_cost(s)
        old_net = s.get("net_resource_cost_BUSD")

        if old_net is not None and abs(old_net - new_net) <= TOL:
            already += 1                       # already migrated
        elif old_net is not None and abs(old_net - new_tot) > TOL:
            print(f"  {cc}/{name}: stored net={old_net!r} matches neither definition "
                  f"(net={new_net!r}, total={new_tot!r}); left untouched")
            refused += 1
            continue
        else:
            changed += 1

        s["net_resource_cost_BUSD"] = new_net
        s["total_expected_cost_BUSD"] = new_tot

    print(f"  {cc}: {len(scenarios)} scenarios | rewritten {changed} | "
          f"already current {already} | refused {refused}")

    if write and not refused:
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print(f"  {cc}: wrote {path.relative_to(ROOT)}")
    return refused


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    args = ap.parse_args()
    print("cost-schema migration" + (" (check only)" if args.check else ""))
    refused = sum(migrate(cc, write=not args.check) for cc in COUNTRIES)
    if refused:
        print(f"FAILED: {refused} scenario(s) refused")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
