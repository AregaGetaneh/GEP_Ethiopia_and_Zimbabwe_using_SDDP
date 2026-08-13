# Power expansion planning under uncertainty in sub-Saharan Africa

Replication code and data for the paper *Optimal power expansion planning under
uncertainty in sub-Saharan Africa: A stochastic dual dynamic programming
approach*, with case studies for Ethiopia and Zimbabwe.

The model is a multistage stochastic generation expansion planning (GEP) problem
solved with stochastic dual dynamic programming (SDDP.jl and Gurobi). This
repository holds everything needed to reproduce every result, table, and figure
in the paper from the two country input datasets.

Repository: <https://github.com/AregaGetaneh/GEP_Ethiopia_and_Zimbabwe_using_SDDP>

Repository: https://github.com/AregaGetaneh/GEP_Ethiopia_and_Zimbabwe_using_SDDP

## Repository layout

```
export_params.py   1. build model inputs: read data/*.xlsx, write params_<CC>.json
gep_sddp.jl        2. the SDDP model: solve each scenario, write results/<CC>/*.json
benchmarks.jl      3. value of adaptivity (VSS) and perfect information (EVPI)
make_figures.py    4. all figures from results/<CC>/*.json      -> figures/
make_tables.py     5. all LaTeX tables from results/<CC>/*.json  -> tables/

params_ETH.json    model-ready inputs for Ethiopia (output of step 1, included)
params_ZWE.json    model-ready inputs for Zimbabwe (output of step 1, included)
data/              raw country workbooks the parameters are calibrated from
  ETHSummary_corrected_like_NSW_filled.xlsx
  ZWE_Summary_2025_2050.xlsx
results/           solved scenario outputs, one JSON per scenario (included)
  ETH/  ZWE/
Project.toml       Julia dependencies
requirements.txt   Python dependencies
```

`<CC>` is the country code, `ETH` or `ZWE`. The solved `results/` JSONs are
included, so the figures and tables can be regenerated without a Gurobi run.
Figures and LaTeX tables themselves are produced by the code and are not tracked.

## Requirements

- Julia 1.9 or later, with the packages pinned in `Project.toml` (SDDP, JuMP,
  Gurobi, JSON).
- A working Gurobi installation and license. Gurobi is free for academic use.
- Python 3.10 or later, with the packages in `requirements.txt` (pandas, numpy,
  matplotlib, openpyxl).

The results in the paper were produced with Julia 1.10.4 and Gurobi 11.0.2.

## Reproducing the results

Run everything from the repository root. Because the solved `results/` JSONs are
included, you can jump straight to step 4 to regenerate the figures and tables.
Steps 1 to 3 re-solve the model from scratch and need a Gurobi license.

1. Build the model inputs. This step is optional, because the generated
   `params_<CC>.json` files are already included.
   ```
   python export_params.py
   ```

2. Solve every scenario for each country. Each run writes one JSON per scenario
   to `results/<CC>/`. The first Julia run installs the packages from
   `Project.toml`.
   ```
   julia --project=. gep_sddp.jl ETH
   julia --project=. gep_sddp.jl ZWE
   ```

3. Compute the value-of-adaptivity benchmarks (VSS and EVPI), which write
   `results/<CC>/benchmarks.json`.
   ```
   julia --project=. benchmarks.jl ETH
   julia --project=. benchmarks.jl ZWE
   ```

4. Generate all figures and tables from the result JSONs.
   ```
   python make_figures.py
   python make_tables.py
   ```
   Figures are written to `figures/` and LaTeX table fragments to `tables/`.

Steps 2 and 3 require a Gurobi license and are the only compute-heavy part. Steps
1 and 4 need only Python. The forward-pass seed and thread count are set inside
`gep_sddp.jl` for reproducible policies.

## Data

The two country cases are calibrated from the workbooks in `data/`. Each workbook
holds the technology cost and performance parameters, the retirement schedule,
and the demand path for that country. `export_params.py` reads these workbooks,
applies the calibration described in the paper, and writes the model-ready
`params_<CC>.json`. The JSON files are included so the model can be run without
repeating the preprocessing step.

## Citation

If you use this code or data, please cite:

Arega Getaneh Abate, Xiao-Bing Zhang, Xiufeng Liu, Ruyu Liu, and Per Nielsen.
*Optimal power expansion planning under uncertainty in sub-Saharan Africa: A
stochastic dual dynamic programming approach.* Energy Economics.

```bibtex
@article{Abate_GEP_SSA,
  author  = {Abate, Arega Getaneh and Zhang, Xiao-Bing and Liu, Xiufeng and Liu, Ruyu and Nielsen, Per},
  title   = {Optimal power expansion planning under uncertainty in sub-{S}aharan {A}frica: A stochastic dual dynamic programming approach},
  journal = {Energy Economics},
  year    = {2026}
}
```

