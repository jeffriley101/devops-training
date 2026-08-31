import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "static/js/app.js").read_text(encoding="utf-8")


def run_team_grouping(rows):
    start = APP.index("  const BOARD_CONTEST_TITLES")
    end = APP.index("  function wirePastWinners()", start)
    function_source = APP[start:end]
    script = (
        function_source
        + "\nconst rows = "
        + json.dumps(rows)
        + ";\nconsole.log(JSON.stringify(groupTeamContestResults(rows)));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def team_result(contest_key, contest_name, team_name, rank, score):
    return {
        "contest": {"key": contest_key, "name": contest_name},
        "subject_type": "team",
        "division": "open",
        "team_name": team_name,
        "rank": rank,
        "score": score,
    }


def test_historical_team_rank_sequences_are_grouped_by_contest_scope():
    rows = [
        team_result("team-average-practice", "Team Average Practice", "The Teachers", 1, 12000),
        team_result("team-average-practice", "Team Average Practice", "Union", 2, 3850),
        team_result("team-average-practice", "Team Average Practice", "St. Louis", 3, 3000),
        team_result("team-seasonal-points", "Team Seasonal Points", "Eureka", 1, 7),
        team_result("team-seasonal-points", "Team Seasonal Points", "Union", 2, 3),
        team_result("team-seasonal-points", "Team Seasonal Points", "The Teachers", 3, 2),
        team_result("team-weekly-practice", "Team Practice Minutes This Week", "Eureka", 1, 200),
    ]

    groups = run_team_grouping(rows)

    assert [group["contestKey"] for group in groups] == [
        "team-average-practice",
        "team-seasonal-points",
        "team-weekly-practice",
    ]
    assert [group["contestName"] for group in groups] == [
        "Team Average Practice",
        "Team Seasonal Points",
        "Practice Minutes this Week by Team",
    ]
    for group in groups:
        assert {row["contest"]["key"] for row in group["results"]} == {
            group["contestKey"]
        }
        rank_ones = [row for row in group["results"] if row["rank"] == 1]
        assert len(rank_ones) == 1


def test_true_olympic_rank_one_tie_remains_in_one_contest_group():
    rows = [
        team_result("team-weekly-practice", "Team Practice Minutes This Week", "Alpha", 1, 90),
        team_result("team-weekly-practice", "Team Practice Minutes This Week", "Beta", 1, 90),
        team_result("team-weekly-practice", "Team Practice Minutes This Week", "Gamma", 3, 70),
    ]

    groups = run_team_grouping(rows)

    assert len(groups) == 1
    rank_ones = [row for row in groups[0]["results"] if row["rank"] == 1]
    assert len(rank_ones) == 2
    assert len({row["score"] for row in rank_ones}) == 1


def test_medal_board_renders_clear_team_contest_headings_and_correct_metrics():
    renderer = APP[APP.index("    function renderContest"):APP.index("    function renderResults")]

    assert 'groupTeamContestResults(rows)' in renderer
    assert 'groupSection.className = "medal-contest team-medal-contest-group"' in renderer
    assert 'heading.textContent = group.contestName' in renderer
    assert 'groupRows.appendChild(row)' in renderer
    assert '["team-weekly-activity-points", "team-seasonal-points"]' in renderer
    assert 'result.contest.key === "team-average-practice"' in renderer
    assert '(result.score / 100).toFixed(2)' in renderer
