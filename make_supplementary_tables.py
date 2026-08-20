"""
Generate supplementary and robustness LaTeX tables from results/<CC>/*.json.

Produces the temporal-resolution, calibration-sensitivity, storage,
convergence-diagnostic, uncertainty-decomposition, value-of-lost-load,
robustness, reserve-adequacy, and value-of-adaptivity tables. Writes one .tex
fragment per table to the tables/ directory.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
OUT = HERE / "tables"
OUT.mkdir(exist_ok=True)


def L(cc, s):
    return json.loads((RES / cc / f"{s}.json").read_text())


def f(x, d=2):
    return f"{x:.{d}f}"


def cap(r, name):
    return r["capacity_MW"][r["tech"].index(name)][-1] / 1000.0


# Temporal-resolution comparison, annual versus four-block
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


# Calibration sensitivities
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


# Zimbabwe battery-storage sensitivity
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


# SDDP convergence diagnostics
def convergence():
    lines = [r"\begin{tabular}{lrrr}", r"\toprule",
             r"Country & Lower bound & Simulated & 95\% CI\\",
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


# Demand-versus-hydrology uncertainty decomposition, with salvage shown explicitly
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


# VSS sensitivity to the value of lost load
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


# Robustness of the baseline (discretization, temporal, persistence, risk)
# Two country panels; columns match tab_robustness in the paper (cost, hydro, solar, emissions, EUE).
def robustness():
    rows = [("baseline", "Baseline (5 nodes, 4 blocks)"), ("gh9", "9 Gauss--Hermite nodes"),
            ("blocks_12", "12 operating blocks"), ("hydro_persist_lo", "Lower hydrology persistence"),
            ("hydro_persist_hi", "Higher hydrology persistence"), ("risk_cvar", "Risk-averse (mean--CVaR)")]
    lines = [r"\begin{tabular}{lrrrrr}", r"\toprule",
             r"Case & Resource cost (\$B) & Hydro (GW) & Solar (GW) & Avg.\ emissions (Mt) & EUE (TWh)\\",
             r"\midrule"]
    for cc in ("ETH", "ZWE"):
        nm = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc]
        lines.append(r"\multicolumn{6}{@{}l}{\textit{%s}}\\" % nm)
        for scn, lab in rows:
            r = L(cc, scn)
            lines.append(f"\\quad {lab} & {f(r['resource_cost_BUSD'])} & {f(cap(r,'Hydro'),1)} & "
                         f"{f(cap(r,'Solar'),1)} & {f(r['avg_emissions_Mt'])} & {f(r['eue_TWh'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# Reserve-adequacy outcomes; matches tab_reserve in the paper
def reserve():
    sets = {"ETH": [("baseline", "Baseline"), ("constrained_hydro", "Constrained hydro"),
                    ("high_demand_expanded", "High demand $+$50\\%"),
                    ("drought_stress", "Persistent drought"),
                    ("accelerated_access", "Accelerated access")],
            "ZWE": [("baseline", "Baseline"), ("carbon_tax_50", "Carbon tax \\$50/t"),
                    ("cap_matched_50", "Matched annual cap"),
                    ("budget_matched_50", "Matched cumulative budget"),
                    ("high_demand", "High demand $+$33\\%"),
                    ("drought_stress", "Persistent drought")]}
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Scenario & EUE (TWh) & Reserve shortfall (MW-year) & Years binding (\%) & Maximum annual (MW)\\",
             r"\midrule"]
    for cc in ("ETH", "ZWE"):
        nm = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc]
        lines.append(r"\multicolumn{5}{@{}l}{\textit{%s}}\\" % nm)
        for scn, lab in sets[cc]:
            r = L(cc, scn)
            lines.append(f"\\quad {lab} & {f(r['eue_TWh'])} & {r['reserve_short_MWyr']:,.0f} & "
                         f"{100*r['reserve_bind_frac']:.0f} & {r['reserve_short_max_MW']:,.0f}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


# Value-of-adaptivity benchmarks; matches tab_vss in the paper
def vss():
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Plan & Resource cost (\$B, NPV) & Expected unserved energy (TWh) & "
             r"Salvage credit (\$B) & Total expected cost (\$B)\\", r"\midrule"]
    for cc in ("ETH", "ZWE"):
        b = L(cc, "benchmarks")
        nm = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}[cc]
        lines.append(r"\multicolumn{5}{@{}l}{\textit{%s}}\\" % nm)
        for lab, pre in (("Perfect information", "pi"), ("Stochastic (SDDP)", "sddp"),
                         ("Deterministic (EV)", "ev")):
            lines.append(f"\\quad {lab} & {f(b[pre+'_econ_BUSD'])} & {f(b[pre+'_eue_TWh'])} & "
                         f"{f(b.get(pre+'_salvage_BUSD', 0))} & {f(b[pre+'_total_BUSD'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


if __name__ == "__main__":
    gens = {"T5_temporal": temporal, "T6_sensitivity": sens, "T7_storage": storage,
            "T8_convergence": convergence, "T9_uncdecomp": uncdecomp, "T10_vollvss": voll_vss,
            "T11_robustness": robustness, "T12_reserve": reserve, "T13_vss": vss}
    for name, gen in gens.items():
        try:
            tex = gen()
        except (KeyError, FileNotFoundError) as e:
            print("SKIP", name, "(missing field):", e)
            continue
        (OUT / f"{name}.tex").write_text(tex, encoding="utf-8")
        print("=" * 30, name)
        print(tex)
        print()
