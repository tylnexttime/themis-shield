"""Business-hour arithmetic and disclosure-controlled synthetic reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import math
import statistics

from ..timezone import business_timezone


def business_hours_between(
    start: datetime,
    end: datetime,
    holidays: frozenset[date] = frozenset(),
) -> float:
    """Count weekday 09:00-17:00 wall-clock hours in the business timezone.

    Public holidays are explicitly supplied local dates. No calendar is fetched.
    """
    if (
        not isinstance(start, datetime)
        or not isinstance(end, datetime)
        or start.utcoffset() is None
        or end.utcoffset() is None
    ):
        raise ValueError("timezone-aware datetime required")
    if end.astimezone(timezone.utc) < start.astimezone(timezone.utc):
        raise ValueError("negative decision latency")
    if any(type(day) is not date for day in holidays):
        raise ValueError("holidays must contain local dates")
    left = start.astimezone(business_timezone()).replace(tzinfo=None)
    right = end.astimezone(business_timezone()).replace(tzinfo=None)
    total = timedelta()
    day = left.date()
    while day <= right.date():
        if day.weekday() < 5 and day not in holidays:
            opening = datetime.combine(day, time(9))
            closing = datetime.combine(day, time(17))
            a, b = max(left, opening), min(right, closing)
            if b > a:
                total += b - a
        day += timedelta(days=1)
    return total.total_seconds() / 3600


def latency_stats(values: list[float], minimum_cohort: int = 5) -> dict:
    """p90 is nearest-rank: sorted_values[ceil(0.9*n)-1]."""
    if not values:
        return {"n": 0, "median": None, "p90": None}
    if len(values) < minimum_cohort:
        return {"n": None, "median": None, "p90": None, "suppressed": True}
    ordered = sorted(values)
    return {
        "n": len(values),
        "median": statistics.median(ordered),
        "p90": ordered[math.ceil(0.9 * len(ordered)) - 1],
    }


def build_report(rows: list[dict], events: list[dict], calendars: dict, minimum_cohort=5):
    """Job counts use creation month/current route/status.

    Approval and refusal events use decision month. Latency starts at complete
    submission. Operator decisions include escalation, but review-board turnaround is
    reported separately, starting at that revision's escalation.
    """
    buckets = defaultdict(lambda: {
        "counts": Counter(), "approval": [], "operator": [], "board": [],
        "refusals": 0, "sla_breaches": 0,
    })

    def parse(value):
        return datetime.fromisoformat(value)

    def month(value):
        return parse(value).astimezone(business_timezone()).strftime("%Y-%m")

    for row in rows:
        buckets[month(row["created_utc"])]["counts"][(row["route"], row["status"])] += 1

    escalations = {}
    for event in events:
        key = (event["job_id"], event["approval_revision"])
        if (
            event["event"] == "ESCALATED"
            and event["from_status"] == "Awaiting-Operator"
        ):
            escalations[key] = event["occurred_utc"]
        if event["event"] not in {"APPROVED", "REFUSED", "ESCALATED"}:
            continue
        if not event["received_utc"]:
            continue
        bucket = buckets[month(event["occurred_utc"])]
        holidays = calendars[event["calendar_version"]]
        elapsed = business_hours_between(
            parse(event["received_utc"]), parse(event["occurred_utc"]), holidays,
        )
        if event["event"] == "APPROVED":
            bucket["approval"].append(elapsed)
        if event["event"] == "REFUSED":
            bucket["refusals"] += 1
        if event["from_status"] == "Awaiting-Operator":
            bucket["operator"].append(elapsed)
            if event["due_utc"] and parse(event["occurred_utc"]) > parse(event["due_utc"]):
                bucket["sla_breaches"] += 1
        if (
            event["from_status"] == "Escalated-Review"
            and event["event"] in {"APPROVED", "REFUSED"}
            and key in escalations
        ):
            bucket["board"].append(business_hours_between(
                parse(escalations[key]), parse(event["occurred_utc"]), holidays,
            ))

    def count(value):
        return value if value == 0 or value >= minimum_cohort else None

    return {
        "mode": "SYNTHETIC REHEARSAL — not production assurance",
        "timezone": business_timezone().key,
        "p90_method": "nearest-rank",
        "minimum_cohort": minimum_cohort,
        "calendar_versions": sorted(calendars),
        "months": [
            {
                "month": name,
                "counts": [
                    {"route": route, "status": status, "count": count(number)}
                    for (route, status), number in sorted(bucket["counts"].items())
                ],
                "approval_latency_business_hours": latency_stats(
                    bucket["approval"], minimum_cohort,
                ),
                "operator_decision_latency_business_hours": latency_stats(
                    bucket["operator"], minimum_cohort,
                ),
                "board_decision_latency_business_hours": latency_stats(
                    bucket["board"], minimum_cohort,
                ),
                "refusals": count(bucket["refusals"]),
                "operator_sla_breaches": count(bucket["sla_breaches"]),
            }
            for name, bucket in sorted(buckets.items())
        ],
        "suppression_note": (
            "Small cells and latency cohorts suppressed. This simple pilot rule "
            "is not a production complementary-suppression policy."
        ),
    }
