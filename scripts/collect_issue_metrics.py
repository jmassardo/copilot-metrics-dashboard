"""
Collect Issue lifecycle metrics for tracked repositories.

Uses the following GitHub REST API endpoints:
  - GET /repos/{owner}/{repo}/issues (state=closed, state=open)
  - GET /repos/{owner}/{repo}/issues/{issue_number}/comments
  - GET /repos/{owner}/{repo}/issues/{issue_number}/timeline

Collects:
  - Issue lifespan (open to close duration)
  - Time to first response (first comment)
  - Issue throughput (opened vs. closed per week)
  - Stale issue count (no activity in 30+ days)
  - Backlog trend (is it growing or shrinking?)

Stores raw data in data/issues/ with daily snapshots.
"""

import os
import sys
import requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from config import (
    GITHUB_API_BASE,
    GITHUB_ORG,
    COLLECTION_DAYS,
    get_headers,
    get_data_path,
    save_json,
    get_repos,
)


def collect_closed_issues(repo: str, since: datetime) -> list[dict]:
    """
    Collect recently closed issues for a repo.
    Filters out pull requests (GitHub API returns PRs in the issues endpoint).

    GET /repos/{owner}/{repo}/issues?state=closed&sort=updated&direction=desc
    """
    issues = []
    page = 1

    while True:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{GITHUB_ORG}/{repo}/issues",
            headers=get_headers(),
            params={
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
                "since": since.isoformat(),
            },
        )
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        for issue in batch:
            # Skip pull requests (they show up in the issues endpoint)
            if "pull_request" in issue:
                continue
            issues.append(issue)

        page += 1

        if page > 10:
            break

    return issues


def collect_open_issues(repo: str) -> list[dict]:
    """
    Collect currently open issues for a repo.

    GET /repos/{owner}/{repo}/issues?state=open
    """
    issues = []
    page = 1

    while True:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{GITHUB_ORG}/{repo}/issues",
            headers=get_headers(),
            params={
                "state": "open",
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        for issue in batch:
            if "pull_request" in issue:
                continue
            issues.append(issue)

        page += 1

        if page > 5:
            break

    return issues


def collect_issue_comments(repo: str, issue_number: int) -> list[dict]:
    """
    Collect comments for a specific issue.

    GET /repos/{owner}/{repo}/issues/{issue_number}/comments
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{GITHUB_ORG}/{repo}/issues/{issue_number}/comments",
        headers=get_headers(),
        params={"per_page": 10},  # Only need first few for response time
    )
    resp.raise_for_status()
    return resp.json()


def calculate_issue_metrics(issue: dict, comments: list[dict]) -> dict:
    """
    Calculate lifecycle metrics for a single issue.
    """
    created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
    closed = issue.get("closed_at")
    now = datetime.now(timezone.utc)

    # Issue lifespan
    lifespan_hours = None
    if closed:
        close_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
        lifespan_hours = round((close_dt - created).total_seconds() / 3600, 2)

    # Time to first response (first comment not by the author)
    time_to_first_response_hours = None
    author = issue["user"]["login"]
    non_author_comments = [
        c for c in comments if c["user"]["login"] != author
    ]
    if non_author_comments:
        first_comment = min(non_author_comments, key=lambda c: c["created_at"])
        comment_dt = datetime.fromisoformat(
            first_comment["created_at"].replace("Z", "+00:00")
        )
        time_to_first_response_hours = round(
            (comment_dt - created).total_seconds() / 3600, 2
        )

    # Is it stale? (open + no activity in 30 days)
    last_activity = issue.get("updated_at", issue["created_at"])
    last_activity_dt = datetime.fromisoformat(
        last_activity.replace("Z", "+00:00")
    )
    is_stale = issue["state"] == "open" and (now - last_activity_dt).days > 30

    # Days open (for open issues)
    days_open = (now - created).days if issue["state"] == "open" else None

    return {
        "repo": issue["repository_url"].split("/")[-1] if "repository_url" in issue else "",
        "number": issue["number"],
        "title": issue["title"],
        "author": author,
        "state": issue["state"],
        "created_at": issue["created_at"],
        "closed_at": closed,
        "lifespan_hours": lifespan_hours,
        "time_to_first_response_hours": time_to_first_response_hours,
        "comment_count": issue.get("comments", 0),
        "is_stale": is_stale,
        "days_open": days_open,
        "labels": [l["name"] for l in issue.get("labels", [])],
    }


def calculate_aggregate_stats(issue_metrics: list[dict]) -> dict:
    """
    Calculate aggregate issue health statistics.
    """
    import statistics

    closed_issues = [i for i in issue_metrics if i["state"] == "closed"]
    open_issues = [i for i in issue_metrics if i["state"] == "open"]
    stale_issues = [i for i in issue_metrics if i.get("is_stale", False)]

    lifespans = [
        i["lifespan_hours"] for i in closed_issues if i["lifespan_hours"] is not None
    ]
    response_times = [
        i["time_to_first_response_hours"]
        for i in issue_metrics
        if i["time_to_first_response_hours"] is not None
    ]

    def safe_median(values):
        return round(statistics.median(values), 2) if values else None

    def safe_p90(values):
        if not values:
            return None
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * 0.9)
        return round(sorted_vals[min(idx, len(sorted_vals) - 1)], 2)

    def safe_mean(values):
        return round(statistics.mean(values), 2) if values else None

    # Weekly throughput: opened vs. closed per week
    weekly_opened = {}
    weekly_closed = {}

    for issue in issue_metrics:
        created_dt = datetime.fromisoformat(
            issue["created_at"].replace("Z", "+00:00")
        )
        week_key = created_dt.strftime("%Y-W%W")
        weekly_opened[week_key] = weekly_opened.get(week_key, 0) + 1

    for issue in closed_issues:
        if issue["closed_at"]:
            closed_dt = datetime.fromisoformat(
                issue["closed_at"].replace("Z", "+00:00")
            )
            week_key = closed_dt.strftime("%Y-W%W")
            weekly_closed[week_key] = weekly_closed.get(week_key, 0) + 1

    # Backlog trend: delta (opened - closed) per week
    all_weeks = sorted(set(list(weekly_opened.keys()) + list(weekly_closed.keys())))
    weekly_delta = {}
    for week in all_weeks:
        opened = weekly_opened.get(week, 0)
        closed = weekly_closed.get(week, 0)
        weekly_delta[week] = opened - closed

    # Is the backlog growing? Check the last N weeks
    recent_deltas = [weekly_delta[w] for w in all_weeks[-6:]] if len(all_weeks) >= 3 else []
    backlog_growing_weeks = 0
    for delta in reversed(recent_deltas):
        if delta > 0:
            backlog_growing_weeks += 1
        else:
            break

    return {
        "total_issues_analyzed": len(issue_metrics),
        "closed_issues": len(closed_issues),
        "open_issues": len(open_issues),
        "stale_issues": len(stale_issues),
        "lifespan": {
            "median_hours": safe_median(lifespans),
            "p90_hours": safe_p90(lifespans),
            "mean_hours": safe_mean(lifespans),
        },
        "time_to_first_response": {
            "median_hours": safe_median(response_times),
            "p90_hours": safe_p90(response_times),
            "mean_hours": safe_mean(response_times),
        },
        "throughput": {
            "weekly_opened": weekly_opened,
            "weekly_closed": weekly_closed,
            "weekly_delta": weekly_delta,
        },
        "backlog_growing_weeks": backlog_growing_weeks,
    }


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = datetime.now(timezone.utc) - timedelta(days=COLLECTION_DAYS)
    data_path = get_data_path("issues")

    repos = get_repos()
    print(f"Collecting issue metrics for {len(repos)} repos since {since.strftime('%Y-%m-%d')}")

    all_issue_metrics = []

    for repo in repos:
        print(f"\n  Repo: {GITHUB_ORG}/{repo}")

        # Collect closed issues
        closed_issues = collect_closed_issues(repo, since)
        print(f"    Closed issues: {len(closed_issues)}")

        # Collect open issues
        open_issues = collect_open_issues(repo)
        print(f"    Open issues: {len(open_issues)}")

        all_issues = closed_issues + open_issues

        # Calculate metrics for each issue
        for issue in all_issues:
            try:
                comments = collect_issue_comments(repo, issue["number"])
                metrics = calculate_issue_metrics(issue, comments)
                # Set repo name since repository_url isn't always reliable
                metrics["repo"] = repo
                all_issue_metrics.append(metrics)
            except Exception as e:
                print(f"    Error processing issue #{issue['number']}: {e}")

    # Calculate aggregate stats
    aggregate = calculate_aggregate_stats(all_issue_metrics)

    # Group metrics by repo
    repo_stats = {}
    for repo in repos:
        repo_issues = [i for i in all_issue_metrics if i["repo"] == repo]
        if repo_issues:
            repo_stats[repo] = calculate_aggregate_stats(repo_issues)

    result = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "period_start": since.isoformat(),
        "period_end": datetime.now(timezone.utc).isoformat(),
        "aggregate": aggregate,
        "by_repo": repo_stats,
        "issues": all_issue_metrics,
    }

    save_json(os.path.join(data_path, f"issue_metrics_{today}.json"), result)

    print(f"\nIssue metrics saved to {data_path}/issue_metrics_{today}.json")
    print(f"  Total issues analyzed: {aggregate['total_issues_analyzed']}")
    print(f"  Open: {aggregate['open_issues']}, Closed: {aggregate['closed_issues']}")
    print(f"  Stale issues: {aggregate['stale_issues']}")
    if aggregate["lifespan"]["median_hours"] is not None:
        print(f"  Median issue lifespan: {aggregate['lifespan']['median_hours']} hours")
    print(f"  Backlog growing for {aggregate['backlog_growing_weeks']} consecutive weeks")


if __name__ == "__main__":
    main()
