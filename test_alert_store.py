import base64
import copy
import json
import time
import unittest
from unittest.mock import Mock

import alert_store
import cloud_watch
import watch
from cinema_scan import Result


class MemoryGitHub:
    def __init__(self, document=None):
        self.document = document or {"schema": 2, "schedules": {}, "events": [], "outbox": [], "health": {}}
        self.revision = 1

    def __call__(self, method="GET", payload=None):
        if method == "GET":
            return {"sha": str(self.revision), "content": base64.b64encode(json.dumps(self.document).encode()).decode()}
        if payload["sha"] != str(self.revision):
            raise RuntimeError("GitHub state PUT failed: HTTP 409")
        self.document = json.loads(base64.b64decode(payload["content"]))
        self.revision += 1


class OutboxTests(unittest.TestCase):
    subscription = {"endpoint": "https://fcm.googleapis.com/example"}

    def test_all_thirteen_hits_are_queued_and_delivered_in_three_distinct_alerts(self):
        github = MemoryGitHub()
        result = Result("CGV", {f"CGV|강남|20990919|{index}|1관": {"left": 1, "url": "https://cgv.co.kr"}
                                for index in range(13)}, [])
        alert_store.transaction(github, lambda document: alert_store.enqueue_result(
            document, result, [self.subscription], watch.changes, cloud_watch.filter_hits))
        self.assertEqual(len(github.document["outbox"]), 3)
        send = Mock()
        self.assertEqual(alert_store.deliver_pending(github, [self.subscription], send), [])
        self.assertEqual(sum(len(call.args[1].splitlines()) for call in send.call_args_list), 13)
        self.assertEqual(len({call.args[3] for call in send.call_args_list}), 3)
        self.assertEqual(len(github.document["events"]), 3)
        self.assertEqual(github.document["outbox"], [])

    def test_failed_push_survives_seat_disappearing_and_retries_without_rescan(self):
        github = MemoryGitHub()
        result = Result("CGV", {"CGV|강남|20990919|12:00|1관": {"left": 1, "url": "https://cgv.co.kr"}}, [])
        def enqueue(document):
            alert_store.enqueue_result(document, result, [self.subscription], watch.changes, cloud_watch.filter_hits)
        alert_store.transaction(github, enqueue)
        self.assertTrue(alert_store.deliver_pending(github, [self.subscription], Mock(side_effect=RuntimeError("offline"))))
        self.assertEqual(len(github.document["outbox"]), 1)
        self.assertEqual(github.document["events"], [])
        result.schedules = {}
        alert_store.transaction(github, enqueue)
        send = Mock()
        alert_store.deliver_pending(github, [self.subscription], send)
        send.assert_called_once()
        self.assertEqual(len(github.document["events"]), 1)

    def test_sha_conflict_preserves_other_writers_events_and_baseline(self):
        memory = MemoryGitHub()
        conflict = False
        event = {"id": "other", "created_at": time.time()}
        def github(method="GET", payload=None):
            nonlocal conflict
            if method == "PUT" and not conflict:
                conflict = True
                memory.document["events"].append(event)
                memory.revision += 1
            return memory(method, payload)
        alert_store.transaction(github, lambda document: document["schedules"].update({"CGV|new": {"left": 0}}))
        self.assertEqual(memory.document["events"], [event])
        self.assertIn("CGV|new", memory.document["schedules"])

    def test_failed_secondary_device_does_not_block_primary_or_lose_pending_alert(self):
        secondary = {"endpoint": "https://fcm.googleapis.com/second"}
        github = MemoryGitHub()
        for index, device in enumerate([self.subscription, secondary]):
            github.document["outbox"].append(alert_store.notification("title", "body", "url", "tag", device, index == 0))
        send = Mock(side_effect=[None, RuntimeError("offline")])
        errors = alert_store.deliver_pending(github, [self.subscription, secondary], send)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(github.document["events"]), 1)
        self.assertEqual(len(github.document["outbox"]), 1)


if __name__ == "__main__":
    unittest.main()
