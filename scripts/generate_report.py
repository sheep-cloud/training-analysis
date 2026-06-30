#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Training analysis report generator
Merges data from Xunji App (xunji-api) + Coros App (Coros)
"""

import json
import os
import sys
import sqlite3
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

# Coros MCP local SQLite cache (filled by `coros-mcp sync`)
COROS_CACHE_DB = Path.home() / ".config" / "coros-mcp" / "cache.db"

# Coros sport_type values that are strength training (overlap with Xunji data)
STRENGTH_SPORT_TYPES = {402}

# Coros sport_type values that count as a "workout" for the daily summary card
# (strength is counted via Xunji; this set tracks cardio/ball/other sports from Coros)
CARDIO_SPORT_TYPES = {
    1,    # 跑步（跑步机）
    2,    # 室内骑行
    100,  # 跑步
    101,  # 跑步（alt）
    102,  # 越野跑
    103,  # 场地跑
    200,  # 公路骑行
    201,  # 室内骑行（alt）
    300,  # 游泳
    401,  # 飞盘
    403,  # 乒乓球
    404,  # 骑行（通用）
}


# ── tiny helpers shared across the pipeline ──

def _num(value, default=0):
    """None-safe numeric accessor; returns *value* if it is a number, else *default*."""
    return value if isinstance(value, (int, float)) else default


def _esc(value):
    """HTML-escape a string value so it can be safely embedded in markup."""
    import html
    if value is None:
        return ""
    return html.escape(str(value))


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
        # encoding="utf-8" is required: the skill script outputs UTF-8, but on
        # Windows subprocess defaults to GBK and crashes on Chinese bytes,
        # silently turning stdout into None and dropping all strength data.
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", cwd=PROJECT_ROOT
        )

        if result.returncode != 0:
            print(f"[WARN] Xunji fetch failed (exit {result.returncode}): {result.stderr.strip()[:300]}")
            return None

        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[WARN] Xunji output not valid JSON: {e}")
            print(f"       stdout head: {(result.stdout or '')[:200]!r}")
            print(f"       stderr head: {(result.stderr or '')[:200]!r}")
            return None

        return data.get("res", {})
    except Exception as e:
        print(f"[WARN] Xunji API error: {e}")
        return None


def fetch_coros_data(date_str):
    """
    Get data from Coros App by reading the coros-mcp local SQLite cache
    (~/.config/coros-mcp/cache.db), populated by `coros-mcp sync`.

    Returns {"activities": [...], "daily_metrics": {...}, "sleep_data": {...}}.
    Degrades gracefully to empty structures if the cache is missing/empty.
    """
    empty = {"activities": [], "daily_metrics": {}, "sleep_data": {}}

    if not COROS_CACHE_DB.exists():
        print(f"[WARN] Coros cache not found: {COROS_CACHE_DB}")
        print("       Run `coros-mcp sync` first to populate it.")
        return empty

    # merge keys on YYYYMMDD; date_str arrives as YYYY-MM-DD
    day = date_str.replace("-", "")

    try:
        con = sqlite3.connect(COROS_CACHE_DB)
        con.row_factory = sqlite3.Row

        activities = []
        for row in con.execute(
            "SELECT data FROM activities WHERE start_day = ? ORDER BY start_day", (day,)
        ):
            act = json.loads(row["data"])
            # cache stores start/end as raw UTC Unix-seconds strings; normalize
            # to int epoch seconds so we can window-match Xunji strength
            # sessions (also epoch seconds) by absolute-time overlap.
            st = act.get("start_time")
            et = act.get("end_time")
            act["start_ts"] = _coros_ts_to_epoch(st)
            act["end_ts"] = _coros_ts_to_epoch(et)
            activities.append(act)

        daily_metrics = {}
        drow = con.execute(
            "SELECT data FROM daily_records WHERE date = ?", (day,)
        ).fetchone()
        if drow:
            daily_metrics = json.loads(drow["data"])

        sleep_data = {}
        srow = con.execute(
            "SELECT data FROM sleep_records WHERE date = ?", (day,)
        ).fetchone()
        if srow:
            sleep_data = json.loads(srow["data"])

        con.close()

        if not activities and not daily_metrics and not sleep_data:
            print(f"[WARN] Coros cache has no data for {date_str}. Run `coros-mcp sync`.")

        return {
            "activities": activities,
            "daily_metrics": daily_metrics,
            "sleep_data": sleep_data,
        }

    except Exception as e:
        print(f"[WARN] Coros cache read error: {e}")
        return empty

def _coros_ts_to_epoch(value):
    """Coros cache start_time/end_time is a UTC Unix seconds (or ms) string.
    Return epoch seconds (int) or None."""
    if not value:
        return None
    s = str(value)
    if s.isdigit():
        if len(s) == 13:  # milliseconds
            return int(s) // 1000
        if len(s) == 10:  # seconds
            return int(s)
    return None


def _intervals_overlap(a_start, a_end, b_start, b_end):
    """True if two [start, end] epoch-second intervals overlap. None-safe."""
    if None in (a_start, a_end, b_start, b_end):
        return False
    return a_start <= b_end and b_start <= a_end


def merge_training_data(xunji_data, coros_data, date_str):
    """
    Merge data from Xunji and Coros.

    Rules (confirmed with user):
    - Xunji is the source of truth for strength training DETAILS (movements/sets).
    - Coros and Xunji both log the same strength session; match them by time
      window overlap and attach Coros calories/training_load onto the Xunji row.
    - Coros strength activities with no matching Xunji session are kept as a
      detail-less strength_training row (calories/load only).
    - Calories and training_load always come from Coros (calories in cal -> /1000).
    - Non-strength Coros activities (running, etc.) go to cardio_activities.
    - daily_metrics / sleep_data are attached as-is.
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

    # Split Coros activities into strength (for dedup) vs cardio
    coros_strength = []
    coros_cardio = []
    if coros_data and coros_data.get("activities"):
        for activity in coros_data["activities"]:
            if activity.get("sport_type") in STRENGTH_SPORT_TYPES:
                coros_strength.append(activity)
            else:
                coros_cardio.append(activity)

    # --- Xunji strength training (source of truth for details) ---
    if xunji_data and "trains" in xunji_data:
        for train in xunji_data["trains"]:
            t_start = train.get("start", 0)
            t_end = train.get("end", 0)
            train_info = {
                "title": train.get("title", "Unknown"),
                "start_time": t_start,
                "end_time": t_end,
                "duration_minutes": (t_end - t_start) // 60000,
                "calories": None,        # filled from matched Coros activity
                "training_load": None,   # filled from matched Coros activity
                "movements": []
            }

            for movement in train.get("movements", []):
                move_info = {
                    "name": movement.get("name", "Unknown"),
                    "sets": []
                }
                for set_data in movement.get("sets", []):
                    move_info["sets"].append({
                        "done": set_data.get("done", True),
                        "weight": set_data.get("weight"),
                        "unit": set_data.get("unit", "kg"),
                        "reps": set_data.get("reps"),
                        "time": set_data.get("time"),
                        "rpe": set_data.get("rpe", ""),
                        "note": set_data.get("note", ""),
                        "set_type": set_data.get("setType", ""),
                        "rest_seconds": set_data.get("restSeconds"),
                        "left_weight": set_data.get("leftWeight"),
                    })
                train_info["movements"].append(move_info)

            # Match a Coros strength activity by time-window overlap (Xunji ms -> s)
            t_start_s = t_start // 1000 if t_start else None
            t_end_s = t_end // 1000 if t_end else None
            for ca in list(coros_strength):
                if _intervals_overlap(t_start_s, t_end_s, ca.get("start_ts"), ca.get("end_ts")):
                    train_info["calories"] = round(_num(ca.get("calories")) / 1000, 1)
                    train_info["training_load"] = _num(ca.get("training_load"))
                    coros_strength.remove(ca)  # consumed; won't become an orphan
                    break

            merged["strength_training"].append(train_info)
            merged["summary"]["workout_count"] += 1

    # --- Orphan Coros strength activities (no Xunji detail) ---
    for ca in coros_strength:
        merged["strength_training"].append({
            "title": ca.get("name", "Strength"),
            "start_time": (ca.get("start_ts") or 0) * 1000,
            "end_time": (ca.get("end_ts") or 0) * 1000,
            "duration_minutes": (ca.get("duration_seconds", 0) // 60),
            "calories": round(_num(ca.get("calories")) / 1000, 1),
            "training_load": _num(ca.get("training_load")),
            "movements": []  # no detail available from Coros
        })
        merged["summary"]["workout_count"] += 1

    # --- Coros cardio activities ---
    for activity in coros_cardio:
        merged["cardio_activities"].append({
            "name": activity.get("name", "Unknown"),
            "sport_type": activity.get("sport_type"),
            "duration_seconds": activity.get("duration_seconds", 0),
            "distance_meters": activity.get("distance_meters", 0),
            "avg_hr": activity.get("avg_hr"),    # may be None — renderer handles it
            "calories": round(_num(activity.get("calories")) / 1000, 1),
            "training_load": _num(activity.get("training_load"))
        })
        merged["summary"]["total_duration"] += activity.get("duration_seconds", 0)
        merged["summary"]["workout_count"] += 1  # cardio sports count as workouts

    # --- Summary: calories & load always from Coros (every activity) ---
    # total_calories / total_load across every Coros activity (strength + cardio)
    all_coros = (coros_data.get("activities", []) if coros_data else [])
    for a in all_coros:
        merged["summary"]["total_calories"] += _num(a.get("calories")) / 1000
        merged["summary"]["total_load"] += _num(a.get("training_load"))
    merged["summary"]["total_calories"] = round(merged["summary"]["total_calories"], 1)

    # daily metrics / sleep
    if coros_data:
        merged["daily_metrics"] = coros_data.get("daily_metrics", {})
        merged["sleep_data"] = coros_data.get("sleep_data", {})

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
    <title>训练分析报告 - {date_str}</title>
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

        .badge-success {{ background: #d4edda; color: #155724; }}
        .badge-warning {{ background: #fff3cd; color: #856404; }}
        .badge-danger  {{ background: #f8d7da; color: #721c24; }}

        .alert {{
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 15px;
            border-left: 4px solid;
        }}

        .alert-info    {{ background: #e7f3ff; border-color: #0066cc; color: #004085; }}
        .alert-warning {{ background: #fffbea; border-color: #ffc107; color: #856404; }}
        .alert-danger  {{ background: #ffe5e5; border-color: #dc3545; color: #721c24; }}

        .recommendation {{
            background: #f0f4ff;
            border-left: 4px solid #667eea;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 15px;
        }}

        .recommendation h4 {{
            color: #667eea;
            margin-bottom: 8px;
        }}

        .recommendation p {{
            color: #555;
            line-height: 1.6;
        }}

        .score-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}

        .score-circle {{
            width: 100px;
            height: 100px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.8em;
            font-weight: 700;
            color: white;
            margin: 0 auto 10px;
        }}

        .score-good {{ background: linear-gradient(135deg, #84fab0 0%, #8fd3f4 100%); }}
        .score-fair {{ background: linear-gradient(135deg, #fa709a 0%, #fee140 100%); }}
        .score-poor {{ background: linear-gradient(135deg, #ff6a00 0%, #ee0979 100%); }}

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
            <h1>训练分析报告</h1>
            <p>{date_str}</p>
        </div>

        <div class="content">
            <!-- 综合分析(若有) -->
            {generate_analysis_html(merged_data, date_str)}

            <!-- 当日概览 -->
            <div class="section">
                <h2>当日概览</h2>
                <div class="stat-grid">
                    <div class="stat-card">
                        <h3>训练场次</h3>
                        <div class="value">{merged_data['summary']['workout_count']}</div>
                    </div>
                    <div class="stat-card">
                        <h3>有氧时长</h3>
                        <div class="value">{merged_data['summary']['total_duration']}</div>
                        <div>分钟</div>
                    </div>
                    <div class="stat-card">
                        <h3>消耗热量</h3>
                        <div class="value">{merged_data['summary']['total_calories']:.0f}</div>
                        <div>千卡</div>
                    </div>
                    <div class="stat-card">
                        <h3>训练负荷</h3>
                        <div class="value">{merged_data['summary']['total_load']}</div>
                    </div>
                </div>
            </div>

            <!-- 力量训练 -->
            {generate_strength_html(merged_data)}

            <!-- 有氧运动 -->
            {generate_cardio_html(merged_data)}

            <!-- 生理指标 -->
            {generate_metrics_html(merged_data)}

            <!-- 睡眠 -->
            {generate_sleep_html(merged_data)}
        </div>

        <div class="footer">
            <p>数据来源:训记 App + 高驰 App</p>
            <p style="margin-top: 10px; font-size: 0.9em;">生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
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

    html = '<div class="section"><h2>力量训练</h2>'

    for train in merged_data["strength_training"]:
        # Coros-sourced calories/load, attached during merge (may be None)
        extra = []
        if train.get("calories") is not None:
            extra.append(f"{train['calories']:.0f} 千卡")
        if train.get("training_load") is not None:
            extra.append(f"负荷 {train['training_load']}")
        extra_str = (" &nbsp;·&nbsp; " + " &nbsp;·&nbsp; ".join(extra)) if extra else ""

        # Orphan Coros strength rows have no movement detail
        if not train["movements"]:
            html += f"""
        <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="color: #333; margin-bottom: 10px;">{_esc(train['title'])}</h3>
            <p style="color: #666;">时长:{train['duration_minutes']} 分钟{extra_str}</p>
            <p style="color: #999; font-size: 0.9em; margin-top: 8px;">无动作明细(仅高驰记录)</p>
        </div>
            """
            continue

        html += f"""
        <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="color: #333; margin-bottom: 15px;">{_esc(train['title'])}</h3>
            <p style="color: #666; margin-bottom: 10px;">时长:{train['duration_minutes']} 分钟{extra_str}</p>

            <table>
                <thead>
                    <tr>
                        <th>动作</th>
                        <th>组</th>
                        <th>类型</th>
                        <th>重量</th>
                        <th>次数</th>
                        <th>左侧</th>
                        <th>休息</th>
                        <th>完成</th>
                    </tr>
                </thead>
                <tbody>
        """

        for movement in train["movements"]:
            for i, set_data in enumerate(movement["sets"], 1):
                # set_type: 组类型(热=热身,空=正式组等)
                set_type = set_data.get("set_type", "")
                if set_type == "热":
                    type_text = "热身"
                    type_cls = "badge-warning"
                elif set_type:
                    type_text = _esc(set_type)
                    type_cls = "badge-success"
                else:
                    type_text = "正式"
                    type_cls = "badge-success"

                # weight display: 当有左侧重量且与重量不同时注明
                weight = set_data.get("weight")
                left_w = set_data.get("left_weight")
                if weight and left_w and str(left_w) != str(weight):
                    weight_display = f"{weight} <small style='color:#888;'>(左{left_w})</small>"
                elif weight:
                    weight_display = f"{weight}"
                else:
                    weight_display = "-"
                weight_display += f" {set_data.get('unit','kg')}"

                # rest seconds
                rest = set_data.get("rest_seconds")
                rest_display = f"{rest} 秒" if rest else "-"

                # reps
                reps_display = f"{set_data['reps']} 次" if set_data.get("reps") else "-"

                # done status
                done = set_data.get("done", True)
                status_text = "完成" if done else "未完成"
                status_cls = "badge-success" if done else "badge-warning"

                html += f"""
                    <tr>
                        <td>{_esc(movement['name'])}</td>
                        <td>第 {i} 组</td>
                        <td><span class="badge {type_cls}">{type_text}</span></td>
                        <td>{weight_display}</td>
                        <td>{reps_display}</td>
                        <td>{left_w if left_w else '-'}</td>
                        <td>{rest_display}</td>
                        <td><span class="badge {status_cls}">{status_text}</span></td>
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

    html = '<div class="section"><h2>有氧运动</h2>'

    for activity in merged_data["cardio_activities"]:
        distance_km = activity["distance_meters"] / 1000
        duration_min = activity["duration_seconds"] / 60
        avg_hr = activity.get("avg_hr")
        hr_display = f"{avg_hr} bpm" if avg_hr else "-"

        html += f"""
        <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="color: #333; margin-bottom: 10px;">{_esc(activity['name'])}</h3>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px;">
                <div>
                    <p style="color: #999; font-size: 0.9em;">距离</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{distance_km:.2f} km</p>
                </div>
                <div>
                    <p style="color: #999; font-size: 0.9em;">时长</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{duration_min:.0f} 分钟</p>
                </div>
                <div>
                    <p style="color: #999; font-size: 0.9em;">平均心率</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{hr_display}</p>
                </div>
                <div>
                    <p style="color: #999; font-size: 0.9em;">消耗热量</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{activity['calories']:.0f} 千卡</p>
                </div>
            </div>
        </div>
        """

    html += "</div>"
    return html


def generate_metrics_html(merged_data):
    """Generate physiological daily-metrics HTML (HRV / RHR / load / fitness)."""
    m = merged_data.get("daily_metrics") or {}
    if not m:
        return ""

    # (label, value, unit) — skip cards whose value is missing
    cards = [
        ("HRV(心率变异性)", m.get("avg_sleep_hrv"), "ms"),
        ("HRV 基线", m.get("baseline"), "ms"),
        ("静息心率", m.get("rhr"), "bpm"),
        ("训练负荷", m.get("training_load"), ""),
        ("负荷比(急/慢)", m.get("training_load_ratio"), ""),
        ("最大摄氧量", m.get("vo2max"), ""),
        ("体能水平", m.get("stamina_level"), ""),
    ]

    html = '<div class="section"><h2>生理指标</h2><div class="stat-grid">'
    rendered = 0
    for label, value, unit in cards:
        if value is None:
            continue
        unit_html = f'<div>{unit}</div>' if unit else ""
        html += f"""
                    <div class="stat-card">
                        <h3>{label}</h3>
                        <div class="value">{value}</div>
                        {unit_html}
                    </div>"""
        rendered += 1

    html += "</div></div>"
    return html if rendered else ""


def generate_sleep_html(merged_data):
    """Generate sleep stage breakdown HTML."""
    s = merged_data.get("sleep_data") or {}
    if not s:
        return ""

    total = s.get("total_duration_minutes")
    phases = s.get("phases") or {}

    def fmt(minutes):
        if minutes is None:
            return "-"
        return f"{minutes // 60} 小时 {minutes % 60} 分" if minutes >= 60 else f"{minutes} 分"

    total_html = fmt(total)
    stage_defs = [
        ("深睡", phases.get("deep_minutes")),
        ("浅睡", phases.get("light_minutes")),
        ("REM", phases.get("rem_minutes")),
        ("清醒", phases.get("awake_minutes")),
    ]
    stages_html = ""
    for name, minutes in stage_defs:
        if minutes is None:
            continue
        stages_html += f"""
                <div>
                    <p style="color: #999; font-size: 0.9em;">{name}</p>
                    <p style="font-size: 1.5em; font-weight: 700;">{minutes} 分</p>
                </div>"""

    avg_hr = s.get("avg_hr")
    hr_html = ""
    if avg_hr is not None:
        hr_html = f'<p style="color: #666; margin-top: 12px;">睡眠平均心率:{avg_hr} bpm</p>'

    return f"""<div class="section"><h2>睡眠</h2>
        <div style="background: #f9f9f9; padding: 20px; border-radius: 8px;">
            <h3 style="color: #333; margin-bottom: 15px;">总时长:{total_html}</h3>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px;">{stages_html}
            </div>
            {hr_html}
        </div>
    </div>"""


def load_analysis(date_str):
    """Load optional human/LLM-authored analysis for a date.

    Returns the parsed dict from data/daily/{date}.analysis.json, or None if
    the file is absent. This file is NOT produced by the automated pipeline —
    it is authored on demand (see project decision: analysis is generated in
    a Claude session, not by an API call inside this script).
    """
    analysis_file = DATA_DIR / f"{date_str}.analysis.json"
    if not analysis_file.exists():
        return None
    try:
        with open(analysis_file, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] Analysis file unreadable: {e}")
        return None


def _score_class(value):
    """Map a 0-10 score to a circle color class."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "score-fair"
    if v >= 8:
        return "score-good"
    if v >= 6:
        return "score-fair"
    return "score-poor"


def _rating_badge(rating):
    """Map a rating keyword to a badge class. Accepts good/warn/bad (zh aliases too)."""
    mapping = {
        "good": "badge-success", "好": "badge-success", "优": "badge-success",
        "warn": "badge-warning", "warning": "badge-warning", "中": "badge-warning", "注意": "badge-warning",
        "bad": "badge-danger", "danger": "badge-danger", "差": "badge-danger", "警告": "badge-danger",
    }
    return mapping.get(str(rating).lower(), "badge-success")


def generate_analysis_html(merged_data, date_str):
    """Render the on-demand analysis block from {date}.analysis.json, if present."""
    a = load_analysis(date_str)
    if not a:
        return ""

    parts = ['<div class="section"><h2>综合分析</h2>']

    # 1. Score circles
    scores = a.get("scores") or []
    if a.get("overall_score") is not None:
        scores = list(scores) + [{"label": "综合评分", "value": a["overall_score"]}]
    if scores:
        parts.append('<div class="score-grid">')
        for s in scores:
            cls = _score_class(s.get("value"))
            parts.append(f"""
                <div style="text-align:center;">
                    <div class="score-circle {cls}">{s.get('value')}</div>
                    <p style="color:#333; font-weight:600;">{s.get('label','')}</p>
                </div>""")
        parts.append('</div>')

    # 2. Core diagnosis
    if a.get("summary"):
        parts.append(f"""
        <div class="alert alert-info">
            <strong>核心诊断</strong>
            <p style="margin-top:8px;">{_esc(a['summary'])}</p>
        </div>""")

    # 3. Dimension tables (recovery / load / strength / sleep)
    for dim in a.get("dimensions") or []:
        parts.append(f'<h3 style="color:#333; margin:20px 0 12px;">{_esc(dim.get("title",""))}</h3>')
        if dim.get("verdict"):
            parts.append(f'<p style="color:#666; margin-bottom:12px; line-height:1.6;">{_esc(dim["verdict"])}</p>')
        rows = dim.get("rows") or []
        if rows:
            parts.append('<table><thead><tr><th>指标</th><th>数值</th><th>评价</th></tr></thead><tbody>')
            for r in rows:
                badge = _rating_badge(r.get("rating", "good"))
                comment = r.get("comment", "")
                parts.append(f"""
                    <tr>
                        <td>{_esc(r.get('label',''))}</td>
                        <td>{_esc(r.get('value',''))}</td>
                        <td><span class="badge {badge}">{_esc(comment)}</span></td>
                    </tr>""")
            parts.append('</tbody></table>')

    # 4. Recommendations
    recs = a.get("recommendations") or []
    if recs:
        parts.append('<h3 style="color:#333; margin:25px 0 15px;">建议</h3>')
        for r in recs:
            parts.append(f"""
            <div class="recommendation">
                <h4>{_esc(r.get('title',''))}</h4>
                <p>{_esc(r.get('body',''))}</p>
            </div>""")

    # 5. Follow-up plan
    plan = a.get("plan") or []
    if plan:
        parts.append('<h3 style="color:#333; margin:25px 0 15px;">后续训练计划</h3>')
        parts.append('<table><thead><tr><th>日期</th><th>训练内容</th><th>强度</th><th>说明</th></tr></thead><tbody>')
        for p in plan:
            parts.append(f"""
                    <tr>
                        <td>{_esc(p.get('date',''))}</td>
                        <td>{_esc(p.get('content',''))}</td>
                        <td>{_esc(p.get('intensity',''))}</td>
                        <td>{_esc(p.get('note',''))}</td>
                    </tr>""")
        parts.append('</tbody></table>')

    # Attribution
    model = a.get("model", "Claude")
    parts.append(f'<p style="color:#999; font-size:0.85em; margin-top:20px;">分析由 {model} 生成 · 仅供参考,不替代专业医疗/教练建议</p>')

    parts.append('</div>')
    return "".join(parts)


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
            # Skip summary.json and the optional {date}.analysis.json sidecars
            if json_file.name == "summary.json" or json_file.name.endswith(".analysis.json"):
                continue
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
