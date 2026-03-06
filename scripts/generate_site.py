"""
Generate the static dashboard site from collected metrics data.

Reads JSON data from data/ subdirectories, aggregates historical trends,
generates alerts, and writes the final dashboard data as JSON files
that the static HTML/JS site consumes.

Also handles merging daily snapshots into rolling historical data files.
"""

import os
import sys
import glob
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from config import (
    ALERTS,
    DASHBOARD_DAYS,
    get_data_path,
    get_site_path,
    load_json,
    save_json,
)


def merge_copilot_history() -> dict:
    """
    Merge all daily Copilot summary files into a single historical dataset.
    Deduplicates by date, keeping the most recent collection for each day.
    """
    data_path = get_data_path("copilot")
    history_file = os.path.join(get_data_path("aggregated"), "copilot_history.json")
    history = load_json(history_file)

    if isinstance(history, dict) and "daily" not in history:
        history = {"daily": {}, "seats_history": []}

    # Load all summary files
    for filepath in sorted(glob.glob(os.path.join(data_path, "summary_*.json"))):
        summary = load_json(filepath)
        for day in summary.get("daily", []):
            date = day.get("date", "")
            if date:
                history["daily"][date] = day

        # Track seat snapshots
        if "seats" in summary:
            seats_entry = {
                "date": summary.get("collected_at", "")[:10],
                **summary["seats"],
            }
            # Avoid duplicates for same date
            existing_dates = [s["date"] for s in history.get("seats_history", [])]
            if seats_entry["date"] not in existing_dates:
                history.setdefault("seats_history", []).append(seats_entry)

    # Trim to dashboard window
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)
    ).strftime("%Y-%m-%d")
    history["daily"] = {
        k: v for k, v in history["daily"].items() if k >= cutoff
    }
    history["seats_history"] = [
        s for s in history.get("seats_history", []) if s["date"] >= cutoff
    ]

    save_json(history_file, history)
    return history


def merge_pr_history() -> dict:
    """
    Merge all daily PR metric files into a single historical dataset.
    """
    data_path = get_data_path("pulls")
    history_file = os.path.join(get_data_path("aggregated"), "pr_history.json")
    history = load_json(history_file)

    if isinstance(history, dict) and "snapshots" not in history:
        history = {"snapshots": {}, "weekly_throughput": {}}

    for filepath in sorted(glob.glob(os.path.join(data_path, "pr_metrics_*.json"))):
        data = load_json(filepath)
        date = filepath.split("pr_metrics_")[-1].replace(".json", "")

        # Store the aggregate snapshot for this collection date
        if "aggregate" in data:
            history["snapshots"][date] = {
                "date": date,
                "total_prs": data["aggregate"].get("total_prs_analyzed", 0),
                "merged_prs": data["aggregate"].get("merged_prs", 0),
                "open_prs": data["aggregate"].get("open_prs", 0),
                "merge_rate_pct": data["aggregate"].get("merge_rate_pct"),
                "median_lifespan_hours": data["aggregate"]
                .get("lifespan", {})
                .get("median_hours"),
                "p90_lifespan_hours": data["aggregate"]
                .get("lifespan", {})
                .get("p90_hours"),
                "median_ttfr_hours": data["aggregate"]
                .get("time_to_first_review", {})
                .get("median_hours"),
                "median_pr_size": data["aggregate"]
                .get("pr_size", {})
                .get("median_changes"),
            }

            # Merge weekly throughput
            for week, count in (
                data["aggregate"].get("weekly_throughput", {}).items()
            ):
                history["weekly_throughput"][week] = count

    # Trim to dashboard window
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)
    ).strftime("%Y-%m-%d")
    history["snapshots"] = {
        k: v for k, v in history["snapshots"].items() if k >= cutoff
    }

    save_json(history_file, history)
    return history


def merge_issue_history() -> dict:
    """
    Merge all daily issue metric files into a single historical dataset.
    """
    data_path = get_data_path("issues")
    history_file = os.path.join(get_data_path("aggregated"), "issue_history.json")
    history = load_json(history_file)

    if isinstance(history, dict) and "snapshots" not in history:
        history = {"snapshots": {}, "weekly_opened": {}, "weekly_closed": {}}

    for filepath in sorted(
        glob.glob(os.path.join(data_path, "issue_metrics_*.json"))
    ):
        data = load_json(filepath)
        date = filepath.split("issue_metrics_")[-1].replace(".json", "")

        if "aggregate" in data:
            history["snapshots"][date] = {
                "date": date,
                "total_issues": data["aggregate"].get("total_issues_analyzed", 0),
                "open_issues": data["aggregate"].get("open_issues", 0),
                "closed_issues": data["aggregate"].get("closed_issues", 0),
                "stale_issues": data["aggregate"].get("stale_issues", 0),
                "median_lifespan_hours": data["aggregate"]
                .get("lifespan", {})
                .get("median_hours"),
                "p90_lifespan_hours": data["aggregate"]
                .get("lifespan", {})
                .get("p90_hours"),
                "median_ttfr_hours": data["aggregate"]
                .get("time_to_first_response", {})
                .get("median_hours"),
                "backlog_growing_weeks": data["aggregate"].get(
                    "backlog_growing_weeks", 0
                ),
            }

            # Merge weekly throughput
            throughput = data["aggregate"].get("throughput", {})
            for week, count in throughput.get("weekly_opened", {}).items():
                history["weekly_opened"][week] = count
            for week, count in throughput.get("weekly_closed", {}).items():
                history["weekly_closed"][week] = count

    # Trim to dashboard window
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)
    ).strftime("%Y-%m-%d")
    history["snapshots"] = {
        k: v for k, v in history["snapshots"].items() if k >= cutoff
    }

    save_json(history_file, history)
    return history


def generate_alerts(
    copilot_history: dict, pr_history: dict, issue_history: dict
) -> list[dict]:
    """
    Evaluate all alert conditions and return a list of active alerts.

    Each alert includes:
    - severity: "critical", "warning", "info"
    - category: "copilot", "pr", "issue"
    - title: short description
    - detail: longer explanation with data
    - timestamp: when the alert was generated
    """
    alerts = []
    now = datetime.now(timezone.utc).isoformat()

    # --- Copilot Alerts ---

    # 1. Inactive seats
    seats_history = copilot_history.get("seats_history", [])
    if seats_history:
        latest_seats = seats_history[-1]
        inactive = latest_seats.get("inactive", 0)
        never_used = latest_seats.get("never_used", 0)
        total = latest_seats.get("total", 0)
        waste_count = inactive + never_used

        if waste_count > 0:
            alerts.append(
                {
                    "severity": "warning" if waste_count < 10 else "critical",
                    "category": "copilot",
                    "title": "Unused Copilot Seats Detected",
                    "detail": (
                        f"{waste_count} of {total} seats are unused "
                        f"({inactive} inactive 30+ days, {never_used} never used). "
                        f"Estimated monthly waste: ${waste_count * 19}/mo at $19/seat."
                    ),
                    "timestamp": now,
                }
            )

    # 2. Acceptance rate drop
    daily = copilot_history.get("daily", {})
    sorted_days = sorted(daily.keys())
    if len(sorted_days) >= 14:
        # Compare last 7 days to previous 7 days
        recent_7 = sorted_days[-7:]
        prev_7 = sorted_days[-14:-7]

        recent_rates = [
            daily[d]["acceptance_rate"]
            for d in recent_7
            if daily[d].get("acceptance_rate", 0) > 0
        ]
        prev_rates = [
            daily[d]["acceptance_rate"]
            for d in prev_7
            if daily[d].get("acceptance_rate", 0) > 0
        ]

        if recent_rates and prev_rates:
            recent_avg = sum(recent_rates) / len(recent_rates)
            prev_avg = sum(prev_rates) / len(prev_rates)
            drop = prev_avg - recent_avg

            if drop >= ALERTS["acceptance_rate_drop"]:
                alerts.append(
                    {
                        "severity": "warning",
                        "category": "copilot",
                        "title": "Copilot Acceptance Rate Declining",
                        "detail": (
                            f"Acceptance rate dropped {drop:.1f} percentage points "
                            f"week-over-week (from {prev_avg:.1f}% to {recent_avg:.1f}%). "
                            f"Threshold: {ALERTS['acceptance_rate_drop']}pp."
                        ),
                        "timestamp": now,
                    }
                )

    # 3. Low active user percentage
    if seats_history:
        latest = seats_history[-1]
        total = latest.get("total", 0)
        active = latest.get("active", 0)
        if total > 0:
            active_pct = (active / total) * 100
            if active_pct < ALERTS["min_active_user_pct"]:
                alerts.append(
                    {
                        "severity": "warning",
                        "category": "copilot",
                        "title": "Low Copilot Active User Rate",
                        "detail": (
                            f"Only {active_pct:.1f}% of seats are actively used "
                            f"({active}/{total}). Threshold: {ALERTS['min_active_user_pct']}%."
                        ),
                        "timestamp": now,
                    }
                )

    # --- PR Alerts ---

    pr_snapshots = pr_history.get("snapshots", {})
    if pr_snapshots:
        latest_pr = list(pr_snapshots.values())[-1]

        # 4. PR lifespan too high
        median_lifespan = latest_pr.get("median_lifespan_hours")
        if median_lifespan and median_lifespan > ALERTS["pr_lifespan_hours"]:
            alerts.append(
                {
                    "severity": "warning",
                    "category": "pr",
                    "title": "PR Lifespan Exceeds Threshold",
                    "detail": (
                        f"Median PR lifespan is {median_lifespan:.1f} hours "
                        f"(threshold: {ALERTS['pr_lifespan_hours']}h). "
                        f"PRs are taking too long to merge."
                    ),
                    "timestamp": now,
                }
            )

        # 5. Time to first review too high
        median_ttfr = latest_pr.get("median_ttfr_hours")
        if median_ttfr and median_ttfr > ALERTS["time_to_first_review_hours"]:
            alerts.append(
                {
                    "severity": "warning",
                    "category": "pr",
                    "title": "Slow Time to First Review",
                    "detail": (
                        f"Median time to first review is {median_ttfr:.1f} hours "
                        f"(threshold: {ALERTS['time_to_first_review_hours']}h). "
                        f"Code review bottleneck detected."
                    ),
                    "timestamp": now,
                }
            )

    # --- Issue Alerts ---

    issue_snapshots = issue_history.get("snapshots", {})
    if issue_snapshots:
        latest_issue = list(issue_snapshots.values())[-1]

        # 6. Issue backlog growing
        growing_weeks = latest_issue.get("backlog_growing_weeks", 0)
        if growing_weeks >= ALERTS["issue_backlog_growing_weeks"]:
            alerts.append(
                {
                    "severity": "critical" if growing_weeks >= 5 else "warning",
                    "category": "issue",
                    "title": "Issue Backlog Growing",
                    "detail": (
                        f"Issue backlog has been growing for {growing_weeks} "
                        f"consecutive weeks. More issues are being opened than "
                        f"closed. Threshold: {ALERTS['issue_backlog_growing_weeks']} weeks."
                    ),
                    "timestamp": now,
                }
            )

        # 7. High stale issue count
        stale = latest_issue.get("stale_issues", 0)
        if stale > 10:
            alerts.append(
                {
                    "severity": "info",
                    "category": "issue",
                    "title": "Stale Issues Accumulating",
                    "detail": (
                        f"{stale} issues have had no activity in 30+ days. "
                        f"Consider triaging or closing stale issues."
                    ),
                    "timestamp": now,
                }
            )

    return alerts


def generate_dashboard_data(
    copilot_history: dict,
    pr_history: dict,
    issue_history: dict,
    alerts: list[dict],
) -> dict:
    """
    Build the final JSON data structure consumed by the dashboard HTML/JS.
    """
    now = datetime.now(timezone.utc)

    # Copilot time series for charts
    daily = copilot_history.get("daily", {})
    sorted_days = sorted(daily.keys())

    copilot_chart = {
        "dates": sorted_days,
        "acceptance_rate": [daily[d].get("acceptance_rate", 0) for d in sorted_days],
        "active_users": [daily[d].get("active_users", 0) for d in sorted_days],
        "engaged_users": [daily[d].get("engaged_users", 0) for d in sorted_days],
        "total_suggestions": [
            daily[d].get("total_suggestions", 0) for d in sorted_days
        ],
        "total_acceptances": [
            daily[d].get("total_acceptances", 0) for d in sorted_days
        ],
        "chat_turns": [daily[d].get("chat_turns", 0) for d in sorted_days],
    }

    # Seat utilization over time
    seats_history = copilot_history.get("seats_history", [])
    seats_chart = {
        "dates": [s["date"] for s in seats_history],
        "total": [s.get("total", 0) for s in seats_history],
        "active": [s.get("active", 0) for s in seats_history],
        "inactive": [s.get("inactive", 0) for s in seats_history],
        "never_used": [s.get("never_used", 0) for s in seats_history],
    }

    # Language breakdown from most recent day
    language_breakdown = {}
    if sorted_days:
        latest = daily[sorted_days[-1]]
        language_breakdown = latest.get("languages", {})

    # Editor breakdown from most recent day
    editor_breakdown = {}
    if sorted_days:
        latest = daily[sorted_days[-1]]
        editor_breakdown = latest.get("editors", {})

    # PR metrics time series
    pr_snapshots = pr_history.get("snapshots", {})
    pr_dates = sorted(pr_snapshots.keys())
    pr_chart = {
        "dates": pr_dates,
        "median_lifespan": [
            pr_snapshots[d].get("median_lifespan_hours") for d in pr_dates
        ],
        "p90_lifespan": [
            pr_snapshots[d].get("p90_lifespan_hours") for d in pr_dates
        ],
        "median_ttfr": [
            pr_snapshots[d].get("median_ttfr_hours") for d in pr_dates
        ],
        "merge_rate": [
            pr_snapshots[d].get("merge_rate_pct") for d in pr_dates
        ],
        "median_size": [
            pr_snapshots[d].get("median_pr_size") for d in pr_dates
        ],
    }

    # PR weekly throughput
    pr_weekly = pr_history.get("weekly_throughput", {})
    pr_weeks = sorted(pr_weekly.keys())
    pr_throughput_chart = {
        "weeks": pr_weeks,
        "merged": [pr_weekly.get(w, 0) for w in pr_weeks],
    }

    # Issue metrics time series
    issue_snapshots = issue_history.get("snapshots", {})
    issue_dates = sorted(issue_snapshots.keys())
    issue_chart = {
        "dates": issue_dates,
        "open_issues": [
            issue_snapshots[d].get("open_issues", 0) for d in issue_dates
        ],
        "stale_issues": [
            issue_snapshots[d].get("stale_issues", 0) for d in issue_dates
        ],
        "median_lifespan": [
            issue_snapshots[d].get("median_lifespan_hours") for d in issue_dates
        ],
        "median_ttfr": [
            issue_snapshots[d].get("median_ttfr_hours") for d in issue_dates
        ],
    }

    # Issue weekly throughput
    weekly_opened = issue_history.get("weekly_opened", {})
    weekly_closed = issue_history.get("weekly_closed", {})
    all_issue_weeks = sorted(
        set(list(weekly_opened.keys()) + list(weekly_closed.keys()))
    )
    issue_throughput_chart = {
        "weeks": all_issue_weeks,
        "opened": [weekly_opened.get(w, 0) for w in all_issue_weeks],
        "closed": [weekly_closed.get(w, 0) for w in all_issue_weeks],
    }

    # Summary cards (latest values)
    summary = {
        "generated_at": now.isoformat(),
        "dashboard_window_days": DASHBOARD_DAYS,
    }

    if sorted_days:
        latest_copilot = daily[sorted_days[-1]]
        summary["copilot"] = {
            "acceptance_rate": latest_copilot.get("acceptance_rate", 0),
            "active_users": latest_copilot.get("active_users", 0),
            "total_suggestions_today": latest_copilot.get("total_suggestions", 0),
            "chat_turns_today": latest_copilot.get("chat_turns", 0),
        }

    if seats_history:
        latest_seats = seats_history[-1]
        summary["seats"] = {
            "total": latest_seats.get("total", 0),
            "active": latest_seats.get("active", 0),
            "inactive": latest_seats.get("inactive", 0),
            "never_used": latest_seats.get("never_used", 0),
            "utilization_pct": round(
                (latest_seats.get("active", 0) / max(latest_seats.get("total", 1), 1))
                * 100,
                1,
            ),
        }

    if pr_dates:
        latest_pr = pr_snapshots[pr_dates[-1]]
        summary["prs"] = {
            "median_lifespan_hours": latest_pr.get("median_lifespan_hours"),
            "median_ttfr_hours": latest_pr.get("median_ttfr_hours"),
            "merge_rate_pct": latest_pr.get("merge_rate_pct"),
            "median_size": latest_pr.get("median_pr_size"),
        }

    if issue_dates:
        latest_issue = issue_snapshots[issue_dates[-1]]
        summary["issues"] = {
            "open": latest_issue.get("open_issues", 0),
            "stale": latest_issue.get("stale_issues", 0),
            "median_lifespan_hours": latest_issue.get("median_lifespan_hours"),
            "backlog_growing_weeks": latest_issue.get("backlog_growing_weeks", 0),
        }

    return {
        "summary": summary,
        "alerts": alerts,
        "charts": {
            "copilot": copilot_chart,
            "seats": seats_chart,
            "languages": language_breakdown,
            "editors": editor_breakdown,
            "prs": pr_chart,
            "pr_throughput": pr_throughput_chart,
            "issues": issue_chart,
            "issue_throughput": issue_throughput_chart,
        },
    }


def main():
    print("Generating dashboard data...")

    # Merge historical data
    print("  Merging Copilot history...")
    copilot_history = merge_copilot_history()

    print("  Merging PR history...")
    pr_history = merge_pr_history()

    print("  Merging issue history...")
    issue_history = merge_issue_history()

    # Generate alerts
    print("  Evaluating alerts...")
    alerts = generate_alerts(copilot_history, pr_history, issue_history)
    print(f"    {len(alerts)} active alerts")

    for alert in alerts:
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(
            alert["severity"], "⚪"
        )
        print(f"    {icon} [{alert['category']}] {alert['title']}")

    # Build dashboard data
    print("  Building dashboard JSON...")
    dashboard_data = generate_dashboard_data(
        copilot_history, pr_history, issue_history, alerts
    )

    # Write to site directory
    site_path = get_site_path()
    output_file = os.path.join(site_path, "data", "dashboard.json")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    save_json(output_file, dashboard_data)

    # Also save alerts separately for easy consumption
    save_json(os.path.join(site_path, "data", "alerts.json"), alerts)

    print(f"\nDashboard data written to {output_file}")
    print(f"Alerts written to {os.path.join(site_path, 'data', 'alerts.json')}")


if __name__ == "__main__":
    main()
