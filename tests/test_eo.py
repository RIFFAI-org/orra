"""Offline tests for eo.py. Earth Engine is replaced by a MagicMock whose getInfo() answers are canned, so these cover what
eo.py does in Python with the answers (episodes, shares, wind, deltas, rounding, the date logic) and that the thresholds are
wired in. Nothing here can reach Earth Engine, Google auth or the network.

Run all Python tests from the repo root with `.venv/bin/python -m unittest`.
"""
import base64
import math
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import eo

UTC = timezone.utc
T0 = datetime(2024, 9, 1, 11, 0, tzinfo=UTC)


def ms(when):
    return int(when.timestamp() * 1000)


def iso(when):
    return when.isoformat(timespec="minutes")


def fixed_date(y, m, d):
    class Fixed(date):
        @classmethod
        def today(cls):
            return cls(y, m, d)
    return Fixed


class EeTestCase(unittest.TestCase):
    """eo.ee, eo._init and eo.date replaced; `self.ee.Dictionary.return_value.getInfo` is where an EE answer is set."""
    TODAY = (2026, 10, 18)

    def setUp(self):
        self.ee = mock.MagicMock(name="ee")
        for patcher in (mock.patch.object(eo, "ee", self.ee), mock.patch.object(eo, "_init"),
                        mock.patch.object(eo, "date", fixed_date(*self.TODAY))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def answer(self, info):
        self.ee.Dictionary.return_value.getInfo.return_value = info


class HelpersTest(unittest.TestCase):
    def test_iso_is_utc_to_the_minute(self):
        self.assertEqual(eo._iso(ms(datetime(2024, 9, 15, 23, 16, 59, tzinfo=UTC))), "2024-09-15T23:16+00:00")

    def test_compass_names_the_eight_points(self):
        for deg, name in ((0, "N"), (44, "NE"), (90, "E"), (135, "SE"), (180, "S"), (225, "SW"), (270, "W"), (315, "NW"), (359.9, "N"),
                          (350, "N"), (10, "N"), (100, "E")):
            self.assertEqual(eo._compass(deg), name, deg)

    def test_compass_of_nothing_is_nothing(self):
        self.assertIsNone(eo._compass(None))

    def test_png_downloads_the_thumbnail_and_base64s_it(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"\x89PNG-data"
        image = mock.Mock()
        image.getThumbURL.return_value = "https://thumb"
        with mock.patch.object(eo.urllib.request, "urlopen", return_value=response) as urlopen:
            out = eo._png(image, "REGION")
            eo._png(image, "REGION", 256)
        self.assertEqual(base64.b64decode(out), b"\x89PNG-data")
        self.assertEqual([c.args[0] for c in image.getThumbURL.call_args_list],
                         [{"region": "REGION", "dimensions": 512, "format": "png"}, {"region": "REGION", "dimensions": 256, "format": "png"}])
        urlopen.assert_called_with("https://thumb", timeout=120)


class InitTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(eo, "_ready", False), mock.patch.object(eo, "ee"),
                        mock.patch.object(eo.google.auth, "default", return_value=("creds", "adc-project")),
                        mock.patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "my-project"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_initialises_once_with_the_project_never_bare(self):
        eo._init()
        eo._init()
        eo.ee.Initialize.assert_called_once_with("creds", project="my-project")
        self.assertTrue(eo._ready)

    def test_without_a_project_it_fails_and_stays_uninitialised(self):
        with mock.patch.dict(os.environ):
            del os.environ["GOOGLE_CLOUD_PROJECT"]
            with self.assertRaises(KeyError):
                eo._init()
        self.assertFalse(eo._ready)
        eo.ee.Initialize.assert_not_called()


class ThresholdsTest(EeTestCase):
    """The numbers every verdict rests on are the ones passed to Earth Engine."""

    def test_sentinel1_search_is_vv_iw_since_border_noise_removal_to_tomorrow(self):
        eo._s1("PT")
        ic = self.ee.ImageCollection
        ic.assert_called_once_with("COPERNICUS/S1_GRD")
        ic.return_value.filterBounds.assert_called_once_with("PT")
        self.ee.Filter.eq.assert_called_once_with("instrumentMode", "IW")
        self.ee.Filter.listContains.assert_called_once_with("transmitterReceiverPolarisation", "VV")
        dated = ic.return_value.filterBounds.return_value.filter.return_value.filter.return_value
        dated.filterDate.assert_called_once_with("2018-01-12", "2026-10-19")
        dated.filterDate.return_value.select.assert_called_once_with("VV")

    def test_flood_prone_ground_is_not_permanent_water_and_within_20_m_of_drainage(self):
        eo._flood_prone()
        self.assertEqual([c.args[0] for c in self.ee.Image.call_args_list], ["JRC/GSW1_4/GlobalSurfaceWater", "MERIT/Hydro/v1_0_1"])
        picked = self.ee.Image.return_value.select.return_value
        picked.unmask.assert_called_once_with(0)  # never-water is masked in JRC; it must count as 0 %
        picked.unmask.return_value.lt.assert_called_once_with(eo.PERMANENT_PCT)
        picked.lte.assert_called_once_with(eo.HAND_MAX_M)
        self.assertEqual((eo.PERMANENT_PCT, eo.HAND_MAX_M), (50, 20))

    def test_a_pixel_is_flooded_when_dark_and_much_darker_than_usual_on_flood_prone_ground(self):
        img, base, prone = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        out = eo._flooded(img, base, prone)
        vv = img.focalMedian.return_value
        img.focalMedian.assert_called_once_with(30, "circle", "meters")  # speckle smoothing
        vv.lt.assert_called_once_with(eo.WATER_DB)
        vv.subtract.assert_called_once_with(base)
        vv.subtract.return_value.lt.assert_called_once_with(eo.DROP_DB)
        vv.lt.return_value.And.assert_called_once_with(vv.subtract.return_value.lt.return_value)
        vv.lt.return_value.And.return_value.updateMask.assert_called_once_with(prone)
        self.assertIs(out, vv.lt.return_value.And.return_value.updateMask.return_value.rename.return_value)
        self.assertEqual((eo.WATER_DB, eo.DROP_DB), (-17, -3))


class WaterHistoryTest(EeTestCase):
    def run_history(self, rows, site=None, orbits=(99,)):
        col = mock.MagicMock()
        col.aggregate_array.return_value.distinct.return_value.getInfo.return_value = list(orbits)
        self.answer({"rows": rows, "site": {"hand": 3.04, "occurrence": 0} if site is None else site})
        with mock.patch.object(eo, "_s1", return_value=col):
            return eo.water_history(20.0, 100.0)

    @staticmethod
    def row(days, share, count=100, orbit=99, minutes=0):
        when = T0 + timedelta(days=days, minutes=minutes)
        return {"properties": {"f_mean": share, "f_count": count, "t": ms(when), "orbit": orbit}}

    def when(self, days, minutes=0):
        return iso(T0 + timedelta(days=days, minutes=minutes))

    def test_passes_are_clustered_into_flood_episodes_with_their_worst_pass(self):
        out = self.run_history([self.row(0, 0.02), self.row(6, 0.10), self.row(12, 0.30), self.row(18, 0.04), self.row(60, 0.06)])
        a = {"time": self.when(12), "share_flooded_1km": 0.3, "wet_passes": 2, "start": self.when(6), "end": self.when(12)}
        b = {"time": self.when(60), "share_flooded_1km": 0.06, "wet_passes": 1, "start": self.when(60), "end": self.when(60)}
        self.assertEqual(out["events"], [b, a])  # newest first
        self.assertEqual((out["flood_events"], out["worst"], out["latest"]), (2, a, b))
        self.assertEqual(out["passes"], 5)  # dry passes count as passes
        self.assertEqual(out["source"], "Sentinel-1 radar (Copernicus), 5 passes 2024-09-01 to 2024-10-31; "
                                        "JRC Global Surface Water 1984-2021; MERIT Hydro HAND")

    def test_five_percent_of_flood_prone_ground_is_the_least_that_makes_a_wet_pass(self):
        out = self.run_history([self.row(0, 0.0499), self.row(40, 0.05)])
        self.assertEqual([e["time"] for e in out["events"]], [self.when(40)])

    def test_wet_passes_16_days_apart_are_one_episode_17_are_two(self):
        self.assertEqual(self.run_history([self.row(0, 0.2), self.row(16, 0.2)])["flood_events"], 1)
        self.assertEqual(self.run_history([self.row(0, 0.2), self.row(17, 0.2)])["flood_events"], 2)

    def test_a_dry_pass_in_between_does_not_bridge_two_floods(self):
        out = self.run_history([self.row(0, 0.2), self.row(10, 0.0), self.row(20, 0.2)])
        self.assertEqual((out["flood_events"], out["passes"]), (2, 3))

    def test_the_gap_is_measured_from_the_last_wet_pass_so_a_long_flood_stays_one_episode(self):
        out = self.run_history([self.row(d, 0.2) for d in (0, 15, 30, 45)])
        self.assertEqual(out["flood_events"], 1)
        self.assertEqual((out["events"][0]["wet_passes"], out["events"][0]["start"], out["events"][0]["end"]), (4, self.when(0), self.when(45)))

    def test_the_episode_is_dated_by_its_first_worst_pass(self):
        out = self.run_history([self.row(0, 0.4), self.row(6, 0.4), self.row(12, 0.2)])
        self.assertEqual(out["events"][0]["time"], self.when(0))

    def test_slices_of_one_orbit_on_one_day_are_one_pass_weighted_by_pixels(self):
        out = self.run_history([self.row(3, 0.10, count=100, minutes=1), self.row(3, 0.30, count=300, minutes=0)])
        self.assertEqual(out["passes"], 1)
        (event,) = out["events"]
        self.assertEqual((event["share_flooded_1km"], event["time"]), (0.25, self.when(3)))  # earliest slice dates the pass

    def test_the_same_day_on_two_orbits_is_two_passes(self):
        out = self.run_history([self.row(3, 0.10, orbit=99), self.row(3, 0.10, orbit=128)])
        self.assertEqual(out["passes"], 2)

    def test_slices_with_no_valid_pixels_are_skipped_not_counted_as_dry(self):
        out = self.run_history([self.row(0, None, count=0), {"properties": {"f_mean": 0.5, "t": ms(T0), "orbit": 99}}, self.row(6, 0.2)])
        self.assertEqual((out["passes"], out["flood_events"]), (1, 1))

    def test_only_the_twelve_latest_events_are_listed_but_all_are_counted(self):
        out = self.run_history([self.row(40 * i, 0.2) for i in range(15)])
        self.assertEqual(out["flood_events"], 15)
        self.assertEqual(len(out["events"]), 12)
        self.assertEqual(out["events"][0]["time"], self.when(40 * 14))
        self.assertEqual(out["latest"]["time"], self.when(40 * 14))

    def test_height_above_drainage_and_permanent_water_are_rounded(self):
        out = self.run_history([self.row(0, 0.0)], site={"hand": 4.26, "occurrence": 12.34})
        self.assertEqual((out["hand_m"], out["permanent_water_pct_at_pin"]), (4.3, 12.3))

    def test_unknown_height_stays_unknown_not_zero(self):
        out = self.run_history([self.row(0, 0.0)], site={"hand": None, "occurrence": None})
        self.assertEqual((out["hand_m"], out["permanent_water_pct_at_pin"]), (None, 0.0))
        self.assertIsNone(self.run_history([self.row(0, 0.0)], site={})["hand_m"])

    def test_no_usable_passes_says_so_and_has_no_events(self):
        out = self.run_history([])
        self.assertEqual((out["passes"], out["flood_events"], out["events"], out["worst"], out["latest"]), (0, 0, [], None, None))
        self.assertEqual(out["source"], "Sentinel-1 radar (Copernicus); no usable passes")
        self.assertEqual(out["note"], "No radar passes with flood-prone ground within 1 km to measure.")

    def test_passes_without_floods_have_a_quiet_history(self):
        out = self.run_history([self.row(0, 0.01), self.row(12, 0.0)])
        self.assertEqual((out["passes"], out["flood_events"], out["worst"]), (2, 0, None))
        self.assertIn("HAND = height above nearest drainage", out["note"])

    def test_a_baseline_is_built_for_every_orbit_and_month(self):
        self.run_history([self.row(0, 0.0)], orbits=(1, 2, 3))
        (groups,) = self.ee.FeatureCollection.call_args.args
        self.assertEqual(len(groups), 3 * 12)

    def test_the_pin_is_longitude_first_and_the_search_is_a_kilometre_around_it(self):
        self.run_history([self.row(0, 0.0)])
        self.ee.Geometry.Point.assert_called_once_with([100.0, 20.0])
        self.ee.Geometry.Point.return_value.buffer.assert_any_call(eo.NEAR_M)
        self.assertEqual(eo.NEAR_M, 1000)


class FloodDayImagesTest(EeTestCase):
    PASS = datetime(2024, 9, 15, 23, 20, tzinfo=UTC)
    INFO = {"system:time_start": ms(PASS), "relativeOrbitNumber_start": 128, "orbitProperties_pass": "DESCENDING"}
    STATS = {"km": 0.12345, "m300": 0.5, "pin": 1, "usual": -8.123}

    def run_images(self, event_time="2024-09-15T23:16+00:00", window_days=None, info=INFO, found=3, stats=STATS):
        s1 = mock.MagicMock()
        window = s1.filterDate.return_value
        window.size.return_value.getInfo.return_value = found
        window.map.return_value.sort.return_value.first.return_value.toDictionary.return_value.getInfo.return_value = info
        self.answer(stats)
        args = () if window_days is None else (window_days,)
        with mock.patch.object(eo, "_s1", return_value=s1), mock.patch.object(eo, "_png", side_effect=["BEFORE", "AFTER"]) as png:
            return eo.flood_day_images(20.0, 100.0, event_time, *args), s1, png

    def test_a_pass_near_the_event_gives_facts_chips_and_a_dated_source(self):
        out, _, png = self.run_images()
        self.assertEqual(out["pass_time"], "2024-09-15T23:20+00:00")
        self.assertEqual(out["source"], "Sentinel-1 radar pass 2024-09-15T23:20+00:00 UTC (orbit 128, descending) "
                                        "vs median of the same orbit in month 9, 2018-now")
        self.assertEqual(out["days_from_request"], 0.0)
        self.assertEqual((out["share_flooded_1km"], out["share_flooded_300m"]), (0.123, 0.5))
        self.assertEqual((out["water_at_pin"], out["usual_vv_at_pin_db"], out["pin_built_up"]), (True, -8.1, False))
        self.assertEqual(out["images"], {"before": "BEFORE", "after": "AFTER"})
        self.assertIn("north up", out["image_notes"])
        self.assertEqual(png.call_count, 2)

    def test_the_search_window_is_a_day_either_side_unless_asked_otherwise(self):
        _, s1, _ = self.run_images("2024-09-15T23:16+00:00")
        self.assertEqual(s1.filterDate.call_args_list[0].args, ("2024-09-14T23:16:00+00:00", "2024-09-16T23:16:00+00:00"))
        _, s1, _ = self.run_images("2024-09-15T23:16+00:00", window_days=6)  # the claim check
        self.assertEqual(s1.filterDate.call_args_list[0].args, ("2024-09-09T23:16:00+00:00", "2024-09-21T23:16:00+00:00"))

    def test_only_that_one_pass_and_orbit_are_used_and_the_baseline_is_its_calendar_month(self):
        _, s1, _ = self.run_images()
        self.assertEqual(s1.filterDate.call_args_list[1].args, ("2024-09-15T22:20:00+00:00", "2024-09-16T00:20:00+00:00"))
        self.ee.Filter.eq.assert_any_call("relativeOrbitNumber_start", 128)
        self.ee.Filter.calendarRange.assert_any_call(9, 9, "month")

    def test_a_z_suffix_a_date_without_time_and_a_zone_are_all_understood(self):
        for event_time, day_before in (("2024-09-15T23:16:00Z", datetime(2024, 9, 14, 23, 16, tzinfo=UTC)),
                                       ("2024-09-15", datetime(2024, 9, 14, tzinfo=UTC)),  # no zone means UTC
                                       ("2024-09-15T23:16:00+07:00", datetime(2024, 9, 14, 16, 16, tzinfo=UTC))):
            with self.subTest(event_time):
                _, s1, _ = self.run_images(event_time)
                self.assertEqual(datetime.fromisoformat(s1.filterDate.call_args_list[0].args[0]), day_before)  # the same instant, however written

    def test_days_from_request_is_signed_and_rounded_to_a_tenth(self):
        before, after = datetime(2024, 9, 15, 12, tzinfo=UTC), datetime(2024, 9, 15, 17, tzinfo=UTC)
        out, *_ = self.run_images("2024-09-15T00:00:00Z", info={**self.INFO, "system:time_start": ms(before)})
        self.assertEqual(out["days_from_request"], 0.5)
        out, *_ = self.run_images("2024-09-15T23:00:00Z", info={**self.INFO, "system:time_start": ms(after)})
        self.assertEqual(out["days_from_request"], -0.2)

    def test_no_pass_in_the_window_says_so(self):
        out, _, png = self.run_images("2024-09-15T23:16+00:00", found=0)
        self.assertEqual(out, {"source": "Sentinel-1 radar", "note": "No radar pass within 1 day(s) of 2024-09-15T23:16+00:00."})
        png.assert_not_called()
        out, *_ = self.run_images("2024-09-12", window_days=6, found=0)
        self.assertEqual(out["note"], "No radar pass within 6 day(s) of 2024-09-12.")

    def test_a_pin_the_radar_could_not_measure_is_unknown_not_dry(self):
        out, *_ = self.run_images(stats={"km": None, "m300": None, "pin": None, "usual": None})
        self.assertEqual((out["share_flooded_1km"], out["share_flooded_300m"]), (None, None))
        self.assertEqual((out["water_at_pin"], out["usual_vv_at_pin_db"], out["pin_built_up"]), (None, None, False))
        out, *_ = self.run_images(stats={})
        self.assertIsNone(out["water_at_pin"])

    def test_no_water_at_the_pin_is_false_not_unknown(self):
        out, *_ = self.run_images(stats={**self.STATS, "pin": 0})
        self.assertIs(out["water_at_pin"], False)

    def test_bright_ground_is_flagged_built_up_because_dry_there_proves_nothing(self):
        for usual, built in ((-5.0, True), (-5.99, True), (-6.0, False), (-9.0, False)):
            with self.subTest(usual=usual):
                out, *_ = self.run_images(stats={**self.STATS, "usual": usual})
                self.assertIs(out["pin_built_up"], built)
        self.assertEqual(eo.BUILT_DB, -6)

    def test_the_pin_is_longitude_first(self):
        self.run_images()
        self.ee.Geometry.Point.assert_called_once_with([100.0, 20.0])


class HeatTest(EeTestCase):
    @property
    def collection(self):
        return (self.ee.ImageCollection.return_value.merge.return_value.filterBounds.return_value.filterDate.return_value
                .filter.return_value.map.return_value)

    def run_heat(self, info=None, today=None):
        self.answer(info or {"n": 12, "pin": 40.96, "area": 38.04})
        with mock.patch.object(eo, "_png", return_value="HEATPNG"):
            if today:
                with mock.patch.object(eo, "date", fixed_date(*today)):
                    return eo.heat(20.0, 100.0)
            return eo.heat(20.0, 100.0)

    def test_pin_area_and_difference_come_from_the_unrounded_values(self):
        out = self.run_heat()
        self.assertEqual((out["pin_c"], out["area_2km_c"], out["delta_c"], out["scenes"]), (41.0, 38.0, 2.9, 12))  # not 41.0 - 38.0
        self.assertEqual(out["images"], {"heat": "HEATPNG"})
        self.assertIn("not air temperature", out["image_notes"])

    def test_source_names_the_three_latest_complete_hot_seasons(self):
        self.assertIn("Mar-May 2024-2026, 12 scenes", self.run_heat(today=(2026, 10, 18))["source"])
        self.assertIn("Mar-May 2023-2025, 12 scenes", self.run_heat(today=(2026, 5, 31))["source"])  # this year's season is not over
        self.assertIn("Mar-May 2024-2026, 12 scenes", self.run_heat(today=(2026, 6, 1))["source"])
        self.assertTrue(self.run_heat()["source"].startswith("Landsat 8/9 Collection 2 land surface temperature"))

    def test_both_landsats_are_merged_and_the_dates_span_the_three_seasons(self):
        self.run_heat()
        self.assertEqual([c.args[0] for c in self.ee.ImageCollection.call_args_list], ["LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"])
        self.ee.ImageCollection.return_value.merge.return_value.filterBounds.return_value.filterDate.assert_called_once_with("2024-01-01", "2026-12-31")
        self.ee.Filter.calendarRange.assert_called_once_with(3, 5, "month")

    def test_the_colour_scale_is_five_degrees_either_side_of_the_area_average(self):
        self.run_heat()
        kwargs = self.collection.median.return_value.visualize.call_args.kwargs
        self.assertEqual((kwargs["min"], kwargs["max"]), (38.04 - 5, 38.04 + 5))
        self.assertEqual(len(kwargs["palette"]), 6)

    def test_no_clear_pixels_is_a_note_and_no_chip_not_a_zero(self):
        for info in ({"n": 3, "pin": None, "area": 38.0}, {"n": 3, "pin": 40.0, "area": None}, {"n": 0}):
            with self.subTest(info), mock.patch.object(eo, "_png") as png:
                self.answer(info)
                out = eo.heat(20.0, 100.0)
                self.assertEqual(out["note"], "No cloud-free Landsat pixels at this pin in the hot season.")
                self.assertNotIn("delta_c", out)
                png.assert_not_called()

    def test_the_pin_is_longitude_first(self):
        self.run_heat()
        self.ee.Geometry.Point.assert_called_once_with([100.0, 20.0])


class Band:
    """Just enough of an ee.Image band to run heat()'s Landsat closure on plain numbers."""

    def __init__(self, v, masked=False):
        self.v, self.masked = v, masked

    def multiply(self, k):
        return Band(self.v * k, self.masked)

    def add(self, k):
        return Band(self.v + k, self.masked)

    def subtract(self, k):
        return Band(self.v - k, self.masked)

    def bitwiseAnd(self, k):
        return Band(int(self.v) & k)

    def eq(self, k):
        return Band(self.v == k)

    def updateMask(self, mask):
        return Band(self.v, self.masked or not mask.v)

    def rename(self, name):
        self.name = name
        return self


class Scene:
    def __init__(self, st, qa):
        self.bands = {"ST_B10": Band(st), "QA_PIXEL": Band(qa)}

    def select(self, name):
        return self.bands[name]


class LandsatTemperatureTest(EeTestCase):
    def lst(self):
        self.answer({"n": 1, "pin": 40.0, "area": 38.0})
        with mock.patch.object(eo, "_png"):
            eo.heat(20.0, 100.0)
        return self.ee.ImageCollection.return_value.merge.return_value.filterBounds.return_value.filterDate.return_value.filter.return_value.map.call_args.args[0]

    def test_raw_st_b10_becomes_celsius(self):
        out = self.lst()(Scene(44000, 0))
        self.assertAlmostEqual(out.v, 44000 * 0.00341802 + 149.0 - 273.15, places=6)
        self.assertAlmostEqual(out.v, 26.243, places=3)
        self.assertFalse(out.masked)
        self.assertEqual(out.name, "lst")

    def test_fill_dilated_cloud_cirrus_cloud_and_shadow_pixels_are_masked(self):
        lst = self.lst()
        for bit in range(5):
            with self.subTest(bit=bit):
                self.assertTrue(lst(Scene(44000, 1 << bit)).masked)

    def test_other_quality_flags_such_as_snow_do_not_mask(self):
        self.assertFalse(self.lst()(Scene(44000, 1 << 5)).masked)


class FireTest(EeTestCase):
    @staticmethod
    def toward(deg, size=10.0):
        """Hotspot sums that point at compass bearing `deg`, fully concentrated."""
        return {"wdx": size * math.sin(math.radians(deg)), "wdy": size * math.cos(math.radians(deg)), "wdist": size}

    @staticmethod
    def blowing_from(deg, speed=3.0):
        return {"u_component_of_wind_10m": speed * math.sin(math.radians(deg + 180)),
                "v_component_of_wind_10m": speed * math.cos(math.radians(deg + 180))}

    def run_fire(self, r5=None, rr=None, wind=None, radius=20, today=None):
        self.answer({"r5": r5 or {}, "rr": rr or {}, "wind": wind or {}})
        if today:
            with mock.patch.object(eo, "date", fixed_date(*today)):
                return eo.fire(20.0, 100.0, radius)
        return eo.fire(20.0, 100.0, radius)

    def test_a_typical_dry_season(self):
        rr = {"y2022": 10.4, "y2023": 20.6, "y2024": 0, "y2025": 5, "y2026": None, "m1": 1.2, "m3": 40.6, "m12": 3, "w": 36,
              "wdx": 0.0, "wdy": 30.0, "wdist": 100.0}
        r5 = {"y2022": 1.4, "y2023": 2.2, "y2025": 1}
        out = self.run_fire(r5=r5, rr=rr, wind={"u_component_of_wind_10m": 0.0, "v_component_of_wind_10m": -3.0}, radius=20)
        self.assertEqual(out["radius_km"], 20)
        self.assertEqual(out["dry_season_fire_days_radius"], 36)
        self.assertEqual(out["dry_season_fire_days_5km"], 5)
        self.assertEqual(out["by_year_dry_season_radius"], {"2022": 10, "2023": 21, "2024": 0, "2025": 5, "2026": 0})
        self.assertEqual(list(out["by_month_last_5y_radius"]), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        self.assertEqual((out["by_month_last_5y_radius"]["Jan"], out["by_month_last_5y_radius"]["Mar"], out["by_month_last_5y_radius"]["Dec"],
                          out["by_month_last_5y_radius"]["Jul"]), (1, 41, 3, 0))
        self.assertEqual((out["hotspots_mostly_to"], out["hotspot_concentration"]), ("N", 0.3))
        self.assertEqual((out["dry_season_wind_from"], out["wind_speed_ms"], out["hotspots_upwind"]), ("N", 3.0, True))

    def test_the_source_names_the_dry_seasons_and_the_monthly_window(self):
        self.assertIn("dry seasons Jan-Apr 2022-2026 and monthly 2021-10-18 to 2026-10-18", self.run_fire(today=(2026, 10, 18))["source"])
        out = self.run_fire(today=(2026, 3, 1), rr={"y2021": 1, "y2025": 2})  # this year's dry season is not complete
        self.assertIn("dry seasons Jan-Apr 2021-2025 and monthly 2021-03-01 to 2026-03-01", out["source"])
        self.assertEqual(list(out["by_year_dry_season_radius"]), ["2021", "2022", "2023", "2024", "2025"])
        self.assertEqual(self.run_fire(today=(2026, 5, 1))["by_year_dry_season_radius"].keys(), {"2022", "2023", "2024", "2025", "2026"})

    def test_hotspots_are_reported_towards_the_direction_they_lie_in(self):
        for deg, name in ((0, "N"), (45, "NE"), (90, "E"), (135, "SE"), (180, "S"), (225, "SW"), (270, "W"), (315, "NW")):
            with self.subTest(deg=deg):
                out = self.run_fire(rr={"w": 5, **self.toward(deg)})
                self.assertEqual((out["hotspots_mostly_to"], out["hotspot_concentration"]), (name, 1.0))

    def test_wind_is_reported_as_where_it_comes_from_not_where_it_blows(self):
        for deg, name in ((0, "N"), (90, "E"), (180, "S"), (270, "W"), (225, "SW")):
            with self.subTest(deg=deg):
                out = self.run_fire(wind=self.blowing_from(deg, 4.0))
                self.assertEqual((out["dry_season_wind_from"], out["wind_speed_ms"]), (name, 4.0))
        self.assertEqual(self.run_fire(wind={"u_component_of_wind_10m": 0.0, "v_component_of_wind_10m": -3.0})["dry_season_wind_from"], "N")

    def test_upwind_means_within_45_degrees_of_where_the_wind_comes_from(self):
        for hot, wind, upwind in ((0, 44, True), (0, 46, False), (10, 350, True), (350, 10, True), (90, 90, True), (180, 0, False),
                                  (90, 270, False), (200, 160, True)):
            with self.subTest(hot=hot, wind=wind):
                out = self.run_fire(rr={"w": 5, **self.toward(hot)}, wind=self.blowing_from(wind))
                self.assertIs(out["hotspots_upwind"], upwind)

    def test_a_light_breeze_has_no_direction_so_nothing_is_upwind(self):
        out = self.run_fire(rr={"w": 5, **self.toward(0)}, wind={"u_component_of_wind_10m": 0.5, "v_component_of_wind_10m": 0.5})
        self.assertEqual((out["dry_season_wind_from"], out["wind_speed_ms"], out["hotspots_upwind"]), (None, 0.7, None))

    def test_hotspots_spread_all_around_have_no_dominant_direction(self):
        out = self.run_fire(rr={"w": 5, "wdx": 10.0, "wdy": 10.0, "wdist": 100.0}, wind=self.blowing_from(225))
        self.assertEqual((out["hotspots_mostly_to"], out["hotspot_concentration"], out["hotspots_upwind"]), (None, 0.14, None))
        edge = self.run_fire(rr={"w": 5, "wdx": 0.0, "wdy": 29.9, "wdist": 100.0})  # 0.299 is still spread out, 0.3 is not
        self.assertIsNone(edge["hotspots_mostly_to"])
        self.assertEqual(self.run_fire(rr={"w": 5, "wdx": 0.0, "wdy": 30.0, "wdist": 100.0})["hotspots_mostly_to"], "N")

    def test_no_fires_means_no_direction_even_when_the_wind_is_known(self):
        out = self.run_fire(rr={"w": 0, "wdx": 0, "wdy": 0, "wdist": 0}, wind=self.blowing_from(90))
        self.assertEqual((out["hotspots_mostly_to"], out["hotspot_concentration"], out["hotspots_upwind"]), (None, None, None))
        self.assertEqual(out["dry_season_wind_from"], "E")
        self.assertEqual(out["dry_season_fire_days_radius"], 0)

    def test_missing_wind_is_unknown_not_calm(self):
        out = self.run_fire(rr={"w": 5, **self.toward(0)}, wind={})
        self.assertEqual((out["dry_season_wind_from"], out["wind_speed_ms"], out["hotspots_upwind"]), (None, None, None))

    def test_the_search_radius_is_in_metres_and_the_inner_ring_is_five_km(self):
        self.run_fire(radius=50)
        buffers = [c.args[0] for c in self.ee.Geometry.Point.return_value.buffer.call_args_list]
        self.assertEqual(buffers, [5000, 50000])
        self.assertEqual(self.run_fire(radius=50)["radius_km"], 50)

    def test_east_west_distance_shrinks_with_latitude_and_north_south_does_not(self):
        self.run_fire()
        lonlat = self.ee.Image.pixelLonLat.return_value
        factors = [c.args[0] for c in lonlat.select.return_value.subtract.return_value.multiply.call_args_list]
        self.assertEqual(len(factors), 2)
        self.assertAlmostEqual(factors[0], 111.32 * math.cos(math.radians(20.0)), places=6)  # dx, longitude
        self.assertAlmostEqual(factors[1], 110.57, places=6)  # dy, latitude

    def test_the_pin_is_longitude_first(self):
        self.run_fire()
        self.ee.Geometry.Point.assert_called_once_with([100.0, 20.0])


class ChangeTest(EeTestCase):
    BEFORE = {"label": {"1": 700, "6": 300}}
    AFTER = {"label": {"0": 5, "1": 600, "6": 380, "7": 15}}

    def run_change(self, before=BEFORE, after=AFTER, today=None):
        self.answer({"before": before, "after": after})
        with mock.patch.object(eo, "_png", side_effect=["BEFORE", "AFTER"]):
            if today:
                with mock.patch.object(eo, "date", fixed_date(*today)):
                    return eo.change(20.0, 100.0)
            return eo.change(20.0, 100.0)

    def test_land_cover_shares_and_the_points_that_moved_by_a_point_or_more(self):
        out = self.run_change()
        self.assertEqual(out["land_cover_pct_1km"]["before"], {"trees": 70.0, "built": 30.0})
        self.assertEqual(out["land_cover_pct_1km"]["after"], {"water": 0.5, "trees": 60.0, "built": 38.0, "bare": 1.5})
        self.assertEqual(out["change_points_1km"], {"trees": -10.0, "built": 8.0, "bare": 1.5})  # water's +0.5 is under a point
        self.assertNotIn("note", out)
        self.assertEqual(out["images"], {"before": "BEFORE", "after": "AFTER"})

    def test_a_change_of_exactly_one_point_counts_and_less_does_not(self):
        out = self.run_change(before={"label": {"1": 500, "6": 500}}, after={"label": {"1": 490, "6": 510}})
        self.assertEqual(out["change_points_1km"], {"trees": -1.0, "built": 1.0})
        out = self.run_change(before={"label": {"1": 500, "6": 500}}, after={"label": {"1": 495, "6": 505}})
        self.assertEqual(out["change_points_1km"], {})

    def test_class_ids_may_arrive_as_floats(self):
        out = self.run_change(before={"label": {"1.0": 50, "6.0": 50}}, after={"label": {"1.0": 25, "6.0": 75}})
        self.assertEqual(out["change_points_1km"], {"trees": -25.0, "built": 25.0})

    def test_nothing_moved_is_an_empty_change_not_missing_data(self):
        out = self.run_change(after=self.BEFORE)
        self.assertEqual(out["change_points_1km"], {})
        self.assertNotIn("note", out)

    def test_a_period_with_no_land_cover_says_missing_instead_of_guessing(self):
        for empty in ({}, {"label": {}}):
            with self.subTest(empty):
                out = self.run_change(after=empty)
                self.assertIsNone(out["change_points_1km"])
                self.assertEqual(out["land_cover_pct_1km"], {"before": {"trees": 70.0, "built": 30.0}, "after": None})
                self.assertEqual(out["note"], "Land cover missing for one period.")

    def test_periods_are_the_2017_dry_season_and_the_latest_complete_one(self):
        out = self.run_change()
        self.assertEqual((out["before_period"], out["after_period"]), ("Nov 2017 - Apr 2018", "Nov 2025 - Apr 2026"))
        self.assertEqual(out["source"], "Dynamic World land cover + Sentinel-2 (Copernicus) dry-season composites, "
                                        "Nov 2017 - Apr 2018 vs Nov 2025 - Apr 2026")
        dw = self.ee.ImageCollection.return_value.filterBounds.return_value.select.return_value
        self.assertEqual([c.args for c in dw.filterDate.call_args_list], [("2017-11-01", "2018-05-01"), ("2025-11-01", "2026-05-01")])
        chips = self.ee.ImageCollection.return_value.filterBounds.return_value.filterDate
        self.assertEqual([c.args for c in chips.call_args_list], [("2017-11-01", "2018-05-01"), ("2025-11-01", "2026-05-01")])

    def test_before_the_season_ends_the_latest_complete_one_is_last_years(self):
        self.assertEqual(self.run_change(today=(2026, 4, 30))["after_period"], "Nov 2024 - Apr 2025")
        self.assertEqual(self.run_change(today=(2026, 5, 1))["after_period"], "Nov 2025 - Apr 2026")

    def test_the_pin_is_longitude_first(self):
        self.run_change()
        self.ee.Geometry.Point.assert_called_once_with([100.0, 20.0])


if __name__ == "__main__":
    unittest.main()
