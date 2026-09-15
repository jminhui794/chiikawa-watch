import json
import unittest
from unittest.mock import MagicMock, patch

import watch


class CgvFailureTests(unittest.TestCase):
    def page(self, responses=(), body="예매"):
        pg = MagicMock()
        pg.locator.return_value.inner_text.return_value = body
        pg.evaluate.side_effect = responses
        return pg

    def test_access_restriction_stops_requests(self):
        pg = self.page(body="이용이 제한되었습니다. RAY_ID")
        with self.assertRaisesRegex(RuntimeError, "접근 제한"):
            watch.cgv(pg, {})
        pg.evaluate.assert_not_called()

    def test_partial_failure_does_not_publish_partial_results(self):
        success = {"s": 200, "b": json.dumps({"statusCode": 0, "data": [
            dict(ROW, siteNo="0056", scnYmd=watch.DATES[0])
        ]})}
        pg = self.page([success, {"s": 401, "b": ""}])
        out = {"other": {"left": 1}}
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            watch.cgv(pg, out)
        self.assertEqual(out, {"other": {"left": 1}})

    def test_invalid_response_is_not_empty_schedule(self):
        for payload in ({"error": "unauthorized"}, {"data": None},
                        {"data": {}}, {"data": [{"schList": None}]}):
            with self.subTest(payload=payload):
                pg = self.page([{"s": 200, "b": json.dumps(payload)}])
                with self.assertRaises(RuntimeError):
                    watch.cgv(pg, {})

    def test_confirmed_empty_results_succeed(self):
        count = len(watch.CFG["cgv"]["sites"]) * len(watch.DATES)
        pg = self.page([{"s": 200, "b": '{"statusCode": 0, "data": []}'}] * count)
        out = {}
        watch.cgv(pg, out)
        self.assertEqual(out, {})
        self.assertEqual(pg.evaluate.call_count, count)

    def test_scan_retains_cgv_baseline_on_failure(self):
        previous = {"CGV|강남|20260919|12:00|1관": {"left": 0},
                    "롯데|old": {"left": 2}}
        with patch.object(watch, "sync_playwright"), \
                patch.object(watch, "megabox"), patch.object(watch, "lotte"), \
                patch.object(watch, "cineq"), \
                patch.object(watch, "cgv", side_effect=RuntimeError("HTTP 401")) as failed:
            failed.__name__ = "cgv"
            self.assertEqual(watch.scan(previous),
                             {k: v for k, v in previous.items() if k.startswith("CGV|")})


ROW = {"movNo": "30001367", "siteNo": "0366", "scnYmd": "20990919",
       "scnsNo": "004", "scnSseq": "2", "scnsNm": "4관 (Laser)",
       "scnsrtTm": "1210", "frSeatCnt": "1", "stcnt": "108", "cntlYn": "N"}


class CgvAlertTests(unittest.TestCase):
    def rows(self, **kwargs):
        return watch.parse_cgv([dict(ROW, **kwargs)], "0366", "고덕강일", "20990919", "30001367")

    def test_real_response_fields_and_string_seats(self):
        row = next(iter(self.rows().values()))
        self.assertEqual(row["left"], 1)
        self.assertEqual(row["total"], 108)
        self.assertTrue(row["opened"])

    def test_sold_out_to_available_alerts_once(self):
        old, current = self.rows(frSeatCnt="0"), self.rows()
        new, seats = watch.changes(old, current)
        self.assertEqual(len(seats), 1)
        self.assertEqual(new, [])
        self.assertEqual(watch.changes(current, current), ([], []))

    def test_preparing_to_open_is_opening_not_cancellation(self):
        old = self.rows(cntlYn="Y")
        self.assertEqual(watch.changes({}, old), ([], []))
        new, seats = watch.changes(old, self.rows())
        self.assertEqual(len(new), 1)
        self.assertEqual(seats, [])

    def test_wrong_movie_and_invalid_seats_fail(self):
        for kwargs in ({"movNo": "other"}, {"frSeatCnt": "-1"}, {"frSeatCnt": "unknown"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.rows(**kwargs)


if __name__ == "__main__":
    unittest.main()
