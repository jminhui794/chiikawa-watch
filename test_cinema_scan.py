import json
import threading
import unittest
from unittest.mock import patch

import cinema_scan
import watch


class ScopeRecoveryTests(unittest.TestCase):
    def test_all_configured_sites_and_dates_are_requested(self):
        for brand, config in (("CGV", watch.CFG["cgv"]["sites"]), ("롯데", watch.CFG["lotte"]["cinemas"])):
            jobs = cinema_scan.jobs_for(brand)
            self.assertEqual(len(jobs), len(config) * len(watch.DATES))
            self.assertEqual(len({job["scope"] for job in jobs}), len(jobs))
        self.assertEqual(len(cinema_scan.jobs_for("메가박스")), len(watch.DATES))

    def test_failed_scope_keeps_sold_out_baseline_while_other_scope_updates(self):
        jobs = [{"scope": "롯데|A|20990919|"}, {"scope": "롯데|B|20990919|"}]
        previous = {jobs[0]["scope"] + "12:00|1관": {"left": 0},
                    jobs[1]["scope"] + "12:00|1관": {"left": 0}}
        good = {"s": 200, "b": json.dumps({"PlaySeqs": {"Items": [{"MovieNameKR": "치이카와",
            "TotalSeatCount": 100, "BookingSeatCount": 99, "StartTime": "12:00", "ScreenNameKR": "1관"}]}})}
        result = cinema_scan.merge_responses("롯데", jobs, [{"error": "TimeoutError"}, good], previous)
        self.assertEqual(result.schedules[jobs[0]["scope"] + "12:00|1관"]["left"], 0)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(watch.changes(previous, result.schedules)[1]), 1)
        recovered = cinema_scan.merge_responses("롯데", jobs, [good, good], result.schedules)
        new, seats = watch.changes(result.schedules, recovered.schedules)
        self.assertEqual(new, [])
        self.assertEqual(len(seats), 1)

    def test_invalid_and_missing_responses_never_erase_existing_scope(self):
        job = {"scope": "메가박스|(전지점)|20990919|", "date": "20990919"}
        previous = {job["scope"] + "편성됨|-": {"left": None}}
        for responses in ([], [{"s": 200, "b": "<html>blocked</html>"}],
                          [{"s": 200, "b": '{}'}], [{"s": 403, "b": ""}]):
            result = cinema_scan.merge_responses("메가박스", [job], responses, previous)
            self.assertEqual(result.schedules, previous)
            self.assertTrue(result.errors)
        result = cinema_scan.merge_responses("메가박스", [job], [{"s": 200, "b": json.dumps({
            "movieList": [], "statCd": 0, "paramMap": {"playDe": job["date"]}})}], previous)
        self.assertEqual(result.schedules, {})
        self.assertEqual(result.errors, [])

    def test_megabox_movie_listing_without_date_availability_does_not_alert(self):
        job = {"scope": "메가박스|(전지점)|20260930|", "date": "20260930"}
        movie = {"movieNo": "26048500", "movieNm": "극장판 치이카와: 인어 섬의 비밀", "formAt": "N"}
        data = {"statCd": 0, "paramMap": {"playDe": "20260930"}, "movieList": [movie]}
        def parse():
            return cinema_scan.parse("메가박스", job, {"s": 200, "b": json.dumps(data)})
        self.assertEqual(parse(), {})
        movie["formAt"] = "Y"
        self.assertEqual(len(parse()), 1)
        movie["formAt"] = None
        with self.assertRaises(ValueError):
            parse()
        movie["formAt"] = "Y"
        data["paramMap"]["playDe"] = "20260919"
        with self.assertRaises(ValueError):
            parse()

    def test_brands_start_together_and_fast_results_do_not_wait_for_slow_brand(self):
        started = threading.Barrier(4)
        release = threading.Event()
        def scan(brand, previous):
            started.wait(timeout=5)
            if brand == "롯데":
                release.wait(timeout=5)
            return cinema_scan.Result(brand, {}, [])
        with patch.object(cinema_scan, "scan_brand", side_effect=scan):
            results = cinema_scan.scan_iter({})
            try:
                self.assertNotEqual(next(results).brand, "롯데")
            finally:
                release.set()
            self.assertEqual(len(list(results)), 3)


if __name__ == "__main__":
    unittest.main()
