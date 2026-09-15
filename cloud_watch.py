"""One scheduled scan; state lives in GitHub, push subscription only in Secrets."""
import base64
import json
import os
import sys
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


def send_push(title, body, url, tag):
    from pywebpush import webpush, WebPushException
    subscription = json.loads(os.environ["PUSH_SUBSCRIPTION"])
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


def run():
    # Missing registration fails loudly, never claims monitoring is active.
    for name in ("PUSH_SUBSCRIPTION", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        if not os.environ.get(name):
            raise ValueError(f"Missing secret: {name}")
    if "--test-push" in sys.argv:
        send_push("치이카와 알림 테스트", "아이폰 푸시 연결 확인용입니다. 실제 취소표 알림이 아닙니다.",
                  "https://cgv.co.kr/cnm/movieBook/movie", "chiikawa-test")
        return
    saved = github()
    previous = json.loads(base64.b64decode(saved["content"])) if saved else {}
    current = watch.scan(previous, strict_cgv=True)
    openings, cancellations = watch.changes(previous, current)
    for title, hits, tag in (("치이카와 새 회차 오픈", openings, "chiikawa-open"),
                             ("치이카와 취소표 발생", cancellations, "chiikawa-seats")):
        if hits:
            send_push(title, "\n".join(hit[0] for hit in hits[:6]), hits[0][2], tag)
    encoded = base64.b64encode(json.dumps(current, ensure_ascii=False).encode()).decode()
    if current != previous or saved is None:
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
        sys.exit(1)
