"""
Build the model-ready parameter files for the SDDP generation-expansion model.

Reads the country workbooks in ``data/``, applies the calibration described in
the accompanying paper (seasonal operating blocks, three-state hydrology,
lead-time construction pipeline, demand-deviation process, finance, salvage),
and writes one JSON file per country together with its scenario list. The Julia
model reads these JSON files directly and never opens the workbooks.

Run:  python export_params.py
Out:  params_ETH.json , params_ZWE.json
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent                 # repository root
DATA = ROOT / "data"                                   # input workbooks
YEARS = list(range(2025, 2051))
NT = len(YEARS)                                         # 26 planning years

# ----------------------------------------------------------------------------
# Country base configuration
# ----------------------------------------------------------------------------
COUNTRY = {
    "ETH": {
        "xlsx": "ethiopia_data.xlsx",
        "tech": ["Hydro", "Wind", "Solar", "Geothermal", "Bioenergy"],
        # resource ceilings (MW) used by the scenario runner
        "ub": {"Hydro": 15000.0, "Wind": 10000.0, "Solar": 10000.0,
               "Geothermal": 5000.0, "Bioenergy": 3000.0},
        # observed 2024 generation anchor (TWh) and accelerated-access anchor (TWh)
        "demand_anchor_observed_TWh": 17.75,
        "demand_anchor_access_TWh": 40.0,
    },
    "ZWE": {
        "xlsx": "zimbabwe_data.xlsx",
        "tech": ["Hydro", "Coal", "Solar", "Gas", "Bioenergy"],
        "ub": {"Hydro": 5000.0, "Coal": 1900.0, "Solar": 5000.0,
               "Gas": 3000.0, "Bioenergy": 1000.0},
        "demand_anchor_observed_TWh": 8.71,
        "demand_anchor_access_TWh": 8.71,
    },
}

# ----------------------------------------------------------------------------
# Calibration constants
# ----------------------------------------------------------------------------
# Demand-deviation AR(1):  u_{t+1} = rho * u_t + sigma_frac * Dbar_t * eps.
# Persistence and innovation scale are estimated from an AR(1) fit to detrended
# log annual electricity demand for the two countries over 2000-2023; common
# pooled values are adopted given the short annual samples.
DEMAND = {"rho": 0.81, "sigma_frac": 0.077, "n_nodes": 5}

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
    """Normalize the regime multipliers so the stationary-weighted mean water
    energy equals one. This keeps alpha_e as the long-run average hydro energy
    factor rather than letting the Markov chain shift the mean away from the
    calibration. The stage-1 regime distribution is the stationary distribution."""
    pi = stationary_dist(HYDRO["P"])
    mu_raw = HYDRO["mu_raw"][cc]
    mean = float(np.dot(pi, mu_raw))
    mu = [m / mean for m in mu_raw]
    return dict(states=HYDRO["states"], init=pi.tolist(), P=HYDRO["P"],
                mu=mu, mu_raw=mu_raw, stationary=pi.tolist(), raw_mean=mean)

# Construction lead times L_e (years)
LEAD = {
    "ETH": {"Hydro": 5, "Wind": 1, "Solar": 1, "Geothermal": 3, "Bioenergy": 1},
    "ZWE": {"Hydro": 5, "Coal": 4, "Solar": 1, "Gas": 3, "Bioenergy": 2},
}

# Seasonal operating blocks, order: [wet-peak, wet-off, dry-peak, dry-off].
# Peak blocks hold about 20% of each season's hours and carry about 26-28% of
# its demand, so firm capacity is valued without forcing artificial scarcity.
BLOCKS = {
    "names": ["wet_peak", "wet_off", "dry_peak", "dry_off"],
    "ETH": {"hours": [584.0, 2336.0, 1168.0, 4672.0],
            "demand_share": [0.109, 0.311, 0.151, 0.429]},
    "ZWE": {"hours": [876.0, 3504.0, 876.0, 3504.0],
            "demand_share": [0.134, 0.346, 0.146, 0.374]},
}

# Two availability regimes.
#  Energy-limited technologies (variable renewables and hydro): the block
#  capacity factor is a resource shape, rescaled per technology so its
#  duration-weighted average reproduces the annual factor alpha_e from the
#  workbook. Solar is low in the peak (evening) blocks, which is what makes
#  adequacy a binding constraint. Hydro is additionally scaled by the hydrology
#  regime multiplier inside the Julia model.
ENERGY_LIMITED = {
    "ETH": {"Hydro": [0.60, 0.55, 0.42, 0.40],
            "Wind":  [0.30, 0.32, 0.36, 0.36],
            "Solar": [0.10, 0.26, 0.14, 0.30]},
    "ZWE": {"Hydro": [0.62, 0.58, 0.40, 0.38],
            "Solar": [0.10, 0.24, 0.14, 0.30]},
}
#  Dispatchable technologies: block availability is a flat power limit (not an
#  annual energy cap); annual output is determined by dispatch economics.
DISPATCHABLE_AVAIL = {"Coal": 0.85, "Gas": 0.90, "Bioenergy": 0.80, "Geothermal": 0.90}

# Reservoir hydro: a turbine power limit in every block plus a seasonal
# water-energy budget. Water can be dispatched to the peak within a season, but
# the seasonal total is capped and the dry season is scarcer. The hydrology
# regime multiplier scales the water budget to represent drought risk.
HYDRO_AVAIL = 0.90                      # turbine power availability (fraction of nameplate)
WATER_FRAC = {"wet": 0.60, "dry": 0.40}  # share of annual water energy by season
BLOCK_SEASON = [1, 1, 2, 2]            # 1 = wet, 2 = dry, matching block order

# Planning reserve margin. Firm capacity, rated at dry-year firm output, must
# cover peak demand plus a margin. The requirement is soft: a capacity-shortfall
# variable priced at a peaker-like cost yields a bounded adequacy signal rather
# than a value-of-lost-load energy blow-up when an emissions limit binds.
RESERVE_MARGIN = 0.15                   # firm capacity >= (1+margin) * peak load
CAP_SHORT_PRICE = 300000.0             # $/MW-yr penalty for unmet reserve
VRE_CREDIT = {"Solar": 0.05, "Wind": 0.10}   # capacity credit of variable renewables at the peak

# Finance: concessional versus commercial WACC and technology life
FINANCE = {"wacc_commercial": 0.12, "wacc_concessional": 0.06,
           "life_years": {"Hydro": 40, "Wind": 25, "Solar": 25,
                          "Geothermal": 30, "Coal": 35, "Gas": 30,
                          "Bioenergy": 25}}


def crf(w, n):
    """Capital recovery factor for WACC w and life n."""
    return w * (1 + w) ** n / ((1 + w) ** n - 1)


# Effective CAPEX multiplier for moving solar from a commercial to a
# concessional WACC. The ratio of capital-recovery factors is the effective
# overnight-CAPEX reduction that cheaper capital delivers.
SOLAR_FIN_MULT = round(crf(FINANCE["wacc_concessional"], FINANCE["life_years"]["Solar"]) /
                       crf(FINANCE["wacc_commercial"], FINANCE["life_years"]["Solar"]), 4)

DISCOUNT = 0.05
VOLL = 20000.0                     # $/MWh
SALVAGE = True                     # remaining-life salvage credit at the terminal stage
COAL_REFURB_USD_PER_KW = 600.0     # existing-coal life-extension cost

# Battery storage sensitivity: 4-hour Li-ion, bundled power CAPEX ($/MW),
# round-trip efficiency, firm capacity credit, and fixed O&M. Two cost points
# bracket the plausible range.
STORAGE = {"capex_usd_per_mw": 1_200_000.0, "capex_cheap_usd_per_mw": 700_000.0,
           "eff": 0.85, "credit": 0.90, "fom_usd_per_mw_yr": 25_000.0}


# ----------------------------------------------------------------------------
# Workbook loading
# ----------------------------------------------------------------------------
def load_country(cc):
    cfg = COUNTRY[cc]
    xlsx = DATA / cfg["xlsx"]
    tech = cfg["tech"]

    inv = pd.read_excel(xlsx, sheet_name="InvCost($kW)")
    inv.columns = [str(c).strip() for c in inv.columns]
    a2 = inv[tech].iloc[:NT].to_numpy(float) * 1000.0          # $/kW -> $/MW, [NT x nE]

    cost = pd.read_excel(xlsx, sheet_name="Cost1")
    cost.columns = [str(c).strip() for c in cost.columns]
    gc = "GenCost ($/MWh)" if "GenCost ($/MWh)" in cost.columns else "GenCost"
    a1 = cost[gc].iloc[:len(tech)].to_numpy(float)             # $/MWh variable operating cost
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
    """Block capacity factors. Energy-limited technologies (variable renewables,
    hydro) are rescaled so sum_b cf[e,b]*H[b] == alpha_e (the annual resource).
    Dispatchable technologies get a flat availability (power limit), so their
    block CF*H does not equal alpha_e."""
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
# Ethiopia demand paths: anchor at observed 2024 generation, ramp to the
# electrification target by 2030, then grow at a moderate rate.
# ----------------------------------------------------------------------------
def eth_phased(a2025, target2030, cagr_after, nT=NT):
    """17.75 TWh (2025) -> target by 2030 (electrification ramp) -> cagr_after per year."""
    ramp = (target2030 / a2025) ** (1 / 5) - 1
    return [a2025 * (1 + ramp) ** t if t <= 5
            else target2030 * (1 + cagr_after) ** (t - 5) for t in range(nT)]

ETH_BASELINE = eth_phased(17.75e6, 40.0e6, 0.035)    # -> ~79.6 TWh in 2050
ETH_HIGH     = eth_phased(17.75e6, 40.0e6, 0.056)    # +50% over baseline at 2050
ETH_ACCESS   = [40.0e6 * 1.035 ** t for t in range(NT)]   # 40 TWh anchor -> ~94.5 TWh


# ----------------------------------------------------------------------------
# Scenario definitions
# ----------------------------------------------------------------------------
def experiment_scenarios(cc):
    """Supplementary experiments: temporal resolution, uncertainty decomposition,
    and parameter sensitivities. Each inherits the country's baseline policy
    setting and does not feed the main result tables."""
    base = {"emission_cap": 5.0e9} if cc == "ZWE" else {}
    exp = [
        dict(name="temporal_annual", label="Annual single block", annual_blocks=True, **base),
        dict(name="unc_deterministic", label="Deterministic demand and hydrology",
             det_demand=True, det_hydro=True, **base),
        dict(name="unc_demand_only", label="Stochastic demand only", det_hydro=True, **base),
        dict(name="unc_hydro_only", label="Stochastic hydrology only", det_demand=True, **base),
    ]
    sens = [("disc3", {"discount": 0.03}), ("disc8", {"discount": 0.08}),
            ("voll10k", {"voll": 10000.0}), ("voll40k", {"voll": 40000.0}),
            ("rm10", {"reserve_margin": 0.10}), ("rm20", {"reserve_margin": 0.20}),
            ("cc_lo", {"cap_credit_mult": 0.80}), ("cc_hi", {"cap_credit_mult": 1.20}),
            ("mudry_lo", {"mu_dry_mult": 0.85}), ("mudry_hi", {"mu_dry_mult": 1.15})]
    for tag, kw in sens:
        exp.append(dict(name=f"sens_{tag}", label=f"Sensitivity {tag}", **base, **kw))
    if cc == "ZWE":       # battery-storage sensitivity on firm-fossil retention
        st = dict(storage_eff=STORAGE["eff"], storage_credit=STORAGE["credit"],
                  storage_fom=STORAGE["fom_usd_per_mw_yr"])
        exp += [
            dict(name="storage_zwe", label="Battery storage 4h, $1200/kW",
                 storage_capex=STORAGE["capex_usd_per_mw"], **base, **st),
            dict(name="storage_zwe_cheap", label="Battery storage 4h, $700/kW",
                 storage_capex=STORAGE["capex_cheap_usd_per_mw"], **base, **st),
        ]
    return exp


def robustness_scenarios(cc):
    """Numerical and stochastic discretization checks on the baseline: temporal
    resolution, demand quadrature, hydrology persistence, and risk attitude.
    For Zimbabwe the baseline emission cap is retained only for the block-structure
    variant, matching the runs behind the robustness table."""
    return [
        dict(name="temporal_annual_fixedpeak", label="Annual block, four-block peak",
             annual_blocks=True, keep_peak=True,
             **({"emission_cap": 5.0e9} if cc == "ZWE" else {})),
        dict(name="risk_cvar", label="Risk-averse (mean-CVaR, lambda=0.5, beta=0.1)",
             risk_lambda=0.5, risk_beta=0.1),
        dict(name="blocks_12", label="Twelve operating blocks", blocks_12=True),
        dict(name="gh9", label="Nine Gauss-Hermite nodes", gh_nodes=9),
        dict(name="hydro_persist_hi", label="More persistent hydrology", hydro_persist=0.2),
        dict(name="hydro_persist_lo", label="Less persistent hydrology", hydro_persist=-0.2),
    ]


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
            # accelerated-access case: 40 TWh first-year anchor
            dict(name="accelerated_access", label="Accelerated access (40 TWh anchor)",
                 demand_path=ETH_ACCESS),
            # high-demand variants: +50% over baseline at 2050, with and without expanded ceilings
            dict(name="high_demand_fixedceil", label="High demand +50% (baseline ceilings)",
                 demand_path=ETH_HIGH),
            dict(name="high_demand_expanded", label="High demand +50% (expanded ceilings)",
                 demand_path=ETH_HIGH,
                 ub_override={"Hydro": 20000.0, "Wind": 15000.0, "Solar": 15000.0}),
            # constrained hydro caps new hydro build rather than total capacity
            dict(name="constrained_hydro", label="Constrained hydro (new build <= 3 GW)",
                 new_build_cap={"Hydro": 3000.0},
                 inv_learning={"Wind": [1.0, 0.65], "Solar": [1.0, 0.55]}),
            dict(name="drought_stress", label="Persistent dry hydrology",
                 hydrology="dry_persistent"),
        ] + experiment_scenarios("ETH") + robustness_scenarios("ETH")
    else:  # ZWE
        return [
            dict(name="baseline", label="Baseline (5 Mt cap, no tax)",
                 emission_cap=5.0e9),
            dict(name="carbon_tax_30", label="Carbon tax $30/tCO2", carbon_tax=0.03),
            dict(name="carbon_tax_50", label="Carbon tax $50/tCO2", carbon_tax=0.05),
            # matched-stringency cap and budget, calibrated in Julia to the tax emissions
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
                 finance_subsidy={"tech": "Solar", "mult": SOLAR_FIN_MULT},
                 # this case does not reach the 3% gap within 200 iterations; raise its limit
                 iteration_limit=400),
            dict(name="re_inv_m50", label="RE invest -50% (5 Mt cap, no tax)",
                 emission_cap=5.0e9, inv_mult={"Solar": 0.50, "Hydro": 0.50}),
            dict(name="high_demand", label="High demand +50% (5 Mt cap, no tax)",
                 emission_cap=5.0e9, demand_cagr=0.0469),
            # coal-exit decomposition
            dict(name="coal_retire_fixed", label="Coal exit: schedule only",
                 emission_cap=5.0e9, coal_retire="fixed"),
            dict(name="coal_retire_delayed", label="Coal exit: delayed schedule",
                 emission_cap=5.0e9, coal_retire="delayed"),
            dict(name="coal_retire_refurb", label="Coal exit: with refurbishment cost",
                 emission_cap=5.0e9, coal_retire="refurb"),
            dict(name="drought_stress", label="Persistent dry hydrology",
                 emission_cap=5.0e9, hydrology="dry_persistent"),
        ] + experiment_scenarios("ZWE") + robustness_scenarios("ZWE")


def build(cc):
    d = load_country(cc)
    tech = d["tech"]
    # Ethiopia baseline demand uses the observed-anchored phased path; Zimbabwe
    # keeps its workbook central path, already anchored at observed 8.71 TWh.
    central_base = ETH_BASELINE if cc == "ETH" else list(map(float, d["central"]))
    hyd = normalized_hydrology(cc)                 # stationary-mean-one multipliers
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
    # consistency check: the energy-limited block CF reproduces the annual alpha
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
        out = ROOT / f"params_{cc}.json"
        out.write_text(json.dumps(p, indent=2))
        print(f"[export] {cc}: {len(p['scenarios'])} scenarios -> {out}")
