"""Earth Engine evidence for one map pin.

Every tool returns plain JSON facts plus `source` (dataset + the date range actually
used) and, where useful, `images` (base64 PNG chips). Missing data is None plus a
`note`, never 0, so the agent can say "not enough evidence" instead of guessing.

`python eo.py` runs the self-check against the test pins in pins.json.
"""
import base64
import json
import math
import os
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ee
import google.auth

# Thresholds: one place each, with where the number comes from.
WATER_DB = -17           # VV below this reads as open water (usual S1 range -15..-20 dB; a tested fallback)
DROP_DB = -3             # ...and this far below the usual VV for that orbit and month (change detection)
PERMANENT_PCT = 50       # JRC occurrence >= 50 % of months = permanent water, not flooding
HAND_MAX_M = 20          # > 20 m above nearest drainage = not flood-prone; drops radar-shadow false positives
EVENT_SHARE = 0.05       # >= 5 % of flood-prone ground within 1 km flagged on a pass = flood event
EPISODE_GAP_DAYS = 16    # wet passes closer than this belong to the same flood episode
BUILT_DB = -6            # usual VV above this at the pin = bright like buildings, where floodwater doesn't look dark
S1_START = "2018-01-12"  # EE applies border-noise removal from here; earlier swath edges read as water
NEAR_M = 1000
CLASSES = ["water", "trees", "grass", "flooded_vegetation", "crops",
           "shrub_and_scrub", "built", "bare", "snow_and_ice"]  # Dynamic World label order
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

_lock = threading.Lock()
_ready = False


def _init():
    """Lazy, thread-safe init. Always pass the project: a bare ee.Initialize() fails on Cloud Run."""
    global _ready
    with _lock:
        if not _ready:
            creds, _ = google.auth.default()
            ee.Initialize(creds, project=os.environ["GOOGLE_CLOUD_PROJECT"])
            _ready = True


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="minutes")


def _png(img, region, dims=512):
    url = img.getThumbURL({"region": region, "dimensions": dims, "format": "png"})
    with urllib.request.urlopen(url, timeout=120) as r:
        return base64.b64encode(r.read()).decode()


def _pin(pt):
    """White reticle ring with a black edge: reads on radar grey, true colour, the heat palette and cyan masks."""
    ring = ee.FeatureCollection([ee.Feature(pt.buffer(60))])
    dot = ee.FeatureCollection([ee.Feature(pt.buffer(10))])
    edge = ee.Image().byte().paint(ring, 1, 5).visualize(palette=["000000"])
    return (edge.blend(ee.Image().byte().paint(ring, 1, 3).visualize(palette=["ffffff"]))
            .blend(ee.Image().byte().paint(dot, 1).visualize(palette=["ffffff"])))


def _compass(deg):
    return None if deg is None else COMPASS[round(deg / 45) % 8]


def _s1(pt):
    return (ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(pt)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filterDate(S1_START, (date.today() + timedelta(days=1)).isoformat())
            .select("VV"))


def _flood_prone():
    occ = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").unmask(0)  # unmask: never-water is masked
    hand = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")
    return occ.lt(PERMANENT_PCT).And(hand.lte(HAND_MAX_M))


def _flooded(img, base, prone):
    vv = img.focalMedian(30, "circle", "meters")
    return vv.lt(WATER_DB).And(vv.subtract(base).lt(DROP_DB)).updateMask(prone).rename("f")


def water_history(lat, lon):
    _init()
    pt = ee.Geometry.Point([lon, lat])
    near = pt.buffer(NEAR_M)
    prone = _flood_prone()
    col = _s1(near)
    orbits = col.aggregate_array("relativeOrbitNumber_start").distinct().getInfo()

    stat = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)
    groups = []
    # ponytail: fixed dB thresholds; switch to per-scene Otsu if false positives show up.
    for o in orbits:  # one all-years median per orbit x month, shared by that group's passes
        oc = col.filter(ee.Filter.eq("relativeOrbitNumber_start", o))
        for m in range(1, 13):
            g = oc.filter(ee.Filter.calendarRange(m, m, "month"))
            base = g.median()
            groups.append(g.map(lambda img, base=base: ee.Feature(None, _flooded(img, base, prone)
                .reduceRegion(stat, near, 20, maxPixels=1e9)
                .combine({"t": img.get("system:time_start"), "orbit": img.get("relativeOrbitNumber_start")}))))
    site = (ee.Image("MERIT/Hydro/v1_0_1").select("hnd").rename("hand")
            .addBands(ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").unmask(0))
            .reduceRegion(ee.Reducer.mean(), pt.buffer(100), 30))
    fc = ee.FeatureCollection(groups).flatten()
    # toList: a collection nested in a Dictionary comes back from getInfo() without its features.
    out = ee.Dictionary({"rows": fc.toList(fc.size()), "site": site}).getInfo()

    # Same-day slices of one orbit are one pass: pixel-weighted merge.
    passes = {}
    for f in out["rows"]:
        p = f["properties"]
        if p.get("f_count"):
            key = (_iso(p["t"])[:10], p["orbit"])
            s, n, t = passes.get(key, (0.0, 0, p["t"]))
            passes[key] = (s + p["f_mean"] * p["f_count"], n + p["f_count"], min(t, p["t"]))
    rows = sorted((t, s / n) for s, n, t in passes.values())

    episodes = []
    for t, share in rows:
        if share < EVENT_SHARE:
            continue
        if episodes and (t - episodes[-1]["_last"]) / 864e5 <= EPISODE_GAP_DAYS:
            e = episodes[-1]
            e["_last"], e["wet_passes"] = t, e["wet_passes"] + 1
            if share > e["share_flooded_1km"]:
                e["time"], e["share_flooded_1km"] = _iso(t), round(share, 3)
        else:
            episodes.append({"time": _iso(t), "share_flooded_1km": round(share, 3), "wet_passes": 1,
                             "start": _iso(t), "_last": t})
    for e in episodes:
        e["end"] = _iso(e.pop("_last"))

    site = out["site"]
    hand = site.get("hand")
    return {
        "source": (f"Sentinel-1 radar (Copernicus), {len(rows)} passes {_iso(rows[0][0])[:10]} to "
                   f"{_iso(rows[-1][0])[:10]}; JRC Global Surface Water 1984-2021; MERIT Hydro HAND") if rows
                  else "Sentinel-1 radar (Copernicus); no usable passes",
        "passes": len(rows),
        "flood_events": len(episodes),
        "events": sorted(episodes, key=lambda e: e["time"], reverse=True)[:12],
        "worst": max(episodes, key=lambda e: e["share_flooded_1km"]) if episodes else None,
        "latest": max(episodes, key=lambda e: e["time"]) if episodes else None,
        "hand_m": None if hand is None else round(hand, 1),
        "permanent_water_pct_at_pin": round(site.get("occurrence") or 0, 1),
        "note": ("share_flooded_1km = share of flood-prone ground within 1 km (no permanent water, no high "
                 "ground) that radar saw as unusually wet on that pass. Radar under-detects water between "
                 "buildings. HAND = height above nearest drainage; under ~5 m is flood-prone."
                 if rows else "No radar passes with flood-prone ground within 1 km to measure."),
    }


def flood_day_images(lat, lon, event_time, window_days=1):
    """Radar before/after chips for the pass nearest `event_time`: an ISO time from water_history, or a date
    such as an insurance claim's, looking up to `window_days` either side."""
    _init()
    pt = ee.Geometry.Point([lon, lat])
    t = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    win = _s1(pt).filterDate((t - timedelta(days=window_days)).isoformat(), (t + timedelta(days=window_days)).isoformat())
    first = (win.map(lambda i: i.set("dt", ee.Number(i.get("system:time_start")).subtract(t.timestamp() * 1000).abs()))
             .sort("dt").first())
    info = first.toDictionary(["system:time_start", "relativeOrbitNumber_start", "orbitProperties_pass"]).getInfo() \
        if win.size().getInfo() else None
    if not info:
        return {"source": "Sentinel-1 radar", "note": f"No radar pass within {window_days} day(s) of {event_time}."}

    orbit = info["relativeOrbitNumber_start"]
    p0 = datetime.fromtimestamp(info["system:time_start"] / 1000, timezone.utc)
    month = p0.month
    # only this pass: in a wide window the same orbit can come round again
    img = (_s1(pt).filterDate((p0 - timedelta(hours=1)).isoformat(), (p0 + timedelta(hours=1)).isoformat())
           .filter(ee.Filter.eq("relativeOrbitNumber_start", orbit)).mosaic()
           .setDefaultProjection(first.projection()))
    base = (_s1(pt).filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))
            .filter(ee.Filter.calendarRange(month, month, "month")).median())
    flooded = _flooded(img, base, _flood_prone())
    stats = ee.Dictionary({
        "km": flooded.reduceRegion(ee.Reducer.mean(), pt.buffer(NEAR_M), 20, maxPixels=1e9).get("f"),
        "m300": flooded.reduceRegion(ee.Reducer.mean(), pt.buffer(300), 10).get("f"),
        "pin": flooded.reduceRegion(ee.Reducer.max(), pt.buffer(50), 10).get("f"),
        "usual": base.reduceRegion(ee.Reducer.mean(), pt.buffer(50), 10).get("VV"),
    }).getInfo()

    region, pin = pt.buffer(1500).bounds(), _pin(pt)
    grey = {"min": -25, "max": 0}
    water = flooded.selfMask().visualize(palette=["00e5ff"], opacity=0.75)  # cyan analytical mask
    pass_time = _iso(info["system:time_start"])
    rnd = lambda v: None if v is None else round(v, 3)
    return {
        "source": (f"Sentinel-1 radar pass {pass_time} UTC (orbit {orbit}, {info['orbitProperties_pass'].lower()}) "
                   f"vs median of the same orbit in month {month}, 2018-now"),
        "pass_time": pass_time,
        "days_from_request": round((p0 - t).total_seconds() / 86400, 1),
        "share_flooded_1km": rnd(stats.get("km")),
        "share_flooded_300m": rnd(stats.get("m300")),
        "water_at_pin": None if stats.get("pin") is None else bool(stats["pin"]),
        "usual_vv_at_pin_db": None if stats.get("usual") is None else round(stats["usual"], 1),
        "pin_built_up": stats.get("usual") is not None and stats["usual"] > BUILT_DB,  # no water seen there proves nothing
        "images": {"before": _png(base.focalMedian(30, "circle", "meters").visualize(**grey).blend(pin), region),
                   "after": _png(img.focalMedian(30, "circle", "meters").visualize(**grey).blend(water).blend(pin),
                                 region)},
        "image_notes": ("Both 3 km wide, north up, white ring = pin. Grey = radar brightness (dark = smooth "
                        "surface such as water). 'before' = usual for this month; 'after' = this pass, cyan = "
                        "flagged flood water."),
    }


def heat(lat, lon):
    _init()
    pt = ee.Geometry.Point([lon, lat])
    today = date.today()
    y1 = today.year if today.month >= 6 else today.year - 1  # last complete Mar-May hot season
    y0 = y1 - 2

    def lst(img):  # QA_PIXEL bits 0-4: fill, dilated cloud, cirrus, cloud, shadow
        clear = img.select("QA_PIXEL").bitwiseAnd(0b11111).eq(0)
        return img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).updateMask(clear).rename("lst")

    col = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
           .filterBounds(pt).filterDate(f"{y0}-01-01", f"{y1}-12-31")
           .filter(ee.Filter.calendarRange(3, 5, "month")).map(lst))
    comp = col.median()
    s = ee.Dictionary({
        "n": col.size(),
        "pin": comp.reduceRegion(ee.Reducer.mean(), pt.buffer(100), 30).get("lst"),
        "area": comp.reduceRegion(ee.Reducer.mean(), pt.buffer(2000), 30).get("lst"),
    }).getInfo()
    src = f"Landsat 8/9 Collection 2 land surface temperature, Mar-May {y0}-{y1}, {s['n']} scenes"
    if s.get("pin") is None or s.get("area") is None:
        return {"source": src, "note": "No cloud-free Landsat pixels at this pin in the hot season."}

    vis = {"min": s["area"] - 5, "max": s["area"] + 5,
           "palette": ["313695", "74add1", "ffffbf", "fdae61", "d73027", "a50026"]}
    return {
        "source": src,
        "pin_c": round(s["pin"], 1),
        "area_2km_c": round(s["area"], 1),
        "delta_c": round(s["pin"] - s["area"], 1),
        "scenes": s["n"],
        "images": {"heat": _png(comp.visualize(**vis).blend(_pin(pt)), pt.buffer(2000).bounds())},
        "image_notes": ("4 km wide, north up, white ring = pin. Blue = cooler, red = hotter than the 2 km "
                        "average (+-5 C). Land surface temperature, not air temperature."),
    }


def fire(lat, lon, radius_km):
    _init()
    pt = ee.Geometry.Point([lon, lat])
    today = date.today()
    y1 = today.year if today.month >= 5 else today.year - 1  # last complete Jan-Apr dry season
    years = range(y1 - 4, y1 + 1)
    start = today.replace(year=today.year - 5)

    firms = ee.ImageCollection("FIRMS").select("T21")
    days = lambda c: c.map(lambda i: i.gt(0).unmask(0)).sum()  # fire-days per 1 km pixel
    rolling = firms.filterDate(start.isoformat(), today.isoformat())
    by_month = [days(rolling.filter(ee.Filter.calendarRange(m, m, "month"))).rename(f"m{m}") for m in range(1, 13)]
    dry = [days(firms.filterDate(f"{y}-01-01", f"{y}-05-01")).rename(f"y{y}") for y in years]
    w = ee.Image.cat(dry).reduce(ee.Reducer.sum())
    ll = ee.Image.pixelLonLat()
    dx = ll.select("longitude").subtract(lon).multiply(111.32 * math.cos(math.radians(lat)))
    dy = ll.select("latitude").subtract(lat).multiply(110.57)
    img = ee.Image.cat(by_month + dry + [w.rename("w"), w.multiply(dx).rename("wdx"), w.multiply(dy).rename("wdy"),
                                         w.multiply(dx.hypot(dy)).rename("wdist")])
    wind = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
            .filterDate(f"{years[0]}-01-01", f"{y1}-05-01").filter(ee.Filter.calendarRange(1, 4, "month"))
            .select(["u_component_of_wind_10m", "v_component_of_wind_10m"]).mean())
    out = ee.Dictionary({
        "r5": img.reduceRegion(ee.Reducer.sum(), pt.buffer(5000), 1000),
        "rr": img.reduceRegion(ee.Reducer.sum(), pt.buffer(radius_km * 1000), 1000, maxPixels=1e9),
        "wind": wind.reduceRegion(ee.Reducer.mean(), pt, 11132),
    }).getInfo()

    r5, rr, wd = out["r5"], out["rr"], out["wind"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    hot_dir = conc = wind_from = speed = None
    if rr.get("w"):
        hot_dir = math.degrees(math.atan2(rr["wdx"], rr["wdy"])) % 360
        conc = math.hypot(rr["wdx"], rr["wdy"]) / rr["wdist"] if rr.get("wdist") else None
        if conc is not None and conc < 0.3:
            hot_dir = None  # spread all around: no dominant direction
    u, v = wd.get("u_component_of_wind_10m"), wd.get("v_component_of_wind_10m")
    if u is not None and v is not None:
        speed = math.hypot(u, v)
        wind_from = math.degrees(math.atan2(-u, -v)) % 360 if speed >= 1 else None  # u/v blow towards
    upwind = None
    if hot_dir is not None and wind_from is not None:
        upwind = abs((hot_dir - wind_from + 180) % 360 - 180) <= 45

    return {
        "source": (f"NASA FIRMS active fire (MODIS, 1 km), dry seasons Jan-Apr {years[0]}-{y1} and monthly "
                   f"{start.isoformat()} to {today.isoformat()}; ERA5-Land 10 m wind, Jan-Apr"),
        "radius_km": radius_km,
        "dry_season_fire_days_5km": round(sum(r5.get(f"y{y}") or 0 for y in years)),
        "dry_season_fire_days_radius": round(sum(rr.get(f"y{y}") or 0 for y in years)),
        "by_year_dry_season_radius": {str(y): round(rr.get(f"y{y}") or 0) for y in years},
        "by_month_last_5y_radius": {months[m - 1]: round(rr.get(f"m{m}") or 0) for m in range(1, 13)},
        "hotspots_mostly_to": _compass(hot_dir),
        "hotspot_concentration": None if conc is None else round(conc, 2),
        "dry_season_wind_from": _compass(wind_from),
        "wind_speed_ms": None if speed is None else round(speed, 1),
        "hotspots_upwind": upwind,
        "note": ("Fire-days = days a 1 km MODIS pixel saw active fire, summed over pixels. hotspots_upwind = "
                 "the main hotspot direction is within 45 degrees of where the dry-season wind comes from."),
    }


def change(lat, lon):
    _init()
    pt = ee.Geometry.Point([lon, lat])
    near = pt.buffer(NEAR_M)
    today = date.today()
    y1 = today.year if today.month >= 5 else today.year - 1
    periods = {"before": ("2017-11-01", "2018-05-01"), "after": (f"{y1 - 1}-11-01", f"{y1}-05-01")}
    label = lambda p: f"Nov {p[0][:4]} - Apr {p[1][:4]}"

    dw = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(near).select("label")
    hist = ee.Dictionary({k: dw.filterDate(*p).mode().reduceRegion(ee.Reducer.frequencyHistogram(), near, 10,
                                                                    maxPixels=1e9)
                          for k, p in periods.items()}).getInfo()
    shares = {}
    for k, h in hist.items():
        h = next(iter(h.values()), None) or {}
        total = sum(h.values())
        shares[k] = {CLASSES[int(float(c))]: round(100 * n / total, 1) for c, n in h.items()} if total else None

    cs = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    region, pin = pt.buffer(1000).bounds(), _pin(pt)

    def chip(p):
        img = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED").filterBounds(pt).filterDate(*p)
               .linkCollection(cs, ["cs_cdf"]).map(lambda i: i.updateMask(i.select("cs_cdf").gte(0.6)))
               .median())
        return _png(img.visualize(bands=["B4", "B3", "B2"], min=0, max=3000, gamma=1.3).blend(pin), region)

    before, after = shares.get("before"), shares.get("after")
    delta = None
    if before and after:
        delta = {c: round(after.get(c, 0) - before.get(c, 0), 1) for c in CLASSES
                 if abs(after.get(c, 0) - before.get(c, 0)) >= 1}
    return {
        "source": (f"Dynamic World land cover + Sentinel-2 (Copernicus) dry-season composites, "
                   f"{label(periods['before'])} vs {label(periods['after'])}"),
        "before_period": label(periods["before"]),
        "after_period": label(periods["after"]),
        "land_cover_pct_1km": shares,
        "change_points_1km": delta,
        "images": {"before": chip(periods["before"]), "after": chip(periods["after"])},
        "image_notes": "Both 2 km wide, north up, white ring = pin, true colour.",
        **({} if delta is not None else {"note": "Land cover missing for one period."}),
    }


if __name__ == "__main__":
    pins = {p["id"]: p for p in json.loads((Path(__file__).parent / "pins.json").read_text())}
    png = lambda b: base64.b64decode(b)[:4] == b"\x89PNG"
    for pid, p in pins.items():
        lat, lon = p["lat"], p["lon"]
        res = {"water": water_history(lat, lon), "heat": heat(lat, lon), "fire": fire(lat, lon, 20),
               "change": change(lat, lon)}
        w = res["water"]
        if w["worst"]:
            res["flood"] = flood_day_images(lat, lon, w["worst"]["time"])
        for k, r in res.items():
            assert r.get("source"), (pid, k)
            json.dumps(r, allow_nan=False)
            assert all(png(b) for b in r.get("images", {}).values()), (pid, k)
        print(pid, "| events", w["flood_events"], "worst", w["worst"], "| hand", w["hand_m"],
              "| heat", res["heat"].get("delta_c"), "| fire", res["fire"]["dry_season_fire_days_radius"],
              "| change", res["change"]["change_points_1km"],
              "| pin wet", res.get("flood", {}).get("water_at_pin"))
        if pid == "maesai":
            assert any(e["time"].startswith("2024-09") for e in w["events"]), w["events"]
            assert res["flood"]["water_at_pin"], res["flood"]
        if pid == "control":
            assert w["flood_events"] == 0, w["events"]
    print("eo self-check passed")
