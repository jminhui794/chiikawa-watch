import unittest
import json
from unittest.mock import patch

import cloud_watch
from cloud_watch import filter_hits


class SubscriptionFilterTests(unittest.TestCase):
    hits = [
        ("CGV 다산 20260930 12:00 1관 (잔여 1)", "CGV", "https://cgv.co.kr"),
        ("CGV 강남 20260930 12:00 1관", "CGV", "https://cgv.co.kr"),
        ("메가박스 강남 20260930 12:00 1관", "메가박스", "https://www.megabox.co.kr"),
        ("롯데 월드타워 20260930 12:00 1관", "롯데", "https://www.lottecinema.co.kr"),
        ("씨네큐 신도림 - 상영예정 -", "씨네큐", "https://www.cineq.co.kr"),
    ]

    def test_selected_branch_receives_alert(self):
        self.assertEqual(filter_hits(self.hits, {"theaters": ["CGV|다산"], "dates": ["20260930"]}), self.hits[:1])

    def test_brand_wide_selection(self):
        self.assertEqual(filter_hits(self.hits, {"theaters": ["메가박스|전체 지점"]}), self.hits[2:3])

    def test_brand_alias(self):
        self.assertEqual(filter_hits(self.hits, {"theaters": ["씨네Q|신도림"]}), self.hits[4:])

    def test_all_theaters_and_date_filter(self):
        self.assertEqual(filter_hits(self.hits, {"theaters": [], "dates": ["20260930"]}), self.hits[:4])
        self.assertEqual(filter_hits(self.hits, {"dates": ["20260919"]}), [])

    def test_original_device_receives_all_dates_and_theaters(self):
        hits = [(f"CGV 강남 {date} 12:00 1관", "CGV", "https://cgv.co.kr")
                for date in ("20260919", "20260920", "20260930")]
        legacy = {"theaters": ["CGV|다산"], "dates": ["20260930"]}
        self.assertEqual(filter_hits(hits, legacy, primary_device=True), hits)
        self.assertEqual(filter_hits(hits, legacy), [])

    def test_delivery_to_original_device_preserves_other_device_filters(self):
        first = {"endpoint": "first", "dates": ["20260930"], "theaters": ["CGV|다산"]}
        second = {"endpoint": "second", "dates": ["20260930"]}
        hit = ("CGV 강남 20260919 12:00 1관", "CGV", "https://cgv.co.kr")
        for subscriptions in (first, [first, second]):
            with self.subTest(subscriptions=subscriptions), \
                    patch.dict("os.environ", {"PUSH_SUBSCRIPTION": json.dumps(subscriptions),
                                              "VAPID_PRIVATE_KEY": "test", "VAPID_SUBJECT": "test"}), \
                    patch.object(cloud_watch.sys, "argv", ["cloud_watch.py"]), \
                    patch.object(cloud_watch, "github", return_value=None), \
                    patch.object(cloud_watch.watch, "scan", return_value={}), \
                    patch.object(cloud_watch.watch, "changes", return_value=([hit], [hit])), \
                    patch.object(cloud_watch, "send_push") as send:
                cloud_watch.run()
                self.assertEqual(send.call_count, 2)
                self.assertTrue(all(call.kwargs["target"] == first for call in send.call_args_list))


if __name__ == "__main__":
    unittest.main()
