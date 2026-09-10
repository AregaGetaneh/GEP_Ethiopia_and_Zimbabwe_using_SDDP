"""Download GloFAS v5.0 daily discharge at the Blue Nile / El Diem box.
GloFAS reanalysis starts 1980, and the EWDS cost limit rejects multi-year daily
requests, so this pulls ONE YEAR at a time and automatically splits a year into
month-blocks if even one year is too large. Out-of-range/invalid years are
skipped with a warning. Every NetCDF is written to data/raw/ethiopia_el_diem/.
Run from the package root: python scripts/download_el_diem.py
"""
import os
import glob
import zipfile
import cdsapi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATASET = "cems-glofas-historical"
AREA = [11.35, 34.85, 11.15, 35.05]    # N, W, S, E  (Blue Nile ~11.24N, 34.95E)
OUTDIR = os.path.join(ROOT, "data", "raw", "ethiopia_el_diem")
PREFIX = "el_diem"
START, END = 1980, 2024                 # v5.0 reanalysis starts 1980; end at 2024
ALL_MONTHS = [f"{m:02d}" for m in range(1, 13)]
ALL_DAYS = [f"{d:02d}" for d in range(1, 32)]

client = cdsapi.Client()


def request_for(year, months):
    return {
        "system_version": ["version_5_0"],
        "hydrological_model": ["lisflood"],
        "product_type": ["consolidated"],
        "timespan": ["time_mean"],
        "variable": ["average_river_discharge_in_the_last_24_hours"],
        "year": [str(year)],
        "month": months,
        "day": ALL_DAYS,
        "data_format": "netcdf",
        "download_format": "zip",
        "area": AREA,
    }


def too_large(e):
    m = str(e).lower()
    return ("cost" in m) or ("too large" in m)


def invalid(e):
    m = str(e).lower()
    return ("invalid" in m) or ("not produced a valid combination" in m)


def extract(zipname, tag):
    with zipfile.ZipFile(zipname) as z:
        for member in z.namelist():
            if member.endswith(".nc"):
                tgt = os.path.join(OUTDIR, f"{tag.split(chr(95))[0]}.nc")
                with z.open(member) as src, open(tgt, "wb") as dst:
                    dst.write(src.read())
    os.remove(zipname)


def fetch(year, months):
    tag = f"{year}_{months[0]}_{months[-1]}"
    zipname = f"{PREFIX}_{tag}.zip"
    try:
        print(f"  {year}  months {months[0]}-{months[-1]} ...")
        client.retrieve(DATASET, request_for(year, months)).download(zipname)
    except Exception as e:
        if too_large(e) and len(months) > 1:
            mid = len(months) // 2
            print(f"    {year} {months[0]}-{months[-1]} too large; splitting months")
            fetch(year, months[:mid])
            fetch(year, months[mid:])
            return
        if invalid(e):
            print(f"    SKIP {year}: {str(e)[:90]}")
            return
        raise
    extract(zipname, tag)
    print(f"    saved {tag}")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for y in range(START, END + 1):
        fetch(y, ALL_MONTHS)
    ncs = sorted(glob.glob(os.path.join(OUTDIR, "*.nc")))
    print(f"Finished. {len(ncs)} NetCDF files in {OUTDIR}/")
    for f in ncs[:6]:
        print("   ", os.path.basename(f))
    if len(ncs) > 6:
        print(f"    ... and {len(ncs) - 6} more")


if __name__ == "__main__":
    main()
