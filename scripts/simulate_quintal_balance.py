"""Deterministic offline audit. Reads live catalog; never changes settings/accounts.

Visits and meals are 12-hour events. Crop/task intervals are integrated exactly
between events, so a 10-minute recipe is not rounded to the event cadence.
"""
import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta
import json
import hashlib
import math
from pathlib import Path
import random
import statistics

from app.config import get_settings
from app.services.quintal_catalog import CROPS, RECIPES, FACILITIES, ORDERS
from app.services.quintal_production import evolve

DAY = 86400
SEED = 20261016
# Object selection + the controls used in the current Flutter flow.
TAPS = {"plant": 3, "harvest": 2, "cook": 3, "collect_cook": 3,
        "collect_coop": 2, "collect_cow": 2, "feed_residents": 2,
        "water_residents": 2, "water_cat": 2, "serve_two": 4, "pet": 2}


def advance_resident(ready, progress, cycle, elapsed, supplied):
    """Same capacity/cycle arithmetic as reconcile; returns disjoint idle causes."""
    supplied = max(0, min(elapsed, supplied))
    productive = min(supplied, max(0, (2 - ready) * cycle - progress))
    total = progress + productive
    ready = min(2, ready + int(total // cycle))
    progress = 0 if ready == 2 else total % cycle
    return ready, progress, productive, supplied - productive, elapsed - supplied


def run_scenario(cats, visits, recipe="garden", *, days=28, warmup=7, plots=12):
    definition = RECIPES[recipe]
    batch_inputs, seconds, batch_yield = definition[1:4]
    daily_batches = math.ceil(cats * 2 / batch_yield)
    demand = Counter({"corn": 6, "wheat": 4})
    demand.update({k: v * daily_batches for k, v in batch_inputs.items() if k in CROPS})
    stock = Counter({k: v * 3 for k, v in demand.items()})
    stock.update({"egg": 8, "milk": 4, "ration": cats * 2})
    beds = [None] * plots
    tasks, bowls = [], [2] * cats
    residents = {"coop": {"n": 4, "cycle": 21600, "ready": 0, "progress": 0, "until": 0},
                 "pasture": {"n": 2, "cycle": 28800, "ready": 0, "progress": 0, "until": 0}}
    metrics, actions, ingredients, feed_inputs = Counter(), Counter(), Counter(), Counter()
    minimum = {"corn": float("inf"), "wheat": float("inf")}
    start, end = warmup * DAY, (warmup + days) * DAY
    initial = None
    def action(name, count=1):
        if measured: actions[name] += count
    def available(inputs):
        return all(stock[k] >= v for k, v in inputs.items())
    def spend(inputs, destination):
        for k, v in inputs.items():
            stock[k] -= v
            if measured: destination[k] += v
    for t in range(0, end + 1, DAY // 2):
        measured = start <= t < end
        previous = max(0, t - DAY // 2)
        integrate = t > start
        if t == start: initial = dict(stock)
        if t:
            for kind, resident in residents.items():
                values = advance_resident(resident["ready"], resident["progress"],
                    resident["cycle"], t - previous, resident["until"] - previous)
                resident["ready"], resident["progress"] = values[:2]
                if integrate:
                    metrics[kind + "_producing_seconds"] += values[2]
                    metrics[kind + "_full_seconds"] += values[3]
                    metrics[kind + "_unsupplied_seconds"] += values[4]
            if integrate:
                metrics["kitchen_busy_seconds"] += sum(
                    max(0, min(t, finish) - max(previous, begin)) for begin, finish in tasks)
                metrics["garden_waiting_bed_seconds"] += sum(
                    max(0, t - max(previous, bed[1])) for bed in beds if bed)
        if t == end: break
        # Meals happen before a visit; a just-served hungry cat eats immediately.
        pending_meals = []
        for cat in range(cats):
            if bowls[cat]:
                bowls[cat] -= 1
                if measured: metrics["meals"] += 1
            else: pending_meals.append(cat)
        if t % (DAY // visits) == 0:
            if measured: metrics["visits"] += 1
            ready_tasks = [task for task in tasks if task[1] <= t]
            stock["ration"] += len(ready_tasks) * batch_yield
            action("collect_cook", len(ready_tasks))
            tasks = [task for task in tasks if task[1] > t]
            for i, bed in enumerate(beds):
                if bed and bed[1] <= t:
                    crop = bed[0]
                    stock[crop] += CROPS[crop][3]
                    if measured: metrics["harvested_" + crop] += CROPS[crop][3]
                    action("harvest")
                    beds[i] = None
            for kind, resident in residents.items():
                product = "egg" if kind == "coop" else "milk"
                amount = resident["ready"] * resident["n"]
                if amount:
                    stock[product] += amount
                    if measured: metrics["collected_" + product] += amount
                    action("collect_coop" if kind == "coop" else "collect_cow",
                           1 if kind == "coop" else resident["n"])
                    resident["ready"] = 0
                if resident["until"] <= t:
                    needed = {"corn": 4} if kind == "coop" else {"corn": 2, "wheat": 4}
                    if available(needed):
                        spend(needed, feed_inputs)
                        resident["until"] = t + DAY
                        action("feed_residents")
                        action("water_residents")
            if t % DAY == 0:
                action("water_cat", cats)
                action("pet", cats)
            for cat in range(cats):
                if bowls[cat] < 2 and stock["ration"] >= 2:
                    # Server replaces the bowl, refunding the unconsumed portion.
                    stock["ration"] += bowls[cat] - 2
                    bowls[cat] = 2
                    action("serve_two")
            for cat in list(pending_meals):
                if bowls[cat]:
                    bowls[cat] -= 1
                    pending_meals.remove(cat)
                    if measured: metrics["meals"] += 1
            while stock["ration"] + len(tasks) * batch_yield < cats * 2 and len(tasks) < 4:
                if not available(batch_inputs):
                    if measured: metrics["visits_without_recipe_inputs"] += 1
                    break
                spend(batch_inputs, ingredients)
                begin = max(t, tasks[-1][1]) if tasks else t
                tasks.append((begin, begin + seconds))
                action("cook")
            # Replenish two days of farm demand, including crops already growing.
            future = Counter(stock)
            for bed in beds:
                if bed: future[bed[0]] += CROPS[bed[0]][3]
            for i, bed in enumerate(beds):
                if bed: continue
                deficits = {k: (2 * amount - future[k]) / CROPS[k][3]
                            for k, amount in demand.items()}
                crop = max(deficits, key=deficits.get)
                if deficits[crop] <= 0: break
                beds[i] = (crop, t + CROPS[crop][2])
                future[crop] += CROPS[crop][3]
                action("plant")
        if measured:
            metrics["missed_meals"] += len(pending_meals)
            for crop in minimum: minimum[crop] = min(minimum[crop], stock[crop])
        assert all(value >= 0 for value in stock.values())
    def per_day(counter): return {k: round(v / days, 3) for k, v in sorted(counter.items())}
    hours = {k.removesuffix("_seconds"): round(v / days / 3600, 3)
             for k, v in metrics.items() if k.endswith("_seconds")}
    hours["kitchen_idle"] = round(24 - hours.get("kitchen_busy", 0), 3)
    taps = sum(amount * TAPS[name] for name, amount in actions.items())
    return {"cats": cats, "visits_per_day": visits, "hens": 4, "cows": 2,
        "garden_slots": plots, "kitchen_queue": 4, "recipe": recipe,
        "days_measured": days, "warmup_days": warmup,
        "actions_per_day": per_day(actions), "estimated_taps_per_day": round(taps / days, 2),
        "estimated_taps_per_visit": round(taps / metrics["visits"], 2),
        "recipe_inputs_per_day": per_day(ingredients), "resident_inputs_per_day": per_day(feed_inputs),
        "stock_at_measurement_start": initial, "stock_at_end": dict(stock),
        "minimum_available": minimum,
        "metrics_per_day": per_day({k: v for k, v in metrics.items() if not k.endswith("_seconds")}),
        "hours_per_day": hours}


def preferences(samples=10000):
    rng = random.Random(SEED)
    favorites, rejected, censored = [], 0, 0
    for _ in range(samples):
        draw = rng.random()
        state = {"level": 1 if draw < .15 else 2 if draw < .4 else 3, "streak": 0}
        for day in range(365):
            for hour in (0, 12):
                evolve(state, datetime(2026, 10, 16, tzinfo=UTC) + timedelta(days=day, hours=hour), rng)
            if state["level"] == 0:
                rejected += 1
                break
            if state["level"] == 6:
                favorites.append(day + 1)
                break
        else: censored += 1
    favorites.sort()
    return {"samples": samples, "seed": SEED, "horizon_days": 365,
        "favorites": len(favorites), "rejected_before_favorite": rejected, "not_terminal": censored,
        "median_days_among_favorites": statistics.median(favorites) if favorites else None,
        "p90_days_among_favorites": favorites[math.ceil(len(favorites) * .9) - 1] if favorites else None,
        "warning": "Conditional on this recipe becoming a favorite; rejection is terminal. Two meals per day still evaluate at most once per 24 hours."}


def simulate():
    settings = get_settings()
    return {"schema": 2, "seed": SEED,
        "source_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                          for name in ("scripts/simulate_quintal_balance.py",
                                       "app/services/quintal_catalog.py",
                                       "app/services/quintal_production.py")},
        "configuration_observed": {"free_shop": settings.habitat_free_shop,
                                   "instant_build": settings.habitat_instant_build},
        "preserved_base_prices": {"facilities": {k: v[1] for k, v in FACILITIES.items()},
                                  "orders": {k: v[1] for k, v in ORDERS.items()}},
        "tap_assumptions": TAPS,
        "assumptions": [
            "Established garden level 3, kitchen level 2, four hens and two cows in all scenarios.",
            "Seven-day warmup followed by 28 measured days; visits equally spaced, no waiting inside a visit.",
            "Two accepted special meals per cat/day; free basic food excluded conservatively. Refusals are audited separately, not hidden in the capacity baseline.",
            "Initial crop stock covers three days, plus eight eggs, four milk and two rations/cat. Report start/end stocks to expose depletion.",
            "Farm grows current recipe inputs and animal feed, targeting two days of supplies; no watering boost.",
            "Daily water and petting; serving replaces/refunds old portions. No optional play/fishing, travel, camera gestures or notification taps counted.",
            "Taps are an explicit command-flow estimate, not device telemetry; repeated object selection is conservative.",
            "Resident hours are per animal (all same-species residents share supplies); garden waiting uses bed-hours. Kitchen idle means no pending recipe, not a blocked server.",
            "This deterministic capacity model does not replace the deferred connected/PostgreSQL validation."],
        "scenarios": [run_scenario(cats, visits, recipe)
                      for recipe in ("garden", "egg") for cats in (3, 6) for visits in (1, 2)],
        "recipe_daily_budget": [{"cats": cats, "recipe": recipe,
            "batches": math.ceil(cats * 2 / definition[3]),
            "portions": math.ceil(cats * 2 / definition[3]) * definition[3],
            "inputs": {k: v * math.ceil(cats * 2 / definition[3]) for k, v in definition[1].items()}}
            for cats in (3, 6) for recipe, definition in RECIPES.items()],
        "preferences": preferences(),
        "applied_balance_changes": [],
        "proposals_not_applied": [
            "Keep current prices, free-shop flag, capacities and meal cadence pending device playtesting.",
            "For F04, evaluate fruit recipes yielding 3 portions from 1 wheat + 2 fruit in 20 minutes; target satisfaction 16.",
            "For F04, evaluate a trade of 3 apples + 3 pears for 2 chicken with a one-hour order, keeping current coin prices."]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(simulate(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    else:
        print(result)
