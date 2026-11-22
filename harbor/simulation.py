"""Illustrative assumed-effect simulator; no fitted model or causal evidence."""

import argparse
import json
import math
import random
import statistics
from pathlib import Path

ASSUMPTIONS = {
    "population": "Entirely synthetic; each seed simulates independent scheduled visits.",
    "propensity": "logit=-2.5 + 0.018*lead_days + 0.55*prior_misses + 0.18*(weekday==0) + 0.20*(hour>=16) + 0.15*service_index",
    "reminder_delivery_probability": 0.90,
    "assumed_relative_no_show_reduction_if_delivered": 0.25,
    "cancellation_probability": 0.10,
    "eligible_waitlist_probability": 0.80,
    "offer_acceptance_probability": 0.70,
    "design": "Paired common uniform draws for baseline and reminder arms; cancellation is separate from no-show. One offer per cancelled visit.",
    "warning": "Effects are assumptions, not learned estimates. No real-world effectiveness, clinical benefit, or return-on-investment claim.",
}


def simulate(seed: int, visits: int = 10000) -> dict:
    rng = random.Random(seed)
    counts = dict(
        visits=visits,
        cancellations=0,
        eligible_offers=0,
        refills=0,
        baseline_no_shows=0,
        reminder_no_shows=0,
        attended_denominator=0,
    )
    for _ in range(visits):
        lead, history, weekday, hour, service = (
            rng.randrange(1, 91),
            rng.randrange(4),
            rng.randrange(5),
            rng.randrange(9, 18),
            rng.randrange(6),
        )
        p = 1 / (
            1
            + math.exp(
                -(-2.5 + 0.018 * lead + 0.55 * history + 0.18 * (weekday == 0) + 0.20 * (hour >= 16) + 0.15 * service)
            )
        )
        cancelled = rng.random() < 0.10
        delivered, draw = rng.random() < 0.90, rng.random()
        if cancelled:
            counts["cancellations"] += 1
            if rng.random() < 0.80:
                counts["eligible_offers"] += 1
                counts["refills"] += rng.random() < 0.70
        else:
            counts["attended_denominator"] += 1
            counts["baseline_no_shows"] += draw < p
            counts["reminder_no_shows"] += draw < p * (0.75 if delivered else 1)
    denom = counts["attended_denominator"]
    return {
        "seed": seed,
        **counts,
        "baseline_no_show_rate": counts["baseline_no_shows"] / denom,
        "reminder_no_show_rate": counts["reminder_no_shows"] / denom,
        "cancelled_slot_fill_rate": counts["refills"] / max(1, counts["cancellations"]),
    }


def report(seeds=range(10), visits=10000):
    runs = [simulate(seed, visits) for seed in seeds]
    return {
        "kind": "synthetic_assumed_effect_simulation",
        "assumptions": ASSUMPTIONS,
        "runs": runs,
        "summary": {
            k: {
                "mean": statistics.mean(r[k] for r in runs),
                "min": min(r[k] for r in runs),
                "max": max(r[k] for r in runs),
            }
            for k in ["baseline_no_show_rate", "reminder_no_show_rate", "cancelled_slot_fill_rate"]
        },
        "uncertainty_note": "Min/max summarize simulation seeds only, not uncertainty about real-world effect assumptions.",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="reports/simulation.json")
    p.add_argument("--visits", type=int, default=10000)
    args = p.parse_args()
    if args.visits < 1:
        p.error("--visits must be positive")
    result = report(visits=args.visits)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
