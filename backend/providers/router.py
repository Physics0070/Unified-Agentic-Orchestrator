"""Model router: pick the model for a task from capability, cost and latency.

`catalog.rank_models` answers "which model is best at this *kind* of task". That
is only one of the things a router should care about. A greeting does not need
the strongest model, and paying for it (in money or in seconds) makes the
product worse. So this scores each candidate on three normalised axes -

    capability   how well it fits the intent (the catalog's weighted score)
    cost         blended USD per 1M tokens, cheaper is better
    latency      typical time to a useful answer, faster is better

- and weights them by how hard the task is (`complexity`) and by what the user
asked to optimise (`priority`). "simple" leans towards cheap and fast; "deep"
leans towards capable. The decision carries every candidate's component scores
so the UI can show *why*, not just *what*.

Pure and deterministic: same inputs, same decision - ties keep input order.
"""

import math

from pydantic import BaseModel

from backend.providers.catalog import AXES, _weights_for, economics_for, profile_for

PRIORITIES: tuple[str, ...] = ("balanced", "quality", "speed", "cost")

# How much of the score is capability, by task difficulty. The remainder is
# split between cost and latency according to `priority`.
_CAPABILITY_SHARE = {"simple": 0.30, "moderate": 0.55, "deep": 0.75}

# (cost share, latency share) of the non-capability remainder.
_REMAINDER_SPLIT = {
    "balanced": (0.5, 0.5),
    "quality": (0.5, 0.5),
    "speed": (0.2, 0.8),
    "cost": (0.8, 0.2),
}
# Capability share nudged by priority: quality buys capability, speed/cost sell it.
_PRIORITY_SHIFT = {"balanced": 0.0, "quality": 0.20, "speed": -0.15, "cost": -0.15}


class Candidate(BaseModel):
    model: str
    capability: float
    cost: float          # 1.0 = cheapest of the candidates
    latency: float       # 1.0 = fastest of the candidates
    score: float
    cost_per_mtok_usd: float
    typical_latency_ms: int
    estimated: bool = False   # True when cost/latency came from the default row


class RouteDecision(BaseModel):
    model: str
    reason: str
    priority: str
    complexity: str
    intent: str
    weights: dict[str, float]
    candidates: list[Candidate]   # best first

    @property
    def ranked_models(self) -> list[str]:
        return [c.model for c in self.candidates]


def weights_for(complexity: str, priority: str) -> dict[str, float]:
    cap = _CAPABILITY_SHARE.get(complexity, _CAPABILITY_SHARE["moderate"])
    cap = min(0.9, max(0.15, cap + _PRIORITY_SHIFT.get(priority, 0.0)))
    cost_share, lat_share = _REMAINDER_SPLIT.get(priority, _REMAINDER_SPLIT["balanced"])
    rest = 1.0 - cap
    return {
        "capability": round(cap, 4),
        "cost": round(rest * cost_share, 4),
        "latency": round(rest * lat_share, 4),
    }


def _invert_minmax(values: list[float]) -> list[float]:
    """Lower raw value -> higher score, scaled to 0..1 across the candidates.
    When every candidate is equal the axis cannot separate them, so it gives
    all of them 1.0 rather than inventing a spread."""
    lo, hi = min(values), max(values)
    if math.isclose(lo, hi):
        return [1.0] * len(values)
    return [(hi - v) / (hi - lo) for v in values]


def route_model(
    models: list[str],
    intent: str,
    complexity: str = "moderate",
    priority: str = "balanced",
) -> RouteDecision:
    """Raises IndexError on an empty list, like catalog.pick_model - callers
    already guard against no models being configured upstream."""
    if not models:
        raise IndexError("no models to route between")
    if priority not in PRIORITIES:
        priority = "balanced"

    w = weights_for(complexity, priority)
    intent_w = _weights_for(intent)

    caps, costs, lats, estimated = [], [], [], []
    for m in models:
        profile, _ = profile_for(m)
        caps.append(sum(getattr(profile, a) * intent_w[a] for a in AXES))
        econ, key = economics_for(m)
        # log1p: the gap between free and $0.40 matters far more than the gap
        # between $5 and $6, and a straight scale would let one premium model
        # flatten every cheap one to the same score.
        costs.append(math.log1p(econ.cost_per_mtok_usd))
        lats.append(float(econ.typical_latency_ms))
        estimated.append(key == "default")

    cost_scores = _invert_minmax(costs)
    lat_scores = _invert_minmax(lats)

    cands = []
    for i, m in enumerate(models):
        econ, _ = economics_for(m)
        total = (
            w["capability"] * caps[i] + w["cost"] * cost_scores[i] + w["latency"] * lat_scores[i]
        )
        cands.append(Candidate(
            model=m, capability=round(caps[i], 4), cost=round(cost_scores[i], 4),
            latency=round(lat_scores[i], 4), score=round(total, 4),
            cost_per_mtok_usd=econ.cost_per_mtok_usd, typical_latency_ms=econ.typical_latency_ms,
            estimated=estimated[i],
        ))
    cands.sort(key=lambda c: c.score, reverse=True)  # stable: ties keep input order

    best = cands[0]
    return RouteDecision(
        model=best.model,
        reason=_reason(best, cands, w, intent, complexity, priority),
        priority=priority, complexity=complexity, intent=intent,
        weights=w, candidates=cands,
    )


def _reason(best: Candidate, cands: list[Candidate], w: dict[str, float],
            intent: str, complexity: str, priority: str) -> str:
    if len(cands) == 1:
        return "only model available"
    lead = max(("capability", "cost", "latency"), key=lambda a: w[a] * getattr(best, a))
    why = {
        "capability": "best fit for the task",
        "cost": "best value for the price",
        "latency": "best speed for the quality",
    }[lead]
    runner = cands[1]
    return (
        f"{why} - a {complexity} {intent} question on '{priority}' priority "
        f"(capability {best.capability:.2f}, ~{best.typical_latency_ms}ms, "
        f"${best.cost_per_mtok_usd:g}/1M tok; next best: {runner.model})"
    )
