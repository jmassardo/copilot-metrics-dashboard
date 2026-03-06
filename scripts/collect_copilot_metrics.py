"""
Collect GitHub Copilot usage metrics and seat data.

Uses the following GitHub REST API endpoints:
  - GET /orgs/{org}/copilot/metrics
  - GET /orgs/{org}/copilot/billing/seats

Stores raw data in data/copilot/ with daily snapshots.
"""

import os
import sys
import requests
from datetime import datetime, timezone

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from config import (
    GITHUB_API_BASE,
    GITHUB_ORG,
    get_headers,
    get_data_path,
    load_json,
    save_json,
)


def collect_copilot_metrics() -> dict:
    """
    Collect Copilot usage metrics from the org-level metrics API.

    GET /orgs/{org}/copilot/metrics

    Returns daily metrics including:
    - total_active_users
    - total_engaged_users
    - copilot_ide_code_completions (suggestions, acceptances, lines)
    - copilot_ide_chat (turns, insertions, copy events)
    - copilot_dotcom_chat, copilot_dotcom_pull_requests
    - breakdown by language and editor
    """
    print(f"Collecting Copilot metrics for org: {GITHUB_ORG}")

    all_metrics = []
    page = 1

    while True:
        resp = requests.get(
            f"{GITHUB_API_BASE}/orgs/{GITHUB_ORG}/copilot/metrics",
            headers=get_headers(),
            params={"per_page": 28, "page": page},
        )

        if resp.status_code == 404:
            print("  Copilot metrics API returned 404. Is Copilot enabled for this org?")
            return {"metrics": [], "collected_at": datetime.now(timezone.utc).isoformat()}

        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        all_metrics.extend(data)
        page += 1

        # The API returns max 28 days, so one page is usually sufficient
        if len(data) < 28:
            break

    print(f"  Collected {len(all_metrics)} days of Copilot metrics")

    return {
        "metrics": all_metrics,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def collect_copilot_seats() -> dict:
    """
    Collect Copilot seat assignment and activity data.

    GET /orgs/{org}/copilot/billing/seats

    Returns per-user data including:
    - assignee login
    - assigning_team
    - created_at (when seat was assigned)
    - last_activity_at
    - last_activity_editor
    - pending_cancellation_date
    """
    print(f"Collecting Copilot seat data for org: {GITHUB_ORG}")

    all_seats = []
    page = 1

    while True:
        resp = requests.get(
            f"{GITHUB_API_BASE}/orgs/{GITHUB_ORG}/copilot/billing/seats",
            headers=get_headers(),
            params={"per_page": 100, "page": page},
        )

        if resp.status_code == 404:
            print("  Copilot billing API returned 404. Check permissions.")
            return {
                "total_seats": 0,
                "seats": [],
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }

        resp.raise_for_status()
        data = resp.json()

        seats = data.get("seats", [])
        if not seats:
            break

        all_seats.extend(seats)
        total = data.get("total_seats", 0)

        print(f"  Page {page}: {len(seats)} seats (total: {total})")

        if len(all_seats) >= total:
            break

        page += 1

    # Calculate summary stats
    now = datetime.now(timezone.utc)
    active_seats = 0
    inactive_seats = 0
    never_used = 0

    for seat in all_seats:
        last_activity = seat.get("last_activity_at")
        if last_activity is None:
            never_used += 1
        else:
            activity_date = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
            days_since = (now - activity_date).days
            if days_since <= 30:
                active_seats += 1
            else:
                inactive_seats += 1

    print(f"  Total seats: {len(all_seats)}")
    print(f"  Active (30d): {active_seats}")
    print(f"  Inactive (30d+): {inactive_seats}")
    print(f"  Never used: {never_used}")

    return {
        "total_seats": len(all_seats),
        "active_seats": active_seats,
        "inactive_seats": inactive_seats,
        "never_used": never_used,
        "seats": all_seats,
        "collected_at": now.isoformat(),
    }


def process_metrics_summary(metrics_data: dict) -> dict:
    """
    Process raw Copilot metrics into a summary for the dashboard.

    Extracts daily time series for:
    - acceptance_rate (acceptances / suggestions)
    - active_users
    - engaged_users
    - total_suggestions
    - total_acceptances
    - total_lines_suggested
    - total_lines_accepted
    - chat_turns
    - language_breakdown
    - editor_breakdown
    """
    daily = []

    for day in metrics_data.get("metrics", []):
        date = day.get("date", "")

        # Extract code completion metrics
        completions = day.get("copilot_ide_code_completions", {})
        total_suggestions = 0
        total_acceptances = 0
        total_lines_suggested = 0
        total_lines_accepted = 0
        language_stats = {}
        editor_stats = {}

        for editor in completions.get("editors", []):
            editor_name = editor.get("name", "unknown")
            editor_suggestions = 0
            editor_acceptances = 0

            for model in editor.get("models", []):
                for lang in model.get("languages", []):
                    lang_name = lang.get("name", "unknown")
                    s = lang.get("total_code_suggestions", 0)
                    a = lang.get("total_code_acceptances", 0)
                    ls = lang.get("total_code_lines_suggested", 0)
                    la = lang.get("total_code_lines_accepted", 0)

                    total_suggestions += s
                    total_acceptances += a
                    total_lines_suggested += ls
                    total_lines_accepted += la
                    editor_suggestions += s
                    editor_acceptances += a

                    if lang_name not in language_stats:
                        language_stats[lang_name] = {
                            "suggestions": 0,
                            "acceptances": 0,
                        }
                    language_stats[lang_name]["suggestions"] += s
                    language_stats[lang_name]["acceptances"] += a

            editor_stats[editor_name] = {
                "suggestions": editor_suggestions,
                "acceptances": editor_acceptances,
            }

        # Extract chat metrics
        chat = day.get("copilot_ide_chat", {})
        chat_turns = 0
        chat_insertions = 0
        chat_copies = 0
        for editor in chat.get("editors", []):
            for model in editor.get("models", []):
                chat_turns += model.get("total_chat_turns", 0)
                chat_insertions += model.get("total_chat_insertion_events", 0)
                chat_copies += model.get("total_chat_copy_events", 0)

        acceptance_rate = (
            round((total_acceptances / total_suggestions) * 100, 2)
            if total_suggestions > 0
            else 0.0
        )

        daily.append(
            {
                "date": date,
                "active_users": day.get("total_active_users", 0),
                "engaged_users": day.get("total_engaged_users", 0),
                "total_suggestions": total_suggestions,
                "total_acceptances": total_acceptances,
                "acceptance_rate": acceptance_rate,
                "total_lines_suggested": total_lines_suggested,
                "total_lines_accepted": total_lines_accepted,
                "chat_turns": chat_turns,
                "chat_insertions": chat_insertions,
                "chat_copies": chat_copies,
                "languages": language_stats,
                "editors": editor_stats,
            }
        )

    return {"daily": sorted(daily, key=lambda x: x["date"])}


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data_path = get_data_path("copilot")

    # Collect raw metrics
    metrics_data = collect_copilot_metrics()
    save_json(os.path.join(data_path, f"metrics_{today}.json"), metrics_data)

    # Collect seat data
    seats_data = collect_copilot_seats()
    save_json(os.path.join(data_path, f"seats_{today}.json"), seats_data)

    # Process summary
    summary = process_metrics_summary(metrics_data)
    summary["seats"] = {
        "total": seats_data["total_seats"],
        "active": seats_data["active_seats"],
        "inactive": seats_data["inactive_seats"],
        "never_used": seats_data["never_used"],
    }
    summary["collected_at"] = metrics_data["collected_at"]

    save_json(os.path.join(data_path, f"summary_{today}.json"), summary)

    print(f"\nCopilot data saved to {data_path}/")
    print(f"  metrics_{today}.json - Raw API response")
    print(f"  seats_{today}.json   - Seat assignments and activity")
    print(f"  summary_{today}.json - Processed summary for dashboard")


if __name__ == "__main__":
    main()
