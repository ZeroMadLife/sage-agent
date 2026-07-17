"""HTML report generator for benchmark results.

Produces a self-contained HTML document (no external CSS/JS dependencies) with
metric cards, per-category bar charts, and a scenario detail table.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def generate_html_report(report_data: dict[str, Any], output_path: Path) -> None:
    """Generate a self-contained HTML report with tables and CSS bar charts."""
    metrics = report_data.get("metrics", {})
    results = report_data.get("results", [])

    # Metrics cards
    metric_cards: list[str] = []
    for key, value in metrics.items():
        if "rate" in key:
            pct = value * 100 if value <= 1 else value
            color = "#10b981" if pct >= 80 else "#f59e0b" if pct >= 60 else "#ef4444"
            metric_cards.append(
                f"""
                <div class="metric-card" style="border-left: 4px solid {color};">
                    <div class="metric-value" style="color: {color};">{pct:.1f}%</div>
                    <div class="metric-label">{html.escape(key.replace("_", " ").title())}</div>
                </div>"""
            )
        else:
            metric_cards.append(
                f"""
                <div class="metric-card">
                    <div class="metric-value">{value}ms</div>
                    <div class="metric-label">{html.escape(key.replace("_", " ").title())}</div>
                </div>"""
            )

    # Scenario table rows
    table_rows: list[str] = []
    for r in results:
        status_class = "pass" if r["passed"] else "fail"
        status_icon = "&#x2705;" if r["passed"] else "&#x274C;"
        table_rows.append(
            f"""
            <tr class="{status_class}">
                <td>{status_icon}</td>
                <td>{html.escape(str(r["name"]))}</td>
                <td>{html.escape(str(r["category"]))}</td>
                <td>{r["tool_calls"]}</td>
                <td>{r["duration_ms"]}ms</td>
                <td>{html.escape(str(r.get("detail", "")) or "ok")}</td>
            </tr>"""
        )

    # Category summary
    categories: dict[str, dict[str, int]] = {}
    for r in results:
        cat = str(r["category"])
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0}
        categories[cat]["total"] += 1
        if r["passed"]:
            categories[cat]["passed"] += 1

    category_bars: list[str] = []
    for cat, stats in sorted(categories.items()):
        pct = (stats["passed"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        color = "#10b981" if pct >= 80 else "#f59e0b" if pct >= 60 else "#ef4444"
        category_bars.append(
            f"""
            <div class="category-bar">
                <div class="category-label">{html.escape(cat)}</div>
                <div class="bar-container">
                    <div class="bar-fill" style="width: {pct:.1f}%; background: {color};">{stats["passed"]}/{stats["total"]}</div>
                </div>
            </div>"""
        )

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sage V6 Benchmark Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f9fafb; color: #111827; }}
        h1 {{ font-size: 24px; margin-bottom: 8px; }}
        .timestamp {{ color: #6b7280; font-size: 13px; margin-bottom: 24px; }}
        .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 32px; }}
        .metric-card {{ background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .metric-value {{ font-size: 28px; font-weight: 800; }}
        .metric-label {{ font-size: 12px; color: #6b7280; margin-top: 4px; }}
        .section-title {{ font-size: 18px; font-weight: 700; margin: 24px 0 12px; }}
        .category-bars {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 24px; }}
        .category-bar {{ display: flex; align-items: center; gap: 12px; }}
        .category-label {{ width: 180px; font-size: 13px; font-weight: 600; }}
        .bar-container {{ flex: 1; background: #e5e7eb; border-radius: 4px; height: 24px; overflow: hidden; }}
        .bar-fill {{ height: 100%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; color: #fff; border-radius: 4px; transition: width 0.3s; }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th {{ background: #f3f4f6; padding: 10px 12px; text-align: left; font-size: 12px; font-weight: 700; color: #374151; text-transform: uppercase; }}
        td {{ padding: 10px 12px; font-size: 13px; border-top: 1px solid #e5e7eb; }}
        tr.pass {{ background: #f0fdf4; }}
        tr.fail {{ background: #fef2f2; }}
    </style>
</head>
<body>
    <h1>Sage V6 Benchmark Report</h1>
    <div class="timestamp">Generated: {html.escape(str(report_data.get("timestamp", "N/A")))}</div>

    <div class="section-title">Core Metrics</div>
    <div class="metrics-grid">
        {''.join(metric_cards)}
    </div>

    <div class="section-title">Category Results</div>
    <div class="category-bars">
        {''.join(category_bars)}
    </div>

    <div class="section-title">Scenario Details</div>
    <table>
        <thead>
            <tr><th>Status</th><th>Scenario</th><th>Category</th><th>Tool Calls</th><th>Duration</th><th>Detail</th></tr>
        </thead>
        <tbody>
            {''.join(table_rows)}
        </tbody>
    </table>
</body>
</html>"""

    output_path.write_text(html_content, encoding="utf-8")
