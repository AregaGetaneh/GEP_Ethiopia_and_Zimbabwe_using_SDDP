"""
Generate the appendix LaTeX tables from results/<CC>/*.json.

Companion to make_tables.py. Writes one .tex fragment per table to the tables/
directory. All numbers are read directly from the result JSON files.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
TAB = HERE / "tables"; TAB.mkdir(exist_ok=True)


def load(cc, s):
    return json.loads((RES / cc / f"{s}.json").read_text())


def f(x, d=2):
    return f"{x:.{d}f}"


def cap2050(r, e):
    return r["capacity_MW"][e][-1] / 1000.0


# Ethiopia demand-anchor and high-demand de-confounding tables
def eth_deconf():
    rows = [("baseline", "Baseline (observed anchor)"),
            ("accelerated_access", "Accelerated access (40 TWh anchor)"),
            ("high_demand_expanded", "High demand, expanded ceilings"),
            ("high_demand_fixedceil", "High demand, baseline ceilings")]
    ts = load("ETH", "baseline")["tech"]
    lines = [r"\begin{tabular}{lrrrrrrrr}", r"\toprule",
             r"Scenario & Cost & Hydro & Wind & Solar & Geo. & Bio. & Avg.\ em. & EUE\\",
             r" & (\$B) & \multicolumn{5}{c}{2050 capacity (GW)} & (Mt) & (TWh)\\", r"\midrule"]
    for scn, lab in rows:
        r = load("ETH", scn)
        caps = " & ".join(f(cap2050(r, e), 1) for e in range(len(ts)))
        lines.append(f"{lab} & {f(r['resource_cost_BUSD'])} & {caps} & "
                     f"{f(r['avg_emissions_Mt'])} & {f(r['eue_TWh'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# Zimbabwe coal-exit decomposition table
def zwe_coalexit():
    rows = [("baseline", "Baseline (retirement to floor)"),
            ("coal_retire_fixed", "Schedule only (fixed floor)"),
            ("coal_retire_delayed", "Delayed retirement floor"),
            ("coal_retire_refurb", "Floor + refurbishment cost")]
    lines = [r"\begin{tabular}{lrrrrr}", r"\toprule",
             r"Scenario & Cost & Avg.\ em. & 2050 em. & Coal 2050 & Gas 2050\\",
             r" & (\$B) & (Mt) & (Mt) & (GW) & (GW)\\", r"\midrule"]
    for scn, lab in rows:
        r = load("ZWE", scn)
        lines.append(f"{lab} & {f(r['resource_cost_BUSD'])} & {f(r['avg_emissions_Mt'])} & "
                     f"{f(r['y2050_emissions_Mt'])} & {f(cap2050(r,1),1)} & {f(cap2050(r,3),1)}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# Full scenario tables (all scenarios, 2050 capacity by technology plus metrics)
ETH_ALL = [("baseline", "Baseline"), ("re_inv_minus30", "RE invest $-$30\\%"),
           ("re_inv_minus50", "RE invest $-$50\\%"), ("learning_curves", "Learning curves"),
           ("solar_only_50", "Solar-only $-$50\\%"), ("constrained_hydro", "Constrained hydro"),
           ("high_demand_expanded", "High demand $+$50\\%"), ("high_demand_fixedceil", "High demand, fixed ceil."),
           ("drought_stress", "Persistent drought"), ("accelerated_access", "Accelerated access")]

ZWE_ALL = [("baseline", "Baseline (5 Mt cap)"), ("carbon_tax_30", "Carbon tax \\$30/t"),
           ("carbon_tax_50", "Carbon tax \\$50/t"), ("cap_matched_30", "Cap matched \\$30"),
           ("cap_matched_50", "Cap matched \\$50"), ("budget_matched_50", "Budget matched \\$50"),
           ("emission_cap_glide", "Glide cap 5$\\to$2 Mt"), ("combined_tax50_solar", "Tax \\$50 + Solar $-$30\\%"),
           ("re_inv_m50", "RE invest $-$50\\%"), ("high_demand", "High demand $+$50\\%"),
           ("coal_retire_fixed", "Coal exit: fixed"), ("coal_retire_delayed", "Coal exit: delayed"),
           ("coal_retire_refurb", "Coal exit: refurb"), ("drought_stress", "Persistent drought")]


def full(cc, rows):
    ts = load(cc, "baseline")["tech"]
    head = "l" + "r" * (2 + len(ts) + 3)
    lines = [r"\begin{tabular}{" + head + "}", r"\toprule",
             r"Scenario & Cost & Cash & " + " & ".join(ts) + r" & Avg.\ em. & 2050 em. & EUE\\",
             r" & (\$B) & (\$B) & \multicolumn{" + str(len(ts)) + r"}{c}{2050 capacity (GW)} & (Mt) & (Mt) & (TWh)\\",
             r"\midrule"]
    for scn, lab in rows:
        r = load(cc, scn)
        caps = " & ".join(f(cap2050(r, e), 1) for e in range(len(ts)))
        lines.append(f"{lab} & {f(r['resource_cost_BUSD'])} & {f(r['cash_cost_BUSD'])} & {caps} & "
                     f"{f(r['avg_emissions_Mt'])} & {f(r['y2050_emissions_Mt'])} & {f(r['eue_TWh'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


if __name__ == "__main__":
    tables = {"A1_eth_deconf": eth_deconf(), "A2_zwe_coalexit": zwe_coalexit(),
              "A3_eth_full": full("ETH", ETH_ALL), "A4_zwe_full": full("ZWE", ZWE_ALL)}
    for name, tex in tables.items():
        (TAB / f"{name}.tex").write_text(tex, encoding="utf-8")
        print("=" * 30, name)
        print(tex)
        print()
