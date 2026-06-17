"""Generate climatology JSON files for the weather page.

Fetches ~35 years of daily data from the Open-Meteo ERA5 archive and
computes percentile statistics over a +/-7 day window for each calendar day.

Run from the repo root:
    python weather/build_climatology.py chicago
    python weather/build_climatology.py london
    python weather/build_climatology.py phoenix
    python weather/build_climatology.py all      # all four cities
    python weather/build_climatology.py          # Melbourne only (recreates climatology.json)
"""
import json, math, datetime as dt, urllib.request, sys
from collections import defaultdict

CITIES = {
    "melbourne": {"name": "Melbourne, Australia", "lat": -37.8136, "lon": 144.9631, "tz": "Australia/Melbourne"},
    "chicago":   {"name": "Chicago, USA",          "lat": 41.8781,  "lon": -87.6298,  "tz": "America/Chicago"},
    "london":    {"name": "London, UK",             "lat": 51.5074,  "lon": -0.1278,   "tz": "Europe/London"},
    "phoenix":   {"name": "Phoenix, USA",           "lat": 33.4484,  "lon": -112.0740, "tz": "America/Phoenix"},
}

START, END = "1990-01-01", "2024-12-31"
WINDOW = 7
RAIN_THRESHOLD = 0.2


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


def build(city_key):
    cfg = CITIES[city_key]
    tz_enc = cfg["tz"].replace("/", "%2F")
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={cfg['lat']}&longitude={cfg['lon']}"
        f"&start_date={START}&end_date={END}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
        f"&timezone={tz_enc}"
    )
    print(f"Fetching {cfg['name']} from Open-Meteo archive ...")
    req = urllib.request.Request(url, headers={"User-Agent": "climatology-builder/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = json.load(r)

    times = raw["daily"]["time"]
    maxes = raw["daily"]["temperature_2m_max"]
    mins  = raw["daily"]["temperature_2m_min"]
    precs = raw["daily"]["precipitation_sum"]

    by_md = defaultdict(list)
    for t, vmax, vmin, vprec in zip(times, maxes, mins, precs):
        d = dt.date.fromisoformat(t)
        by_md[(d.month, d.day)].append((vmax, vmin, vprec, t))

    # 366 calendar slots using a leap year as the reference.
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

        trows = [r for r in rows if r[0] is not None and r[1] is not None]
        vals = sorted(r[0] for r in trows)
        n = len(vals)
        mean = sum(vals) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in vals) / n)

        rec_high_max = max(trows, key=lambda r: r[0])
        rec_low_max  = min(trows, key=lambda r: r[0])
        rec_high_min = max(trows, key=lambda r: r[1])
        rec_low_min  = min(trows, key=lambda r: r[1])

        prows = [r for r in rows if r[2] is not None]
        rvals = sorted(r[2] for r in prows)
        rn = len(rvals)
        rmean = sum(rvals) / rn
        pop = sum(1 for v in rvals if v > RAIN_THRESHOLD) / rn * 100
        rain_rec = max(prows, key=lambda r: r[2])

        out.append({
            "doy": i + 1, "month": md[0], "day": md[1], "n": n,
            "p2_5":  round(percentile(vals, 2.5),  2),
            "p16":   round(percentile(vals, 16),    2),
            "p50":   round(percentile(vals, 50),    2),
            "p84":   round(percentile(vals, 84),    2),
            "p97_5": round(percentile(vals, 97.5),  2),
            "mean": round(mean, 2), "std": round(std, 2),
            "rec_high_max": round(rec_high_max[0], 1), "rec_high_max_date": rec_high_max[3],
            "rec_low_max":  round(rec_low_max[0],  1), "rec_low_max_date":  rec_low_max[3],
            "rec_high_min": round(rec_high_min[1], 1), "rec_high_min_date": rec_high_min[3],
            "rec_low_min":  round(rec_low_min[1],  1), "rec_low_min_date":  rec_low_min[3],
            "rain_p50":  round(percentile(rvals, 50),   2),
            "rain_p84":  round(percentile(rvals, 84),   2),
            "rain_p97_5": round(percentile(rvals, 97.5), 2),
            "rain_mean": round(rmean, 2),
            "rain_pop":  round(pop, 1),
            "rain_rec":  round(rain_rec[2], 1), "rain_rec_date": rain_rec[3],
        })

    meta = {
        "location":          cfg["name"],
        "latitude":          raw["latitude"],
        "longitude":         raw["longitude"],
        "source":            "Open-Meteo ERA5 archive",
        "period":            f"{times[0]} to {times[-1]}",
        "variable":          "daily max/min 2m temperature (degC) and precipitation sum (mm)",
        "window_days":       WINDOW,
        "rain_threshold_mm": RAIN_THRESHOLD,
        "note":              f"Percentiles and records computed over a +/-{WINDOW} day window across all years.",
    }

    # Melbourne keeps the original filename for backward compatibility.
    out_path = "weather/climatology.json" if city_key == "melbourne" else f"weather/climatology_{city_key}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "days": out}, f, separators=(",", ":"))
    print(f"Wrote {out_path} with {len(out)} days ({meta['period']}).")


if __name__ == "__main__":
    targets = sys.argv[1:] if sys.argv[1:] else ["melbourne"]
    if targets == ["all"]:
        targets = list(CITIES.keys())
    for city in targets:
        if city not in CITIES:
            print(f"Unknown city: {city}. Choose from: {', '.join(CITIES)}")
            sys.exit(1)
        build(city)
