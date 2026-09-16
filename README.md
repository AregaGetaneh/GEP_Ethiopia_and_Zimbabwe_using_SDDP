# Optimal power expansion planning under uncertainty in sub-Saharan Africa

Replication code and data for the paper *Optimal power expansion planning under
uncertainty in sub-Saharan Africa: A stochastic dual dynamic programming
approach*. The study builds a multistage stochastic generation expansion model
for two contrasting power systems, Ethiopia (hydro-rich) and Zimbabwe
(coal-legacy with carbon policy), over 2025-2050, and solves it with stochastic
dual dynamic programming (SDDP.jl and Gurobi).

Repository: <https://github.com/AregaGetaneh/GEP_Ethiopia_and_Zimbabwe_using_SDDP>

## Two reproduction routes

- **Fast route -- Python only, no solver.** The solved results are archived in
  `results/ETH.json` and `results/ZWE.json`, so every manuscript figure and table
  is regenerated with Python alone.
- **Full re-solution -- Julia and Gurobi.** Re-solving every scenario from the
  input data requires Julia, the pinned package environment, and a working Gurobi
  installation and license.
- **Hydrology rebuild -- Copernicus access.** Rebuilding the inflow calibration
  from the original GloFAS product requires a free Copernicus account. The
  calibrated contract the model consumes is already included, so this is optional.

## Repository layout

```
model/                         the SDDP model and the input builder
  export_params.py           read data/*.xlsx + hydrology contract -> data/params_<CC>.json
  gep_sddp.jl                the annual SDDP model (solve one scenario -> results/<CC>/)
  run_production.jl          manifest-driven driver: solve a scenario group or all
  benchmarks.jl              value of adaptive planning (VSS) and of perfect information (EVPI)

analysis/                    figures and tables from the solved results (Python, no solver)
  figures.py                 figure PDFs                 -> figures/  (generated)
  tables.py                  all result tables (LaTeX)   -> tables/tex/ (generated)
  consolidate.py             bundle per-scenario solver output -> results/<CC>.json

data/                        input data and generated model inputs
  ethiopia_data.xlsx         Ethiopia technology, cost, retirement, and demand workbook
  zimbabwe_data.xlsx         Zimbabwe workbook
  params_ETH.json            model inputs built by export_params.py
  params_ZWE.json

hydrology/                   inflow-calibration sub-package (see hydrology/README.md)
  scripts/                   download and calibration code
  outputs/                   calibrated contract read by export_params.py
  data/derived/              annual discharge, states, and transition tables

results/                     consolidated solved results, one file per country
  ETH.json  ZWE.json         all scenarios and the benchmarks for each country

hpc/                         cluster submission
  submit_country.lsf         LSF job script: solve one country, then its benchmarks

Project.toml, Manifest.toml  Julia dependencies (exact pinned versions)
requirements.txt             Python dependencies (pinned)
LICENSE                      MIT license for the code
```

`<CC>` is the country code, `ETH` or `ZWE`. Figures and tables are not versioned;
they are regenerated from `results/<CC>.json` by the analysis scripts.

## Fast reproduction (Python only)

```
pip install -r requirements.txt
python analysis/figures.py      # manuscript figures  -> figures/
python analysis/tables.py       # all result tables   -> tables/tex/
```

| Command | Inputs | Outputs | Solver |
|---|---|---|---|
| `python analysis/figures.py` | `results/{ETH,ZWE}.json`, `data/params_*.json` | 13 manuscript figure PDFs in `figures/` | none |
| `python analysis/figures.py --all` | same | the 13 manuscript figures plus 6 supplementary figures | none |
| `python analysis/tables.py` | `results/{ETH,ZWE}.json` | 13 LaTeX table fragments in `tables/tex/` | none |
| `python model/export_params.py` | `data/*.xlsx`, `hydrology/outputs/hydro_calibration.json` | `data/params_{ETH,ZWE}.json` | none |

`export_params.py` is optional in this route: the shipped `data/params_*.json` are already the exact inputs behind
the archived results, and re-running the builder reproduces them byte-for-byte.

## Full re-solution (Julia and Gurobi)

Run from the repository root, in order. Steps 2-4 require Gurobi.

| Step | Command | Inputs | Outputs | Solver |
|---|---|---|---|---|
| 1 | `python model/export_params.py` | `data/*.xlsx`, hydrology contract | `data/params_<CC>.json` | none |
| 2 | `julia --project=. model/run_production.jl ETH all`<br>`julia --project=. model/run_production.jl ZWE all` | `data/params_<CC>.json` | `results/<CC>/<group>/<scenario>.json` | Gurobi |
| 3 | `julia --project=. model/benchmarks.jl ETH results/ETH/benchmarks`<br>`julia --project=. model/benchmarks.jl ZWE results/ZWE/benchmarks` | `data/params_<CC>.json` | `results/<CC>/benchmarks/benchmarks.json` | Gurobi |
| 4 | `python analysis/consolidate.py` | per-scenario `results/<CC>/**` | `results/<CC>.json` | none |
| 5 | `python analysis/figures.py`<br>`python analysis/tables.py` | `results/<CC>.json` | `figures/`, `tables/tex/` | none |

A single scenario or one policy group can be solved instead of `all`, for example
`julia --project=. model/run_production.jl ZWE carbon_tax`. The forward-pass seed is
fixed inside `gep_sddp.jl` for reproducible training, and Gurobi runs with its
default settings. Because the linear programs can admit alternative optimal bases,
capacity splits at a fixed cost may vary marginally across solver versions or
thread counts. The SDDP training procedure is Algorithm 1 in the paper appendix.

## Software environment

- **Julia 1.10.4** with the packages in `Project.toml` (SDDP, JuMP, Gurobi, JSON,
  and standard-library packages), pinned to exact versions in `Manifest.toml`.
- **Gurobi 13.0.2**, a commercial solver that is free for academic use. Only the
  solving steps (`run_production.jl`, `benchmarks.jl`, `gep_sddp.jl`) need Gurobi.
- **Python 3.13** with the packages in `requirements.txt` (pandas, numpy,
  matplotlib, openpyxl). The Python layer does not need Gurobi.

Recreate the environments from the repository root:

```
julia --project=. -e 'using Pkg; Pkg.instantiate()'   # installs the pinned Julia packages
python -m venv .venv && . .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Hardware and runtime

Results were produced on a Linux x86-64 cluster under Julia 1.10.4 and Gurobi
13.0.2. Each scenario needs one CPU core and roughly 2 to 4 GB of memory; no GPU
is required. A baseline scenario trains on a single core in a few minutes; the
exact wall time depends on the Gurobi license mode (a node-locked license is
faster than a floating token server) and on the per-scenario iteration count,
which is recorded together with the wall time in each scenario's provenance in
`results/<CC>.json`. Scenarios are independent and can be submitted as separate
jobs.

## Manuscript artifacts

`python analysis/figures.py` writes the 13 figure files used in the manuscript
(`--all` adds six supplementary figures not shown in the paper). All figures read
`results/{ETH,ZWE}.json`; the cost-decomposition figure additionally reads
`data/params_<CC>.json`.

| Manuscript figure | Generated file(s) | `figures.py` function |
|---|---|---|
| Fig. 1  Baseline installed capacity | `F1b_ETH_capacity_baseline.pdf`, `F1b_ZWE_capacity_baseline.pdf` | `fig_baseline_captraj` |
| Fig. 2  Installed capacity across Ethiopia scenarios | `F1_ETH_capacity_traj.pdf` | `fig_capacity_traj` |
| Fig. 3  Technology capacity trajectories, Ethiopia | `F1c_ETH_build_traj.pdf` | `fig_build_traj` |
| Fig. 4  Generation by technology, Ethiopia scenarios | `F2_ETH_genmix_traj.pdf` | `fig_genmix_traj` |
| Fig. 5  Resource cost decomposition | `F5_ETH_cost_decomp.pdf`, `F5_ZWE_cost_decomp.pdf` | `fig_cost_decomp` |
| Fig. 6  Installed capacity across Zimbabwe scenarios | `F1_ZWE_capacity_traj.pdf` | `fig_capacity_traj` |
| Fig. 7  Technology capacity trajectories, Zimbabwe | `F1c_ZWE_build_traj.pdf` | `fig_build_traj` |
| Fig. 8  Annual emissions across Zimbabwe policies | `F7_ZWE_emissions_traj.pdf` | `fig_zwe_emissions_traj` |
| Fig. 9  Generation by technology, Zimbabwe 2050 | `F4_ZWE_genmix2050.pdf` | `fig_genmix2050` |
| Fig. 10  Expected unserved energy across scenarios | `F11_adequacy.pdf` | `fig_adequacy` |
| Fig. 11  Baseline annual system cost | `F12_annual_cost.pdf` | `fig_annual_cost` |

`python analysis/tables.py` writes all result tables (13 fragments) from
`results/{ETH,ZWE}.json`. The literature-positioning, notation, and
technology-parameter tables (manuscript Tables 1, 2, and 3) are input and
reference tables maintained directly in the manuscript and are not generated
here. Manuscript table numbers below follow the published numbering, in which
the appendix tables carry the prefixes A, B, and C.

| Manuscript table | Generated fragment |
|---|---|
| Tab. 4  Ethiopia scenario results | `tables/tex/T1_eth.tex` |
| Tab. 5  Zimbabwe policy outcomes | `tables/tex/T2_zwe.tex` |
| Tab. 6  Value-of-adaptive-planning benchmarks | `tables/tex/T4_vss.tex` |
| Tab. 7  Cross-country baseline comparison | `tables/tex/T3_cross.tex` |
| Tab. 8  One-at-a-time sensitivity | `tables/tex/sensitivity.tex` |
| Tab. B.13  SDDP training diagnostics | `tables/tex/convergence.tex` |
| Tab. B.14  Robustness checks | `tables/tex/robustness.tex` |
| Tab. C.15  Value of adaptive planning under alternative VoLL | `tables/tex/voll_value_of_adaptive_planning.tex` |
| Tab. C.16  Uncertainty decomposition | `tables/tex/uncertainty_decomposition.tex` |
| Tab. C.17  Scenario de-confounding (panels a, b) | `tables/tex/A1_eth_deconf.tex`, `tables/tex/A2_zwe_coalexit.tex` |

The net-resource-cost column of manuscript Table 5 is not part of the generated
`T2_zwe.tex` fragment. It is `net_resource_cost_BUSD`, defined below.

`tables.py` also writes two complete-scenario tables (`A3_eth_full.tex`,
`A4_zwe_full.tex`) that are supplementary to the manuscript.

## Cost fields in `results/<CC>.json`

Every cost field is an out-of-sample expectation in discounted billion US
dollars. The paper uses four cost measures and each has its own field. They
differ in how the terminal salvage credit, the shortage cost, and the carbon-tax
transfer are treated, so they are not interchangeable.

| Field | Definition | Manuscript term |
|---|---|---|
| `gross_resource_BUSD` | investment + fixed O&M + variable cost. No tax, no shortage cost, no salvage. | gross resource cost |
| `net_resource_cost_BUSD` | `gross_resource_BUSD - salvage_BUSD` | net resource cost |
| `total_expected_cost_BUSD` | `gross_resource_BUSD + voll_cost_BUSD - salvage_BUSD + refurb_cost_BUSD` | total expected cost |
| `cash_cost_BUSD` | `gross_resource_BUSD + tax_payment_BUSD` | cash cost |
| `voll_cost_BUSD` | value of lost load times expected unserved energy | shortage cost |
| `tax_payment_BUSD` | carbon-tax payment, a transfer excluded from every resource-cost measure | carbon-tax transfer |
| `salvage_BUSD` | terminal salvage credit on new build | terminal salvage credit |
| `refurb_cost_BUSD` | recurring continued-operation cost charged to inherited coal. Non-zero only in `coal_retire_refurb`. | continued-operation cost |
| `finance_subsidy_BUSD` | grant-equivalent value of concessional financing. Non-zero only in `combined_tax50_solar`. | concessional financing |
| `planner_objective_BUSD` | the solved objective, which does include the tax payment | planner objective |
| `reconciliation_residual_BUSD` | `planner_objective_BUSD` less the sum of its components. Approximately zero; a non-zero value indicates a capacity-slack penalty. | diagnostic, not reported |

Four fields are duplicates retained for convenience: `resource_cost_BUSD`
repeats `gross_resource_BUSD`, `reliability_cost_BUSD` repeats
`voll_cost_BUSD`, `tax_transfer_BUSD` repeats `tax_payment_BUSD`, and
`total_cost_BUSD` repeats `planner_objective_BUSD`.

## Scenario keys

Scenario keys in `results/<CC>.json` are the manifest names defined in
`model/export_params.py`. The table maps each key to the scenario name used in
the paper.

| Repository key | Manuscript scenario | Country |
|---|---|---|
| `baseline` | baseline | both |
| `unc_deterministic` | no uncertainty represented (uncertainty decomposition) | both |
| `unc_demand_only` | demand uncertainty only | both |
| `unc_hydro_only` | hydrological uncertainty only | both |
| `drought_stress` | **low-inflow stress** (every planning year fixed to the low-inflow condition) | both |
| `gh9` | robustness check with **nine Gauss-Hermite demand nodes** instead of the baseline five | both |
| `hydro_persist_hi` / `hydro_persist_lo` | more / less persistent hydrology | both |
| `sens_disc3` / `sens_disc8` | discount rate 3% / 8% | both |
| `sens_voll5k` / `sens_voll20k` | VoLL $5,000 / $20,000 per MWh | both |
| `sens_mudry_lo` / `sens_mudry_hi` | low-inflow multiplier -15% / +15% | both |
| `re_inv_minus30` / `re_inv_minus50` | renewable investment cost -30% / -50% | Ethiopia |
| `learning_curves` | learning-curve cost trajectory | Ethiopia |
| `solar_only_50` | solar-only investment cost -50% | Ethiopia |
| `accelerated_access` | accelerated access, 50 TWh by 2030 | Ethiopia |
| `high_demand_expanded` | high demand with expanded ceilings | Ethiopia |
| `high_demand_fixedceil` | high demand with baseline ceilings | Ethiopia |
| `constrained_hydro` | constrained hydropower, new build limited to 3 GW | Ethiopia |
| `carbon_tax_30` / `carbon_tax_50` | carbon tax $30 / $50 per tCO2 | Zimbabwe |
| `emission_cap_glide` | glide-path cap, 8 to 3 MtCO2 per year | Zimbabwe |
| `cap_matched_30` / `cap_matched_50` | annual cap matched to the $30 / $50 tax | Zimbabwe |
| `combined_tax50_solar` | $50 tax with concessional solar financing | Zimbabwe |
| `re_inv_m50` | renewable investment cost -50% | Zimbabwe |
| `high_demand` | high demand | Zimbabwe |
| `coal_retire_fixed` | coal exit, schedule only | Zimbabwe |
| `coal_retire_delayed` | coal exit, delayed retirement floor | Zimbabwe |
| `coal_retire_refurb` | coal exit with **continued-operation cost** on inherited coal | Zimbabwe |

## Resource and deployment ceilings

Each technology carries an upper bound on cumulative installed capacity,
`X_te <= Xbar_e`, imposed through equation (4j) of the paper. These bounds are
author-defined long-term planning assumptions that express the scale of
deployment considered feasible over the 2025-2050 horizon, informed by the
existing system, development plans, broad resource availability and endowment,
and technology costs. They are not estimates of technical resource potential.

The production values are set in `COUNTRY[cc]["ub"]` in `model/export_params.py`
and are recorded for documentation in the `Conversion` and `Assumptions` sheets of
the country workbooks:

| Technology | Ethiopia (MW) | Zimbabwe (MW) |
|---|---|---|
| Hydro | 15,000 | 5,000 |
| Wind | 10,000 | -- |
| Solar PV | 10,000 | 5,000 |
| Geothermal | 5,000 | -- |
| Coal | -- | 1,900 |
| Gas | -- | 3,000 |
| Bioenergy | 3,000 | 1,000 |

Three Ethiopia scenarios change these bounds. `accelerated_access` and
`high_demand_expanded` raise hydro, wind, and solar to 20,000, 15,000, and
15,000 MW through `ub_override`. `constrained_hydro` instead caps cumulative new
hydro build at 3,000 MW through `new_build_cap`, giving a total hydro bound of
12,213 MW. `high_demand_fixedceil` keeps the baseline bounds, which is what
distinguishes it from `high_demand_expanded`.

## Hydrology inputs

The three-state inflow Markov chain is calibrated from GloFAS v5.0 daily river
discharge. The `hydrology/` sub-package documents the full download and
calibration chain and ships the calibrated contract and the derived annual tables.
The raw NetCDF archives are not distributed because of their size; the download
scripts regenerate them (a free Copernicus account is required). See
`hydrology/README.md` and `hydrology/DATA_DICTIONARY.md`. The model pipeline
consumes only `hydrology/outputs/hydro_calibration.json`, which is included, so a
full re-solve does not require downloading the raw data.

## License

The code is released under the MIT license (`LICENSE`). The authors' compiled and
derived data in `data/`, `results/`, and `hydrology/` are released under CC BY 4.0,
to the extent the authors hold the rights. The workbooks adopt planning values
calibrated from third-party benchmarks (IRENA, IEA, World Bank, and Global Solar
Atlas), which retain their own terms and are attributed in the workbook
`Assumptions` and `References` sheets. The hydrology inputs derive from Copernicus
GloFAS (DOI 10.24381/cds.a4fdd6b9); any redistribution must keep the attribution
"Generated using Copernicus Emergency Management Service information (2026)."

## Citation

If you use this repository, please cite the accompanying paper:

Abate, A. G., Zhang, X.-B., Liu, X., Liu, R., and Nielsen, P. (2026). Optimal
power expansion planning under uncertainty in sub-Saharan Africa: A stochastic
dual dynamic programming approach. *Energy Economics*.
