"""One scheduled scan; state lives in GitHub, push subscription only in Secrets."""
import json
import os
import sys
import time
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
    import alert_store
    for name in ("PUSH_SUBSCRIPTION", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        if not os.environ.get(name):
            raise ValueError(f"Missing secret: {name}")
    subscriptions = json.loads(os.environ["PUSH_SUBSCRIPTION"])
    if isinstance(subscriptions, dict):
        subscriptions = [subscriptions]
    if not subscriptions:
        raise ValueError("No registered devices")
    testing = "--test-push" in sys.argv
    if testing:
        items = [alert_store.notification("???? ?? ???",
                 "????PC ?? ?? ?? ?????. ?? ? ??? ??? ??? ????.",
                 "https://cgv.co.kr/cnm/movieBook/movie", "chiikawa-test", subscription, index == 0)
                 for index, subscription in enumerate(subscriptions)]
        alert_store.transaction(github, lambda document: document["outbox"].extend(items))
        errors = alert_store.deliver_pending(github, subscriptions, send_push,
                                             only_ids={item["id"] for item in items})
        if errors:
            raise RuntimeError("Test notification queued for retry: " + "; ".join(errors))
        print("Test notification sent and published to desktop", flush=True)
        return
    initial = alert_store.decode(github())
    # Concurrent test runs own their new events; a regular scan retries only
    # previously queued IDs and notifications it creates itself.
    errors = alert_store.deliver_pending(github, subscriptions, send_push,
                                         only_ids={item["id"] for item in initial["outbox"]
                                                   if not item["tag"].startswith("chiikawa-test-")
                                                   or item["created_at"] < time.time() - 300})
    failed_brands = []
    for result in watch.scan_iter(initial["schedules"]):
        before_ids = set()
        def enqueue(document):
            before_ids.clear()
            before_ids.update(item["id"] for item in document["outbox"])
            alert_store.enqueue_result(document, result, subscriptions, watch.changes, filter_hits)
        document = alert_store.transaction(github, enqueue)
        new_ids = {item["id"] for item in document["outbox"]} - before_ids
        errors.extend(alert_store.deliver_pending(github, subscriptions, send_push, only_ids=new_ids))
        if result.errors:
            failed_brands.append(result.brand)
        print(f"Published {result.brand}: queued={len(new_ids)}, scan_errors={len(result.errors)}", flush=True)
    if failed_brands or errors:
        raise RuntimeError(f"Partial scan: failed cinemas={failed_brands}; pending deliveries={len(errors)}. Previous failed scopes and queued alerts retained.")
    print("Cloud scan complete: all cinemas checked, queued notifications delivered", flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        # Web-push exceptions may contain private subscription URLs; do not dump them.
        print(f"Cloud scan failed: {type(error).__name__}", file=sys.stderr)
        if isinstance(error, (RuntimeError, ValueError)):
            print(str(error)[:180], file=sys.stderr)
        sys.exit(1)
