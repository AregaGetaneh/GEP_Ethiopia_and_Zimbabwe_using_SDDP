"""
Build the model parameter files (data/params_<CC>.json) from the country
workbooks and the calibrated hydrology contract. Run: python export_params.py
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent                 # model/
REPO = ROOT.parent                                     # repository root
DATA = REPO / "data"                                   # input workbooks and generated params
YEARS = list(range(2025, 2051))
NT = len(YEARS)                                        # 26 planning years

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
    },
    "ZWE": {
        "xlsx": "zimbabwe_data.xlsx",
        "tech": ["Hydro", "Coal", "Solar", "Gas", "Bioenergy"],
        "ub": {"Hydro": 5000.0, "Coal": 1900.0, "Solar": 5000.0,
               "Gas": 3000.0, "Bioenergy": 1000.0},
    },
}

# ----------------------------------------------------------------------------
# Calibrated model constants (see the accompanying paper, Section 4)
# ----------------------------------------------------------------------------
HOURS = 8760.0
# capacity factors f_e -> annual availability eta_e = 8760 f_e
CAPFACTOR = {
    "ETH": {"Hydro": 0.45, "Wind": 0.34, "Solar": 0.196, "Geothermal": 0.88, "Bioenergy": 0.55},
    "ZWE": {"Hydro": 0.50, "Coal": 0.55, "Solar": 0.203, "Gas": 0.50, "Bioenergy": 0.55},
}
DEMAND_ANCHOR_TWH = {"ETH": 25.18, "ZWE": 11.082}   # 2025 demand anchor (TWh)
# committed initial construction pipeline Q^init: {tech: {bucket_k: MW}}; bucket = years to commissioning
QINIT = {"ETH": {"Hydro": {"4": 1800.0}},   # Koysha 1800 MW, commissioning 2029 (k=4 at 2025)
         "ZWE": {}}                          # no committed project qualifies -> zero
# X^init override: the ZWE 5 MW gas seed is set to 0 so gas remains a purely investable technology.
XINIT_OVERRIDE = {"ETH": {}, "ZWE": {"Gas": 0.0}}
# calibrated hydrology contract produced by hydrology/scripts/calibrate_hydro.py
HYDRO_JSON = REPO / "hydrology" / "outputs" / "hydro_calibration.json"
CC_NAME = {"ETH": "Ethiopia", "ZWE": "Zimbabwe"}


def load_hydro_json(cc):
    j = json.loads(HYDRO_JSON.read_text())["countries"][CC_NAME[cc]]
    onehot = {"L": [1.0, 0.0, 0.0], "N": [0.0, 1.0, 0.0], "H": [0.0, 0.0, 1.0]}
    # P^H is rebuilt from the integer transition counts (exact row-stochastic);
    # the stored P_H_empirical is the same matrix rounded to 4 dp, whose rows can
    # sum to 1.0001 and would violate the MarkovianPolicyGraph row-sum requirement.
    N = j["transition_counts"]
    P = [[n / sum(row) for n in row] for row in N]
    return dict(states=["L", "N", "H"],
                mu=[float(x) for x in j["mu_normalized"]],
                P=P,
                init=onehot[j["h1"]],
                stationary=[float(x) for x in j["stationary_distribution"]],
                h1=j["h1"])


# Demand-deviation AR(1): u_{t+1} = rho * u_t + sigma_frac * Dbar_t * eps; parameters
# from an AR(1) fit to detrended log demand (2000-2023), pooled across countries.
DEMAND = {"rho": 0.81, "sigma_frac": 0.077, "n_nodes": 5}

# Construction lead times L_e (years)
LEAD = {
    "ETH": {"Hydro": 5, "Wind": 1, "Solar": 1, "Geothermal": 3, "Bioenergy": 1},
    "ZWE": {"Hydro": 5, "Coal": 4, "Solar": 1, "Gas": 3, "Bioenergy": 2},
}

# Finance: concessional versus commercial WACC and technology life. Used to derive the
# effective solar CAPEX multiplier for the concessional-financing scenario.
FINANCE = {"wacc_commercial": 0.12, "wacc_concessional": 0.06,
           "life_years": {"Hydro": 40, "Wind": 25, "Solar": 25,
                          "Geothermal": 30, "Coal": 35, "Gas": 30,
                          "Bioenergy": 25}}


def crf(w, n):
    """Capital recovery factor for WACC w and life n."""
    return w * (1 + w) ** n / ((1 + w) ** n - 1)


# Effective CAPEX multiplier for a concessional vs commercial WACC (ratio of capital-
# recovery factors).
SOLAR_FIN_MULT = round(crf(FINANCE["wacc_concessional"], FINANCE["life_years"]["Solar"]) /
                       crf(FINANCE["wacc_commercial"], FINANCE["life_years"]["Solar"]), 4)

DISCOUNT = 0.05
VOLL = 10000.0                     # $/MWh central value (literature-standard; sensitivities 5k/20k)
SALVAGE = True                     # remaining-life salvage credit at the terminal stage
COAL_REFURB_USD_PER_KW = 600.0     # existing-coal life-extension cost


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
                alpha=alpha, accum=accum, central=central, ub=cfg["ub"])


# ----------------------------------------------------------------------------
# Ethiopia demand paths: anchor at the 2025 value, ramp to the electrification
# target by 2030, then grow at a moderate rate.
# ----------------------------------------------------------------------------
def eth_phased(a2025, target2030, cagr_after, nT=NT):
    """2025 anchor -> target by 2030 (electrification ramp) -> cagr_after per year."""
    ramp = (target2030 / a2025) ** (1 / 5) - 1
    return [a2025 * (1 + ramp) ** t if t <= 5
            else target2030 * (1 + cagr_after) ** (t - 5) for t in range(nT)]

ETH_BASELINE = eth_phased(DEMAND_ANCHOR_TWH["ETH"] * 1e6, 40.0e6, 0.035)  # 25.18 -> ~79.6 TWh 2050
ETH_HIGH     = eth_phased(DEMAND_ANCHOR_TWH["ETH"] * 1e6, 40.0e6, 0.056)  # +50% at 2050
# accelerated access: start at the 25.18 TWh 2025 anchor (feasible first stage) and
# electrify faster than baseline -- 50 TWh by 2030 (vs baseline 40) -> ~96 TWh 2050.
ETH_ACCESS   = eth_phased(DEMAND_ANCHOR_TWH["ETH"] * 1e6, 50.0e6, 0.033)


# ----------------------------------------------------------------------------
# Scenario definitions
# ----------------------------------------------------------------------------
def experiment_scenarios(cc):
    """Supplementary experiments: uncertainty decomposition and parameter
    sensitivities. Each inherits the country's unconstrained least-cost baseline
    (no background emission cap for either country) and does not feed the main
    result tables."""
    base = {}
    exp = [
        dict(name="unc_deterministic", label="Deterministic demand and hydrology",
             det_demand=True, det_hydro=True, **base),
        dict(name="unc_demand_only", label="Stochastic demand only", det_hydro=True, **base),
        dict(name="unc_hydro_only", label="Stochastic hydrology only", det_demand=True, **base),
    ]
    sens = [("disc3", {"discount": 0.03}), ("disc8", {"discount": 0.08}),
            ("voll5k", {"voll": 5000.0}), ("voll20k", {"voll": 20000.0}),
            ("mudry_lo", {"mu_dry_mult": 0.85}), ("mudry_hi", {"mu_dry_mult": 1.15})]
    for tag, kw in sens:
        exp.append(dict(name=f"sens_{tag}", label=f"Sensitivity {tag}", **base, **kw))
    return exp


def robustness_scenarios(cc):
    """Numerical and stochastic discretization checks on the baseline: demand
    quadrature and hydrology persistence. Each variant inherits the country's
    unconstrained least-cost baseline (no emission cap for either country),
    keeping the comparison with the baseline like-for-like."""
    cap = {}
    return [
        dict(name="gh9", label="Nine Gauss-Hermite nodes", gh_nodes=9, **cap),
        dict(name="hydro_persist_hi", label="More persistent hydrology", hydro_persist=0.2, **cap),
        dict(name="hydro_persist_lo", label="Less persistent hydrology", hydro_persist=-0.2, **cap),
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
            # accelerated-access case: rapid electrification (50 TWh by 2030) with expanded
            # build ceilings so the higher demand can be supplied rather than shed.
            dict(name="accelerated_access", label="Accelerated access (50 TWh by 2030)",
                 demand_path=ETH_ACCESS,
                 ub_override={"Hydro": 20000.0, "Wind": 15000.0, "Solar": 15000.0}),
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
            # baseline is the unconstrained least-cost counterfactual (no cap, no tax),
            # exactly like Ethiopia. An emission cap only enters where it is a policy.
            dict(name="baseline", label="Baseline (least-cost, no policy)"),
            dict(name="carbon_tax_30", label="Carbon tax $30/tCO2", carbon_tax=0.03),
            dict(name="carbon_tax_50", label="Carbon tax $50/tCO2", carbon_tax=0.05),
            # emissions-matched annual cap: the annual cap follows the tax scenario's
            # expected annual emissions path (trajectory match), so it is feasible in
            # every year (no 2025 cliff) and matches the tax year by year.
            dict(name="cap_matched_30", label="Cap matched to tax $30 (annual path)",
                 match_emissions_of="carbon_tax_30", instrument="annual_cap", match_mode="trajectory"),
            dict(name="cap_matched_50", label="Cap matched to tax $50 (annual path)",
                 match_emissions_of="carbon_tax_50", instrument="annual_cap", match_mode="trajectory"),
            # declining-cap policy. The 8 Mt start is set just above the ~7.1 Mt 2025
            # uncontrolled level (u_1=0, h_1 known), so year one is a non-binding envelope
            # and there is no forced first-stage shedding; the cap then tightens linearly
            # to 3 Mt in 2050, an illustrative deep-decarbonization target of roughly a
            # 60% cut from the 2025 physical level (stated policy target, not a fitted value).
            dict(name="emission_cap_glide", label="Glide cap 8->3 Mt/yr",
                 emission_cap=list(np.linspace(8.0e9, 3.0e9, NT))),
            dict(name="combined_tax50_solar", label="Tax $50 + concessional solar financing",
                 carbon_tax=0.05, inv_mult={"Solar": SOLAR_FIN_MULT},
                 finance_subsidy={"tech": "Solar", "mult": SOLAR_FIN_MULT},
                 # this case approaches the 3% gap more slowly than the others; its
                 # iteration budget is raised above the production default so that it
                 # terminates on the gap rather than on the iteration cap.
                 iteration_limit=1500),
            dict(name="re_inv_m50", label="RE invest -50% (no policy)",
                 inv_mult={"Solar": 0.50, "Hydro": 0.50}),
            dict(name="high_demand", label="High demand (4.69%/yr, no policy)",
                 demand_cagr=0.0469),
            # coal-exit decomposition: the retirement schedule IS the policy (no extra cap)
            dict(name="coal_retire_fixed", label="Coal exit: schedule only",
                 coal_retire="fixed"),
            dict(name="coal_retire_delayed", label="Coal exit: delayed schedule",
                 coal_retire="delayed"),
            dict(name="coal_retire_refurb", label="Coal exit: with refurbishment cost",
                 coal_retire="refurb"),
            dict(name="drought_stress", label="Persistent dry hydrology",
                 hydrology="dry_persistent"),
        ] + experiment_scenarios("ZWE") + robustness_scenarios("ZWE")


def build(cc):
    d = load_country(cc)
    tech = d["tech"]
    # demand: re-anchor the 2025 value to the calibrated anchor, preserving growth shape.
    anchor = DEMAND_ANCHOR_TWH[cc] * 1e6                       # TWh -> MWh
    if cc == "ETH":
        central_base = ETH_BASELINE                           # phased from the 25.18 TWh anchor
    else:
        w = list(map(float, d["central"]))                    # workbook path
        s = anchor / w[0] if w[0] > 0 else 1.0
        central_base = [x * s for x in w]                     # scale to the 11.082 TWh anchor
    eta = [HOURS * CAPFACTOR[cc][t] for t in tech]            # eta_e = 8760 f_e
    xinit = list(map(float, d["xinit"]))
    for t, v in XINIT_OVERRIDE.get(cc, {}).items():
        xinit[tech.index(t)] = float(v)                       # ZWE gas 5 -> 0
    hyd = load_hydro_json(cc)                                 # calibrated hydrology contract
    params = dict(
        country=cc, years=YEARS, tech=tech, nE=len(tech),
        discount=DISCOUNT, voll=VOLL, salvage=SALVAGE,
        a1=list(map(float, d["a1"])),
        a2=[list(map(float, row)) for row in d["a2"]],        # [NT][nE]  $/MW
        b1=list(map(float, d["b1"])),
        xinit=xinit,
        EM=list(map(float, d["EM"])),
        eta=eta,                                              # MWh/MW-yr
        qinit=QINIT[cc],                                      # {tech: {bucket_k: MW}}
        accum=[list(map(float, row)) for row in d["accum"]],  # [NT][nE] mandatory retirement floor
        central=list(map(float, central_base)),
        ub=[float(d["ub"][t]) for t in tech],
        lead=[int(LEAD[cc][t]) for t in tech],
        demand=DEMAND,
        hydro=hyd,                                            # mu (normalized), P (empirical), init (h1), stationary
        coal_refurb_usd_per_kw=COAL_REFURB_USD_PER_KW,
        demand_anchor_2025_TWh=DEMAND_ANCHOR_TWH[cc],
        scenarios=scenarios(cc),
    )
    return params


if __name__ == "__main__":
    for cc in ("ETH", "ZWE"):
        p = build(cc)
        out = DATA / f"params_{cc}.json"
        out.write_text(json.dumps(p, indent=2))
        print(f"[export] {cc}: {len(p['scenarios'])} scenarios -> {out}")
