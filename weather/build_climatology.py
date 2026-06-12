"""Regenerate climatology.json for the Melbourne weather page.

Fetches ~35 years of daily-max temperature from the Open-Meteo archive, then for
each day-of-year collects all values within a +/-7 day window (across all years)
and stores percentiles + mean + std. Run from the repo root:

    python weather/build_climatology.py

Only the standard library is required (urllib + json).
"""
import json
import math
import datetime as dt
import urllib.request
from collections import defaultdict

LAT, LON = -37.8136, 144.9631
START, END = "1990-01-01", "2024-12-31"
WINDOW = 7  # +/- days
OUT = "weather/climatology.json"

URL = (
    "https://archive-api.open-meteo.com/v1/archive"
    f"?latitude={LAT}&longitude={LON}&start_date={START}&end_date={END}"
    "&daily=temperature_2m_max,temperature_2m_min&timezone=Australia%2FMelbourne"
)


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main():
    print("Fetching archive from Open-Meteo ...")
    with urllib.request.urlopen(URL) as r:
        raw = json.load(r)

    times = raw["daily"]["time"]
    maxes = raw["daily"]["temperature_2m_max"]
    mins = raw["daily"]["temperature_2m_min"]

    # (month, day) -> list of (max, min, isodate)
    by_md = defaultdict(list)
    for t, vmax, vmin in zip(times, maxes, mins):
        if vmax is None or vmin is None:
            continue
        d = dt.date.fromisoformat(t)
        by_md[(d.month, d.day)].append((vmax, vmin, t))

    # 366 ordered (month, day) slots using a leap year.
    base = dt.date(2000, 1, 1)
    slots = [(base + dt.timedelta(days=i)).timetuple()[1:3] for i in range(366)]
    slot_index = {md: i for i, md in enumerate(slots)}

    def windowed(md):
        idx = slot_index[md]
        rows = []
        for off in range(-WINDOW, WINDOW + 1):
            rows.extend(by_md.get(slots[(idx + off) % 366], []))
        return rows

    out = []
    for i, md in enumerate(slots):
        rows = windowed(md)
        vals = sorted(r[0] for r in rows)  # daily-max values, for the distribution
        n = len(vals)
        mean = sum(vals) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in vals) / n)

        # records over the same window (value, date)
        rec_high_max = max(rows, key=lambda r: r[0])   # hottest day (highest max)
        rec_low_max = min(rows, key=lambda r: r[0])     # coldest day (lowest max)
        rec_high_min = max(rows, key=lambda r: r[1])    # warmest night (highest min)
        rec_low_min = min(rows, key=lambda r: r[1])     # coldest night (lowest min)

        out.append({
            "doy": i + 1, "month": md[0], "day": md[1], "n": n,
            "p2_5": round(percentile(vals, 2.5), 2),
            "p16": round(percentile(vals, 16), 2),
            "p50": round(percentile(vals, 50), 2),
            "p84": round(percentile(vals, 84), 2),
            "p97_5": round(percentile(vals, 97.5), 2),
            "mean": round(mean, 2), "std": round(std, 2),
            "rec_high_max": round(rec_high_max[0], 1), "rec_high_max_date": rec_high_max[2],
            "rec_low_max": round(rec_low_max[0], 1), "rec_low_max_date": rec_low_max[2],
            "rec_high_min": round(rec_high_min[1], 1), "rec_high_min_date": rec_high_min[2],
            "rec_low_min": round(rec_low_min[1], 1), "rec_low_min_date": rec_low_min[2],
        })

    meta = {
        "location": "Melbourne, Australia",
        "latitude": raw["latitude"], "longitude": raw["longitude"],
        "source": "Open-Meteo ERA5 archive",
        "period": f"{times[0]} to {times[-1]}",
        "variable": "daily maximum 2m temperature (degC); records also cover daily minimum",
        "window_days": WINDOW,
        "note": f"Percentiles and records computed over a +/-{WINDOW} day window across all years.",
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "days": out}, f, separators=(",", ":"))
    print(f"Wrote {OUT} with {len(out)} days ({meta['period']}).")


if __name__ == "__main__":
    main()
