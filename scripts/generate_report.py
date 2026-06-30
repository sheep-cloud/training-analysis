#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Training analysis report generator
Merges data from Xunji App (xunji-api) + Coros App (Coros)
"""

import json
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# Force UTF-8 encoding on Windows
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "daily"
PUBLIC_DIR = PROJECT_ROOT / "public" / "daily"


def fetch_xunji_data(date_str):
    """
    Get data from Xunji App (via xunji-api skill)
    Returns complete training data
    """
    try:
        cmd = [
            "python",
            "./.claude/skills/xunji-api/scripts/fetch_trains.py",
            "read",
            "--date", date_str,
            "--full"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)

        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("res", {})
        else:
            print(f"Error fetching Xunji data: {result.stderr}")
            return None
    except Exception as e:
        print(f"Xunji API error: {e}")
        return None


def fetch_coros_data(date_str):
    """
    Get data from Coros App (via Coros MCP)
    Returns activities and physiological metrics
    """
    try:
        activities_data = {
            "activities": [],
            "daily_metrics": {},
            "sleep_data": {}
        }

        print("[INFO] Coros data integration pending (using mock data)")
        return activities_data

    except Exception as e:
        print(f"Coros API error: {e}")
        return None


def merge_training_data(xunji_data, coros_data, date_str):
    """
    Merge data from Xunji and Coros
    - Xunji provides accurate strength training data
    - Coros provides cardio activities and physiological metrics
    """

    merged = {
        "date": date_str,
        "strength_training": [],
        "cardio_activities": [],
        "daily_metrics": {},
        "sleep_data": {},
        "summary": {
            "total_load": 0,
            "total_calories": 0,
            "total_duration": 0,
            "workout_count": 0
        }
    }

    # Process Xunji data - strength training
    if xunji_data and "trains" in xunji_data:
        for train in xunji_data["trains"]:
            train_info = {
                "title": train.get("title", "Unknown"),
                "start_time": train.get("start", 0),
                "end_time": train.get("end", 0),
                "duration_minutes": (train.get("end", 0) - train.get("start", 0)) // 60000,
                "movements": []
            }

            for movement in train.get("movements", []):
                move_info = {
                    "name": movement.get("name", "Unknown"),
                    "sets": []
                }

                for set_data in movement.get("sets", []):
                    set_info = {
                        "done": set_data.get("done", True),
                        "weight": set_data.get("weight"),
                        "unit": set_data.get("unit", "kg"),
                        "reps": set_data.get("reps"),
                        "time": set_data.get("time"),
                        "rpe": set_data.get("rpe", ""),
                        "note": set_data.get("note", "")
                    }
                    move_info["sets"].append(set_info)

                train_info["movements"].append(move_info)

            merged["strength_training"].append(train_info)
            merged["summary"]["workout_count"] += 1

    # Process Coros data - cardio activities
    if coros_data:
        if "activities" in coros_data:
            for activity in coros_data["activities"]:
                activity_info = {
                    "name": activity.get("name", "Unknown"),
                    "sport_type": activity.get("sport_type"),
                    "duration_seconds": activity.get("duration_seconds", 0),
                    "distance_meters": activity.get("distance_meters", 0),
                    "avg_hr": activity.get("avg_hr", 0),
                    "calories": activity.get("calories", 0),
                    "training_load": activity.get("training_load", 0)
                }
                merged["cardio_activities"].append(activity_info)

                merged["summary"]["total_duration"] += activity.get("duration_seconds", 0)
                merged["summary"]["total_calories"] += activity.get("calories", 0)
                merged["summary"]["total_load"] += activity.get("training_load", 0)

        if "daily_metrics" in coros_data:
            merged["daily_metrics"] = coros_data["daily_metrics"]

        if "sleep_data" in coros_data:
            merged["sleep_data"] = coros_data["sleep_data"]

    merged["summary"]["total_duration"] = merged["summary"]["total_duration"] // 60

    return merged


def save_json_data(merged_data, date_str):
    """Save merged data as JSON"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    json_file = DATA_DIR / f"{date_str}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, indent=2, ensure_ascii=False)

    print(f"[OK] JSON data saved: {json_file}")
    return json_file


def generate_html_report(merged_data, date_str):
    """Generate HTML report"""

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Training Analysis - {date_str}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
        }}

        .content {{
            padding: 40px;
        }}

        .section {{
            margin-bottom: 40px;
        }}

        .section h2 {{
            font-size: 1.8em;
            color: #333;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #667eea;
        }}

        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}

        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        }}

        .stat-card h3 {{
            font-size: 0.9em;
            opacity: 0.9;
            margin-bottom: 10px;
        }}

        .stat-card .value {{
            font-size: 2em;
            font-weight: 700;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
            background: #f9f9f9;
        }}

        th {{
            background: #667eea;
            color: white;
            padding: 15px;
            text-align: left;
        }}

        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #eee;
        }}

        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            background: #d4edda;
            color: #155724;
        }}

        .footer {{
            background: #f9f9f9;
            padding: 20px 40px;
            text-align: center;
            color: #666;
            border-top: 1px solid #eee;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Training Analysis Report</h1>
            <p>{date_str}</p>
        </div>

        <div class="content">
            <!-- Summary -->
            <div class="section">
                <h2>Daily Summary</h2>
                <div class="stat-grid">
                    <div class="stat-card">
                        <h3>Workouts</h3>
                        <div class="value">{merged_data['summary']['workout_count']}</div>
                    </div>
                    <div class="stat-card">
                        <h3>Total Duration</h3>
                        <div class="value">{merged_data['summary']['total_duration']}</div>
                        <div>minutes</div>
                    </div>
                    <div class="stat-card">
                        <h3>Calories Burned</h3>
                        <div class="value">{merged_data['summary']['total_calories']:.0f}</div>
                        <div>kcal</div>
                    </div>
                    <div class="stat-card">
                        <h3>Training Load</h3>
                        <div class="value">{merged_data['summary']['total_load']}</div>
                    </div>
                </div>
            </div>

            <!-- Strength Training -->
            {generate_strength_html(merged_data)}

            <!-- Cardio Activities -->
            {generate_cardio_html(merged_data)}
        </div>

        <div class="footer">
            <p>Data source: Xunji App + Coros App</p>
            <p style="margin-top: 10px; font-size: 0.9em;">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
</body>
</html>
"""

    return html_content


def generate_strength_html(merged_data):
    """Generate strength training HTML"""
    if not merged_data["strength_training"]:
        return ""

    html = '<div class="section"><h2>Strength Training</h2>'

    for train in merged_data["strength_training"]:
        html += f"""
        <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="color: #333; margin-bottom: 15px;">{train['title']}</h3>
            <p style="color: #666; margin-bottom: 10px;">Duration: {train['duration_minutes']} minutes</p>

            <table>
                <thead>
                    <tr>
                        <th>Exercise</th>
                        <th>Set</th>
                        <th>Weight</th>
                        <th>Reps</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
        """

        for movement in train["movements"]:
            for i, set_data in enumerate(movement["sets"], 1):
                status = "Done" if set_data["done"] else "Incomplete"
                weight_display = f"{set_data['weight']} {set_data['unit']}" if set_data['weight'] else "-"
                reps_display = f"{set_data['reps']}x" if set_data['reps'] else "-"

                html += f"""
                    <tr>
                        <td>{movement['name']}</td>
                        <td>#{i}</td>
                        <td>{weight_display}</td>
                        <td>{reps_display}</td>
                        <td><span class="badge">{status}</span></td>
                    </tr>
                """

        html += """
                </tbody>
            </table>
        </div>
        """

    html += "</div>"
    return html


def generate_cardio_html(merged_data):
    """Generate cardio activities HTML"""
    if not merged_data["cardio_activities"]:
        return ""

    html = '<div class="section"><h2>Cardio Activities</h2>'

    for activity in merged_data["cardio_activities"]:
        distance_km = activity["distance_meters"] / 1000
        duration_min = activity["duration_seconds"] / 60

        html += f"""
        <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="color: #333; margin-bottom: 10px;">{activity['name']}</h3>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px;">
                <div>
                    <p style="color: #999; font-size: 0.9em;">Distance</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{distance_km:.2f} km</p>
                </div>
                <div>
                    <p style="color: #999; font-size: 0.9em;">Duration</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{duration_min:.0f} min</p>
                </div>
                <div>
                    <p style="color: #999; font-size: 0.9em;">Avg HR</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{activity['avg_hr']} bpm</p>
                </div>
                <div>
                    <p style="color: #999; font-size: 0.9em;">Calories</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{activity['calories']:.0f} kcal</p>
                </div>
            </div>
        </div>
        """

    html += "</div>"
    return html


def save_html_report(html_content, date_str):
    """Save HTML report"""
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    html_file = PUBLIC_DIR / f"{date_str}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"[OK] HTML report generated: {html_file}")
    return html_file


def update_summary(date_str):
    """Update summary.json for index page"""
    summary_file = DATA_DIR / "summary.json"

    summary_data = {"dates": []}

    if DATA_DIR.exists():
        for json_file in sorted(DATA_DIR.glob("*.json")):
            if json_file.name != "summary.json":
                date = json_file.stem
                if date not in summary_data["dates"]:
                    summary_data["dates"].append(date)

    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Summary updated: {summary_file}")


def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description="Generate training analysis report")
    parser.add_argument("--date", type=str, help="Target date (format: YYYY-MM-DD)")
    args = parser.parse_args()

    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    print(f"\n[START] Generating training report for {date_str}...\n")

    # Fetch data
    print("[INFO] Fetching Xunji data...")
    xunji_data = fetch_xunji_data(date_str)

    print("[INFO] Fetching Coros data...")
    coros_data = fetch_coros_data(date_str)

    # Merge data
    print("[INFO] Merging data...")
    merged_data = merge_training_data(xunji_data, coros_data, date_str)

    # Save JSON
    save_json_data(merged_data, date_str)

    # Generate HTML
    print("[INFO] Generating HTML report...")
    html_content = generate_html_report(merged_data, date_str)
    save_html_report(html_content, date_str)

    # Update summary
    update_summary(date_str)

    print(f"\n[DONE] Report generation completed!\n")
    print(f"JSON: data/daily/{date_str}.json")
    print(f"HTML: public/daily/{date_str}.html")


if __name__ == "__main__":
    main()
