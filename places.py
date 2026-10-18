"""Google Maps Platform: address search and the nearest daily needs (Places API New), and recent air (Air Quality API).

Calls run as the service account (OAuth), so there is no API key to keep. Both APIs' terms limit caching,
so nothing here goes through agent.cached.

`python places.py` checks the distance and PM2.5 maths offline.
"""
import functools
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import google.auth
from google.auth.transport.requests import AuthorizedSession

KINDS = {  # one Nearby Search per daily need, with its Places (New) types
    "shops": ["shopping_mall", "supermarket", "convenience_store"],
    "hospital": ["hospital"],
    "school": ["school", "primary_school", "secondary_school"],
    "transit": ["transit_station", "train_station", "subway_station", "bus_station"],
}
NEAR_KM = 10
PM25_LIMIT = 37.5  # Thailand's 24-hour PM2.5 standard in µg/m³ (Pollution Control Department, since June 2023)
BKK = timezone(timedelta(hours=7))


@functools.cache
def _session():
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)  # adds the quota project header for local user credentials


def _post(url, body, headers=None):
    r = _session().post(url, json=body, timeout=15, headers=headers)
    r.raise_for_status()
    return r.json()


def _places(method, body, fields):
    return _post(f"https://places.googleapis.com/v1/places:{method}", body,
                 {"X-Goog-FieldMask": ",".join("places." + f for f in fields)}).get("places", [])


def search(q, lang):
    """Up to 5 places for free text: a condo, a village, an address. Biased to Thailand, not limited to it."""
    return [{"name": p["displayName"]["text"], "address": p.get("formattedAddress", ""),
             "lat": p["location"]["latitude"], "lon": p["location"]["longitude"]}
            for p in _places("searchText", {"textQuery": q, "languageCode": lang, "regionCode": "TH", "pageSize": 5},
                             ["displayName", "formattedAddress", "location"])]


def find(lat, lon, q, lang):
    """Up to 5 places matching free text (a brand such as Big C, or a kind of place), nearest the pin first."""
    ps = _places("searchText", {"textQuery": q, "languageCode": lang, "pageSize": 5, "rankPreference": "DISTANCE",
                                "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 50000}}},
                 ["displayName", "formattedAddress", "location"])
    return {
        "source": f"Google Maps Places, retrieved {date.today().isoformat()}",
        "query": q,
        "nearest": sorted(({"name": p["displayName"]["text"], "address": p.get("formattedAddress", ""),
                            "km": round(km(lat, lon, p["location"]["latitude"], p["location"]["longitude"]), 1)}
                           for p in ps), key=lambda p: p["km"]),
        "note": "Straight-line distance from the pin, not travel time. A brand's names tell its formats apart "
                "(Big C Supercenter vs Big C Mini). An empty list means Google Maps found no match.",
    }


def km(lat, lon, lat2, lon2):
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def nearby(lat, lon, lang):
    """The 3 nearest of each daily need, distances computed here so the evidence rule holds."""
    # ponytail: saved reports keep these names; keep only place IDs (cacheable without limit) before production
    nearest = {}
    for kind, types in KINDS.items():
        ps = _places("searchNearby", {  # primary types: a clinic that also lists "hospital" is not one
            "includedPrimaryTypes": types, "maxResultCount": 3, "rankPreference": "DISTANCE", "languageCode": lang,
            "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": NEAR_KM * 1000}},
        }, ["displayName", "location"])
        nearest[kind] = sorted(({"name": p["displayName"]["text"],
                                 "km": round(km(lat, lon, p["location"]["latitude"], p["location"]["longitude"]), 1)}
                                for p in ps), key=lambda p: p["km"])
    return {
        "source": f"Google Maps Places, retrieved {date.today().isoformat()}",
        "nearest_km": nearest,
        "note": f"Straight-line distance from the pin, not travel time. An empty list means none within {NEAR_KM} km.",
    }


def pm25_days(hours):
    """Daily mean PM2.5 by Thai calendar day from Air Quality API hours. Days with under 18 hours are dropped,
    so a 24-hour mean is fair to compare with the 24-hour standard."""
    by_day = defaultdict(list)
    for h in hours:
        pm = next((p["concentration"]["value"] for p in h.get("pollutants", []) if p["code"] == "pm25"), None)
        if pm is not None:
            by_day[datetime.fromisoformat(h["dateTime"].replace("Z", "+00:00")).astimezone(BKK).date()].append(pm)
    return {d: sum(v) / len(v) for d, v in sorted(by_day.items()) if len(v) >= 18}


def air(lat, lon):
    """PM2.5 at the pin over the last 30 days, the most history the API keeps (168 hours per page)."""
    body = {"hours": 720, "pageSize": 168, "location": {"latitude": lat, "longitude": lon},
            "extraComputations": ["POLLUTANT_CONCENTRATION"], "universalAqi": False}
    hours = []
    while True:
        j = _post("https://airquality.googleapis.com/v1/history:lookup", body)
        hours += j.get("hoursInfo", [])
        if not j.get("nextPageToken"):
            break
        body["pageToken"] = j["nextPageToken"]
    src = f"Google Air Quality API, retrieved {date.today().isoformat()}"
    daily = pm25_days(hours)
    if not daily:
        return {"source": src, "note": "No PM2.5 estimate for this location in the last 30 days."}
    worst = max(daily, key=daily.get)
    return {
        "source": src,
        "period": f"{min(daily).isoformat()} to {max(daily).isoformat()}",
        "pm25_mean_ug_m3": round(sum(daily.values()) / len(daily), 1),
        "pm25_worst_day": {"date": worst.isoformat(), "ug_m3": round(daily[worst], 1)},
        "days_over_thai_standard": sum(v > PM25_LIMIT for v in daily.values()),
        "days": len(daily),
        "thai_standard_ug_m3": PM25_LIMIT,
        "note": "Google's modelled estimate for this spot, not a monitor at the house. Only the last 30 days: it says "
                "nothing about other seasons, and smoke is worst in the dry season (Dec-Apr).",
    }


if __name__ == "__main__":
    assert km(13.75, 100.5, 13.75, 100.5) == 0
    assert abs(km(0, 0, 1, 0) - 111.19) < 0.01, km(0, 0, 1, 0)  # one degree of latitude
    assert abs(km(13.7563, 100.5018, 18.7883, 98.9853) - 584) < 5  # Bangkok to Chiang Mai, straight line
    hours = [{"dateTime": (datetime(2026, 10, 1, 16, tzinfo=timezone.utc) + timedelta(hours=i)).isoformat()
              .replace("+00:00", "Z"), "pollutants": [{"code": "pm25", "concentration": {"value": float(i)}}]}
             for i in range(25)]  # 23:00 Thai time on 1 Oct, then all 24 hours of 2 Oct
    assert pm25_days(hours) == {date(2026, 10, 2): 12.5}, pm25_days(hours)  # the lone hour of 1 Oct is dropped
    print("places self-check passed")
