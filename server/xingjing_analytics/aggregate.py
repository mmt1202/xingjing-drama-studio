from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .contracts import GROUP_LIMIT, UNKNOWN, AnalyticsFailure, AnalyticsQuery, Dataset, Fact, period

BASE_NOTES = {
    "tasks_created_in_range",
    "costs_settled_in_range",
    "holds_current",
    "finals_in_range_deduplicated",
    "shots_from_final_timelines",
    "duration_successful_tasks_only",
    "retry_is_recorded_attempt_not_rework",
    "production_cycle_unavailable",
    "external_publication_unavailable",
    "finals_not_attributed_to_model_or_member",
    "projects_current",
    "comparison_previous_full_period",
    "monthly_six_calendar_months",
}


def metrics(facts: list[Fact], *, days: int | None = None) -> dict[str, object]:
    tasks = [f for f in facts if f.kind == "task"]
    statuses = Counter(f.status for f in tasks)
    succeeded = statuses["succeeded"]
    failed = statuses["failed"] + statuses["timeout"]
    durations = [f.duration for f in tasks if f.status == "succeeded" and f.duration is not None]
    costs: dict[str, dict[str, int]] = defaultdict(
        lambda: {"actualMinor": 0, "heldMinor": 0, "failedMinor": 0, "retryMinor": 0}
    )
    for fact in facts:
        if fact.kind == "cost":
            amounts = costs[fact.currency]
            amounts["actualMinor"] += fact.amount
            if fact.status in {"failed", "timeout"}:
                amounts["failedMinor"] += fact.amount
            if fact.retry:
                amounts["retryMinor"] += fact.amount
        elif fact.kind == "hold":
            costs[fact.currency]["heldMinor"] += fact.amount
    return {
        "projects": len({f.project for f in facts}),
        "episodes": len({(f.project, f.episode) for f in facts if f.kind == "final" and f.episode != UNKNOWN}),
        "shots": len({(f.project, f.episode, shot) for f in facts if f.kind == "final" for shot in f.shots}),
        "tasks": len(tasks),
        "succeeded": succeeded,
        "failed": failed,
        "cancelled": statuses["cancelled"],
        "processing": sum(
            n for status, n in statuses.items() if status not in {"succeeded", "failed", "timeout", "cancelled"}
        ),
        "successRate": round(succeeded / (succeeded + failed) * 100, 4) if succeeded + failed else None,
        "retriedTasks": sum(f.retry for f in tasks),
        "averageTaskDurationMs": round(sum(durations) / len(durations)) if durations else None,
        "taskDurationSamples": len(durations),
        "deliveries": sum(f.kind == "delivery" for f in facts),
        "deliveredEpisodes": len(
            {(f.project, f.episode) for f in facts if f.kind == "delivery" and f.episode != UNKNOWN}
        ),
        "costs": [
            {
                "currency": currency,
                **{key: str(value) for key, value in values.items()},
                "dailyActualMinor": None
                if days is None
                else str(
                    (Decimal(values["actualMinor"]) / Decimal(days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                ),
            }
            for currency, values in sorted(costs.items())
        ],
    }


def key_for(fact: Fact, group: str) -> str:
    if group == "project":
        return fact.project
    if group == "episode":
        return f"{fact.project}:{fact.episode}"
    if group == "model":
        return fact.model
    return fact.member


def label_for(key: str, group: str, data: Dataset) -> str:
    if group == "project":
        return data.projects.get(key, {}).get("label", key)
    if group == "model":
        return data.models.get(key, key)
    if group == "member":
        return data.members.get(key, key)
    project, episode = key.split(":", 1)
    return f"{data.projects.get(project, {}).get('label', project)} · {data.episodes.get((project, episode), episode)}"


def _matches(fact: Fact, query: AnalyticsQuery, data: Dataset) -> bool:
    if query.projectId and fact.project != query.projectId:
        return False
    if query.episodeId and query.episodeId not in {fact.episode, f"{fact.project}:{fact.episode}"}:
        return False
    if query.modelId and fact.model != query.modelId:
        return False
    if query.memberId and fact.member != query.memberId:
        return False
    if fact.kind == "project" and (
        query.episodeId or query.modelId or query.memberId or (query.q and query.groupBy != "project")
    ):
        return False
    if query.q:
        key = key_for(fact, query.groupBy)
        if query.q.casefold() not in f"{key} {label_for(key, query.groupBy, data)}".casefold():
            return False
    return True


def _month_shift(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    return value.replace(year=index // 12, month=index % 12 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def dashboard(data: Dataset, query: AnalyticsQuery, now: datetime, *, all_rows: bool = False) -> dict[str, object]:
    start = period(query.range, now)
    candidates = [f for f in data.facts if _matches(f, query, data) and f.at < now]
    selected = [f for f in candidates if f.kind in {"hold", "project"} or start is None or f.at >= start]
    days = None if start is None else (now.date() - start.date()).days + 1
    comparison = None
    if start is not None:
        previous_start = (
            start - timedelta(days=7)
            if query.range == "week"
            else _month_shift(start, -1 if query.range == "month" else -3)
        )
        previous_facts = [f for f in candidates if f.kind not in {"hold", "project"} and previous_start <= f.at < start]
        comparison = {
            "start": previous_start.isoformat(),
            "end": start.isoformat(),
            "summary": metrics(previous_facts, days=(start.date() - previous_start.date()).days),
        }
    groups: dict[str, list[Fact]] = defaultdict(list)
    for fact in selected:
        if fact.kind == "project" and query.groupBy != "project":
            continue
        groups[key_for(fact, query.groupBy)].append(fact)
    if len(groups) > GROUP_LIMIT:
        raise AnalyticsFailure("ANALYTICS_SCAN_LIMIT", 422)
    rows = []
    for key, facts in sorted(
        groups.items(), key=lambda item: (label_for(item[0], query.groupBy, data).casefold(), item[0])
    ):
        row = {"id": key, "label": label_for(key, query.groupBy, data), **metrics(facts, days=days)}
        if query.groupBy == "project":
            row["projectId"] = key
        elif query.groupBy == "episode":
            row["projectId"], row["episodeId"] = key.split(":", 1)
        elif query.groupBy == "model":
            row["modelId"] = key
        else:
            row["memberId"] = key
        rows.append(row)
    granularity = "month" if query.range in {"quarter", "all"} else "day"
    event_facts = [f for f in selected if f.kind not in {"hold", "project"}]
    trend_start = start or min((f.at for f in event_facts), default=now)
    cursor = (
        trend_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if granularity == "month"
        else trend_start.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    trend = []
    while cursor < now:
        if len(trend) >= 120:
            raise AnalyticsFailure("ANALYTICS_SCAN_LIMIT", 422)
        following = _month_shift(cursor, 1) if granularity == "month" else cursor + timedelta(days=1)
        bucket = [f for f in event_facts if cursor <= f.at < following]
        trend.append({"date": cursor.strftime("%Y-%m" if granularity == "month" else "%Y-%m-%d"), **metrics(bucket)})
        cursor = following
    monthly = []
    for offset in range(-5, 1):
        begin = _month_shift(now, offset)
        end = _month_shift(now, offset + 1)
        monthly.append(
            {
                "month": begin.strftime("%Y-%m"),
                "episodes": len(
                    {
                        (f.project, f.episode)
                        for f in candidates
                        if f.kind == "final" and f.episode != UNKNOWN and begin <= f.at < end
                    }
                ),
            }
        )
    task_facts = [f for f in selected if f.kind == "task"]
    models = Counter(f.model for f in task_facts)
    members: dict[str, list[Fact]] = defaultdict(list)
    for fact in task_facts:
        members[fact.member].append(fact)
    project_ids = {f.project for f in selected}
    states = Counter(data.projects[p]["state"] for p in project_ids)
    notes = BASE_NOTES | data.notes
    missing = {
        "model": sum(f.model == UNKNOWN for f in task_facts),
        "member": sum(f.member == UNKNOWN for f in task_facts),
        "episode": sum(f.episode == UNKNOWN for f in task_facts),
    }
    if any(missing.values()):
        notes.add("unknown_attribution")
    option_facts = data.facts
    model_options = set(f.model for f in option_facts if f.kind in {"task", "cost", "hold", "final", "delivery"})
    member_options = set(f.member for f in option_facts if f.kind in {"task", "cost", "hold", "final", "delivery"})
    episode_options = [
        {"id": f"{p}:{e}", "label": f"{data.projects[p]['label']} · {label}", "projectId": p}
        for (p, e), label in sorted(data.episodes.items())
    ]
    if any(f.episode == UNKNOWN for f in option_facts if f.kind != "project"):
        episode_options.append({"id": UNKNOWN, "label": UNKNOWN, "projectId": ""})
    return {
        "generatedAt": now.isoformat(),
        "range": {
            "key": query.range,
            "start": None if start is None else start.isoformat(),
            "end": now.isoformat(),
            "timeZone": "UTC",
        },
        "filters": query.model_dump(exclude={"offset", "limit", "range"}),
        "summary": metrics(selected, days=days),
        "comparison": comparison,
        "trend": trend,
        "trendGranularity": granularity,
        "monthlyProduction": monthly,
        "projectStates": [{"state": state, "count": count} for state, count in sorted(states.items())],
        "modelUsage": [
            {
                "id": key,
                "label": data.models.get(key, key),
                "tasks": count,
                "share": round(count / len(task_facts) * 100, 4),
            }
            for key, count in sorted(models.items(), key=lambda item: (-item[1], item[0]))
        ],
        "memberContributions": [
            {
                "id": key,
                "label": data.members.get(key, key),
                "tasks": len(facts),
                "succeeded": sum(f.status == "succeeded" for f in facts),
            }
            for key, facts in sorted(members.items(), key=lambda item: (-len(item[1]), item[0]))
        ],
        "rows": rows if all_rows else rows[query.offset : query.offset + query.limit],
        "total": len(rows),
        "offset": query.offset,
        "limit": query.limit,
        "options": {
            "projects": [
                {"id": p, "label": v["label"]}
                for p, v in sorted(data.projects.items(), key=lambda item: (item[1]["label"].casefold(), item[0]))
            ],
            "episodes": episode_options,
            "models": [{"id": key, "label": data.models.get(key, key)} for key in sorted(model_options)],
            "members": [{"id": key, "label": data.members.get(key, key)} for key in sorted(member_options)],
        },
        "coverage": {"sources": data.sources, "missingAttribution": missing, "notes": sorted(notes)},
    }
