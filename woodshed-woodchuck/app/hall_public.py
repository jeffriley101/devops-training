"""Explicit public Hall projections; internal ranking/entitlement data stays private.

These fields preserve the existing response contract, not a new disclosure policy.
Only add public fields deliberately, including fields inside nested structures.
"""


_DIVISIONS = ("open", "verified", "pristine")


def _medals(counts: dict) -> dict:
    return {key: counts[key] for key in ("gold", "silver", "bronze", "total")}


def _named_key(value: dict) -> dict:
    return {"key": value["key"], "name": value["name"]}


def _champion(row: dict, identity_fields: tuple[str, ...], *, student=False) -> dict:
    result = {key: row[key] for key in identity_fields}
    result["rank"] = row["rank"]
    result["medals"] = _medals(row["medals"])
    result["by_division"] = {
        division: _medals(row["by_division"][division])
        for division in _DIVISIONS if division in row["by_division"]
    }
    result["divisions"] = [division for division in row["divisions"] if division in _DIVISIONS]
    result["achievements"] = [{
        "season": _named_key(achievement["season"]),
        "contest": _named_key(achievement["contest"]),
        "division": achievement["division"],
        "medals": _medals(achievement["medals"]),
    } for achievement in row["achievements"]]
    if student:
        result["crown"] = {key: row["crown"][key] for key in (
            "qualifying_wins", "target_wins", "earned", "earned_count",
        )}
    return result


def public_hall_payload(payload: dict) -> dict:
    """Copy only public fields, without changing the internal payload or its children."""
    cups = payload["traveling_cups"]
    return {
        "students": [_champion(row, ("display_name",), student=True)
                     for row in payload["students"]],
        "teams": [_champion(row, ("team_id", "team_name", "emblem_key"))
                  for row in payload["teams"]],
        "instruments": [_champion(row, ("instrument_key", "instrument_label", "instrument_icon"))
                        for row in payload["instruments"]],
        "director_team_contests": [{
            "id": event["id"],
            "title": event["title"],
            "metric": event["metric"],
            "metric_label": event["metric_label"],
            "season": _named_key(event["season"]),
            "starts_at": event["starts_at"],
            "ends_at": event["ends_at"],
            "winners": [{
                "team_name": winner["team_name"],
                "emblem_key": winner["emblem_key"],
                "score": winner["score"],
            } for winner in event["winners"]],
        } for event in payload["director_team_contests"]],
        "traveling_cups": {
            "punxsutawney": {
                "holders": [{
                    "display_name": holder["display_name"],
                    "rank": holder["rank"],
                    "medals": _medals(holder["medals"]),
                } for holder in cups["punxsutawney"]["holders"]],
            },
            "coterie": {
                "teams": [{
                    "team_id": team["team_id"],
                    "team_name": team["team_name"],
                    "emblem_key": team["emblem_key"],
                    "rank": team["rank"],
                    "medals": _medals(team["medals"]),
                } for team in cups["coterie"]["teams"]],
            },
        },
    }
