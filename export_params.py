"""
export_params.py
=================
Single source of truth for the revised SDDP.jl model. Reads the same Excel
workbooks the notebooks use, adds the calibration for the Recommended Energy
Economics package (seasonal blocks, three-state hydrology, lead-time pipeline,
demand-deviation process, finance, salvage), and writes one JSON per country
plus the scenario list. Julia reads these JSON files and never touches Excel.

Run:  python export_params.py
Out:  revision/params_ETH.json , revision/params_ZWE.json

All calibration constants below were signed off against revision/SPEC.md.
Change a value here (not in Julia) to re-tune, then re-run the HPC jobs.
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent / "data"        # raw country workbooks
OUT  = Path(__file__).resolve().parent                 # repo root (beside gep_sddp.jl)
YEARS = list(range(2025, 2051))
NT = len(YEARS)                                         # 26

# ----------------------------------------------------------------------------
# Country base configuration
# ----------------------------------------------------------------------------
COUNTRY = {
    "ETH": {
        "xlsx": "ETHSummary_corrected_like_NSW_filled.xlsx",
        "tech": ["Hydro", "Wind", "Solar", "Geothermal", "Bioenergy"],
        # resource ceilings used by the scenario runner (REALISTIC_UB in the notebook)
        "ub": {"Hydro": 15000.0, "Wind": 10000.0, "Solar": 10000.0,
               "Geothermal": 5000.0, "Bioenergy": 3000.0},
        # observed 2024 generation anchor (TWh) and accelerated-access anchor (TWh)
        "demand_anchor_observed_TWh": 17.75,
        "demand_anchor_access_TWh": 40.0,
    },
    "ZWE": {
        "xlsx": "ZWE_Summary_2025_2050.xlsx",
        "tech": ["Hydro", "Coal", "Solar", "Gas", "Bioenergy"],
        "ub": {"Hydro": 2500.0, "Coal": 1900.0, "Solar": 3000.0,
               "Gas": 1500.0, "Bioenergy": 1000.0},
        "demand_anchor_observed_TWh": 8.71,
        "demand_anchor_access_TWh": 8.71,
    },
}

# ----------------------------------------------------------------------------
# Calibration (signed off in SPEC.md sections 3, 4, 5, 11)
# ----------------------------------------------------------------------------
# Demand-deviation AR(1):  u_{t+1} = rho_D u_t + sigma_frac*Dbar_t * eps
DEMAND = {"rho": 0.90, "sigma_frac": 0.0147, "n_nodes": 5}

# Three-state hydrology Markov chain (order: dry, normal, wet)
HYDRO = {
    "states": ["dry", "normal", "wet"],
    "P": [[0.55, 0.35, 0.10],
          [0.25, 0.50, 0.25],
          [0.10, 0.35, 0.55]],
    # raw multipliers, normalized below so the stationary-weighted mean equals one
    "mu_raw": {"ETH": [0.80, 1.00, 1.12],
               "ZWE": [0.65, 1.00, 1.15]},
}


def stationary_dist(Pmat):
    """Stationary distribution of a row-stochastic transition matrix."""
    A = np.array(Pmat, float)
    vals, vecs = np.linalg.eig(A.T)
    i = int(np.argmin(np.abs(vals - 1.0)))
    v = np.real(vecs[:, i])
    return v / v.sum()


def normalized_hydrology(cc):
    """Normalize regime multipliers so the stationary-weighted mean water energy is
    one (fix A2). This keeps alpha_e as the long-run average hydro energy factor
    rather than letting the Markov chain lower the mean below the calibration.
    The stage-1 regime distribution is the stationary distribution."""
    pi = stationary_dist(HYDRO["P"])
    mu_raw = HYDRO["mu_raw"][cc]
    mean = float(np.dot(pi, mu_raw))
    mu = [m / mean for m in mu_raw]
    return dict(states=HYDRO["states"], init=pi.tolist(), P=HYDRO["P"],
                mu=mu, mu_raw=mu_raw, stationary=pi.tolist(), raw_mean=mean)

# Lead times L_e (years), from paper Table 3
LEAD = {
    "ETH": {"Hydro": 5, "Wind": 1, "Solar": 1, "Geothermal": 3, "Bioenergy": 1},
    "ZWE": {"Hydro": 5, "Coal": 4, "Solar": 1, "Gas": 3, "Bioenergy": 2},
}

# Seasonal operating blocks, order: [wet-peak, wet-off, dry-peak, dry-off].
# Peak blocks are ~20% of each season's hours and carry ~26-28% of its demand
# (peak-to-average ~1.1-1.5), so firm capacity is valued without forcing scarcity.
BLOCKS = {
    "names": ["wet_peak", "wet_off", "dry_peak", "dry_off"],
    "ETH": {"hours": [584.0, 2336.0, 1168.0, 4672.0],
            "demand_share": [0.109, 0.311, 0.151, 0.429]},
    "ZWE": {"hours": [876.0, 3504.0, 876.0, 3504.0],
            "demand_share": [0.134, 0.346, 0.146, 0.374]},
}

# Two availability regimes.
#  Energy-limited techs (VRE and hydro): block CF is a resource SHAPE, rescaled
#  per tech so the duration-weighted average reproduces the Excel annual alpha_e.
#  Solar is low in the peak (evening) blocks, which is what makes adequacy a real
#  constraint. Hydro is also scaled by the hydrology regime multiplier in Julia.
ENERGY_LIMITED = {
    "ETH": {"Hydro": [0.60, 0.55, 0.42, 0.40],
            "Wind":  [0.30, 0.32, 0.36, 0.36],
            "Solar": [0.10, 0.26, 0.14, 0.30]},
    "ZWE": {"Hydro": [0.62, 0.58, 0.40, 0.38],
            "Solar": [0.10, 0.24, 0.14, 0.30]},
}
#  Dispatchable techs: block availability is a flat power limit (not an annual
#  energy cap); annual output is set by dispatch economics.
DISPATCHABLE_AVAIL = {"Coal": 0.85, "Gas": 0.90, "Bioenergy": 0.80, "Geothermal": 0.90}

# Reservoir hydro (GERD, Kariba): a turbine power limit in every block, plus a
# seasonal water-energy budget. Water can be dispatched to the peak within a
# season, but the seasonal total is capped and the dry season is scarcer. The
# hydrology regime multiplier scales the water budget (drought risk).
HYDRO_AVAIL = 0.90                      # turbine power availability (fraction of nameplate)
WATER_FRAC = {"wet": 0.60, "dry": 0.40}  # share of annual water energy by season
BLOCK_SEASON = [1, 1, 2, 2]            # 1 = wet, 2 = dry, matching block order

# Planning reserve margin (adequacy). Firm capacity, rated at dry-year firm output,
# must cover peak demand plus a margin. Enforced softly: a capacity-shortfall
# variable priced at a peaker-like cost, so a binding emissions limit yields a
# bounded adequacy signal (the storage need) rather than a VoLL energy blow-up.
RESERVE_MARGIN = 0.15                   # firm capacity >= (1+margin) * peak load
CAP_SHORT_PRICE = 300000.0             # $/MW-yr penalty for unmet reserve
VRE_CREDIT = {"Solar": 0.05, "Wind": 0.10}   # capacity credit of VRE at the peak

# Finance (SPEC section 11): concessional vs commercial WACC and tech life
FINANCE = {"wacc_commercial": 0.12, "wacc_concessional": 0.06,
           "life_years": {"Hydro": 40, "Wind": 25, "Solar": 25,
                          "Geothermal": 30, "Coal": 35, "Gas": 30,
                          "Bioenergy": 25}}


def crf(w, n):
    """Capital recovery factor for WACC w and life n."""
    return w * (1 + w) ** n / ((1 + w) ** n - 1)


# Effective CAPEX multiplier from moving solar from a commercial to a concessional WACC
# (fix 6.3). The ratio of capital-recovery factors is the effective overnight-CAPEX
# reduction that cheaper capital delivers; (1 - mult) is the financed share (subsidy).
SOLAR_FIN_MULT = round(crf(FINANCE["wacc_concessional"], FINANCE["life_years"]["Solar"]) /
                       crf(FINANCE["wacc_commercial"], FINANCE["life_years"]["Solar"]), 4)

DISCOUNT = 0.05
VOLL = 20000.0                     # $/MWh
SALVAGE = True                     # remaining-life salvage credit at terminal stage
COAL_REFURB_USD_PER_KW = 600.0     # existing-coal life-extension, decomposition (c)

# Battery storage sensitivity (B5): 4-hour Li-ion, bundled power CAPEX ($/MW), round-trip
# efficiency, firm capacity credit, and fixed O&M. Two cost points bracket the range.
STORAGE = {"capex_usd_per_mw": 1_200_000.0, "capex_cheap_usd_per_mw": 700_000.0,
           "eff": 0.85, "credit": 0.90, "fom_usd_per_mw_yr": 25_000.0}


# ----------------------------------------------------------------------------
# Excel loading (mirrors the notebooks' load_data)
# ----------------------------------------------------------------------------
def load_country(cc):
    cfg = COUNTRY[cc]
    xlsx = BASE / cfg["xlsx"]
    tech = cfg["tech"]

    inv = pd.read_excel(xlsx, sheet_name="InvCost($kW)")
    inv.columns = [str(c).strip() for c in inv.columns]
    a2 = inv[tech].iloc[:NT].to_numpy(float) * 1000.0          # $/kW -> $/MW, [NT x nE]

    cost = pd.read_excel(xlsx, sheet_name="Cost1")
    cost.columns = [str(c).strip() for c in cost.columns]
    gc = "GenCost ($/MWh)" if "GenCost ($/MWh)" in cost.columns else "GenCost"
    a1 = cost[gc].iloc[:len(tech)].to_numpy(float)             # $/MWh variable op
    opex_col = [c for c in cost.columns if "Opex" in c][0]
    b1 = pd.to_numeric(cost[opex_col].iloc[:len(tech)], errors="coerce").to_numpy(float) * 1000.0
    xinit = cost["Init (MW)"].iloc[:len(tech)].to_numpy(float)
    em_col = [c for c in cost.columns if c.strip().startswith("EM")][0]
    EM = cost[em_col].iloc[:len(tech)].to_numpy(float)         # kg/MWh

    conv = pd.read_excel(xlsx, sheet_name="Conversion")
    conv.columns = [str(c).strip() for c in conv.columns]
    acol = [c for c in conv.columns if c.strip().startswith("a=") or c.strip() == "alpha"][0]
    alpha = conv[acol].iloc[:len(tech)].to_numpy(float)        # MWh per MW-year

    ret = pd.read_excel(xlsx, sheet_name="retirement")
    annual = np.nan_to_num(ret.iloc[:NT, 1:1+len(tech)].to_numpy(float), nan=0.0)
    accum = np.minimum(np.cumsum(annual, axis=0), xinit)       # [NT x nE] cumulative min-retirement

    dem = pd.read_excel(xlsx, sheet_name="demand")
    dem.columns = [str(c).strip() for c in dem.columns]
    central = dem["Central"].iloc[:NT].to_numpy(float)         # MWh/yr

    return dict(tech=tech, a1=a1, a2=a2, b1=b1, xinit=xinit, EM=EM,
                alpha=alpha, accum=accum, central=central, ub=cfg["ub"],
                observed=cfg["demand_anchor_observed_TWh"],
                access=cfg["demand_anchor_access_TWh"])


def block_cf(cc, tech, alpha):
    """Block capacity factors. Energy-limited techs (VRE, hydro) rescale so
    sum_b cf[e,b]*H[b] == alpha_e (annual resource). Dispatchable techs get a flat
    availability (power limit), so their block CF*H does NOT equal alpha_e."""
    H = np.array(BLOCKS[cc]["hours"])
    cf = {}
    for e, name in enumerate(tech):
        if name in DISPATCHABLE_AVAIL:
            cf[name] = [round(DISPATCHABLE_AVAIL[name], 6)] * len(H)   # flat power limit
        else:
            shape = np.array(ENERGY_LIMITED[cc][name], float)
            weighted = float((shape * H).sum())
            scale = alpha[e] / weighted if weighted > 0 else 0.0
            cf[name] = list(np.round(shape * scale, 6))               # rescaled to annual alpha
    return cf


# ----------------------------------------------------------------------------
# Ethiopia demand paths (fix reviewer 6.2: anchor at observed 2024 generation,
# ramp to the electrification target, then moderate growth).
# ----------------------------------------------------------------------------
def eth_phased(a2025, target2030, cagr_after, nT=NT):
    """17.75 TWh (2025) -> target by 2030 (electrification ramp) -> cagr_after/yr."""
    ramp = (target2030 / a2025) ** (1 / 5) - 1
    return [a2025 * (1 + ramp) ** t if t <= 5
            else target2030 * (1 + cagr_after) ** (t - 5) for t in range(nT)]

ETH_BASELINE = eth_phased(17.75e6, 40.0e6, 0.035)    # -> ~79.6 TWh in 2050
ETH_HIGH     = eth_phased(17.75e6, 40.0e6, 0.056)    # +50% over baseline at 2050
ETH_ACCESS   = [40.0e6 * 1.035 ** t for t in range(NT)]   # 40 TWh anchor -> ~94.5 TWh


# ----------------------------------------------------------------------------
# Scenario definitions (single source of truth; mirrors + fixes the notebooks)
# ----------------------------------------------------------------------------
def experiment_scenarios(cc):
    """Additive validation experiments requested in the re-review: temporal-resolution
    (B1), uncertainty decomposition (B2), and parameter sensitivities (B4). Each inherits
    the country's baseline policy setting. These do not feed the main tables."""
    base = {"emission_cap": 5.0e9} if cc == "ZWE" else {}
    exp = [
        dict(name="temporal_annual", label="Annual single block (B1)", annual_blocks=True, **base),
        dict(name="unc_deterministic", label="Deterministic demand and hydrology (B2)",
             det_demand=True, det_hydro=True, **base),
        dict(name="unc_demand_only", label="Stochastic demand only (B2)", det_hydro=True, **base),
        dict(name="unc_hydro_only", label="Stochastic hydrology only (B2)", det_demand=True, **base),
    ]
    sens = [("disc3", {"discount": 0.03}), ("disc8", {"discount": 0.08}),
            ("voll10k", {"voll": 10000.0}), ("voll40k", {"voll": 40000.0}),
            ("rm10", {"reserve_margin": 0.10}), ("rm20", {"reserve_margin": 0.20}),
            ("cc_lo", {"cap_credit_mult": 0.80}), ("cc_hi", {"cap_credit_mult": 1.20}),
            ("mudry_lo", {"mu_dry_mult": 0.85}), ("mudry_hi", {"mu_dry_mult": 1.15})]
    for tag, kw in sens:
        exp.append(dict(name=f"sens_{tag}", label=f"Sensitivity {tag} (B4)", **base, **kw))
    if cc == "ZWE":       # battery-storage sensitivity tests firm-fossil retention (B5)
        st = dict(storage_eff=STORAGE["eff"], storage_credit=STORAGE["credit"],
                  storage_fom=STORAGE["fom_usd_per_mw_yr"])
        exp += [
            dict(name="storage_zwe", label="Battery storage 4h, $1200/kW (B5)",
                 storage_capex=STORAGE["capex_usd_per_mw"], **base, **st),
            dict(name="storage_zwe_cheap", label="Battery storage 4h, $700/kW (B5)",
                 storage_capex=STORAGE["capex_cheap_usd_per_mw"], **base, **st),
        ]
    return exp


def scenarios(cc):
    if cc == "ETH":
        return [
            dict(name="baseline", label="Baseline"),
            dict(name="re_inv_minus30", label="RE investment -30%",
                 inv_mult={"Wind": 0.70, "Solar": 0.70}),
            dict(name="re_inv_minus50", label="RE investment -50%",
                 inv_mult={"Wind": 0.50, "Solar": 0.50}),
            dict(name="learning_curves", label="RE learning curves",
                 inv_learning={"Wind": [1.0, 0.65], "Solar": [1.0, 0.55]}),
            dict(name="solar_only_50", label="Solar-only investment -50%",
                 inv_mult={"Solar": 0.50}),
            # accelerated-access case: 40 TWh year-1 anchor (aspirational, reviewer 6.2)
            dict(name="accelerated_access", label="Accelerated access (40 TWh anchor)",
                 demand_path=ETH_ACCESS),
            # de-confounded high demand (fix 6.3): two variants, +50% over baseline at 2050
            dict(name="high_demand_fixedceil", label="High demand +50% (baseline ceilings)",
                 demand_path=ETH_HIGH),
            dict(name="high_demand_expanded", label="High demand +50% (expanded ceilings)",
                 demand_path=ETH_HIGH,
                 ub_override={"Hydro": 20000.0, "Wind": 15000.0, "Solar": 15000.0}),
            # fixed constrained-hydro (fix 6.4): cap NEW hydro build, not total < initial
            dict(name="constrained_hydro", label="Constrained hydro (new build <= 3 GW)",
                 new_build_cap={"Hydro": 3000.0},
                 inv_learning={"Wind": [1.0, 0.65], "Solar": [1.0, 0.55]}),
            dict(name="drought_stress", label="Persistent dry hydrology",
                 hydrology="dry_persistent"),
        ] + experiment_scenarios("ETH")
    else:  # ZWE
        return [
            dict(name="baseline", label="Baseline (5 Mt cap, no tax)",
                 emission_cap=5.0e9),
            dict(name="carbon_tax_30", label="Carbon tax $30/tCO2", carbon_tax=0.03),
            dict(name="carbon_tax_50", label="Carbon tax $50/tCO2", carbon_tax=0.05),
            # matched-stringency (fix 5.2): cap/budget calibrated in Julia to tax emissions
            dict(name="cap_matched_30", label="Cap matched to tax $30",
                 match_emissions_of="carbon_tax_30", instrument="annual_cap"),
            dict(name="cap_matched_50", label="Cap matched to tax $50",
                 match_emissions_of="carbon_tax_50", instrument="annual_cap"),
            dict(name="budget_matched_50", label="Budget matched to tax $50",
                 match_emissions_of="carbon_tax_50", instrument="cumulative_budget"),
            dict(name="emission_cap_glide", label="Glide cap 5->2 Mt/yr",
                 emission_cap=list(np.linspace(5.0e9, 2.0e9, NT))),
            dict(name="combined_tax50_solar", label="Tax $50 + concessional solar finance",
                 carbon_tax=0.05, inv_mult={"Solar": SOLAR_FIN_MULT},
                 finance_subsidy={"tech": "Solar", "mult": SOLAR_FIN_MULT}),
            dict(name="re_inv_m50", label="RE invest -50% (5 Mt cap, no tax)",
                 emission_cap=5.0e9, inv_mult={"Solar": 0.50, "Hydro": 0.50}),
            dict(name="high_demand", label="High demand +50% (5 Mt cap, no tax)",
                 emission_cap=5.0e9, demand_cagr=0.0469),
            # coal-exit decomposition (fix 5.4)
            dict(name="coal_retire_fixed", label="Coal exit: schedule only",
                 emission_cap=5.0e9, coal_retire="fixed"),
            dict(name="coal_retire_delayed", label="Coal exit: delayed schedule",
                 emission_cap=5.0e9, coal_retire="delayed"),
            dict(name="coal_retire_refurb", label="Coal exit: with refurbishment cost",
                 emission_cap=5.0e9, coal_retire="refurb"),
            dict(name="drought_stress", label="Persistent dry hydrology",
                 emission_cap=5.0e9, hydrology="dry_persistent"),
        ] + experiment_scenarios("ZWE")


def build(cc):
    d = load_country(cc)
    tech = d["tech"]
    # Ethiopia baseline demand: observed-anchored phased path (reviewer 6.2).
    # Zimbabwe keeps its Excel central path (already anchored at observed 8.71 TWh).
    central_base = ETH_BASELINE if cc == "ETH" else list(map(float, d["central"]))
    hyd = normalized_hydrology(cc)                 # fix A2: stationary-mean-one multipliers
    params = dict(
        country=cc,
        years=YEARS,
        tech=tech,
        nE=len(tech),
        discount=DISCOUNT,
        voll=VOLL,
        salvage=SALVAGE,
        a1=list(map(float, d["a1"])),
        a2=[list(map(float, row)) for row in d["a2"]],          # [NT][nE]
        b1=list(map(float, d["b1"])),
        xinit=list(map(float, d["xinit"])),
        EM=list(map(float, d["EM"])),
        alpha=list(map(float, d["alpha"])),
        accum=[list(map(float, row)) for row in d["accum"]],    # [NT][nE]
        central=list(map(float, central_base)),
        ub=[float(d["ub"][t]) for t in tech],
        lead=[int(LEAD[cc][t]) for t in tech],
        demand=DEMAND,
        hydro=hyd,
        blocks=dict(names=BLOCKS["names"], hours=BLOCKS[cc]["hours"],
                    demand_share=BLOCKS[cc]["demand_share"],
                    cf=block_cf(cc, tech, d["alpha"]),
                    season=BLOCK_SEASON),
        hydro_avail=HYDRO_AVAIL,
        water_wet=WATER_FRAC["wet"],
        water_dry=WATER_FRAC["dry"],
        reserve_margin=RESERVE_MARGIN,
        cap_short_price=CAP_SHORT_PRICE,
        cap_credit=[(DISPATCHABLE_AVAIL[t] if t in DISPATCHABLE_AVAIL else
                     HYDRO_AVAIL * hyd["mu"][0] if t == "Hydro" else
                     VRE_CREDIT.get(t, 0.0)) for t in tech],
        peak_factor=max(BLOCKS[cc]["demand_share"][b] / BLOCKS[cc]["hours"][b]
                        for b in range(len(BLOCKS[cc]["hours"]))),
        finance=dict(wacc_commercial=FINANCE["wacc_commercial"],
                     wacc_concessional=FINANCE["wacc_concessional"],
                     life=[int(FINANCE["life_years"][t]) for t in tech]),
        coal_refurb_usd_per_kw=COAL_REFURB_USD_PER_KW,
        demand_anchor_observed_TWh=d["observed"],
        demand_anchor_access_TWh=d["access"],
        scenarios=scenarios(cc),
    )
    # consistency check: energy-limited block CF reproduces annual alpha
    H = np.array(BLOCKS[cc]["hours"])
    for e, t in enumerate(tech):
        if t in DISPATCHABLE_AVAIL:
            continue
        recon = float((np.array(params["blocks"]["cf"][t]) * H).sum())
        assert abs(recon - d["alpha"][e]) < 1.0, f"{cc} {t}: cf*H={recon} vs alpha={d['alpha'][e]}"
    return params


if __name__ == "__main__":
    for cc in ("ETH", "ZWE"):
        p = build(cc)
        out = OUT / f"params_{cc}.json"
        out.write_text(json.dumps(p, indent=2))
        print(f"[export] {cc}: {len(p['scenarios'])} scenarios -> {out}")
