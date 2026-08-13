"""
make_tables.py -- all LaTeX result tables from results/<CC>/*.json.

Consolidates the main-text, appendix, and revision table generators into one
script. Numbers come straight from the result JSONs, so re-running after a new
model run regenerates every table. Writes .tex fragments to ./tables/ and prints
them. Run:  python make_tables.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
TAB = HERE / "tables"; TAB.mkdir(exist_ok=True)


# ---------------------------------------------------------------- shared helpers
def load(cc, s):
    return json.loads((RES / cc / f"{s}.json").read_text())


L = load                       # alias used by the revision tables


def fnum(x, d=2):
    return f"{x:.{d}f}"


f = fnum                       # alias used by the appendix / revision tables


def bench(cc):
    return json.loads((RES / cc / "benchmarks.json").read_text())


def cap2050(r, e):             # capacity by technology INDEX
    return r["capacity_MW"][e][-1] / 1000.0


def cap(r, name):              # capacity by technology NAME
    return r["capacity_MW"][r["tech"].index(name)][-1] / 1000.0


# ============================================================ main-text tables
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
        r = load("ETH", scn); cap_ = [r["capacity_MW"][e][-1] / 1000 for e in range(len(ts))]
        lines.append(f"{lab} & {fnum(r['resource_cost_BUSD'])} & " +
                     " & ".join(fnum(c, 1) for c in cap_) +
                     f" & {fnum(r['avg_emissions_Mt'])} & {fnum(r['eue_TWh'], 2)}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


ZWE = [("baseline", "Baseline (5 Mt cap, no tax)"), ("carbon_tax_30", "Carbon tax \\$30/t"),
       ("carbon_tax_50", "Carbon tax \\$50/t"), ("emission_cap_glide", "Glide cap 5$\\to$2 Mt"),
       ("combined_tax50_solar", "Tax \\$50 + Solar $-$30\\%"), ("re_inv_m50", "RE invest $-$50\\%"),
       ("high_demand", "High demand $+$50\\%"), ("drought_stress", "Persistent drought"),
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


def t3():
    e = load("ETH", "baseline"); z = load("ZWE", "baseline")
    def capsum(r): return sum(r["capacity_MW"][i][-1] for i in range(len(r["tech"]))) / 1000
    def gensum(r): return sum(r["gen_TWh"][i][-1] for i in range(len(r["tech"])))
    rows = [("Planning horizon", "2025--2050", "2025--2050"),
            ("Technologies", "5", "5"),
            ("2050 capacity (GW)", fnum(capsum(e), 1), fnum(capsum(z), 1)),
            ("2050 generation (TWh)", fnum(gensum(e), 1), fnum(gensum(z), 1)),
            ("Resource cost (\\$B)", fnum(e["resource_cost_BUSD"]), fnum(z["resource_cost_BUSD"])),
            ("Avg.\\ annual emissions (Mt)", fnum(e["avg_emissions_Mt"]), fnum(z["avg_emissions_Mt"])),
            ("Expected unserved energy (TWh)", fnum(e["eue_TWh"], 2), fnum(z["eue_TWh"], 2))]
    lines = [r"\begin{tabular}{lrr}", r"\toprule", r"Metric & Ethiopia & Zimbabwe\\", r"\midrule"]
    for m, a, b in rows:
        lines.append(f"{m} & {a} & {b}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


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


# ============================================================== appendix tables
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


# ========================================================= revision-response tables
def temporal():
    lines = [r"\begin{tabular}{llrlrr}", r"\toprule",
             r"Country & Resolution & Cost & 2050 installed capacity & Avg.\ em. & EUE\\",
             r" & & (\$B) & of firm technologies (GW) & (Mt) & (TWh)\\", r"\midrule"]
    for cc, firm in (("ETH", ["Hydro"]), ("ZWE", ["Coal", "Gas"])):
        nm = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc]
        rows = (("baseline", "Four blocks"),
                ("temporal_annual", "Single annual block"),
                ("temporal_annual_fixedpeak", "Annual block, four-block peak"))
        for scn, lab in rows:
            r = L(cc, scn)
            if r is None:
                continue
            fc = ", ".join(f"{t}~{f(cap(r,t),1)}" for t in firm)
            lines.append(f"{nm if scn=='baseline' else ''} & {lab} & {f(r['resource_cost_BUSD'])} & "
                         f"{fc} & {f(r['avg_emissions_Mt'])} & {f(r['eue_TWh'])}\\\\")
        if cc == "ETH":
            lines.append(r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def sens():
    rows = [("baseline", "Baseline"), ("sens_disc3", "Discount rate 3\\%"),
            ("sens_disc8", "Discount rate 8\\%"), ("sens_voll10k", "VoLL \\$10{,}000/MWh"),
            ("sens_voll40k", "VoLL \\$40{,}000/MWh"), ("sens_rm10", "Reserve margin 10\\%"),
            ("sens_rm20", "Reserve margin 20\\%"), ("sens_cc_lo", "Capacity credit $-$20\\%"),
            ("sens_cc_hi", "Capacity credit $+$20\\%"), ("sens_mudry_lo", "Dry multiplier $-$15\\%"),
            ("sens_mudry_hi", "Dry multiplier $+$15\\%")]
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Sensitivity & \multicolumn{2}{c}{Ethiopia} & \multicolumn{2}{c}{Zimbabwe}\\",
             r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}",
             r" & Cost (\$B) & EUE (TWh) & Cost (\$B) & EUE (TWh)\\", r"\midrule"]
    for scn, lab in rows:
        e, z = L("ETH", scn), L("ZWE", scn)
        lines.append(f"{lab} & {f(e['resource_cost_BUSD'])} & {f(e['eue_TWh'])} & "
                     f"{f(z['resource_cost_BUSD'])} & {f(z['eue_TWh'])}\\\\")
        if scn == "baseline":
            lines.append(r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def storage():
    rows = [("baseline", "No storage (baseline)"), ("storage_zwe", "Storage \\$1{,}200/kW"),
            ("storage_zwe_cheap", "Storage \\$700/kW")]
    lines = [r"\begin{tabular}{lrrrrrr}", r"\toprule",
             r"Scenario & Cost & Coal & Gas & Solar & Avg.\ em. & EUE\\",
             r" & (\$B) & (GW) & (GW) & (GW) & (Mt) & (TWh)\\", r"\midrule"]
    for scn, lab in rows:
        r = L("ZWE", scn)
        lines.append(f"{lab} & {f(r['resource_cost_BUSD'])} & {f(cap(r,'Coal'),1)} & "
                     f"{f(cap(r,'Gas'),1)} & {f(cap(r,'Solar'),1)} & {f(r['avg_emissions_Mt'])} & "
                     f"{f(r['eue_TWh'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def convergence():
    lines = [r"\begin{tabular}{lrrr}", r"\toprule",
             r"Country & Lower bound & Out-of-sample & 95\% CI\\",
             r" & (training, \$B) & cost (\$B) & ($\pm$\$B)\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        b = L(cc, "baseline")
        lb = b["meta"]["lower_bound_BUSD"]
        ub = b["oos"]["oos_cost_BUSD_mean"]
        ci = b["oos"]["oos_cost_BUSD_ci"]
        nm = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc]
        lines.append(f"{nm} & {f(lb)} & {f(ub)} & {f(ci)}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def uncdecomp():
    rows = [("unc_deterministic", "Deterministic"), ("unc_demand_only", "Demand only"),
            ("unc_hydro_only", "Hydrology only"), ("baseline", "Joint (both)")]
    lines = [r"\begin{tabular}{llrrrrr}", r"\toprule",
             r"Country & Uncertainty & Resource & Shortage & Reserve & Salvage & Total\\",
             r" & represented & (\$B) & (\$B) & (\$B) & (\$B) & (\$B)\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        nm = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc]
        for i, (scn, lab) in enumerate(rows):
            r = L(cc, scn)
            res = r["resource_cost_BUSD"]; sh = r["reliability_cost_BUSD"]; tot = r["total_cost_BUSD"]
            salv = r.get("salvage_BUSD", res + sh - tot)
            reserve = tot - res - sh + salv   # residual reserve penalty; total = res + sh + reserve - salv
            lines.append(f"{nm if i==0 else ''} & {lab} & {f(res)} & {f(sh)} & {f(reserve)} & {f(salv)} & {f(tot)}\\\\")
        if cc == "ETH":
            lines.append(r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def voll_vss():
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Country & Avoided EUE & \multicolumn{3}{c}{VSS on total cost (\$B)}\\",
             r"\cmidrule(lr){3-5}",
             r" & (TWh) & \$10{,}000/MWh & \$20{,}000/MWh & \$40{,}000/MWh\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        b = L(cc, "benchmarks")
        nm = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc]
        aeue = b.get("avoided_eue_TWh", b["ev_eue_TWh"] - b["sddp_eue_TWh"])
        lines.append(f"{nm} & {f(aeue)} & {f(b['VSS_total_at_10k'])} & "
                     f"{f(b['VSS_total_at_20k'])} & {f(b['VSS_total_at_40k'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


if __name__ == "__main__":
    gens = [
        # main-text tables
        ("T1_eth", t1), ("T2_zwe", t2), ("T3_cross", t3), ("T4_vss", t4),
        # appendix tables
        ("A1_eth_deconf", eth_deconf), ("A2_zwe_coalexit", zwe_coalexit),
        ("A3_eth_full", lambda: full("ETH", ETH_ALL)), ("A4_zwe_full", lambda: full("ZWE", ZWE_ALL)),
        # revision-response tables
        ("T5_temporal", temporal), ("T6_sensitivity", sens), ("T7_storage", storage),
        ("T8_convergence", convergence), ("T9_uncdecomp", uncdecomp), ("T10_vollvss", voll_vss),
    ]
    for name, gen in gens:
        try:
            tex = gen()
        except (KeyError, FileNotFoundError) as e:
            print("SKIP", name, "(missing field/file, needs a model run):", e)
            continue
        (TAB / f"{name}.tex").write_text(tex, encoding="utf-8")
        print("=" * 30, name)
        print(tex)
        print()
