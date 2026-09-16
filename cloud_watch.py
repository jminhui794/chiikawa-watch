"""One scheduled scan; state lives in GitHub, push subscription only in Secrets."""
import base64
import json
import os
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

import watch


def github(method="GET", payload=None):
    repo = os.environ["GITHUB_REPOSITORY"]
    request = Request(
        f"https://api.github.com/repos/{repo}/contents/.watch-state.json",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                 "User-Agent": "chiikawa-watch"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if method == "GET" and error.code == 404:
            return None
        raise RuntimeError(f"GitHub state {method} failed: HTTP {error.code}") from None


def send_push(title, body, url, tag, target=None):
    from pywebpush import webpush, WebPushException
    subscriptions = [target] if target is not None else json.loads(os.environ["PUSH_SUBSCRIPTION"])
    if isinstance(subscriptions, dict):
        subscriptions = [subscriptions]
    for subscription in subscriptions:
        endpoint = urlsplit(subscription["endpoint"])
        host = endpoint.hostname or ""
        if endpoint.scheme != "https" or not (
                host == "web.push.apple.com" or host.endswith(".push.apple.com")
                or host == "fcm.googleapis.com" or host.endswith(".push.services.mozilla.com")):
            raise ValueError("Unsupported push endpoint")
        try:
            webpush(subscription_info=subscription,
                    data=json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False),
                    vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": os.environ["VAPID_SUBJECT"]},
                    ttl=300, timeout=30)
        except WebPushException as error:
            code = error.response.status_code if error.response is not None else "network"
            raise RuntimeError(f"Push failed ({code}); baseline was not advanced") from None


def filter_hits(hits, subscription, *, primary_device=False):
    # The first PUSH_SUBSCRIPTION entry is the primary (original) device.
    # It receives every monitored screening, ignoring legacy filters.
    if primary_device:
        return list(hits)
    selected = subscription.get("theaters") or []
    dates = subscription.get("dates") or []
    prefixes = []
    for selection in selected:
        brand, branch = selection.split("|", 1)
        brand = {"씨네Q": "씨네큐", "롯데시네마": "롯데"}.get(brand, brand)
        prefixes.append(brand + " " if branch == "전체 지점" else f"{brand} {branch} ")
    return [hit for hit in hits
            if (not prefixes or any(hit[0].startswith(prefix) for prefix in prefixes))
            and (not dates or any(date in hit[0].split() for date in dates))]


def run():
    # Missing registration fails loudly, never claims monitoring is active.
    for name in ("PUSH_SUBSCRIPTION", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        if not os.environ.get(name):
            raise ValueError(f"Missing secret: {name}")
    testing = "--test-push" in sys.argv
    saved = github()
    document = json.loads(base64.b64decode(saved["content"])) if saved else {}
    previous = document.get("schedules", {}) if document.get("schema") == 2 else document
    events = list(document.get("events", [])) if document.get("schema") == 2 else []
    if testing:
        title = "치이카와 알림 테스트"
        body = "휴대폰·PC 공통 알림 연결 확인입니다. 실제 새 회차나 취소표 알림이 아닙니다."
        url = "https://cgv.co.kr/cnm/movieBook/movie"
        send_push(title, body, url, "chiikawa-test")
        events.append({"id": uuid.uuid4().hex, "created_at": time.time(),
                       "title": title, "body": body, "url": url, "tag": "chiikawa-test"})
        current, openings, cancellations = previous, [], []
    else:
        current = watch.scan(previous, strict_cgv=True)
        openings, cancellations = watch.changes(previous, current)
    for title, hits, tag in (("치이카와 새 회차 오픈", openings, "chiikawa-open"),
                             ("치이카와 취소표 발생", cancellations, "chiikawa-seats")):
        if hits:
            subscriptions = json.loads(os.environ["PUSH_SUBSCRIPTION"])
            if isinstance(subscriptions, dict):
                subscriptions = [subscriptions]
            for index, subscription in enumerate(subscriptions):
                filtered = filter_hits(hits, subscription, primary_device=index == 0)
                if filtered:
                    body = "\n".join(hit[0] for hit in filtered[:6])
                    send_push(title, body, filtered[0][2], tag, target=subscription)
                    if index == 0:
                        events.append({"id": uuid.uuid4().hex, "created_at": time.time(),
                                       "title": title, "body": body, "url": filtered[0][2], "tag": tag})
    events = [event for event in events if event["created_at"] >= time.time() - 7 * 86400][-500:]
    updated = {"schema": 2, "schedules": current, "events": events}
    encoded = base64.b64encode(json.dumps(updated, ensure_ascii=False).encode()).decode()
    if updated != document or saved is None:
        payload = {"message": "Update cinema availability", "content": encoded}
        if saved:
            payload["sha"] = saved["sha"]
        github("PUT", payload)
    print(f"Cloud scan complete: {len(current)} schedules, {len(openings)} openings, {len(cancellations)} cancellations")


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        # Web-push exceptions may contain private subscription URLs; do not dump them.
        print(f"Cloud scan failed: {type(error).__name__}", file=sys.stderr)
        if isinstance(error, (RuntimeError, ValueError)):
            print(str(error)[:180], file=sys.stderr)
        # Make a blocked/failed scan visible on the phone instead of silently stopping.
        try:
            if all(os.environ.get(name) for name in ("PUSH_SUBSCRIPTION", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT")):
                send_push("치이카와 감시 오류", "영화관 사이트 조회가 실패했습니다. 다음 실행에서 다시 시도합니다.",
                          "https://chiikawa-alerts.onrender.com", "chiikawa-error")
        except Exception:
            pass
        sys.exit(1)
