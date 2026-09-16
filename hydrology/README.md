# Hydrological calibration (Ethiopia and Zimbabwe)

Reproducible calibration of the annual three-state (low / normal / high inflow)
Markov chain used by the generation-expansion model. From annual river discharge
at one gauge per country this produces, for each country:

- annual-inflow classification into low (L), normal (N), high (H) empirical
  terciles,
- state multipliers `mu_h` (relative to the long-run mean),
- the year-to-year transition matrix `P^H`,
- the stationary distribution `pi`,
- the initial state `h1`.

The single machine-readable output consumed by the model is
`outputs/hydro_calibration.json`; the annual series and transition tables are in
`data/derived/`. See `DATA_DICTIONARY.md` for every field.

## Data source and provenance

- Source: Copernicus Emergency Management Service (CEMS), Global Flood Awareness
  System (GloFAS); dataset `cems-glofas-historical`, DOI 10.24381/cds.a4fdd6b9.
- System version `version_5_0`, hydrological model `lisflood`, product
  `consolidated`; variable `average_river_discharge_in_the_last_24_hours`
  (`avg_dis`, m3/s), 0.05-degree daily grid.
- Access store: CEMS Early Warning Data Store (EWDS),
  `https://ewds.climate.copernicus.eu/api` (free account, accepted dataset
  licence, and a personal API token required). Downloaded 2026-09.
- Underlying reanalysis: Harrigan et al. (2020), GloFAS-ERA5 operational global
  river discharge reanalysis, Earth System Science Data 12, 2043-2060,
  DOI 10.5194/essd-12-2043-2020.

Calibration period is 1980-2024 (45 complete years), fixed by the v5 reanalysis
coverage and the 2025 model start year. The initial state `h1` is the state of
the latest complete year (2024).

### Gauges and selected river cells

| Country | Gauge | Box [N,W,S,E] | Selected cell (lat, lon) |
|---|---|---|---|
| Ethiopia | Blue Nile at El Diem (~11.24 N, 34.95 E) | 11.35, 34.85, 11.15, 35.05 | 11.225, 34.975 |
| Zimbabwe | Zambezi at Victoria Falls (~17.92 S, 25.83 E) | -17.82, 25.73, -18.02, 25.93 | -17.925, 25.825 |

The calibration selects one 0.05-degree river cell per box: among cells whose
long-run mean discharge is at least 50% of the box maximum (isolating the main
channel), the cell nearest the published gauge, ties broken by larger mean
discharge.

## Calibration rules

- A year is used only if it has at least 350 valid daily records.
- Annual value: calendar-year mean of daily discharge at the selected cell.
- State classification: empirical terciles of the annual-mean series (L below
  33.3%, N between, H above 66.7%).
- Multipliers: `mu_h_raw = E[Q_y / mean(Q) | state h]`, normalized so
  `sum_h pi_h mu_h = 1`.
- Transition matrix: raw empirical `N_ij / row_total`, no smoothing.
- Initial state `h1`: the state of the latest complete year.

## Reproduce

From this directory:

```
pip install -r requirements.txt

# EWDS credentials in ~/.cdsapirc (url + personal token; see the license note below)
python scripts/download_el_diem.py            # writes data/raw/ethiopia_el_diem/YYYY.nc
python scripts/download_victoria_falls.py     # writes data/raw/zimbabwe_victoria_falls/YYYY.nc

python scripts/calibrate_hydro.py             # writes outputs/ and data/derived/
```

`cdsapi` is only needed for the download step; calibration needs `xarray`,
`h5netcdf` (or `netCDF4`), `numpy`, and `pandas`. The raw NetCDF archives and the
optional human-readable reports are not versioned in the repository; the download
and calibration steps regenerate them.

## Directory layout

```
scripts/       download_el_diem.py  download_victoria_falls.py  calibrate_hydro.py
data/derived/  annual_discharge.csv  annual_hydrology_states.csv
               transition_counts.csv  transition_probabilities.csv
outputs/       hydro_calibration.json      (machine-readable calibration contract)
README.md  DATA_DICTIONARY.md  requirements.txt
```

## Citation and license

Cite the accompanying paper for this calibration, and the GloFAS dataset for
the raw discharge (DOI 10.24381/cds.a4fdd6b9) together with Harrigan et al. (2020)
for the underlying reanalysis.

The raw GloFAS data and any products derived from it are Copernicus information;
redistribution must retain the attribution "Generated using Copernicus Emergency
Management Service information (2026)." Neither the European Commission nor ECMWF
is responsible for any use of the data. No credentials or tokens are stored in
this repository; do not commit `~/.cdsapirc`. The calibration scripts fall under
the repository's top-level `LICENSE` (code); the derived outputs are released
under CC BY 4.0 as described in the top-level README, retaining the Copernicus
attribution above.
