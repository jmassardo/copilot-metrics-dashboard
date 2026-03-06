/**
 * Copilot Metrics Dashboard - Client-side JavaScript
 *
 * Loads dashboard.json and renders all charts and summary cards.
 * Uses Chart.js for visualization.
 */

// Chart.js defaults for dark theme
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif';

// Color palette
const COLORS = {
    blue: '#58a6ff',
    green: '#3fb950',
    yellow: '#d29922',
    red: '#f85149',
    purple: '#bc8cff',
    orange: '#d18616',
    cyan: '#39d2c0',
    pink: '#f778ba',
    blueFaded: 'rgba(88, 166, 255, 0.2)',
    greenFaded: 'rgba(63, 185, 80, 0.2)',
    yellowFaded: 'rgba(210, 153, 34, 0.2)',
    redFaded: 'rgba(248, 81, 73, 0.2)',
    purpleFaded: 'rgba(188, 140, 255, 0.2)',
};

/**
 * Fetch dashboard data from the generated JSON file.
 */
async function loadDashboardData() {
    try {
        const resp = await fetch('data/dashboard.json');
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }
        return await resp.json();
    } catch (err) {
        console.error('Failed to load dashboard data:', err);
        document.getElementById('lastUpdated').textContent =
            'Error: Could not load dashboard data. Run the collection workflow first.';
        return null;
    }
}

/**
 * Render the "last updated" timestamp.
 */
function renderTimestamp(summary) {
    const el = document.getElementById('lastUpdated');
    if (summary.generated_at) {
        const date = new Date(summary.generated_at);
        el.textContent = `Last updated: ${date.toLocaleString()} (${summary.dashboard_window_days}-day window)`;
    }
}

/**
 * Render summary cards with current values.
 */
function renderSummaryCards(summary) {
    // Acceptance rate
    if (summary.copilot) {
        const rate = summary.copilot.acceptance_rate;
        const el = document.getElementById('valAcceptanceRate');
        el.textContent = `${rate}%`;
        el.className = 'card-value ' + (rate >= 25 ? 'good' : rate >= 15 ? 'warning' : 'bad');
    }

    // Active users
    if (summary.seats) {
        const el = document.getElementById('valActiveUsers');
        el.textContent = `${summary.seats.active}`;
        document.getElementById('detailActiveUsers').textContent =
            `of ${summary.seats.total} seats (${summary.seats.utilization_pct}% utilized)`;

        const pct = summary.seats.utilization_pct;
        el.className = 'card-value ' + (pct >= 70 ? 'good' : pct >= 50 ? 'warning' : 'bad');
    }

    // PR Lifespan
    if (summary.prs) {
        const hours = summary.prs.median_lifespan_hours;
        const el = document.getElementById('valPRLifespan');
        if (hours !== null && hours !== undefined) {
            el.textContent = hours < 24 ? `${hours.toFixed(1)}h` : `${(hours / 24).toFixed(1)}d`;
            el.className = 'card-value ' + (hours <= 24 ? 'good' : hours <= 48 ? 'warning' : 'bad');
        }
    }

    // Time to first review
    if (summary.prs) {
        const hours = summary.prs.median_ttfr_hours;
        const el = document.getElementById('valTimeToReview');
        if (hours !== null && hours !== undefined) {
            el.textContent = `${hours.toFixed(1)}h`;
            el.className = 'card-value ' + (hours <= 8 ? 'good' : hours <= 24 ? 'warning' : 'bad');
        }
    }

    // Merge rate
    if (summary.prs) {
        const rate = summary.prs.merge_rate_pct;
        const el = document.getElementById('valMergeRate');
        if (rate !== null && rate !== undefined) {
            el.textContent = `${rate}%`;
            el.className = 'card-value ' + (rate >= 85 ? 'good' : rate >= 70 ? 'warning' : 'bad');
        }
    }

    // Open issues
    if (summary.issues) {
        const el = document.getElementById('valOpenIssues');
        el.textContent = summary.issues.open;
        if (summary.issues.stale > 0) {
            document.getElementById('detailOpenIssues').textContent =
                `${summary.issues.stale} stale (30+ days inactive)`;
        }
    }
}

/**
 * Render alerts banner.
 */
function renderAlerts(alerts) {
    if (!alerts || alerts.length === 0) return;

    const section = document.getElementById('alertsSection');
    section.classList.remove('hidden');

    const list = document.getElementById('alertsList');
    // Sort: critical first, then warning, then info
    const order = { critical: 0, warning: 1, info: 2 };
    alerts.sort((a, b) => (order[a.severity] || 3) - (order[b.severity] || 3));

    for (const alert of alerts) {
        const item = document.createElement('div');
        item.className = `alert-item ${alert.severity}`;
        item.innerHTML = `
            <div class="alert-category">${alert.category}</div>
            <div class="alert-title">${alert.title}</div>
            <div class="alert-detail">${alert.detail}</div>
        `;
        list.appendChild(item);
    }
}

/**
 * Create a line chart with standard options.
 */
function createLineChart(canvasId, labels, datasets, yAxisLabel = '') {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    return new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                legend: {
                    display: datasets.length > 1,
                    position: 'top',
                    labels: { usePointStyle: true, boxWidth: 8 },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { maxTicksLimit: 10 },
                },
                y: {
                    title: {
                        display: !!yAxisLabel,
                        text: yAxisLabel,
                    },
                    beginAtZero: true,
                },
            },
        },
    });
}

/**
 * Create a bar chart with standard options.
 */
function createBarChart(canvasId, labels, datasets, yAxisLabel = '') {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    return new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    display: datasets.length > 1,
                    position: 'top',
                    labels: { usePointStyle: true, boxWidth: 8 },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { maxTicksLimit: 12 },
                },
                y: {
                    title: {
                        display: !!yAxisLabel,
                        text: yAxisLabel,
                    },
                    beginAtZero: true,
                },
            },
        },
    });
}

/**
 * Create a doughnut chart.
 */
function createDoughnutChart(canvasId, labels, data, colors) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: colors,
                borderWidth: 0,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'right',
                    labels: { usePointStyle: true, boxWidth: 8 },
                },
            },
        },
    });
}

/**
 * Render all Copilot usage charts.
 */
function renderCopilotCharts(charts) {
    const copilot = charts.copilot;
    if (!copilot || !copilot.dates || copilot.dates.length === 0) return;

    // Acceptance Rate
    createLineChart('chartAcceptanceRate', copilot.dates, [
        {
            label: 'Acceptance Rate (%)',
            data: copilot.acceptance_rate,
            borderColor: COLORS.green,
            backgroundColor: COLORS.greenFaded,
            fill: true,
            tension: 0.3,
        },
    ], '%');

    // Active & Engaged Users
    createLineChart('chartActiveUsers', copilot.dates, [
        {
            label: 'Active Users',
            data: copilot.active_users,
            borderColor: COLORS.blue,
            backgroundColor: COLORS.blueFaded,
            fill: true,
            tension: 0.3,
        },
        {
            label: 'Engaged Users',
            data: copilot.engaged_users,
            borderColor: COLORS.purple,
            backgroundColor: COLORS.purpleFaded,
            fill: true,
            tension: 0.3,
        },
    ], 'Users');

    // Suggestions & Acceptances
    createBarChart('chartSuggestions', copilot.dates, [
        {
            label: 'Suggestions',
            data: copilot.total_suggestions,
            backgroundColor: COLORS.blueFaded,
            borderColor: COLORS.blue,
            borderWidth: 1,
        },
        {
            label: 'Acceptances',
            data: copilot.total_acceptances,
            backgroundColor: COLORS.greenFaded,
            borderColor: COLORS.green,
            borderWidth: 1,
        },
    ], 'Count');

    // Chat Activity
    createLineChart('chartChat', copilot.dates, [
        {
            label: 'Chat Turns',
            data: copilot.chat_turns,
            borderColor: COLORS.cyan,
            backgroundColor: 'rgba(57, 210, 192, 0.2)',
            fill: true,
            tension: 0.3,
        },
    ], 'Turns');
}

/**
 * Render seat utilization and language charts.
 */
function renderSeatCharts(charts) {
    // Seat allocation over time
    const seats = charts.seats;
    if (seats && seats.dates && seats.dates.length > 0) {
        createLineChart('chartSeats', seats.dates, [
            {
                label: 'Total Seats',
                data: seats.total,
                borderColor: COLORS.blue,
                tension: 0.3,
            },
            {
                label: 'Active',
                data: seats.active,
                borderColor: COLORS.green,
                tension: 0.3,
            },
            {
                label: 'Inactive',
                data: seats.inactive,
                borderColor: COLORS.yellow,
                tension: 0.3,
            },
            {
                label: 'Never Used',
                data: seats.never_used,
                borderColor: COLORS.red,
                tension: 0.3,
            },
        ], 'Seats');
    }

    // Language breakdown
    const languages = charts.languages;
    if (languages && Object.keys(languages).length > 0) {
        // Sort by suggestions, take top 10
        const sorted = Object.entries(languages)
            .sort((a, b) => b[1].suggestions - a[1].suggestions)
            .slice(0, 10);

        const langColors = [
            COLORS.blue, COLORS.green, COLORS.yellow, COLORS.purple,
            COLORS.orange, COLORS.red, COLORS.cyan, COLORS.pink,
            '#7ee787', '#a5d6ff',
        ];

        createDoughnutChart(
            'chartLanguages',
            sorted.map(([name]) => name),
            sorted.map(([, stats]) => stats.suggestions),
            langColors,
        );
    }
}

/**
 * Render PR health charts.
 */
function renderPRCharts(charts) {
    const prs = charts.prs;
    if (!prs || !prs.dates || prs.dates.length === 0) return;

    // PR Lifespan
    createLineChart('chartPRLifespan', prs.dates, [
        {
            label: 'Median (hours)',
            data: prs.median_lifespan,
            borderColor: COLORS.blue,
            backgroundColor: COLORS.blueFaded,
            fill: true,
            tension: 0.3,
        },
        {
            label: 'P90 (hours)',
            data: prs.p90_lifespan,
            borderColor: COLORS.yellow,
            borderDash: [5, 5],
            tension: 0.3,
        },
    ], 'Hours');

    // Time to First Review
    createLineChart('chartTTFR', prs.dates, [
        {
            label: 'Median TTFR (hours)',
            data: prs.median_ttfr,
            borderColor: COLORS.green,
            backgroundColor: COLORS.greenFaded,
            fill: true,
            tension: 0.3,
        },
    ], 'Hours');

    // Merge Rate
    createLineChart('chartMergeRate', prs.dates, [
        {
            label: 'Merge Rate (%)',
            data: prs.merge_rate,
            borderColor: COLORS.purple,
            backgroundColor: COLORS.purpleFaded,
            fill: true,
            tension: 0.3,
        },
    ], '%');

    // PR Throughput
    const throughput = charts.pr_throughput;
    if (throughput && throughput.weeks && throughput.weeks.length > 0) {
        createBarChart('chartPRThroughput', throughput.weeks, [
            {
                label: 'PRs Merged',
                data: throughput.merged,
                backgroundColor: COLORS.greenFaded,
                borderColor: COLORS.green,
                borderWidth: 1,
            },
        ], 'PRs');
    }
}

/**
 * Render issue health charts.
 */
function renderIssueCharts(charts) {
    const issues = charts.issues;
    if (!issues || !issues.dates || issues.dates.length === 0) return;

    // Open & Stale Issues
    createLineChart('chartIssues', issues.dates, [
        {
            label: 'Open Issues',
            data: issues.open_issues,
            borderColor: COLORS.blue,
            backgroundColor: COLORS.blueFaded,
            fill: true,
            tension: 0.3,
        },
        {
            label: 'Stale Issues',
            data: issues.stale_issues,
            borderColor: COLORS.red,
            backgroundColor: COLORS.redFaded,
            fill: true,
            tension: 0.3,
        },
    ], 'Count');

    // Issue Lifespan
    createLineChart('chartIssueLifespan', issues.dates, [
        {
            label: 'Median Lifespan (hours)',
            data: issues.median_lifespan,
            borderColor: COLORS.orange,
            backgroundColor: COLORS.yellowFaded,
            fill: true,
            tension: 0.3,
        },
    ], 'Hours');

    // Issue Throughput
    const throughput = charts.issue_throughput;
    if (throughput && throughput.weeks && throughput.weeks.length > 0) {
        createBarChart('chartIssueThroughput', throughput.weeks, [
            {
                label: 'Opened',
                data: throughput.opened,
                backgroundColor: COLORS.redFaded,
                borderColor: COLORS.red,
                borderWidth: 1,
            },
            {
                label: 'Closed',
                data: throughput.closed,
                backgroundColor: COLORS.greenFaded,
                borderColor: COLORS.green,
                borderWidth: 1,
            },
        ], 'Issues');
    }
}

/**
 * Main entry point.
 */
async function init() {
    const data = await loadDashboardData();
    if (!data) return;

    renderTimestamp(data.summary);
    renderSummaryCards(data.summary);
    renderAlerts(data.alerts);
    renderCopilotCharts(data.charts);
    renderSeatCharts(data.charts);
    renderPRCharts(data.charts);
    renderIssueCharts(data.charts);
}

// Boot
init();
