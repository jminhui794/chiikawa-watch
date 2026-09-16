"""GitHub state transactions and durable notifications (no subscription secrets)."""
import base64
import copy
import hashlib
import json
import time
import uuid


def decode(saved):
    document = json.loads(base64.b64decode(saved["content"])) if saved else {}
    if document.get("schema") != 2:
        document = {"schema": 2, "schedules": document, "events": []}
    document.setdefault("outbox", [])
    document.setdefault("health", {})
    return document


def transaction(github, mutate):
    # Test runs and scans may append concurrently; reapply the mutation to the
    # newest document after a SHA conflict, preserving both writers' records.
    for attempt in range(4):
        saved = github()
        document = decode(saved)
        before = copy.deepcopy(document)
        mutate(document)
        document["events"] = [event for event in document["events"]
                              if event["created_at"] >= time.time() - 7 * 86400][-500:]
        if document == before and saved:
            return document
        payload = {"message": "Update cinema availability", "content": base64.b64encode(
            json.dumps(document, ensure_ascii=False).encode()).decode()}
        if saved:
            payload["sha"] = saved["sha"]
        try:
            github("PUT", payload)
            return document
        except RuntimeError as error:
            if "HTTP 409" not in str(error) or attempt == 3:
                raise
            time.sleep(0.2 * (attempt + 1))


def device_id(subscription):
    return hashlib.sha256(subscription["endpoint"].encode()).hexdigest()


def notification(title, body, url, tag, subscription, primary):
    identifier = uuid.uuid4().hex
    return {"id": identifier, "created_at": time.time(), "title": title, "body": body,
            "url": url, "tag": tag + "-" + identifier, "device": device_id(subscription), "primary": primary}


def enqueue_result(document, result, subscriptions, changes, filter_hits):
    previous = {key: value for key, value in document["schedules"].items() if key.startswith(result.brand + "|")}
    openings, cancellations = changes(previous, result.schedules)
    if result.brand == "씨네큐" and any("|-|상영예정|-" in key for key in previous):
        openings, cancellations = [], []  # Replace the old menu-based baseline once.
    for title, hits, tag in (("치이카와 새 회차 오픈", openings, "chiikawa-open"),
                             ("치이카와 취소표 발생", cancellations, "chiikawa-seats")):
        for index, subscription in enumerate(subscriptions):
            selected = filter_hits(hits, subscription, primary_device=index == 0)
            for start in range(0, len(selected), 6):
                batch = selected[start:start + 6]
                document["outbox"].append(notification(title, "\n".join(hit[0] for hit in batch),
                    batch[0][2], tag, subscription, index == 0))
    document["schedules"] = {key: value for key, value in document["schedules"].items()
                             if not key.startswith(result.brand + "|")}
    document["schedules"].update(result.schedules)
    document["health"][result.brand] = {"checked_at": time.time(), "seconds": result.seconds,
                                         "requests": result.requests, "errors": result.errors}


def acknowledge(document, item):
    document["outbox"] = [entry for entry in document["outbox"] if entry["id"] != item["id"]]
    if item["primary"] and not any(event["id"] == item["id"] for event in document["events"]):
        document["events"].append({key: item[key] for key in ("id", "created_at", "title", "body", "url", "tag")})


def deliver_pending(github, subscriptions, send_push, *, only_ids=None, exclude_ids=None):
    devices = {device_id(subscription): subscription for subscription in subscriptions}
    pending = decode(github())["outbox"]
    errors = []
    for item in pending:
        if only_ids is not None and item["id"] not in only_ids:
            continue
        if exclude_ids is not None and item["id"] in exclude_ids:
            continue
        try:
            if item["device"] not in devices:
                raise RuntimeError("Registered device missing; queued alert retained")
            send_push(item["title"], item["body"], item["url"], item["tag"], target=devices[item["device"]])
            transaction(github, lambda document: acknowledge(document, item))
        except Exception as error:
            errors.append(f"{item['id']}: {type(error).__name__}")
    return errors
