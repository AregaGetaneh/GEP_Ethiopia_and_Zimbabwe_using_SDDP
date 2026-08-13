"""
make_figures.py  -- publication figures for the revised paper.
Reads results/<CC>/*.json (+ params_<CC>.json for cost splits), writes PDFs to
revision/figures/. Pure JSON + matplotlib. Run:  python make_figures.py
"""

import json
from pathlib import Path
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
FIG = HERE / "figures"
FIG.mkdir(exist_ok=True)

mpl.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 600,
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "font.size": 11, "axes.titlesize": 11, "axes.labelsize": 11,
    "xtick.labelsize": 9.5, "ytick.labelsize": 9.5, "legend.fontsize": 9.5,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.linewidth": 0.6, "axes.grid": True, "grid.linewidth": 0.4,
    "grid.alpha": 0.30, "lines.linewidth": 1.7,
    "xtick.direction": "in", "ytick.direction": "in",
    "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "legend.frameon": False,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.04,
})

TCOL = {"Hydro": "#4477AA", "Wind": "#66CCEE", "Solar": "#CCBB44",
        "Geothermal": "#EE6677", "Bioenergy": "#228833",
        "Coal": "#BBBBBB", "Gas": "#AA3377"}
COST_COL = {"Investment": "#4477AA", "Operating": "#EE6677",
            "Fixed O&M": "#CCBB44", "Unmet penalty": "#BBBBBB"}
YEARS = list(range(2025, 2051))
DISC = [1.05 ** (-t) for t in range(26)]

CORE = {
    "ETH": [("baseline", "Baseline"), ("re_inv_minus30", "RE $-$30%"),
            ("re_inv_minus50", "RE $-$50%"), ("learning_curves", "Learning curves"),
            ("solar_only_50", "Solar-only $-$50%"), ("high_demand_expanded", "High demand"),
            ("constrained_hydro", "Constrained hydro"), ("drought_stress", "Drought")],
    "ZWE": [("baseline", "Baseline"), ("carbon_tax_30", "Tax \\$30"),
            ("carbon_tax_50", "Tax \\$50"), ("emission_cap_glide", "Glide cap"),
            ("combined_tax50_solar", "Combined"), ("re_inv_m50", "RE $-$50%"),
            ("high_demand", "High demand"), ("drought_stress", "Drought")],
}
NAME = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}
SCEN_COL = ["#332288", "#88CCEE", "#44AA99", "#117733", "#DDCC77",
            "#CC6677", "#AA4499", "#882255"]
_P = {}


def load(cc, scn):
    return json.loads((RES / cc / f"{scn}.json").read_text())


def params(cc):
    if cc not in _P:
        _P[cc] = json.loads((HERE / f"params_{cc}.json").read_text())
    return _P[cc]


def techs(cc):
    return load(cc, "baseline")["tech"]


def cost_split(cc, r):
    """Discounted investment / operating / fixed-O&M / unmet-penalty ($B)."""
    p = params(cc); a1, a2, b1, nE = p["a1"], p["a2"], p["b1"], p["nE"]
    inv = op = fom = 0.0
    for t in range(26):
        inv += DISC[t] * sum(a2[t][e] * r["build_MW"][e][t] for e in range(nE))
        op += DISC[t] * sum(a1[e] * r["gen_TWh"][e][t] * 1e6 for e in range(nE))
        fom += DISC[t] * sum(b1[e] * r["capacity_MW"][e][t] for e in range(nE))
    return inv / 1e9, op / 1e9, fom / 1e9


def bottom_legend(fig, ts, ncol=None):
    handles = [plt.Rectangle((0, 0), 1, 1, color=TCOL[t]) for t in ts]
    fig.legend(handles, ts, ncol=ncol or len(ts), loc="lower center",
               bbox_to_anchor=(0.5, -0.01))


# ============================================================= 1. capacity mix trajectory (2x4 area)
def fig_capacity_traj(cc):
    ts = techs(cc)
    fig, axes = plt.subplots(2, 4, figsize=(11.2, 5.4), sharex=True, sharey=True)
    for ax, (scn, lab) in zip(axes.flat, CORE[cc]):
        r = load(cc, scn)
        stacks = [[c / 1000.0 for c in r["capacity_MW"][e]] for e in range(len(ts))]
        ax.stackplot(YEARS, *stacks, colors=[TCOL[t] for t in ts], alpha=0.9,
                     edgecolor="white", linewidth=0.2)
        ax.set_title(lab, fontsize=10); ax.set_xlim(2025, 2050); ax.margins(y=0)
        ax.xaxis.set_major_locator(mtick.MultipleLocator(10))
    for ax in axes[:, 0]:
        ax.set_ylabel("Installed capacity (GW)")
    bottom_legend(fig, ts)
    fig.suptitle(f"{NAME[cc]}: installed capacity mix by scenario, 2025--2050", y=0.99, fontsize=12)
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    fig.savefig(FIG / f"F1_{cc}_capacity_traj.pdf"); plt.close(fig)


# larger direct-labelled bands per country; smaller techs rely on the legend
_BIGBAND = {"ETH": {"Hydro", "Solar", "Bioenergy"},
            "ZWE": {"Hydro", "Solar", "Coal", "Gas", "Bioenergy"}}


def fig_baseline_captraj(cc):
    """Clean single-panel baseline installed-capacity trajectory (headline visual)."""
    ts = techs(cc)
    r = load(cc, "baseline")
    caps = {t: [c / 1000.0 for c in r["capacity_MW"][i]] for i, t in enumerate(ts)}
    fig, ax = plt.subplots(figsize=(7.4, 4.5))
    ax.stackplot(YEARS, *[caps[t] for t in ts], colors=[TCOL[t] for t in ts],
                 alpha=0.92, edgecolor="white", linewidth=0.9)
    tot = [sum(caps[t][k] for t in ts) for k in range(len(YEARS))]
    ax.plot(YEARS, tot, color="#222222", lw=1.3)
    ax.annotate(f"{tot[-1]:.1f} GW", xy=(2050, tot[-1]), xytext=(-2, 5),
                textcoords="offset points", ha="right", va="bottom",
                fontsize=9.5, color="#222222", fontweight="bold")
    cum = 0.0
    for t in ts:
        mid = cum + caps[t][-1] / 2.0
        cum += caps[t][-1]
        if t in _BIGBAND[cc] and caps[t][-1] > 0.6:
            ax.annotate(f"{t} {caps[t][-1]:.1f}", xy=(2050, mid), xytext=(6, 0),
                        textcoords="offset points", ha="left", va="center",
                        fontsize=9.5, color=TCOL[t], fontweight="bold")
    handles = [plt.Rectangle((0, 0), 1, 1, fc=TCOL[t], alpha=0.92) for t in ts]
    ax.legend(handles, ts, loc="upper left", ncol=2, handlelength=1.1,
              columnspacing=1.2, borderaxespad=0.4)
    ax.set_xlim(2025, 2050); ax.set_ylim(0, tot[-1] * 1.16); ax.margins(x=0)
    ax.xaxis.set_major_locator(mtick.MultipleLocator(5))
    ax.set_xlabel("Year"); ax.set_ylabel("Installed capacity (GW)")
    ax.set_title(f"{NAME[cc]}: baseline installed-capacity trajectory")
    fig.tight_layout()
    fig.savefig(FIG / f"F1b_{cc}_capacity_baseline.pdf"); plt.close(fig)


# ============================================================= 1c. new-build trajectory (per technology, per scenario)
def fig_build_traj(cc):
    """New capacity commissioned per year, one panel per technology, one line per scenario."""
    ts = techs(cc)
    scc = CORE[cc]
    cols = SCEN_COL[:len(scc)]
    fig, axes = plt.subplots(2, 3, figsize=(10.4, 5.8))
    axes = axes.ravel()
    for i, t in enumerate(ts):
        ax = axes[i]
        peak = 0.0
        for (scn, _), c in zip(scc, cols):
            r = load(cc, scn)
            series = r["build_MW"][i]
            peak = max(peak, max(series))
            lw = 2.0 if scn == "baseline" else 1.2
            ax.plot(YEARS, series, color=c, lw=lw)
        ax.set_title(t, fontsize=10)
        ax.set_xlim(2025, 2050)
        ax.set_ylabel("MW/yr"); ax.grid(True, alpha=0.3, lw=0.4)
        ax.xaxis.set_major_locator(mtick.MultipleLocator(10))
        if peak < 1.0:
            ax.set_ylim(0, 1)
            ax.text(0.5, 0.5, "no new build", transform=ax.transAxes, ha="center",
                    va="center", fontsize=10, color="#888888", style="italic")
        else:
            ax.set_ylim(bottom=0)
    for j in range(len(ts), len(axes)):
        axes[j].axis("off")
    handles = [plt.Line2D([0], [0], color=c, lw=1.8) for c in cols]
    axes[-1].legend(handles, [lab for _, lab in scc], loc="center",
                    fontsize=9, frameon=False, title="Scenario")
    fig.tight_layout()
    fig.savefig(FIG / f"F1c_{cc}_build_traj.pdf"); plt.close(fig)


# ============================================================= 2. generation mix trajectory (2x4 area)
def fig_genmix_traj(cc):
    ts = techs(cc)
    fig, axes = plt.subplots(2, 4, figsize=(11.2, 5.4), sharex=True, sharey=True)
    for ax, (scn, lab) in zip(axes.flat, CORE[cc]):
        r = load(cc, scn)
        stacks = [r["gen_TWh"][e] for e in range(len(ts))]
        ax.stackplot(YEARS, *stacks, colors=[TCOL[t] for t in ts], alpha=0.9,
                     edgecolor="white", linewidth=0.2)
        dem = [sum(r["gen_TWh"][e][i] for e in range(len(ts))) +
               sum(r["unserved_TWh"][b][i] for b in range(len(r["unserved_TWh"])))
               for i in range(26)]
        ax.plot(YEARS, dem, color="k", lw=1.1, ls="--")
        ax.set_title(lab, fontsize=10); ax.set_xlim(2025, 2050); ax.margins(y=0)
        ax.xaxis.set_major_locator(mtick.MultipleLocator(10))
    for ax in axes[:, 0]:
        ax.set_ylabel("Generation (TWh)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=TCOL[t]) for t in ts]
    handles.append(plt.Line2D([], [], color="k", ls="--", lw=1.1))
    fig.legend(handles, ts + ["Demand"], ncol=len(ts) + 1, loc="lower center",
               bbox_to_anchor=(0.5, -0.01))
    fig.suptitle(f"{NAME[cc]}: generation mix by scenario, 2025--2050 (dashed: demand)",
                 y=0.99, fontsize=12)
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    fig.savefig(FIG / f"F2_{cc}_genmix_traj.pdf"); plt.close(fig)


# ============================================================= 3. 2050 installed capacity by tech (grouped bars)
def fig_capacity_2050(cc):
    ts = techs(cc); rows = CORE[cc]
    labels = [lab for _, lab in rows]
    caps = {t: [load(cc, scn)["capacity_MW"][e][-1] / 1000.0 for scn, _ in rows]
            for e, t in enumerate(ts)}
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    n = len(ts); w = 0.8 / n
    x = range(len(rows))
    for i, t in enumerate(ts):
        ax.bar([xi + (i - (n - 1) / 2) * w for xi in x], caps[t], w,
               color=TCOL[t], label=t, edgecolor="white", linewidth=0.2)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Installed capacity in 2050 (GW)")
    ax.set_title(f"{NAME[cc]}: 2050 installed capacity by technology")
    ax.legend(ncol=n, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    fig.savefig(FIG / f"F3_{cc}_capacity_2050.pdf"); plt.close(fig)


# ============================================================= 4. 2050 generation mix (stacked bars)
def fig_genmix2050(cc):
    ts = techs(cc); rows = CORE[cc]
    labels = [lab for _, lab in rows]
    data = {t: [load(cc, scn)["gen_TWh"][e][-1] for scn, _ in rows] for e, t in enumerate(ts)}
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    x = range(len(labels)); bottom = [0.0] * len(labels)
    for t in ts:
        ax.bar(x, data[t], 0.72, bottom=bottom, color=TCOL[t], edgecolor="white",
               linewidth=0.3, label=t)
        bottom = [b + v for b, v in zip(bottom, data[t])]
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Generation in 2050 (TWh)")
    ax.set_title(f"{NAME[cc]}: 2050 generation mix by scenario")
    ax.legend(ncol=len(ts), loc="upper center", bbox_to_anchor=(0.5, -0.18))
    fig.savefig(FIG / f"F4_{cc}_genmix2050.pdf"); plt.close(fig)


# ============================================================= 5. cost decomposition by scenario (stacked bars)
def fig_cost_decomp(cc):
    rows = CORE[cc]; labels = [lab for _, lab in rows]
    inv, op, fom = [], [], []
    for scn, _ in rows:
        i, o, f = cost_split(cc, load(cc, scn)); inv.append(i); op.append(o); fom.append(f)
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    x = range(len(labels))
    ax.bar(x, inv, 0.7, color=COST_COL["Investment"], label="Investment")
    ax.bar(x, op, 0.7, bottom=inv, color=COST_COL["Operating"], label="Operating")
    ax.bar(x, fom, 0.7, bottom=[a + b for a, b in zip(inv, op)],
           color=COST_COL["Fixed O&M"], label="Fixed O&M")
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Resource cost (\\$B, NPV)")
    ax.set_title(f"{NAME[cc]}: system cost by scenario, decomposed")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    fig.savefig(FIG / f"F5_{cc}_cost_decomp.pdf"); plt.close(fig)


# ============================================================= 6. ZWE baseline capacity + emissions panel
def fig_zwe_baseline_panel():
    r = load("ZWE", "baseline"); ts = r["tech"]
    fig, ax = plt.subplots(figsize=(7.6, 4.3))
    bottom = [0.0] * len(YEARS)
    for e, t in enumerate(ts):
        cap = [c / 1000.0 for c in r["capacity_MW"][e]]
        ax.fill_between(YEARS, bottom, [b + c for b, c in zip(bottom, cap)],
                        color=TCOL[t], alpha=0.9, label=t, linewidth=0)
        bottom = [b + c for b, c in zip(bottom, cap)]
    ax.set_ylabel("Installed capacity (GW)"); ax.set_xlim(2025, 2050); ax.set_xlabel("Year")
    ax.margins(y=0)
    ax2 = ax.twinx()
    ax2.plot(YEARS, r["emissions_Mt"], color="k", lw=2.0, ls="--")
    ax2.set_ylabel("Annual CO$_2$ emissions (Mt)"); ax2.grid(False); ax2.set_ylim(bottom=0)
    ax.set_title("Zimbabwe: installed capacity and annual emissions, baseline")
    handles = [plt.Rectangle((0, 0), 1, 1, color=TCOL[t]) for t in ts]
    handles.append(plt.Line2D([], [], color="k", ls="--", lw=2.0))
    fig.legend(handles, ts + ["Emissions"], ncol=len(ts) + 1, loc="lower center",
               bbox_to_anchor=(0.5, -0.03))
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(FIG / "F6_ZWE_baseline_panel.pdf"); plt.close(fig)


# ============================================================= 7. ZWE emissions trajectories by scenario
def fig_zwe_emissions_traj():
    order = ["baseline", "carbon_tax_30", "carbon_tax_50", "emission_cap_glide",
             "combined_tax50_solar", "re_inv_m50", "high_demand", "drought_stress"]
    lab = dict(CORE["ZWE"])
    cols = SCEN_COL[:len(order)]
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    for scn, c in zip(order, cols):
        r = load("ZWE", scn)
        lw = 2.3 if scn == "baseline" else 1.3
        ax.plot(YEARS, r["emissions_Mt"], color=c, lw=lw, label=lab[scn])
    ax.axhline(5.0, color="k", lw=0.9, ls=":", label="5 Mt cap")
    ax.set_xlim(2025, 2050); ax.set_ylim(bottom=0); ax.set_xlabel("Year")
    ax.set_ylabel("Annual CO$_2$ emissions (Mt)")
    ax.grid(True, alpha=0.3, lw=0.4)
    ax.xaxis.set_major_locator(mtick.MultipleLocator(5))
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.16), fontsize=8.5)
    fig.savefig(FIG / "F7_ZWE_emissions_traj.pdf"); plt.close(fig)


# ============================================================= 8. ZWE cost-emissions frontier
def fig_zwe_frontier():
    order = ["baseline", "carbon_tax_30", "carbon_tax_50", "combined_tax50_solar",
             "emission_cap_glide", "re_inv_m50", "cap_matched_50", "budget_matched_50"]
    lab = {"baseline": "Baseline", "carbon_tax_30": "Tax \\$30", "carbon_tax_50": "Tax \\$50",
           "combined_tax50_solar": "Combined", "emission_cap_glide": "Glide cap",
           "re_inv_m50": "RE $-$50%", "cap_matched_50": "Matched cap",
           "budget_matched_50": "Matched budget"}
    # per-point label offsets to de-clutter the tax50 / matched-cap / matched-budget cluster
    off = {"baseline": (7, 4), "carbon_tax_30": (7, 4), "carbon_tax_50": (8, -11),
           "combined_tax50_solar": (7, 4), "emission_cap_glide": (8, 5),
           "re_inv_m50": (7, 4), "cap_matched_50": (8, 6), "budget_matched_50": (-70, -3)}
    ha = {"budget_matched_50": "right"}
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    for scn in order:
        r = load("ZWE", scn)
        x, y = r["avg_emissions_Mt"], r["resource_cost_BUSD"]
        ax.scatter(x, y, s=55, color="#4477AA", zorder=3, edgecolor="white", linewidth=0.5)
        ax.annotate(lab[scn], (x, y), textcoords="offset points",
                    xytext=off.get(scn, (7, 4)), fontsize=9, ha=ha.get(scn, "left"))
    ax.set_xlabel("Average annual emissions (MtCO$_2$)")
    ax.set_ylabel("Resource cost (\\$B, NPV)")
    ax.set_title("Zimbabwe: cost--emissions outcomes by policy")
    fig.savefig(FIG / "F8_ZWE_frontier.pdf"); plt.close(fig)


# ============================================================= 9. value of adaptivity (both countries)
def fig_vss():
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 4.0))
    for ax, cc in zip(axes, ("ETH", "ZWE")):
        b = json.loads((RES / cc / "benchmarks.json").read_text())
        plans = ["Perfect\ninformation", "Stochastic\n(SDDP)", "Deterministic"]
        econ = [b["pi_econ_BUSD"], b["sddp_econ_BUSD"], b["ev_econ_BUSD"]]
        eue = [b["pi_eue_TWh"], b["sddp_eue_TWh"], b["ev_eue_TWh"]]
        x = range(3)
        bars = ax.bar(x, econ, 0.58, color="#4477AA")
        ax.set_xticks(list(x)); ax.set_xticklabels(plans)
        ax.set_ylabel("Resource cost (\\$B, NPV)"); ax.set_title(NAME[cc])
        ax2 = ax.twinx()
        ax2.plot(x, eue, "o-", color="#EE6677", lw=2.0, ms=7)
        ax2.set_ylabel("Expected unserved energy (TWh)"); ax2.grid(False); ax2.set_ylim(bottom=0)
    h = [plt.Rectangle((0, 0), 1, 1, color="#4477AA"),
         plt.Line2D([], [], color="#EE6677", marker="o", lw=2.0)]
    fig.legend(h, ["Resource cost (\\$B)", "Expected unserved (TWh)"],
               ncol=2, loc="lower center", bbox_to_anchor=(0.5, -0.04))
    fig.suptitle("Value of adaptivity: deterministic vs stochastic vs perfect information",
                 y=1.0, fontsize=12)
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    fig.savefig(FIG / "F9_value_of_adaptivity.pdf"); plt.close(fig)


# ============================================================= 10. ZWE matched price vs quantity
def fig_zwe_matched():
    items = [("Tax \\$50", "carbon_tax_50", "#4477AA"),
             ("Annual cap", "cap_matched_50", "#CCBB44"),
             ("Cumulative\nbudget", "budget_matched_50", "#228833")]
    cost = [load("ZWE", it[1])["resource_cost_BUSD"] for it in items]
    emis = [load("ZWE", it[1])["avg_emissions_Mt"] for it in items]
    eue = [load("ZWE", it[1])["eue_TWh"] for it in items]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.0, 4.0))
    x = range(len(items)); labels = [it[0] for it in items]; col = [it[2] for it in items]
    a1.bar(x, cost, 0.6, color=col)
    a1.set_xticks(list(x)); a1.set_xticklabels(labels)
    a1.set_ylabel("Economic resource cost (\\$B)"); a1.set_title("Resource cost")
    for xi, e in zip(x, eue):
        a1.annotate(f"EUE {e:.2f} TWh", (xi, 0.15), ha="center", fontsize=8, color="#772222")
    a2.bar(x, emis, 0.6, color=col)
    a2.set_xticks(list(x)); a2.set_xticklabels(labels)
    a2.set_ylabel("Average emissions (MtCO$_2$)"); a2.set_title("Emissions (matched)")
    fig.suptitle("Zimbabwe: price vs quantity at matched stringency", y=1.0, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(FIG / "F10_ZWE_matched.pdf"); plt.close(fig)


# ============================================================= 11. adequacy (EUE both countries)
def fig_adequacy():
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, cc in zip(axes, ("ETH", "ZWE")):
        rows = CORE[cc]; labels = [lab for _, lab in rows]
        eue = [load(cc, scn)["eue_TWh"] for scn, _ in rows]
        x = range(len(rows))
        ax.bar(x, eue, 0.62, color="#EE6677", edgecolor="white", linewidth=0.3)
        ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=25, ha="right")
        ax.set_ylabel("Expected unserved energy (TWh)"); ax.set_title(NAME[cc])
    fig.suptitle("Adequacy: expected unserved energy by scenario", y=1.0, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(FIG / "F11_adequacy.pdf"); plt.close(fig)


# ============================================================= 12. annual cost trajectory (baseline, both)
def fig_annual_cost():
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.0), sharex=True)
    for ax, cc in zip(axes, ("ETH", "ZWE")):
        p = params(cc); a1, a2, b1, nE = p["a1"], p["a2"], p["b1"], p["nE"]
        r = load(cc, "baseline")
        inv = [sum(a2[t][e] * r["build_MW"][e][t] for e in range(nE)) / 1e9 for t in range(26)]
        op = [sum(a1[e] * r["gen_TWh"][e][t] * 1e6 for e in range(nE)) / 1e9 for t in range(26)]
        fom = [sum(b1[e] * r["capacity_MW"][e][t] for e in range(nE)) / 1e9 for t in range(26)]
        ax.stackplot(YEARS, inv, op, fom, colors=[COST_COL["Investment"],
                     COST_COL["Operating"], COST_COL["Fixed O&M"]],
                     labels=["Investment", "Operating", "Fixed O&M"], alpha=0.9)
        ax.set_xlim(2025, 2050); ax.set_title(NAME[cc]); ax.set_xlabel("Year"); ax.margins(y=0)
    axes[0].set_ylabel("Annual cost (\\$B/yr)")
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.03))
    fig.suptitle("Annual system cost by component, baseline", y=1.0, fontsize=12)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    fig.savefig(FIG / "F12_annual_cost.pdf"); plt.close(fig)


if __name__ == "__main__":
    for cc in ("ETH", "ZWE"):
        fig_baseline_captraj(cc); fig_build_traj(cc); fig_capacity_traj(cc); fig_genmix_traj(cc); fig_capacity_2050(cc)
        fig_genmix2050(cc); fig_cost_decomp(cc)
    fig_zwe_baseline_panel(); fig_zwe_emissions_traj(); fig_zwe_frontier()
    fig_vss(); fig_zwe_matched(); fig_adequacy(); fig_annual_cost()
    print("figures written to", FIG)
    for p in sorted(FIG.glob("*.pdf")):
        print("  ", p.name)
