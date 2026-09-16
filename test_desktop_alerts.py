import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import cloud_watch
import desktop_alerts
from cinema_scan import Result
from test_alert_store import MemoryGitHub


class SharedAlertsTests(unittest.TestCase):
    def run_cloud(self, github, results=(), testing=False):
        with patch.dict(os.environ, {"PUSH_SUBSCRIPTION": '{"endpoint":"test"}',
                                     "VAPID_PRIVATE_KEY": "test", "VAPID_SUBJECT": "test"}), \
                patch.object(cloud_watch.sys, "argv", ["cloud_watch.py"] + (["--test-push"] if testing else [])), \
                patch.object(cloud_watch, "github", side_effect=github), \
                patch.object(cloud_watch.watch, "scan_iter", return_value=iter(results)) as scan, \
                patch.object(cloud_watch, "send_push") as push:
            cloud_watch.run()
        return scan, push

    def test_test_push_publishes_identical_event_without_scanning_or_changing_schedules(self):
        github = MemoryGitHub()
        github.document["schedules"] = {"CGV|example": {"left": 0}}
        scan, push = self.run_cloud(github, testing=True)
        scan.assert_not_called()
        self.assertEqual(github.document["schedules"], {"CGV|example": {"left": 0}})
        event, = github.document["events"]
        self.assertEqual(tuple(event[key] for key in ("title", "body", "url", "tag")), push.call_args.args)

    def test_phone_payload_is_delivered_once_to_desktop_after_migration(self):
        old = {"씨네큐|신도림|-|상영예정|-": {"left": None}}
        github = MemoryGitHub(old)
        result = Result("CGV", {"CGV|고덕강일|20990920|14:50|4관": {"left": 1, "url": "https://cgv.co.kr"}}, [])
        scan, push = self.run_cloud(github, [result])
        scan.assert_called_once_with(old)
        with tempfile.TemporaryDirectory() as directory:
            cursor = os.path.join(directory, "cursor.json")
            notify = Mock(return_value=True)
            desktop_alerts.deliver(old, cursor, notify)
            self.assertEqual(desktop_alerts.deliver(github.document, cursor, notify), 1)
            self.assertEqual(desktop_alerts.deliver(github.document, cursor, notify), 0)
            desktop_alerts.deliver(old, cursor, notify)
            self.assertEqual(desktop_alerts.deliver(github.document, cursor, notify), 0)
            title, body, url, tag = push.call_args.args
            notify.assert_called_once_with(title, body.splitlines(), url=url)

    def test_first_install_skips_history_and_failed_toast_retries(self):
        event = {"id": "first", "title": "title", "body": "body", "url": "https://cgv.co.kr"}
        document = {"schema": 2, "events": [event]}
        with tempfile.TemporaryDirectory() as directory:
            cursor = os.path.join(directory, "cursor.json")
            notify = Mock(return_value=True)
            self.assertEqual(desktop_alerts.deliver(document, cursor, notify), 0)
            notify.assert_not_called()
            document["events"].append(dict(event, id="next"))
            with self.assertRaises(RuntimeError):
                desktop_alerts.deliver(document, cursor, Mock(return_value=False))
            self.assertEqual(desktop_alerts.deliver(document, cursor, notify), 1)

    def test_fast_cinema_is_published_before_waiting_for_slow_cinema(self):
        github = MemoryGitHub()
        def results():
            yield Result("CGV", {"CGV|new": {"left": 1, "url": "https://cgv.co.kr"}}, [])
            self.assertEqual(len(github.document["events"]), 1)
            yield Result("롯데", {}, [])
        self.run_cloud(github, results())


if __name__ == "__main__":
    unittest.main()
