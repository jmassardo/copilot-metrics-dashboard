"""
Collect Pull Request metrics for tracked repositories.

Uses the following GitHub REST API endpoints:
  - GET /repos/{owner}/{repo}/pulls (state=closed, state=open)
  - GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews

Collects:
  - PR lifespan (open to merge duration)
  - Time to first review
  - Review cycles (rounds of review)
  - PR size (additions + deletions)
  - Merge rate (merged vs. closed without merge)
  - Weekly throughput

Stores raw data in data/pulls/ with daily snapshots.
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


def collect_closed_prs(repo: str, since: datetime) -> list[dict]:
    """
    Collect recently closed (merged or unmerged) PRs for a repo.

    GET /repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc
    """
    prs = []
    page = 1

    while True:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{GITHUB_ORG}/{repo}/pulls",
            headers=get_headers(),
            params={
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        for pr in batch:
            updated = datetime.fromisoformat(
                pr["updated_at"].replace("Z", "+00:00")
            )
            if updated < since:
                return prs
            prs.append(pr)

        page += 1

        # Safety limit
        if page > 10:
            break

    return prs


def collect_open_prs(repo: str) -> list[dict]:
    """
    Collect currently open PRs for a repo.

    GET /repos/{owner}/{repo}/pulls?state=open
    """
    prs = []
    page = 1

    while True:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{GITHUB_ORG}/{repo}/pulls",
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

        prs.extend(batch)
        page += 1

        if page > 5:
            break

    return prs


def collect_pr_reviews(repo: str, pr_number: int) -> list[dict]:
    """
    Collect reviews for a specific PR.

    GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{GITHUB_ORG}/{repo}/pulls/{pr_number}/reviews",
        headers=get_headers(),
        params={"per_page": 100},
    )
    resp.raise_for_status()
    return resp.json()


def calculate_pr_metrics(pr: dict, reviews: list[dict]) -> dict:
    """
    Calculate health metrics for a single PR.

    Returns:
    - lifespan_hours: time from open to close (None if still open)
    - time_to_first_review_hours: time from open to first review
    - review_cycles: number of review rounds
    - additions: lines added
    - deletions: lines removed
    - total_changes: additions + deletions
    - was_merged: whether the PR was merged
    - lead_time_hours: time from first commit (branch created) to merge
    """
    created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
    closed = pr.get("closed_at")
    merged = pr.get("merged_at")

    # PR lifespan
    lifespan_hours = None
    if closed:
        close_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
        lifespan_hours = round((close_dt - created).total_seconds() / 3600, 2)

    # Time to first review
    time_to_first_review_hours = None
    if reviews:
        # Filter to actual reviews (not just comments)
        submitted_reviews = [
            r
            for r in reviews
            if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
            and r.get("submitted_at")
        ]
        if submitted_reviews:
            first_review = min(
                submitted_reviews,
                key=lambda r: r["submitted_at"],
            )
            review_dt = datetime.fromisoformat(
                first_review["submitted_at"].replace("Z", "+00:00")
            )
            time_to_first_review_hours = round(
                (review_dt - created).total_seconds() / 3600, 2
            )

    # Review cycles (count of CHANGES_REQUESTED events as a proxy)
    review_cycles = len(
        [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
    )

    return {
        "repo": pr["base"]["repo"]["name"],
        "number": pr["number"],
        "title": pr["title"],
        "author": pr["user"]["login"],
        "created_at": pr["created_at"],
        "closed_at": closed,
        "merged_at": merged,
        "was_merged": merged is not None,
        "state": pr["state"],
        "lifespan_hours": lifespan_hours,
        "time_to_first_review_hours": time_to_first_review_hours,
        "review_cycles": review_cycles,
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "total_changes": pr.get("additions", 0) + pr.get("deletions", 0),
        "review_count": len(reviews),
    }


def calculate_aggregate_stats(pr_metrics: list[dict]) -> dict:
    """
    Calculate aggregate PR health statistics.

    Returns summary stats including medians, p90s, and rates.
    """
    import statistics

    closed_prs = [p for p in pr_metrics if p["state"] == "closed"]
    merged_prs = [p for p in closed_prs if p["was_merged"]]
    open_prs = [p for p in pr_metrics if p["state"] == "open"]

    # Lifespan stats (merged PRs only for meaningful lifespan)
    lifespans = [p["lifespan_hours"] for p in merged_prs if p["lifespan_hours"] is not None]
    review_times = [
        p["time_to_first_review_hours"]
        for p in pr_metrics
        if p["time_to_first_review_hours"] is not None
    ]
    sizes = [p["total_changes"] for p in pr_metrics if p["total_changes"] > 0]

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

    merge_rate = (
        round(len(merged_prs) / len(closed_prs) * 100, 2)
        if closed_prs
        else None
    )

    # Weekly throughput (group by week)
    weekly = {}
    for p in merged_prs:
        if p["merged_at"]:
            merged_dt = datetime.fromisoformat(
                p["merged_at"].replace("Z", "+00:00")
            )
            week_key = merged_dt.strftime("%Y-W%W")
            weekly[week_key] = weekly.get(week_key, 0) + 1

    return {
        "total_prs_analyzed": len(pr_metrics),
        "closed_prs": len(closed_prs),
        "merged_prs": len(merged_prs),
        "open_prs": len(open_prs),
        "merge_rate_pct": merge_rate,
        "lifespan": {
            "median_hours": safe_median(lifespans),
            "p90_hours": safe_p90(lifespans),
            "mean_hours": safe_mean(lifespans),
        },
        "time_to_first_review": {
            "median_hours": safe_median(review_times),
            "p90_hours": safe_p90(review_times),
            "mean_hours": safe_mean(review_times),
        },
        "pr_size": {
            "median_changes": safe_median(sizes),
            "p90_changes": safe_p90(sizes),
            "mean_changes": safe_mean(sizes),
        },
        "weekly_throughput": weekly,
    }


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = datetime.now(timezone.utc) - timedelta(days=COLLECTION_DAYS)
    data_path = get_data_path("pulls")

    repos = get_repos()
    print(f"Collecting PR metrics for {len(repos)} repos since {since.strftime('%Y-%m-%d')}")

    all_pr_metrics = []

    for repo in repos:
        print(f"\n  Repo: {GITHUB_ORG}/{repo}")

        # Collect closed PRs
        closed_prs = collect_closed_prs(repo, since)
        print(f"    Closed PRs: {len(closed_prs)}")

        # Collect open PRs
        open_prs = collect_open_prs(repo)
        print(f"    Open PRs: {len(open_prs)}")

        all_prs = closed_prs + open_prs

        # Collect reviews and calculate metrics for each PR
        for pr in all_prs:
            try:
                reviews = collect_pr_reviews(repo, pr["number"])
                metrics = calculate_pr_metrics(pr, reviews)
                all_pr_metrics.append(metrics)
            except Exception as e:
                print(f"    Error processing PR #{pr['number']}: {e}")

    # Calculate aggregate stats
    aggregate = calculate_aggregate_stats(all_pr_metrics)

    # Group metrics by repo for per-repo stats
    repo_stats = {}
    for repo in repos:
        repo_prs = [p for p in all_pr_metrics if p["repo"] == repo]
        if repo_prs:
            repo_stats[repo] = calculate_aggregate_stats(repo_prs)

    result = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "period_start": since.isoformat(),
        "period_end": datetime.now(timezone.utc).isoformat(),
        "aggregate": aggregate,
        "by_repo": repo_stats,
        "prs": all_pr_metrics,
    }

    save_json(os.path.join(data_path, f"pr_metrics_{today}.json"), result)

    print(f"\nPR metrics saved to {data_path}/pr_metrics_{today}.json")
    print(f"  Total PRs analyzed: {aggregate['total_prs_analyzed']}")
    print(f"  Merge rate: {aggregate['merge_rate_pct']}%")
    if aggregate["lifespan"]["median_hours"] is not None:
        print(f"  Median PR lifespan: {aggregate['lifespan']['median_hours']} hours")
    if aggregate["time_to_first_review"]["median_hours"] is not None:
        print(
            f"  Median time to first review: {aggregate['time_to_first_review']['median_hours']} hours"
        )


if __name__ == "__main__":
    main()
