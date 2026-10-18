"""Offline tests for places.py: the distance and PM2.5 maths, and the Maps calls with the HTTP layer faked.

Run all Python tests from the repo root with `.venv/bin/python -m unittest`.
"""
import copy
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import requests

import places


THAI = timezone(timedelta(hours=7))  # the test's own clock: a wrong offset in places.py must not cancel out in the test data


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 18)


def hour(when, pm=None, code="pm25"):
    h = {"dateTime": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    if pm is not None:
        h["pollutants"] = [{"code": code, "concentration": {"value": pm}}]
    return h


def thai_day(day, value, hours=24):
    """`hours` hourly readings from 00:00 Thai time on `day`; `value` is a number or a function of the hour."""
    start = datetime(day.year, day.month, day.day, tzinfo=THAI)
    return [hour(start + timedelta(hours=i), value(i) if callable(value) else value) for i in range(hours)]


class KmTest(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertEqual(places.km(13.75, 100.5, 13.75, 100.5), 0)

    def test_one_degree_of_latitude(self):
        self.assertAlmostEqual(places.km(0, 0, 1, 0), 111.19, delta=0.01)

    def test_a_degree_of_longitude_shrinks_towards_the_poles(self):
        self.assertAlmostEqual(places.km(0, 100, 0, 101), 111.19, delta=0.01)
        self.assertAlmostEqual(places.km(60, 100, 60, 101), 55.6, delta=0.1)

    def test_is_symmetric(self):
        a, b = (13.7563, 100.5018), (18.7883, 98.9853)
        self.assertAlmostEqual(places.km(*a, *b), places.km(*b, *a), places=9)

    def test_bangkok_to_chiang_mai_in_a_straight_line(self):
        self.assertAlmostEqual(places.km(13.7563, 100.5018, 18.7883, 98.9853), 584, delta=5)

    def test_crossing_the_antimeridian_takes_the_short_way(self):
        self.assertAlmostEqual(places.km(0, 179.5, 0, -179.5), 111.19, delta=0.01)

    def test_opposite_points_are_half_the_circumference(self):
        self.assertAlmostEqual(places.km(0, 0, 0, 180), 20015.09, delta=0.1)


class Pm25DaysTest(unittest.TestCase):
    def test_no_hours_no_days(self):
        self.assertEqual(places.pm25_days([]), {})

    def test_daily_mean_of_the_hours(self):
        self.assertEqual(places.pm25_days(thai_day(date(2026, 10, 2), float)), {date(2026, 10, 2): 11.5})

    def test_days_follow_the_thai_calendar_not_utc(self):
        # 00:00-06:59 Thai time is still the previous day in UTC, but belongs to the Thai day
        days = places.pm25_days(thai_day(date(2026, 10, 2), 5.0))
        self.assertEqual(list(days), [date(2026, 10, 2)])

    def test_a_day_needs_18_hours_to_count(self):
        d = date(2026, 10, 2)
        self.assertEqual(places.pm25_days(thai_day(d, 5.0, 17)), {})
        self.assertEqual(places.pm25_days(thai_day(d, 5.0, 18)), {d: 5.0})

    def test_hours_without_pm25_are_ignored(self):
        d = date(2026, 10, 2)
        hours = thai_day(d, 8.0)
        hours += [{"dateTime": "2026-10-02T10:00:00Z"}, hour(datetime(2026, 10, 2, 11, tzinfo=timezone.utc), 99.0, "pm10")]
        self.assertEqual(places.pm25_days(hours), {d: 8.0})

    def test_pm25_is_picked_out_of_several_pollutants(self):
        d = date(2026, 10, 2)
        hours = thai_day(d, 8.0)
        for h in hours:
            h["pollutants"].insert(0, {"code": "no2", "concentration": {"value": 500.0}})
        self.assertEqual(places.pm25_days(hours), {d: 8.0})

    def test_a_clean_day_of_zeros_still_counts(self):
        d = date(2026, 10, 2)
        self.assertEqual(places.pm25_days(thai_day(d, 0.0)), {d: 0.0})

    def test_days_come_back_in_date_order(self):
        later, earlier = date(2026, 10, 3), date(2026, 10, 1)
        days = places.pm25_days(thai_day(later, 1.0) + thai_day(earlier, 2.0))
        self.assertEqual(list(days), [earlier, later])

    def test_offsets_other_than_z_parse_too(self):
        d = date(2026, 10, 2)
        hours = [{"dateTime": (datetime(2026, 10, 2, tzinfo=THAI) + timedelta(hours=i)).isoformat(),
                  "pollutants": [{"code": "pm25", "concentration": {"value": 3.0}}]} for i in range(24)]
        self.assertEqual(places.pm25_days(hours), {d: 3.0})


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))

    def json(self):
        return self.payload


class SessionTest(unittest.TestCase):
    def setUp(self):
        places._session.cache_clear()
        self.addCleanup(places._session.cache_clear)

    def test_session_uses_application_default_credentials_and_is_built_once(self):
        with mock.patch.object(places.google.auth, "default", return_value=("creds", "project")) as default, \
                mock.patch.object(places, "AuthorizedSession") as session:
            first, second = places._session(), places._session()
        default.assert_called_once_with(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        session.assert_called_once_with("creds")
        self.assertIs(first, second)


class PostTest(unittest.TestCase):
    def setUp(self):
        self.session = mock.Mock()
        patcher = mock.patch.object(places, "_session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_posts_json_with_a_timeout_and_returns_the_body(self):
        self.session.post.return_value = FakeResponse({"ok": 1})
        self.assertEqual(places._post("https://x", {"a": 1}, {"H": "v"}), {"ok": 1})
        self.session.post.assert_called_once_with("https://x", json={"a": 1}, timeout=15, headers={"H": "v"})

    def test_headers_are_optional(self):
        self.session.post.return_value = FakeResponse({})
        places._post("https://x", {})
        self.session.post.assert_called_once_with("https://x", json={}, timeout=15, headers=None)

    def test_an_http_error_is_raised_not_swallowed(self):
        self.session.post.return_value = FakeResponse({}, 403)
        with self.assertRaises(requests.HTTPError):
            places._post("https://x", {})


class MapsApiTest(unittest.TestCase):
    """places.py with `_post` replaced: `self.responses` are handed out in order, `self.calls` records (url, body, headers)."""

    def setUp(self):
        self.calls, self.responses = [], []

        def fake_post(url, body, headers=None):
            self.calls.append((url, copy.deepcopy(body), headers))  # air() reuses and edits one body
            return self.responses.pop(0)

        for patcher in (mock.patch.object(places, "_post", side_effect=fake_post), mock.patch.object(places, "date", FixedDate)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def place(name, lat, lon, address=None):
        p = {"displayName": {"text": name}, "location": {"latitude": lat, "longitude": lon}}
        if address is not None:
            p["formattedAddress"] = address
        return p

    # -- _places
    def test_places_posts_to_the_method_with_a_field_mask(self):
        self.responses = [{"places": [{"x": 1}]}]
        self.assertEqual(places._places("searchText", {"q": 1}, ["displayName", "location"]), [{"x": 1}])
        url, body, headers = self.calls[0]
        self.assertEqual(url, "https://places.googleapis.com/v1/places:searchText")
        self.assertEqual(body, {"q": 1})
        self.assertEqual(headers, {"X-Goog-FieldMask": "places.displayName,places.location"})

    def test_no_match_is_an_empty_list(self):
        self.responses = [{}]
        self.assertEqual(places._places("searchText", {}, ["displayName"]), [])

    # -- search
    def test_search_maps_places_and_is_biased_to_thailand(self):
        self.responses = [{"places": [self.place("Condo A", 13.7, 100.5, "1 Rd"), self.place("Village B", 18.8, 98.9)]}]
        self.assertEqual(places.search("condo", "th"), [
            {"name": "Condo A", "address": "1 Rd", "lat": 13.7, "lon": 100.5},
            {"name": "Village B", "address": "", "lat": 18.8, "lon": 98.9}])
        _, body, headers = self.calls[0]
        self.assertEqual(body, {"textQuery": "condo", "languageCode": "th", "regionCode": "TH", "pageSize": 5})
        self.assertEqual(headers["X-Goog-FieldMask"], "places.displayName,places.formattedAddress,places.location")

    def test_search_with_no_match_is_empty(self):
        self.responses = [{}]
        self.assertEqual(places.search("zzz", "en"), [])

    # -- find
    def test_find_lists_the_nearest_first_with_straight_line_km(self):
        self.responses = [{"places": [self.place("Far", 13.76, 100.5, "far st"), self.place("Near", 13.752, 100.5)]}]
        out = places.find(13.75, 100.5, "Big C", "en")
        self.assertEqual(out["nearest"], [{"name": "Near", "address": "", "km": 0.2}, {"name": "Far", "address": "far st", "km": 1.1}])
        self.assertEqual(out["query"], "Big C")
        self.assertEqual(out["source"], "Google Maps Places, retrieved 2026-10-18")
        self.assertIn("not travel time", out["note"])

    def test_find_ranks_by_distance_around_the_pin(self):
        self.responses = [{"places": []}]
        out = places.find(13.75, 100.5, "Big C", "th")
        self.assertEqual(out["nearest"], [])
        _, body, headers = self.calls[0]
        self.assertEqual(body, {"textQuery": "Big C", "languageCode": "th", "pageSize": 5, "rankPreference": "DISTANCE",
                                "locationBias": {"circle": {"center": {"latitude": 13.75, "longitude": 100.5}, "radius": 50000}}})
        self.assertEqual(headers["X-Goog-FieldMask"], "places.displayName,places.formattedAddress,places.location")

    # -- nearby
    def test_nearby_asks_once_per_daily_need_by_primary_type(self):
        self.responses = [{} for _ in places.KINDS]
        places.nearby(13.75, 100.5, "en")
        self.assertEqual(len(self.calls), len(places.KINDS))
        for (url, body, headers), types in zip(self.calls, places.KINDS.values()):
            self.assertEqual(url, "https://places.googleapis.com/v1/places:searchNearby")
            self.assertEqual(body, {"includedPrimaryTypes": types, "maxResultCount": 3, "rankPreference": "DISTANCE", "languageCode": "en",
                                    "locationRestriction": {"circle": {"center": {"latitude": 13.75, "longitude": 100.5}, "radius": 10000}}})
            self.assertEqual(headers, {"X-Goog-FieldMask": "places.displayName,places.location"})

    def test_nearby_sorts_each_need_nearest_first_and_keeps_empty_ones(self):
        self.responses = [{"places": [self.place("Mall far", 13.76, 100.5), self.place("Mall near", 13.752, 100.5)]}, {}, {}, {}]
        out = places.nearby(13.75, 100.5, "en")
        self.assertEqual(list(out["nearest_km"]), list(places.KINDS))
        self.assertEqual(out["nearest_km"]["shops"], [{"name": "Mall near", "km": 0.2}, {"name": "Mall far", "km": 1.1}])
        self.assertEqual(out["nearest_km"]["hospital"], [])
        self.assertEqual(out["source"], "Google Maps Places, retrieved 2026-10-18")
        self.assertIn("10 km", out["note"])

    # -- air
    def test_air_summarises_daily_means_across_pages(self):
        days = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
        hours = thai_day(days[0], 10.0) + thai_day(days[1], 50.0) + thai_day(days[2], places.PM25_LIMIT)
        self.responses = [{"hoursInfo": hours[:30], "nextPageToken": "page2"}, {"hoursInfo": hours[30:]}]
        out = places.air(13.75, 100.5)
        self.assertEqual(out, {
            "source": "Google Air Quality API, retrieved 2026-10-18", "period": "2026-10-01 to 2026-10-03",
            "pm25_mean_ug_m3": 32.5, "pm25_worst_day": {"date": "2026-10-02", "ug_m3": 50.0},
            "days_over_thai_standard": 1,  # 37.5 itself is at the standard, not over it
            "days": 3, "thai_standard_ug_m3": 37.5, "note": out["note"]})
        self.assertIn("last 30 days", out["note"])

    def test_air_follows_the_page_token_and_stops_without_one(self):
        self.responses = [{"hoursInfo": [], "nextPageToken": "page2"}, {"hoursInfo": [], "nextPageToken": "page3"}, {"hoursInfo": []}]
        places.air(13.75, 100.5)
        self.assertEqual(len(self.calls), 3)
        first, second, third = (body for _, body, _ in self.calls)
        self.assertEqual(first, {"hours": 720, "pageSize": 168, "location": {"latitude": 13.75, "longitude": 100.5},
                                 "extraComputations": ["POLLUTANT_CONCENTRATION"], "universalAqi": False})
        self.assertEqual(second, {**first, "pageToken": "page2"})
        self.assertEqual(third, {**first, "pageToken": "page3"})
        self.assertEqual({url for url, _, _ in self.calls}, {"https://airquality.googleapis.com/v1/history:lookup"})

    def test_air_without_usable_days_says_so_instead_of_guessing(self):
        for hours in ([], thai_day(date(2026, 10, 1), 5.0, 10)):  # nothing, or too few hours in the day
            self.responses = [{"hoursInfo": hours}]
            self.assertEqual(places.air(13.75, 100.5), {
                "source": "Google Air Quality API, retrieved 2026-10-18",
                "note": "No PM2.5 estimate for this location in the last 30 days."})


if __name__ == "__main__":
    unittest.main()
