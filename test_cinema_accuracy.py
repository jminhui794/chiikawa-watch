import json
import unittest

import cinema_scan
import watch
from cineq_parser import parse_timetable


class AccuracyTests(unittest.TestCase):
    def lotte(self, seats=2, status="Y", **extra):
        item = dict(MovieNameKR="극장판 치이카와: 인어 섬의 비밀", RepresentationMovieCode="24708",
                    CinemaID=1016, PlayDt="2099-09-20", IsBookingYN=status, BookingSeatCount=seats,
                    TotalSeatCount=306, StartTime="18:00", ScreenNameKR="8관")
        item.update(extra)
        return cinema_scan.parse("롯데", {"scope": "롯데|월드타워|20990920|", "cinema": "1016", "date": "20990920"},
                                 {"s": 200, "b": json.dumps({"PlaySeqs": {"Items": [item]}})})

    def test_lotte_uses_remaining_seats_and_booking_status(self):
        self.assertEqual(next(iter(self.lotte().values()))["left"], 2)
        for status in ("N", "J"):
            self.assertEqual(watch.changes({}, self.lotte(status=status)), ([], []))
        self.assertEqual(len(watch.changes(self.lotte(0), self.lotte(1))[1]), 1)
        self.assertEqual(len(watch.changes(self.lotte(0, "E"), self.lotte(1))[1]), 1)

    def test_lotte_checks_cinema_movie_and_date(self):
        for extra in ({"CinemaID": 1004}, {"PlayDt": "2099-09-19"}, {"RepresentationMovieCode": "wrong"}, {"IsBookingYN": "?"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.lotte(**extra)

    def test_legacy_inverted_lotte_count_does_not_generate_fake_cancellation(self):
        current = self.lotte(306)
        legacy = {key: {"left": 0, "total": 306} for key in current}
        self.assertEqual(watch.changes(legacy, current), ([], []))

    def cineq(self, seats="매진", date="20990920"):
        return f'''<div class="title"><span>0</span>극장판 치이카와: 인어 섬의 비밀</div>
            <div class="theater-info">2관 <span class="all-seats">(174석)</span></div>
            <div class="time-box"><div class="time" data-playdate="{date}" data-screenplanid="1092171">
            <span class="from">14:15<span class="to">~16:01</span></span>
            <span class="seats">{seats}<span>(조조)</span></span></div></div>'''

    def parse_cineq(self, html):
        return parse_timetable(html, {"scope": "씨네큐|신도림|20990920|", "date": "20990920"}, "치이카와", "url")

    def test_cineq_uses_real_timetable_and_sold_out_to_available(self):
        old, current = self.parse_cineq(self.cineq()), self.parse_cineq(self.cineq("1석"))
        self.assertEqual(next(iter(old.values()))["left"], 0)
        self.assertEqual(list(current), ["씨네큐|신도림|20990920|14:15|2관"])
        self.assertEqual(len(watch.changes(old, current)[1]), 1)

    def test_cineq_navigation_wrong_date_and_unknown_status_never_count_as_screening(self):
        self.assertEqual(self.parse_cineq("\r\n  "), {})
        for html in ('<a href="/Theater?TheaterCode=1001">신도림</a>', self.cineq(date="20990919"), self.cineq("준비중")):
            with self.subTest(html=html), self.assertRaises(ValueError):
                self.parse_cineq(html)


if __name__ == "__main__":
    unittest.main()
