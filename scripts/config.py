"""
Configuration for the Copilot Metrics Dashboard.

All settings are loaded from environment variables with sensible defaults.
Set these in your GitHub Actions workflow secrets or local .env file.
"""

import os
import json


# GitHub API settings
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_BASE = os.environ.get("GITHUB_API_BASE", "https://api.github.com")

# Organization name (required)
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")

# Repositories to track for PR and issue metrics
# Comma-separated list, e.g., "repo1,repo2,repo3"
# Leave empty to auto-discover all org repos
GITHUB_REPOS = [
    r.strip()
    for r in os.environ.get("GITHUB_REPOS", "").split(",")
    if r.strip()
]

# Data directory (relative to repo root)
DATA_DIR = os.environ.get("DATA_DIR", "data")

# Site output directory
SITE_DIR = os.environ.get("SITE_DIR", "site")

# How many days of history to collect on each run
# The Copilot metrics API returns up to 28 days
COLLECTION_DAYS = int(os.environ.get("COLLECTION_DAYS", "28"))

# How many days of data to display on the dashboard
DASHBOARD_DAYS = int(os.environ.get("DASHBOARD_DAYS", "90"))

# Maximum repos to auto-discover (to avoid API rate limits)
MAX_REPOS = int(os.environ.get("MAX_REPOS", "50"))

# Alerting thresholds
ALERTS = {
    # Copilot seat inactive for this many days triggers an alert
    "seat_inactive_days": int(os.environ.get("ALERT_SEAT_INACTIVE_DAYS", "30")),
    # Copilot acceptance rate weekly drop threshold (percentage points)
    "acceptance_rate_drop": float(
        os.environ.get("ALERT_ACCEPTANCE_RATE_DROP", "10.0")
    ),
    # Median PR lifespan exceeds this many hours
    "pr_lifespan_hours": int(os.environ.get("ALERT_PR_LIFESPAN_HOURS", "48")),
    # Median time to first review exceeds this many hours
    "time_to_first_review_hours": int(
        os.environ.get("ALERT_TIME_TO_FIRST_REVIEW_HOURS", "24")
    ),
    # Issue backlog growing for this many consecutive weeks
    "issue_backlog_growing_weeks": int(
        os.environ.get("ALERT_ISSUE_BACKLOG_GROWING_WEEKS", "3")
    ),
    # Copilot active users drops below this percentage of total seats
    "min_active_user_pct": float(
        os.environ.get("ALERT_MIN_ACTIVE_USER_PCT", "50.0")
    ),
    # New seat with no activity after this many days
    "new_seat_inactive_days": int(
        os.environ.get("ALERT_NEW_SEAT_INACTIVE_DAYS", "14")
    ),
}


def get_headers():
    """Return standard headers for GitHub API requests."""
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_data_path(subdir: str) -> str:
    """Return the absolute path to a data subdirectory."""
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), DATA_DIR)
    path = os.path.join(base, subdir)
    os.makedirs(path, exist_ok=True)
    return path


def get_site_path() -> str:
    """Return the absolute path to the site output directory."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), SITE_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def load_json(filepath: str) -> dict | list:
    """Load a JSON file, returning empty dict if it doesn't exist."""
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return json.load(f)
    return {}


def save_json(filepath: str, data: dict | list):
    """Save data to a JSON file with pretty formatting."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)


def discover_repos() -> list[str]:
    """Auto-discover repositories in the organization."""
    import requests

    repos = []
    page = 1
    while len(repos) < MAX_REPOS:
        resp = requests.get(
            f"{GITHUB_API_BASE}/orgs/{GITHUB_ORG}/repos",
            headers=get_headers(),
            params={
                "type": "all",
                "sort": "pushed",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        repos.extend([r["name"] for r in batch if not r.get("archived", False)])
        page += 1
    return repos[:MAX_REPOS]


def get_repos() -> list[str]:
    """Get the list of repos to track. Uses configured list or auto-discovers."""
    if GITHUB_REPOS:
        return GITHUB_REPOS
    return discover_repos()
