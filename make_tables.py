"""
Generate the main LaTeX result tables from results/<CC>/*.json.

Writes one .tex fragment per table to the tables/ directory and prints each
fragment to standard output.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
TAB = HERE / "tables"; TAB.mkdir(exist_ok=True)


def load(cc, s):
    return json.loads((RES / cc / f"{s}.json").read_text())


def bench(cc):
    return json.loads((RES / cc / "benchmarks.json").read_text())


def fnum(x, d=2):
    return f"{x:.{d}f}"


# ---- T1: Ethiopia scenario summary ----
ETH = [("baseline", "Baseline"), ("re_inv_minus30", "RE invest $-$30\\%"),
       ("re_inv_minus50", "RE invest $-$50\\%"), ("learning_curves", "Learning curves"),
       ("solar_only_50", "Solar-only $-$50\\%"), ("constrained_hydro", "Constrained hydro"),
       ("high_demand_expanded", "High demand $+$50\\%"), ("drought_stress", "Persistent drought"),
       ("accelerated_access", "Accelerated access")]


def t1():
    ts = load("ETH", "baseline")["tech"]
    lines = [r"\begin{tabular}{lrrrrrrrr}", r"\toprule",
             r"Scenario & Cost & Hydro & Wind & Solar & Geo. & Bio. & Avg.\ em. & EUE\\",
             r" & (\$B) & \multicolumn{5}{c}{2050 capacity (GW)} & (Mt) & (TWh)\\",
             r"\midrule"]
    for scn, lab in ETH:
        r = load("ETH", scn); cap = [r["capacity_MW"][e][-1] / 1000 for e in range(len(ts))]
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & " +
                     " & ".join(fnum(c, 1) for c in cap) +
                     f" & {fnum(r['avg_emissions_Mt'])} & {fnum(r['eue_TWh'], 2)}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- T2: Zimbabwe policy summary ----
ZWE = [("baseline", "Baseline (5 Mt cap, no tax)"), ("carbon_tax_30", "Carbon tax \\$30/t"),
       ("carbon_tax_50", "Carbon tax \\$50/t"), ("emission_cap_glide", "Glide cap 5$\\to$2 Mt"),
       ("combined_tax50_solar", "Tax \\$50 + concessional solar"), ("re_inv_m50", "RE invest $-$50\\%"),
       ("high_demand", "High demand $+$33\\%"), ("drought_stress", "Persistent drought"),
       ("cap_matched_50", "Matched annual cap"), ("budget_matched_50", "Matched cumulative budget")]


def t2():
    base = load("ZWE", "baseline")["resource_cost_BUSD"]
    lines = [r"\begin{tabular}{lrrrrrr}", r"\toprule",
             r"Scenario & Resource & Cash & Avg.\ em. & 2050 em. & EUE & Premium\\",
             r" & cost (\$B) & (\$B) & (Mt) & (Mt) & (TWh) & (\%)\\", r"\midrule"]
    for scn, lab in ZWE:
        r = load("ZWE", scn); prem = 100 * (r["resource_cost_BUSD"] - base) / base
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & {fnum(r['cash_cost_BUSD'])} & "
                     f"{fnum(r['avg_emissions_Mt'])} & {fnum(r['y2050_emissions_Mt'])} & "
                     f"{fnum(r['eue_TWh'], 2)} & {prem:+.1f}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- T3: cross-country baseline ----
def t3():
    e = load("ETH", "baseline"); z = load("ZWE", "baseline")
    def cap(r): return sum(r["capacity_MW"][i][-1] for i in range(len(r["tech"]))) / 1000
    def gen(r): return sum(r["gen_TWh"][i][-1] for i in range(len(r["tech"])))
    rows = [("Planning horizon", "2025--2050", "2025--2050"),
            ("Technologies", "5", "5"),
            ("2050 capacity (GW)", fnum(cap(e), 1), fnum(cap(z), 1)),
            ("2050 generation (TWh)", fnum(gen(e), 1), fnum(gen(z), 1)),
            ("Resource cost (\\$B)", fnum(e["resource_cost_BUSD"]), fnum(z["resource_cost_BUSD"])),
            ("Avg.\\ annual emissions (Mt)", fnum(e["avg_emissions_Mt"]), fnum(z["avg_emissions_Mt"])),
            ("Expected unserved energy (TWh)", fnum(e["eue_TWh"], 2), fnum(z["eue_TWh"], 2))]
    lines = [r"\begin{tabular}{lrr}", r"\toprule", r"Metric & Ethiopia & Zimbabwe\\", r"\midrule"]
    for m, a, b in rows:
        lines.append(f"{m} & {a} & {b}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- T4: value of adaptivity (econ cost + EUE decomposition + full welfare objective) ----
def t4():
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Plan & Resource cost & Expected unserved & Salvage & Total expected\\",
             r" & (\$B, NPV) & energy (TWh) & credit (\$B) & cost (\$B)\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        b = bench(cc)
        lines.append(r"\multicolumn{5}{@{}l}{\textit{" + {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc] + r"}}\\")
        for key, lab in [("pi", "Perfect information"), ("sddp", "Stochastic (SDDP)"),
                         ("ev", "Deterministic (EV)")]:
            lines.append(f"\\quad {lab} & {fnum(b[f'{key}_econ_BUSD'])} & "
                         f"{fnum(b[f'{key}_eue_TWh'], 2)} & {fnum(b.get(f'{key}_salvage_BUSD', 0.0))} & "
                         f"{fnum(b[f'{key}_total_BUSD'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


if __name__ == "__main__":
    for name, fn in [("T1_eth", t1), ("T2_zwe", t2), ("T3_cross", t3), ("T4_vss", t4)]:
        tex = fn()
        (TAB / f"{name}.tex").write_text(tex)
        print("=" * 30, name); print(tex); print()
