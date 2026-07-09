"""
Task Prioritization Engine
---------------------------
Rule-based multi-factor scoring model (0-100) that ranks operational tasks
for an Outlet Manager. Designed as a pluggable interface: the scoring
function can later be swapped for a learned model (e.g. gradient-boosted
ranker trained on historical manager actions) without changing callers.

Score = weighted sum of:
  - urgency        (how close to a hard deadline / stock-out / understaffed shift)
  - business_impact (revenue/customer exposure)
  - severity        (complaint severity, audit finding severity)
  - recency         (newer issues nudged up so nothing goes stale)
"""
import datetime as dt
from dataclasses import dataclass

WEIGHTS = {
    "urgency": 0.40,
    "business_impact": 0.30,
    "severity": 0.20,
    "recency": 0.10,
}


@dataclass
class ScoringInput:
    hours_to_deadline: float | None  # None = no hard deadline
    revenue_at_risk: float           # BDT estimate
    severity_1_to_5: int
    created_hours_ago: float


def _urgency_score(hours_to_deadline: float | None) -> float:
    if hours_to_deadline is None:
        return 30.0
    if hours_to_deadline <= 0:
        return 100.0
    if hours_to_deadline >= 72:
        return 10.0
    # linear decay between 0h (100) and 72h (10)
    return max(10.0, 100.0 - (hours_to_deadline / 72.0) * 90.0)


def _impact_score(revenue_at_risk: float) -> float:
    # log-ish scale, cap at 100. 50,000 BDT+ treated as max impact.
    if revenue_at_risk <= 0:
        return 5.0
    return min(100.0, (revenue_at_risk / 50000.0) * 100.0)


def _severity_score(sev: int) -> float:
    return min(100.0, max(0.0, sev * 20.0))


def _recency_score(hours_ago: float) -> float:
    # newer = slightly higher, decays over 48h
    return max(0.0, 100.0 - (hours_ago / 48.0) * 100.0)


def score_task(inp: ScoringInput) -> float:
    s = (
        WEIGHTS["urgency"] * _urgency_score(inp.hours_to_deadline)
        + WEIGHTS["business_impact"] * _impact_score(inp.revenue_at_risk)
        + WEIGHTS["severity"] * _severity_score(inp.severity_1_to_5)
        + WEIGHTS["recency"] * _recency_score(inp.created_hours_ago)
    )
    return round(min(100.0, max(0.0, s)), 1)


def hours_between(now: dt.datetime, target: dt.datetime | None) -> float | None:
    if target is None:
        return None
    return (target - now).total_seconds() / 3600.0
