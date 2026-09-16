"""
tables.py -- all result tables (LaTeX) from results/<CC>.json. Run: python tables.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent                 # analysis/
RES = HERE.parent / "results"                          # consolidated results
TAB = HERE.parent / "tables" / "tex"; TAB.mkdir(parents=True, exist_ok=True)
NM = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}

_RES = {}


def _country(cc):
    if cc not in _RES:
        _RES[cc] = json.loads((RES / f"{cc}.json").read_text())
    return _RES[cc]


def load(cc, s):
    return _country(cc)["scenarios"][s]


def bench(cc):
    return _country(cc)["benchmarks"]


def fnum(x, d=2):
    return f"{x:.{d}f}"


def cap2050(r, e):
    return r["capacity_MW"][e][-1] / 1000.0


def cap_of(r, name):
    return cap2050(r, r["tech"].index(name))


# =====================================================================  main tables

# ---- T1: Ethiopia scenario summary ----
ETH = [("baseline", "Baseline"), ("re_inv_minus30", "RE invest $-$30\\%"),
       ("re_inv_minus50", "RE invest $-$50\\%"), ("learning_curves", "Learning curves"),
       ("solar_only_50", "Solar-only $-$50\\%"), ("constrained_hydro", "Constrained hydro"),
       ("high_demand_expanded", "High demand $+$50\\%"), ("drought_stress", "Low-inflow stress"),
       ("accelerated_access", "Accelerated access")]


def t1():
    ts = load("ETH", "baseline")["tech"]
    lines = [r"\begin{tabular}{lrrrrrrrr}", r"\toprule",
             r"Scenario & Cost & Hydro & Wind & Solar & Geo. & Bio. & Avg.\ em. & EUE\\",
             r" & (\$B) & \multicolumn{5}{c}{2050 capacity (GW)} & (Mt) & (TWh)\\",
             r"\midrule"]
    for scn, lab in ETH:
        r = load("ETH", scn); cap = [cap2050(r, e) for e in range(len(ts))]
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & " +
                     " & ".join(fnum(c, 1) for c in cap) +
                     f" & {fnum(r['avg_emissions_Mt'])} & {fnum(r['eue_TWh'], 2)}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- T2: Zimbabwe policy summary ----
ZWE = [("baseline", "Baseline (no policy)"), ("carbon_tax_30", "Carbon tax \\$30/t"),
       ("carbon_tax_50", "Carbon tax \\$50/t"), ("emission_cap_glide", "Glide cap 8$\\to$3 Mt"),
       ("combined_tax50_solar", "Tax \\$50 + Solar fin."), ("re_inv_m50", "RE invest $-$50\\%"),
       ("high_demand", "High demand"), ("drought_stress", "Low-inflow stress"),
       ("cap_matched_30", "Matched cap (\\$30)"), ("cap_matched_50", "Matched cap (\\$50)")]


def t2():
    """Zimbabwe policy outcomes. Net resource cost is gross resource cost less
    the terminal salvage credit."""
    base = load("ZWE", "baseline")["resource_cost_BUSD"]
    lines = [r"\begin{tabular}{lrrrrrrr}", r"\toprule",
             r"Scenario & Gross resource & Net resource & Cash & Average emission "
             r"& 2050 emission & EUE & Premium\\",
             r" & cost (\$B) & cost (\$B) & (\$B) & (Mt) & (Mt) & (TWh) & (\%)\\", r"\midrule"]
    for scn, lab in ZWE:
        r = load("ZWE", scn); prem = 100 * (r["resource_cost_BUSD"] - base) / base
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & "
                     f"{fnum(r['net_resource_cost_BUSD'])} & {fnum(r['cash_cost_BUSD'])} & "
                     f"{fnum(r['avg_emissions_Mt'])} & {fnum(r['y2050_emissions_Mt'])} & "
                     f"{fnum(r['eue_TWh'], 2)} & {prem:+.1f}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- T3: cross-country baseline ----
def t3():
    e = load("ETH", "baseline"); z = load("ZWE", "baseline")
    def totcap(r): return sum(r["capacity_MW"][i][-1] for i in range(len(r["tech"]))) / 1000
    def totgen(r): return sum(r["gen_TWh"][i][-1] for i in range(len(r["tech"])))
    rows = [("Planning horizon", "2025--2050", "2025--2050"),
            ("Technologies", "5", "5"),
            ("2050 capacity (GW)", fnum(totcap(e), 1), fnum(totcap(z), 1)),
            ("2050 generation (TWh)", fnum(totgen(e), 1), fnum(totgen(z), 1)),
            ("Resource cost (\\$B)", fnum(e["resource_cost_BUSD"]), fnum(z["resource_cost_BUSD"])),
            ("Avg.\\ annual emissions (Mt)", fnum(e["avg_emissions_Mt"]), fnum(z["avg_emissions_Mt"])),
            ("Expected unserved energy (TWh)", fnum(e["eue_TWh"], 2), fnum(z["eue_TWh"], 2))]
    lines = [r"\begin{tabular}{lrr}", r"\toprule", r"Metric & Ethiopia & Zimbabwe\\", r"\midrule"]
    for m, a, b in rows:
        lines.append(f"{m} & {a} & {b}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- T4: value of adaptive planning benchmarks ----
def t4():
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Plan & Gross resource cost & Expected unserved & Salvage & Total expected\\",
             r" & (\$B, NPV) & energy (TWh) & credit (\$B) & cost (\$B)\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        b = bench(cc)
        lines.append(r"\multicolumn{5}{@{}l}{\textit{" + NM[cc] + r"}}\\")
        for key, lab in [("pi", "Perfect information"), ("sddp", "Stochastic (SDDP)"),
                         ("ev", "Deterministic (EV)")]:
            lines.append(f"\\quad {lab} & {fnum(b[f'{key}_econ_BUSD'])} & "
                         f"{fnum(b[f'{key}_eue_TWh'], 2)} & {fnum(b.get(f'{key}_salvage_BUSD', 0.0))} & "
                         f"{fnum(b[f'{key}_total_BUSD'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# =====================================================================  appendix tables

# ---- A1: Ethiopia demand-anchor and high-demand de-confounding ----
def eth_deconf():
    rows = [("baseline", "Baseline (observed anchor)"),
            ("accelerated_access", "Accelerated access (50 TWh by 2030)"),
            ("high_demand_expanded", "High demand, expanded ceilings"),
            ("high_demand_fixedceil", "High demand, baseline ceilings")]
    ts = load("ETH", "baseline")["tech"]
    lines = [r"\begin{tabular}{lrrrrrrrr}", r"\toprule",
             r"Scenario & Cost & Hydro & Wind & Solar & Geo. & Bio. & Avg.\ em. & EUE\\",
             r" & (\$B) & \multicolumn{5}{c}{2050 capacity (GW)} & (Mt) & (TWh)\\", r"\midrule"]
    for scn, lab in rows:
        r = load("ETH", scn)
        caps = " & ".join(fnum(cap2050(r, e), 1) for e in range(len(ts)))
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & {caps} & "
                     f"{fnum(r['avg_emissions_Mt'])} & {fnum(r['eue_TWh'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- A2: Zimbabwe coal-exit decomposition ----
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
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & {fnum(r['avg_emissions_Mt'])} & "
                     f"{fnum(r['y2050_emissions_Mt'])} & {fnum(cap2050(r, 1), 1)} & {fnum(cap2050(r, 3), 1)}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- A3/A4: full scenario tables (all scenarios, 2050 capacity by tech + metrics) ----
ETH_ALL = [("baseline", "Baseline"), ("re_inv_minus30", "RE invest $-$30\\%"),
           ("re_inv_minus50", "RE invest $-$50\\%"), ("learning_curves", "Learning curves"),
           ("solar_only_50", "Solar-only $-$50\\%"), ("constrained_hydro", "Constrained hydro"),
           ("high_demand_expanded", "High demand $+$50\\%"), ("high_demand_fixedceil", "High demand, fixed ceil."),
           ("drought_stress", "Low-inflow stress"), ("accelerated_access", "Accelerated access")]

ZWE_ALL = [("baseline", "Baseline (no policy)"), ("carbon_tax_30", "Carbon tax \\$30/t"),
           ("carbon_tax_50", "Carbon tax \\$50/t"), ("cap_matched_30", "Cap matched \\$30"),
           ("cap_matched_50", "Cap matched \\$50"),
           ("emission_cap_glide", "Glide cap 8$\\to$3 Mt"), ("combined_tax50_solar", "Tax \\$50 + Solar fin."),
           ("re_inv_m50", "RE invest $-$50\\%"), ("high_demand", "High demand"),
           ("coal_retire_fixed", "Coal exit: fixed"), ("coal_retire_delayed", "Coal exit: delayed"),
           ("coal_retire_refurb", "Coal exit: refurb"), ("drought_stress", "Low-inflow stress")]


def full(cc, rows):
    ts = load(cc, "baseline")["tech"]
    head = "l" + "r" * (2 + len(ts) + 3)
    lines = [r"\begin{tabular}{" + head + "}", r"\toprule",
             r"Scenario & Cost & Cash & " + " & ".join(ts) + r" & Avg.\ em. & 2050 em. & EUE\\",
             r" & (\$B) & (\$B) & \multicolumn{" + str(len(ts)) + r"}{c}{2050 capacity (GW)} & (Mt) & (Mt) & (TWh)\\",
             r"\midrule"]
    for scn, lab in rows:
        r = load(cc, scn)
        caps = " & ".join(fnum(cap2050(r, e), 1) for e in range(len(ts)))
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & {fnum(r['cash_cost_BUSD'])} & {caps} & "
                     f"{fnum(r['avg_emissions_Mt'])} & {fnum(r['y2050_emissions_Mt'])} & {fnum(r['eue_TWh'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# =====================================================================  diagnostic tables

# ---- One-at-a-time calibration sensitivities ----
def sensitivity():
    rows = [("baseline", "Baseline (VoLL \\$10,000/MWh)"), ("sens_disc3", "Discount rate 3\\%"),
            ("sens_disc8", "Discount rate 8\\%"), ("sens_voll5k", "VoLL \\$5,000/MWh"),
            ("sens_voll20k", "VoLL \\$20,000/MWh"), ("sens_mudry_lo", "Low-inflow multiplier $-$15\\%"),
            ("sens_mudry_hi", "Low-inflow multiplier $+$15\\%")]
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Sensitivity & \multicolumn{2}{c}{Ethiopia} & \multicolumn{2}{c}{Zimbabwe}\\",
             r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}",
             r" & Cost (\$B) & EUE (TWh) & Cost (\$B) & EUE (TWh)\\", r"\midrule"]
    for scn, lab in rows:
        e, z = load("ETH", scn), load("ZWE", scn)
        lines.append(f"{lab} & {fnum(e['resource_cost_BUSD'])} & {fnum(e['eue_TWh'])} & "
                     f"{fnum(z['resource_cost_BUSD'])} & {fnum(z['eue_TWh'])}\\\\")
        if scn == "baseline":
            lines.append(r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- SDDP training and simulation diagnostics ----
def convergence():
    """Baseline convergence diagnostics. The final column is the relative
    difference between the out-of-sample estimate and the training lower bound."""
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Country & Lower bound (\$B) & OOS cost (\$B) & 95\% CI half-width (\$B) "
             r"& Difference (\%)\\",
             r"\midrule"]
    for cc in ("ETH", "ZWE"):
        b = load(cc, "baseline")
        lb = b["meta"]["lower_bound_BUSD"]
        ub = b["oos"]["oos_cost_BUSD_mean"]
        ci = b["oos"]["oos_cost_BUSD_ci"]
        diff = 100 * (ub - lb) / lb
        lines.append(f"{NM[cc]} & {fnum(lb)} & {fnum(ub)} & {fnum(ci)} & {diff:.1f}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- Demand-versus-hydrology uncertainty decomposition ----
def uncdecomp():
    rows = [("unc_deterministic", "Deterministic"), ("unc_demand_only", "Demand only"),
            ("unc_hydro_only", "Hydrology only"), ("baseline", "Joint")]
    lines = [r"\begin{tabular}{llrrrr}", r"\toprule",
             r"Country & Uncertainty represented & Gross resource cost (\$B) & Shortage cost (\$B) "
             r"& Salvage credit (\$B) & Total expected cost (\$B)\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        for i, (scn, lab) in enumerate(rows):
            r = load(cc, scn)
            res = r["gross_resource_BUSD"]; sh = r["voll_cost_BUSD"]
            salv = r["salvage_BUSD"]; tot = r["planner_objective_BUSD"]   # res + sh - salv (no tax here)
            lines.append(f"{NM[cc] if i == 0 else ''} & {lab} & {fnum(res)} & {fnum(sh)} & {fnum(salv)} & {fnum(tot)}\\\\")
        if cc == "ETH":
            lines.append(r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- Numerical and stochastic robustness of the baseline ----
def robustness():
    rows = [("baseline", "Baseline (5 GH nodes)"), ("gh9", "9 Gauss--Hermite nodes"),
            ("hydro_persist_lo", "Lower hydrology persistence"),
            ("hydro_persist_hi", "Higher hydrology persistence")]
    lines = [r"\begin{tabular}{lrrrrr}", r"\toprule",
             r"Case & Gross resource cost (\$B) & Hydro (GW) & Solar (GW) & Avg.\ emissions (Mt) & EUE (TWh)\\",
             r"\midrule"]
    for cc in ("ETH", "ZWE"):
        lines.append(r"\multicolumn{6}{@{}l}{\textit{%s}}\\" % NM[cc])
        for scn, lab in rows:
            r = load(cc, scn)
            lines.append(f"\\quad {lab} & {fnum(r['resource_cost_BUSD'])} & {fnum(cap_of(r, 'Hydro'), 1)} & "
                         f"{fnum(cap_of(r, 'Solar'), 1)} & {fnum(r['avg_emissions_Mt'])} & {fnum(r['eue_TWh'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# ---- Value of adaptive planning under alternative values of lost load ----
def voll_vss():
    """VSS is affine in the value of lost load; the $5,000 column is obtained by
    revaluing the stored $10,000 and $20,000 differences (no re-optimization)."""
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Country & Avoided EUE (TWh) & \multicolumn{3}{c}{Value of adaptive planning (\$B)}\\",
             r"\cmidrule(lr){3-5}",
             r" & & \$5,000/MWh & \$10,000/MWh & \$20,000/MWh\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        b = bench(cc)
        v10 = b["VSS_total_at_10k"]; v20 = b["VSS_total_at_20k"]
        v5 = v10 - (v20 - v10) / 2.0
        aeue = b.get("avoided_eue_TWh", b["ev_eue_TWh"] - b["sddp_eue_TWh"])
        lines.append(f"{NM[cc]} & {fnum(aeue)} & {fnum(v5)} & {fnum(v10)} & {fnum(v20)}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


TABLES = {
    "T1_eth": t1, "T2_zwe": t2, "T3_cross": t3, "T4_vss": t4,
    "A1_eth_deconf": eth_deconf, "A2_zwe_coalexit": zwe_coalexit,
    "A3_eth_full": lambda: full("ETH", ETH_ALL), "A4_zwe_full": lambda: full("ZWE", ZWE_ALL),
    "sensitivity": sensitivity, "convergence": convergence,
    "uncertainty_decomposition": uncdecomp, "robustness": robustness,
    "voll_value_of_adaptive_planning": voll_vss,
}


if __name__ == "__main__":
    for name, fn in TABLES.items():
        tex = fn()
        (TAB / f"{name}.tex").write_text(tex, encoding="utf-8")
        print("=" * 30, name)
        print(tex)
        print()
