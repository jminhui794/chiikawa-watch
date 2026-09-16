"""Display the primary phone's cloud events, without a second cinema scan."""
import json
import os
import time
from urllib.request import Request, urlopen

import watch

CURSOR = os.path.join(watch.BASE, ".desktop-alerts.json")
FEED = "https://raw.githubusercontent.com/jminhui794/chiikawa-watch/main/.watch-state.json"


def save_cursor(ids, path):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(ids, stream)
    os.replace(temporary, path)


def deliver(document, path=CURSOR, notify=None):
    notify = notify or watch.toast
    events = document.get("events", []) if document.get("schema") == 2 else []
    if not os.path.exists(path):
        # Installing on a new desktop must not replay historical phone alerts.
        save_cursor([event["id"] for event in events], path)
        return 0
    with open(path, encoding="utf-8") as stream:
        seen_ids = json.load(stream)
    seen = set(seen_ids)
    count = 0
    for event in events:
        if event["id"] in seen:
            continue
        if notify(event["title"], event["body"].splitlines(), url=event["url"]) is False:
            raise RuntimeError("Desktop notification failed; will retry")
        seen.add(event["id"])
        seen_ids.append(event["id"])
        save_cursor(seen_ids[-2000:], path)
        count += 1
    return count


def follow(once=False):
    while True:
        try:
            request = Request(FEED + "?t=" + str(time.time_ns()),
                              headers={"Cache-Control": "no-cache", "User-Agent": "chiikawa-desktop"})
            with urlopen(request, timeout=20) as response:
                document = json.load(response)
            count = deliver(document)
            print(f"[{time.strftime('%m-%d %H:%M:%S')}] 공통 알림 확인: {count}건", flush=True)
        except Exception as error:
            print(f"[공통 알림 조회 실패] {type(error).__name__}: {error}", flush=True)
            if once:
                raise
        if once:
            return
        time.sleep(30)
