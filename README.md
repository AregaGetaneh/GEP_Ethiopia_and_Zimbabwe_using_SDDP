# Power expansion planning under uncertainty in sub-Saharan Africa

Replication code and data for a multistage stochastic generation expansion
planning (GEP) study of two contrasting power systems, Ethiopia (hydro-rich)
and Zimbabwe (coal-legacy with carbon policy). The model is solved with
stochastic dual dynamic programming (SDDP.jl and Gurobi). This repository
contains everything needed to reproduce the reported results, tables, and
figures from the two country input datasets.

Repository: <https://github.com/AregaGetaneh/GEP_Ethiopia_and_Zimbabwe_using_SDDP>

## Repository layout

```
export_params.py         build model inputs: read data/*.xlsx, write params_<CC>.json
gep_sddp.jl              the SDDP model: solve each scenario, write results/<CC>/*.json
benchmarks.jl            value of the stochastic solution (VSS) and of perfect information (EVPI)
make_figures.py          all figures from results/<CC>/*.json          -> figures/
make_tables.py           main result tables from results/<CC>/*.json   -> tables/
make_appendix_tables.py  appendix scenario tables                      -> tables/
make_supplementary_tables.py    supplementary and robustness tables           -> tables/

params_ETH.json          model-ready inputs for Ethiopia (output of export_params.py, included)
params_ZWE.json          model-ready inputs for Zimbabwe (output of export_params.py, included)
data/                    country workbooks the parameters are calibrated from
  ethiopia_data.xlsx
  zimbabwe_data.xlsx
results/                 solved scenario outputs, one JSON per scenario (included)
  ETH/  ZWE/
Project.toml             Julia dependencies
requirements.txt         Python dependencies
```

`<CC>` is the country code, `ETH` or `ZWE`. The solved `results/` JSON files
are included, so the figures and tables can be regenerated without solving the
model. The `figures/` and `tables/` output directories are produced by the code
and are not tracked.

## Requirements

- Julia 1.9 or later, with the packages pinned in `Project.toml` (SDDP, JuMP,
  Gurobi, JSON).
- A working Gurobi installation and license (free for academic use). Only the
  model-solving steps need Gurobi.
- Python 3.10 or later, with the packages in `requirements.txt` (pandas, numpy,
  matplotlib, openpyxl).

The reported results were produced with Julia 1.10.4 and Gurobi 11.0.2.

## Reproducing the results

Run everything from the repository root. Because the solved `results/` JSON
files are included, you can go straight to step 4 to regenerate the figures and
tables. Steps 1 to 3 re-solve the model from scratch and require a Gurobi
license.

1. Build the model inputs. Optional: the generated `params_<CC>.json` files are
   already included.
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

4. Generate the figures and tables from the result JSON files.
   ```
   python make_figures.py
   python make_tables.py
   python make_appendix_tables.py
   python make_supplementary_tables.py
   ```
   Figures are written to `figures/` and LaTeX table fragments to `tables/`.

Steps 2 and 3 are the only compute-heavy part and require Gurobi. Steps 1 and 4
need only Python. The forward-pass seed is fixed inside `gep_sddp.jl` for
reproducible training. Gurobi is run with its default settings; because the
linear programs can have alternative optimal bases, capacity splits at a fixed
cost may vary marginally across solver versions or thread counts.

## Data

Each country case is calibrated from a single workbook in `data/`. The workbook
holds the technology cost and performance parameters, the retirement schedule,
and the demand path for that country. `export_params.py` reads the workbook,
applies the calibration described in the accompanying paper (seasonal operating
blocks, three-state hydrology, construction lead times, the demand-deviation
process, finance, and salvage), and writes the model-ready `params_<CC>.json`.
The JSON files are included so the model can be run without repeating this step.

## Citation

If you use this code or data, please cite:

Arega Getaneh Abate, Xiao-Bing Zhang, Xiufeng Liu, Ruyu Liu, and Per Nielsen.
*Optimal power expansion planning under uncertainty in sub-Saharan Africa: A
stochastic dual dynamic programming approach.* Working paper, 2026.

```bibtex
@unpublished{Abate_GEP_SSA,
  author = {Abate, Arega Getaneh and Zhang, Xiao-Bing and Liu, Xiufeng and Liu, Ruyu and Nielsen, Per},
  title  = {Optimal power expansion planning under uncertainty in sub-{S}aharan {A}frica: A stochastic dual dynamic programming approach},
  note   = {Working paper},
  year   = {2026}
}
```
