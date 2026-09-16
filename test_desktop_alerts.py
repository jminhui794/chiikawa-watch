import base64
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import cloud_watch
import desktop_alerts


class SharedAlertsTests(unittest.TestCase):
    def test_test_push_publishes_identical_event_without_scanning_or_changing_schedules(self):
        schedules = {"CGV|example": {"left": 0}}
        saved = {"sha": "old", "content": base64.b64encode(json.dumps(
            {"schema": 2, "schedules": schedules, "events": []}).encode()).decode()}
        with patch.dict(os.environ, {"PUSH_SUBSCRIPTION": '{}', "VAPID_PRIVATE_KEY": "x", "VAPID_SUBJECT": "x"}), \
                patch.object(cloud_watch.sys, "argv", ["cloud_watch.py", "--test-push"]), \
                patch.object(cloud_watch, "github", return_value=saved) as github, \
                patch.object(cloud_watch.watch, "scan") as scan, \
                patch.object(cloud_watch, "send_push") as push:
            cloud_watch.run()
        scan.assert_not_called()
        document = json.loads(base64.b64decode(github.call_args.args[1]["content"]))
        self.assertEqual(document["schedules"], schedules)
        event, = document["events"]
        self.assertEqual(tuple(event[key] for key in ("title", "body", "url", "tag")), push.call_args.args)

    def test_phone_payload_is_delivered_once_to_desktop_after_migration(self):
        old = {"씨네큐|신도림|-|상영예정|-": {"left": None}}
        saved = {"sha": "old", "content": base64.b64encode(json.dumps(old).encode()).decode()}
        hit = ("CGV 고덕강일 20260920 14:50 -> 취소표 1석!", "CGV", "https://cgv.co.kr")
        with patch.dict(os.environ, {"PUSH_SUBSCRIPTION": '{"endpoint":"test"}',
                                     "VAPID_PRIVATE_KEY": "test", "VAPID_SUBJECT": "test"}), \
                patch.object(cloud_watch.sys, "argv", ["cloud_watch.py"]), \
                patch.object(cloud_watch, "github", return_value=saved) as github, \
                patch.object(cloud_watch.watch, "scan", return_value=old) as scan, \
                patch.object(cloud_watch.watch, "changes", return_value=([], [hit])), \
                patch.object(cloud_watch, "send_push") as push:
            cloud_watch.run()
        scan.assert_called_once_with(old, strict_cgv=True)
        document = json.loads(base64.b64decode(github.call_args.args[1]["content"]))
        with tempfile.TemporaryDirectory() as directory:
            cursor = os.path.join(directory, "cursor.json")
            notify = Mock(return_value=True)
            desktop_alerts.deliver(old, cursor, notify)
            self.assertEqual(desktop_alerts.deliver(document, cursor, notify), 1)
            self.assertEqual(desktop_alerts.deliver(document, cursor, notify), 0)
            # A stale cached response must not erase IDs already delivered.
            desktop_alerts.deliver(old, cursor, notify)
            self.assertEqual(desktop_alerts.deliver(document, cursor, notify), 0)
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

    def test_push_failure_does_not_publish_desktop_event_or_advance_baseline(self):
        with patch.dict(os.environ, {"PUSH_SUBSCRIPTION": '{}', "VAPID_PRIVATE_KEY": "x", "VAPID_SUBJECT": "x"}), \
                patch.object(cloud_watch.sys, "argv", ["cloud_watch.py"]), \
                patch.object(cloud_watch, "github", return_value=None) as github, \
                patch.object(cloud_watch.watch, "scan", return_value={}), \
                patch.object(cloud_watch.watch, "changes", return_value=([("new", "CGV", "url")], [])), \
                patch.object(cloud_watch, "send_push", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                cloud_watch.run()
            github.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
