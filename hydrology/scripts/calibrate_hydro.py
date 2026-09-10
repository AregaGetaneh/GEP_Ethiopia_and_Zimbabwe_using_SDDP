#!/usr/bin/env python3
"""Annual-inflow Markov calibration for the GEP hydrology states from the GloFAS
NetCDFs in data/raw/. Writes the calibration contract to outputs/ and the derived
tables to data/derived/. Run from the package root: python scripts/calibrate_hydro.py
Needs: xarray, h5netcdf (or netCDF4), numpy, pandas.
"""
import os
import json
import glob
import numpy as np
import pandas as pd

import xarray as xr

SCHEMA = "gep-hydro-calibration/1.0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATES = ["L", "N", "H"]
MIN_DAYS = 350
LOW_COUNT = 5
JEFFREYS_ALPHA = 0.5

ENGINE = None
for _n, _m in (("netcdf4", "netCDF4"), ("h5netcdf", "h5netcdf")):
    try:
        __import__(_m); ENGINE = _n; break
    except Exception:
        pass

# country: raw subfolder, gauge name, gauge lat/lon, published catchment km2 + source, f_hy
GAUGES = {
    "Ethiopia": dict(
        sub="ethiopia_el_diem", gauge="Blue Nile at El Diem",
        lat=11.24, lon=34.95, catchment_km2=176000,
        catchment_src="Upper Blue Nile at El Diem/Eldeim, ~172,000-176,000 km2 (Blue Nile / GERD hydrology literature)",
        f_hy=0.45),
    "Zimbabwe": dict(
        sub="zimbabwe_victoria_falls", gauge="Zambezi at Victoria Falls (Big Tree / Nana's Farm)",
        lat=-17.92, lon=25.83, catchment_km2=507000,
        catchment_src="Upper Zambezi above Victoria Falls, ~507,000 km2 (Zambezi basin literature)",
        f_hy=0.50),
}


def open_series(sub):
    files = sorted(glob.glob(os.path.join(ROOT, "data", "raw", sub, "*.nc")))
    if not files:
        raise SystemExit(f"no .nc under data/raw/{sub}")
    dsl = [xr.open_dataset(f, engine=ENGINE) for f in files]
    td = "valid_time" if "valid_time" in dsl[0].dims else "time"
    return xr.concat(dsl, dim=td).sortby(td), td


def select_pixel(da, td, glat, glon):
    """Selection rule: among main-stem river cells (mean discharge >= 0.5*max,
    which separates channel from off-river hillslope), choose the cell nearest the
    published gauge coordinates; break ties by larger mean discharge (more
    channelized main stem). Discharge is used only to identify the channel and to
    break geographic ties, not to maximize the series."""
    var = next(v for v in da.data_vars if "dis" in v.lower())
    m = da[var].mean(td)
    lats = m["latitude"].values
    lons = m["longitude"].values
    qmax = float(m.max())
    cand = []
    for i, la in enumerate(lats):
        for j, lo in enumerate(lons):
            q = float(m.values[i, j])
            if q >= 0.5 * qmax:                       # main-stem channel cell
                d = ((float(la) - glat) ** 2 + (float(lo) - glon) ** 2) ** 0.5
                cand.append((d, -q, float(la), float(lo), q))
    cand.sort()                                        # nearest first, then higher q
    d, _, la, lo, q = cand[0]
    series = da[var].sel(latitude=la, longitude=lo)
    return dict(lat=la, lon=lo, mean_m3s=round(q, 1), dist_deg=round(d, 4),
                n_channel_cells=len(cand)), series


def annual_means(series, td):
    grp = series.groupby(f"{td}.year")
    means = grp.mean(td); counts = grp.count(td)
    years = means["year"].values.astype(int)
    q = np.asarray(means.values, float); n = np.asarray(counts.values, float)
    ok = (~np.isnan(q)) & (n >= MIN_DAYS)
    dropped = [(int(y), int(c)) for y, c, k in zip(years, n, ok) if not k]
    return years[ok], q[ok], n[ok].astype(int), dropped


def calibrate(country, cfg):
    da, td = open_series(cfg["sub"])
    pix, series = select_pixel(da, td, cfg["lat"], cfg["lon"])
    years, q, ndays, dropped = annual_means(series, td)
    r = q / q.mean()
    t1, t2 = np.percentile(q, [100/3, 200/3])
    lab = np.where(q < t1, 0, np.where(q < t2, 1, 2))
    mu_raw = np.array([r[lab == s].mean() for s in range(3)])
    N = np.zeros((3, 3), int)
    for a, b in zip(lab[:-1], lab[1:]):
        N[a, b] += 1
    P = N / N.sum(axis=1, keepdims=True)                       # baseline empirical
    Pj = (N + JEFFREYS_ALPHA) / (N.sum(1, keepdims=True) + 3 * JEFFREYS_ALPHA)  # sensitivity
    w, V = np.linalg.eig(P.T)
    pi = np.real(V[:, int(np.argmin(np.abs(w - 1)))]); pi = pi / pi.sum()
    s = float((pi * mu_raw).sum())
    mu_norm = mu_raw / s
    f_hy = cfg["f_hy"]
    checks = dict(
        mu_monotone=bool(mu_raw[0] < mu_raw[1] < mu_raw[2]),
        rows_sum_to_1=bool(np.allclose(P.sum(1), 1)),
        counts_reconcile=bool(int(N.sum()) == len(q) - 1),
        pi_stationary_maxdev=float(np.max(np.abs(pi @ P - pi))),
        sum_pi_mu_norm=float((pi * mu_norm).sum()),
        f_hy_mu_max=float((f_hy * mu_norm).max()),
        f_hy_mu_le_1=bool((f_hy * mu_norm <= 1).all()),
    )
    return dict(
        country=country, gauge=cfg["gauge"], sub=cfg["sub"],
        period=[int(years[0]), int(years[-1])], n_complete_years=int(len(q)),
        dropped_partial_years=dropped,
        selected_gauge=dict(lat=cfg["lat"], lon=cfg["lon"],
                            catchment_km2=cfg["catchment_km2"], catchment_src=cfg["catchment_src"]),
        selected_grid_cell=pix,
        mean_annual_m3s=round(float(q.mean()), 1),
        terciles_m3s=[round(float(t1), 1), round(float(t2), 1)],
        n_years_LNH=[int((lab == s).sum()) for s in range(3)],
        mu_raw=[round(float(x), 4) for x in mu_raw],
        stationary_weighted_mu_raw=round(s, 4),
        mu_normalized=[round(float(x), 4) for x in mu_norm],
        transition_counts=N.tolist(),
        P_H_empirical=[[round(float(x), 4) for x in row] for row in P],
        stationary_distribution=[round(float(x), 4) for x in pi],
        h1=STATES[int(lab[-1])],
        hydro_capacity_factor=f_hy,
        max_fhy_times_mu=round(checks["f_hy_mu_max"], 4),
        validation=checks,
        diagnostics=dict(transition_sensitivity=dict(
            method=f"Jeffreys Dirichlet alpha={JEFFREYS_ALPHA} (SENSITIVITY ONLY, not baseline)",
            jeffreys_alpha_0_5=[[round(float(x), 4) for x in row] for row in Pj])),
        _years=[int(y) for y in years], _q=[round(float(x), 2) for x in q],
        _ndays=[int(x) for x in ndays], _r=[round(float(x), 4) for x in r],
        _lab=[STATES[int(x)] for x in lab], _Pj=Pj,
    )


def write_derived(res):
    dd = os.path.join(ROOT, "data", "derived")
    rows = []
    for r in res:
        for y, q, nd, rn, st in zip(r["_years"], r["_q"], r["_ndays"], r["_r"], r["_lab"]):
            rows.append(dict(country=r["country"], site=r["sub"], year=y, valid_daily_count=nd,
                             mean_daily_discharge_m3s=q, normalized_annual_flow_raw=rn, hydrology_state=st))
    ad = pd.DataFrame(rows)
    ad.to_csv(os.path.join(dd, "annual_discharge.csv"), index=False)
    ad[["country", "site", "year", "hydrology_state"]].to_csv(
        os.path.join(dd, "annual_hydrology_states.csv"), index=False)
    tc, tp = [], []
    for r in res:
        N = np.array(r["transition_counts"]); Pj = r["_Pj"]
        for i, o in enumerate(STATES):
            rt = int(N[i].sum())
            for j, d in enumerate(STATES):
                tc.append(dict(country=r["country"], origin_state=o, destination_state=d, count=int(N[i, j])))
                tp.append(dict(country=r["country"], origin_state=o, destination_state=d,
                               count=int(N[i, j]), row_total=rt,
                               p_empirical=round(float(N[i, j] / rt), 4) if rt else 0.0,
                               p_jeffreys_alpha_0_5=round(float(Pj[i, j]), 4)))
    pd.DataFrame(tc).to_csv(os.path.join(dd, "transition_counts.csv"), index=False)
    pd.DataFrame(tp).to_csv(os.path.join(dd, "transition_probabilities.csv"), index=False)


def write_metadata(res):
    md = os.path.join(ROOT, "metadata")
    gm = [dict(country=r["country"], gauge=r["gauge"],
               gauge_lat=r["selected_gauge"]["lat"], gauge_lon=r["selected_gauge"]["lon"],
               catchment_km2=r["selected_gauge"]["catchment_km2"],
               selected_cell_lat=r["selected_grid_cell"]["lat"],
               selected_cell_lon=r["selected_grid_cell"]["lon"],
               selected_cell_mean_m3s=r["selected_grid_cell"]["mean_m3s"],
               dist_gauge_deg=r["selected_grid_cell"]["dist_deg"],
               catchment_src=r["selected_gauge"]["catchment_src"]) for r in res]
    pd.DataFrame(gm).to_csv(os.path.join(md, "gauge_metadata.csv"), index=False)
    src = dict(
        source="Copernicus Emergency Management Service, Global Flood Awareness System (GloFAS)",
        dataset="cems-glofas-historical", doi="10.24381/cds.a4fdd6b9",
        system_version="version_5_0", hydrological_model="lisflood",
        product_type="consolidated", timespan="time_mean",
        variable="average_river_discharge_in_the_last_24_hours", units="m3/s",
        grid_resolution_deg=0.05, temporal_resolution="daily",
        calibration_period="1980-2024", note_1979="1979 rejected by the API (invalid combination) for this product; retained 1980-2024",
        completeness_rule=f">= {MIN_DAYS} valid daily records per year",
        aggregation="calendar-year mean of daily discharge at the selected cell",
        classification="empirical terciles of annual-mean discharge (L<33.3%<N<66.7%<H)",
        normalization="mu_h = mu_h_raw / sum_j pi_j mu_j_raw so sum_h pi_h mu_h = 1",
        initial_state="h1 = state of the latest complete annual-flow year",
        transition_matrix="raw empirical N_ij / row totals (baseline; no smoothing)",
        schema=SCHEMA)
    json.dump(src, open(os.path.join(md, "source_metadata.json"), "w"), indent=2)


def write_outputs(res):
    out = os.path.join(ROOT, "outputs")
    contract = {"schema": SCHEMA, "countries": {}}
    for r in res:
        c = {k: r[k] for k in r if not k.startswith("_")}
        contract["countries"][r["country"]] = c
    json.dump(contract, open(os.path.join(out, "hydro_calibration.json"), "w"), indent=2)

    def mat(m): return "[" + "; ".join("[" + ", ".join(f"{x}" for x in row) + "]" for row in m) + "]"
    L = ["# Hydrology calibration (baseline)", "",
         f"Schema {SCHEMA}. Baseline transition matrix is the raw empirical P^H (no smoothing).", "",
         "| Quantity | " + " | ".join(r["country"] for r in res) + " |",
         "|---|" + "|".join(["---"] * len(res)) + "|"]
    rowdefs = [
        ("Gauge", lambda r: r["gauge"]),
        ("Selected cell (lat, lon)", lambda r: f'{r["selected_grid_cell"]["lat"]}, {r["selected_grid_cell"]["lon"]}'),
        ("Dist to gauge (deg)", lambda r: r["selected_grid_cell"]["dist_deg"]),
        ("Period / complete yrs", lambda r: f'{r["period"][0]}-{r["period"][1]} / {r["n_complete_years"]}'),
        ("Mean annual (m3/s)", lambda r: r["mean_annual_m3s"]),
        ("Terciles 33/67 (m3/s)", lambda r: " / ".join(map(str, r["terciles_m3s"]))),
        ("L/N/H years", lambda r: " / ".join(map(str, r["n_years_LNH"]))),
        ("mu_raw", lambda r: ", ".join(map(str, r["mu_raw"]))),
        ("mu_norm", lambda r: ", ".join(map(str, r["mu_normalized"]))),
        ("N_ij", lambda r: mat(r["transition_counts"])),
        ("P^H empirical", lambda r: mat(r["P_H_empirical"])),
        ("Stationary pi", lambda r: ", ".join(map(str, r["stationary_distribution"]))),
        ("sum pi*mu_raw -> norm", lambda r: f'{r["stationary_weighted_mu_raw"]} -> {round(r["validation"]["sum_pi_mu_norm"],4)}'),
        ("max f_hy*mu (<=1)", lambda r: f'{r["max_fhy_times_mu"]} ({r["validation"]["f_hy_mu_le_1"]})'),
        ("h1 (latest complete)", lambda r: r["h1"]),
    ]
    for lab, fn in rowdefs:
        L.append(f"| {lab} | " + " | ".join(str(fn(r)) for r in res) + " |")
    open(os.path.join(out, "hydro_calibration.md"), "w").write("\n".join(L))

    S = ["# Hydrology transition-matrix sensitivity (NON-baseline)", "",
         f"Jeffreys Dirichlet prior alpha={JEFFREYS_ALPHA}: P_ij = (N_ij + {JEFFREYS_ALPHA}) / (N_i + {3*JEFFREYS_ALPHA}).",
         "This is a robustness diagnostic only. The baseline model uses the raw empirical",
         "P^H in hydro_calibration.json. Do not substitute these values as the baseline.", ""]
    for r in res:
        S += [f"## {r['country']}",
              "Raw counts N_ij: " + mat(r["transition_counts"]),
              "Empirical P^H (baseline): " + mat(r["P_H_empirical"]),
              "Jeffreys P^H (sensitivity): " + mat([[round(float(x), 4) for x in row] for row in r["_Pj"]]),
              "Thin cells (<%d): " % LOW_COUNT + (", ".join(
                  f"{STATES[i]}->{STATES[j]}:{r['transition_counts'][i][j]}"
                  for i in range(3) for j in range(3) if r["transition_counts"][i][j] < LOW_COUNT) or "none"), ""]
    open(os.path.join(out, "hydro_calibration_sensitivity.md"), "w").write("\n".join(S))


def main():
    res = [calibrate(c, cfg) for c, cfg in GAUGES.items()]
    write_derived(res)
    write_metadata(res)
    write_outputs(res)
    for r in res:
        v = r["validation"]
        print(f"\n## {r['country']}  cell ({r['selected_grid_cell']['lat']},{r['selected_grid_cell']['lon']}) "
              f"dist {r['selected_grid_cell']['dist_deg']} deg | {r['period'][0]}-{r['period'][1]} "
              f"{r['n_complete_years']}yr | mean {r['mean_annual_m3s']} m3/s")
        print(f"  mu_norm {r['mu_normalized']} | pi {r['stationary_distribution']} | h1 {r['h1']}")
        print(f"  N_ij {r['transition_counts']}")
        print(f"  checks: mono {v['mu_monotone']} rows1 {v['rows_sum_to_1']} counts {v['counts_reconcile']} "
              f"piP-pi {v['pi_stationary_maxdev']:.2e} sum_pi_mu {v['sum_pi_mu_norm']:.4f} "
              f"fhy*mu<=1 {v['f_hy_mu_le_1']} (max {v['f_hy_mu_max']:.3f})")
    print("\nwrote outputs/, data/derived/, metadata/")


if __name__ == "__main__":
    main()
