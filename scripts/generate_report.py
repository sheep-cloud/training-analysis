#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Training analysis report generator
Merges data from Xunji App (xunji-api) + Coros App (Coros)
"""

import html
import json
import os
import sys
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 encoding on Windows
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "daily"
PUBLIC_DIR = PROJECT_ROOT / "public" / "daily"

# Allow importing coros_mcp from its isolated venv so we can reuse its
# authentication + sync logic instead of shelling out to the CLI.
COROS_MCP_VENV = Path("C:/Develop/Workspaces/AIProjects/cygnusb/coros-mcp/.venv/Lib/site-packages")
COROS_MCP_SRC = Path("C:/Develop/Workspaces/AIProjects/cygnusb/coros-mcp")
for p in (COROS_MCP_VENV, COROS_MCP_SRC):
    if p.exists():
        sys.path.insert(0, str(p))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

# Coros MCP local SQLite cache (filled by `coros-mcp sync`)
COROS_CACHE_DB = Path.home() / ".config" / "coros-mcp" / "cache.db"

# Coros sport_type values that are strength training (overlap with Xunji data)
STRENGTH_SPORT_TYPES = {402}

# Sport type -> category name for grouping cardio sections
SPORT_TYPE_CATEGORIES = {
    1: "跑步",
    2: "骑行",
    100: "跑步",
    101: "跑步",
    102: "越野跑",
    103: "跑步",
    200: "骑行",
    201: "骑行",
    300: "游泳",
    401: "飞盘",
    403: "乒乓球",
    404: "骑行",
    1100: "飞盘",
}


def ensure_coros_sync(date_str):
    """Auto-login (if needed) and sync Coros data for date_str into the local cache."""
    try:
        import asyncio
        from coros_mcp.coros_api import get_stored_auth, login, get_env_credentials
        from coros_mcp.cache.sync import sync_all

        auth = get_stored_auth()
        if auth is None:
            creds = get_env_credentials()
            if creds is None:
                print("[WARN] Coros not authenticated and no credentials in .env")
                return
            email, password, region = creds
            auth = asyncio.run(login(email, password, region, skip_mobile=False))

        day = date_str.replace("-", "")
        # Sync previous day as well; late-night activities may be stored under the previous date.
        prev_day = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y%m%d")

        stats = asyncio.run(sync_all(auth, prev_day, end_day=day))
        print(f"[INFO] Coros synced: daily={stats['daily']}, sleep={stats['sleep']}, activities={stats['activities']}")
        for e in stats.get("errors", []):
            print(f"[WARN] Coros sync error: {e}")
    except Exception as e:
        print(f"[WARN] Coros sync failed: {e}")


def _num(value, default=0):
    """None-safe numeric accessor; returns *value* if it is a number, else *default*."""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        # Strip percent sign / whitespace so "3.0%" becomes 3.0
        cleaned = value.replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def _esc(value):
    """HTML-escape a string value so it can be safely embedded in markup."""
    if value is None:
        return ""
    return html.escape(str(value))


def _fmt_ts(ts_ms):
    """Format millisecond timestamp to HH:MM."""
    if not ts_ms:
        return "-"
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M")
    except (OSError, ValueError, OverflowError):
        return "-"


def _fmt_duration(minutes):
    """Format minutes to 'X小时Y分' or 'X分'."""
    if minutes is None:
        return "-"
    minutes = int(minutes)
    if minutes >= 60:
        return f"{minutes // 60} 小时 {minutes % 60} 分"
    return f"{minutes} 分"


def _fmt_hms(minutes):
    """Format minutes to 'HH:MM:SS'."""
    if minutes is None:
        return "-"
    total = int(minutes) * 60
    hours = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


def _pace(minutes, km):
    """Compute min/km pace."""
    if not km:
        return "-"
    total_sec = minutes * 60
    pace_sec = total_sec / km
    m = int(pace_sec // 60)
    s = int(pace_sec % 60)
    return f"{m}:{s:02d}"


def _trend_pct(current, history_values, lower_better=False):
    """Return (pct_str, arrow, state) comparing current to historical average."""
    if not history_values or current is None:
        return ("", "", "neutral")
    avg = sum(history_values) / len(history_values)
    if avg == 0:
        return ("", "", "neutral")
    pct = (current - avg) / avg
    if abs(pct) < 0.02:
        return ("0%", "→", "neutral")
    arrow = "↑" if pct > 0 else "↓"
    pct_str = f"{abs(pct) * 100:.0f}%"
    if lower_better:
        state = "good" if pct <= -0.05 else "warn" if pct >= 0.05 else "neutral"
    else:
        state = "good" if pct >= 0.05 else "warn" if pct <= -0.05 else "neutral"
    return (pct_str, arrow, state)


def _trend_span(pct_str, arrow, state):
    if not pct_str:
        return ""
    cls = {"good": "trend-up", "warn": "trend-down", "neutral": "trend-flat"}.get(state, "trend-flat")
    return f"<span class='{cls}'>{arrow}{pct_str}</span>"


def _aggregate_history(history):
    """Aggregate historical data by date for stable trend comparison."""
    by_date = {}
    for h in history:
        date = h.get("date", "unknown")
        if date not in by_date:
            by_date[date] = {
                "strength_volume": 0,
                "cardio_duration": 0,
                "cardio_by_type": {},
                "sleep_minutes": 0,
                "total_load": 0,
                "hrv": 0,
                "rhr": 0,
                "__raw_activities": [],
            }
        for t in h.get("strength_training", []):
            vol = sum(
                _num(s.get("weight")) * _num(s.get("reps"))
                for m in t.get("movements", []) for s in m.get("sets", [])
            )
            by_date[date]["strength_volume"] += vol
        for a in h.get("cardio_activities", []):
            cat = SPORT_TYPE_CATEGORIES.get(a.get("sport_type"), "其他运动")
            dur = a.get("duration_seconds", 0) / 60
            by_date[date]["cardio_duration"] += dur
            by_date[date]["cardio_by_type"].setdefault(cat, 0)
            by_date[date]["cardio_by_type"][cat] += dur
            by_date[date]["__raw_activities"].append(a)
        sleep = h.get("sleep_data") or {}
        by_date[date]["sleep_minutes"] += _num(sleep.get("total_duration_minutes"))
        by_date[date]["total_load"] += _num(h.get("summary", {}).get("total_load"))
        dm = h.get("daily_metrics") or {}
        if dm.get("avg_sleep_hrv"):
            by_date[date]["hrv"] = _num(dm.get("avg_sleep_hrv"))
        if dm.get("rhr"):
            by_date[date]["rhr"] = _num(dm.get("rhr"))
    return by_date


def _recovery_score(m, sleep):
    """Compute 0-100 recovery score from metrics and sleep.

    Each of the five contributors (HRV, RHR, sleep, tiredness, load ratio)
    is scored 0-20 using heuristic thresholds inspired by common recovery
    models. Weights are equal for simplicity and can be calibrated later
    against subjective feedback or training outcomes.
    """
    score = 0

    hrv = _num(m.get("avg_sleep_hrv"))
    baseline = _num(m.get("baseline"))
    if hrv and baseline:
        ratio = hrv / baseline
        if ratio >= 1.0:
            score += 20
        elif ratio >= 0.9:
            score += 15
        elif ratio >= 0.8:
            score += 10
        else:
            score += 5
    else:
        score += 15

    rhr = _num(m.get("rhr"))
    if rhr:
        if 45 <= rhr <= 55:
            score += 20
        elif 40 <= rhr < 45 or 55 < rhr <= 65:
            score += 15
        elif 65 < rhr <= 75:
            score += 10
        else:
            score += 5
    else:
        score += 15

    total = _num(sleep.get("total_duration_minutes"))
    if total:
        if 420 <= total <= 540:
            score += 20
        elif 360 <= total < 420:
            score += 15
        elif 300 <= total < 360:
            score += 10
        else:
            score += 5
    else:
        score += 15

    tired = m.get("tired_rate")
    if tired is not None:
        tired = _num(tired)
        if tired <= 5:
            score += 20
        elif tired <= 15:
            score += 15
        elif tired <= 25:
            score += 10
        else:
            score += 5
    else:
        score += 15

    ratio = _num(m.get("training_load_ratio"))
    if ratio:
        if 0.8 <= ratio <= 1.3:
            score += 20
        elif 0.6 <= ratio < 0.8 or 1.3 < ratio <= 1.6:
            score += 15
        elif 1.6 < ratio <= 2.0:
            score += 10
        else:
            score += 5
    else:
        score += 15

    return min(100, max(0, score))


def _readiness_label(score):
    if score >= 80:
        return ("适宜高强度", "good")
    elif score >= 60:
        return ("适宜中等强度", "neutral")
    elif score >= 40:
        return ("建议恢复性训练", "warn")
    else:
        return ("建议休息", "danger")


def _muscle_group(title):
    title = str(title).lower()
    if any(k in title for k in ["胸", "卧推", "推胸", "bench", "chest", "飞鸟", "夹胸"]):
        return "胸"
    if any(k in title for k in ["背", "引体", "划船", "硬拉", "高位下拉", "pull", "row", "back"]):
        return "背"
    if any(k in title for k in ["腿", "深蹲", "箭步蹲", "leg", "squat", "lunge", "臀"]):
        return "腿"
    if any(k in title for k in ["肩", "推举", "侧平举", "shoulder", "press", "侧举", "面拉"]):
        return "肩"
    if any(k in title for k in ["手臂", "二头", "三头", "弯举", "臂", "curl", "arm", "肱三", "肱二"]):
        return "手臂"
    if any(k in title for k in ["核心", "腹肌", "平板支撑", "core", "abs", "plank", "腹"]):
        return "核心"
    return None


def _running_hr_zones(avg_hr, lthr):
    """Map average HR to a simplified 5-zone model relative to LTHR.

    Thresholds are intentionally simplified for display purposes:
    E < 85%, M 85-90%, T 90-95%, A 95-100%, I >= 100% of LTHR.
    """
    if not avg_hr or not lthr:
        return None
    if avg_hr < lthr * 0.85:
        return "有氧基础区（E区）"
    elif avg_hr < lthr * 0.9:
        return "有氧进阶区（M区）"
    elif avg_hr < lthr * 0.95:
        return "乳酸阈值区（T区）"
    elif avg_hr < lthr * 1.0:
        return "无氧耐力区（A区）"
    else:
        return "无氧能力区（I区）"


def _pace_to_seconds(pace_str):
    try:
        m, s = map(int, pace_str.split(":"))
        return m * 60 + s
    except Exception:
        return None


def fetch_xunji_data(date_str):
    """Get data from Xunji App (via xunji-api skill)."""
    try:
        cmd = [
            "python",
            "./.claude/skills/xunji-api/scripts/fetch_trains.py",
            "read",
            "--date", date_str,
            "--full"
        ]
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
            return None

        return data.get("res", {})
    except Exception as e:
        print(f"[WARN] Xunji API error: {e}")
        return None


def fetch_coros_data(date_str):
    """Read coros-mcp local SQLite cache. Syncs first to refresh token and pick up same-day data."""
    ensure_coros_sync(date_str)
    empty = {"activities": [], "daily_metrics": {}, "sleep_data": {}}

    if not COROS_CACHE_DB.exists():
        print(f"[WARN] Coros cache not found: {COROS_CACHE_DB}")
        return empty

    day = date_str.replace("-", "")

    try:
        con = sqlite3.connect(COROS_CACHE_DB)
        con.row_factory = sqlite3.Row

        activities = []
        for row in con.execute(
            "SELECT data FROM activities WHERE start_day = ? ORDER BY start_day", (day,)
        ):
            act = json.loads(row["data"])
            act["start_ts"] = _coros_ts_to_epoch(act.get("start_time"))
            act["end_ts"] = _coros_ts_to_epoch(act.get("end_time"))
            activities.append(act)

        daily_metrics = {}
        drow = con.execute("SELECT data FROM daily_records WHERE date = ?", (day,)).fetchone()
        if drow:
            daily_metrics = json.loads(drow["data"])

        sleep_data = {}
        srow = con.execute("SELECT data FROM sleep_records WHERE date = ?", (day,)).fetchone()
        if srow:
            sleep_data = json.loads(srow["data"])

        con.close()
        return {"activities": activities, "daily_metrics": daily_metrics, "sleep_data": sleep_data}

    except Exception as e:
        print(f"[WARN] Coros cache read error: {e}")
        return empty


def _coros_ts_to_epoch(value):
    if not value:
        return None
    s = str(value)
    if s.isdigit():
        if len(s) == 13:
            return int(s) // 1000
        if len(s) == 10:
            return int(s)
    return None


def _intervals_overlap(a_start, a_end, b_start, b_end):
    if None in (a_start, a_end, b_start, b_end):
        return False
    return a_start <= b_end and b_start <= a_end


def merge_training_data(xunji_data, coros_data, date_str):
    merged = {
        "date": date_str,
        "strength_training": [],
        "cardio_activities": [],
        "daily_metrics": {},
        "sleep_data": {},
        "summary": {"total_load": 0, "total_calories": 0, "total_duration": 0, "workout_count": 0}
    }

    coros_strength = []
    coros_cardio = []
    if coros_data and coros_data.get("activities"):
        for activity in coros_data["activities"]:
            if activity.get("sport_type") in STRENGTH_SPORT_TYPES:
                coros_strength.append(activity)
            else:
                coros_cardio.append(activity)

    # Xunji strength training
    if xunji_data and "trains" in xunji_data:
        for train in xunji_data["trains"]:
            t_start = train.get("start", 0)
            t_end = train.get("end", 0)
            train_info = {
                "title": train.get("title", "Unknown"),
                "start_time": t_start,
                "end_time": t_end,
                "duration_minutes": (t_end - t_start) // 60000,
                "calories": None,
                "training_load": None,
                "movements": []
            }

            for movement in train.get("movements", []):
                move_info = {"name": movement.get("name", "Unknown"), "sets": []}
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

            t_start_s = t_start // 1000 if t_start else None
            t_end_s = t_end // 1000 if t_end else None
            for ca in list(coros_strength):
                if _intervals_overlap(t_start_s, t_end_s, ca.get("start_ts"), ca.get("end_ts")):
                    train_info["calories"] = round(_num(ca.get("calories")) / 1000, 1)
                    train_info["training_load"] = _num(ca.get("training_load"))
                    coros_strength.remove(ca)
                    break

            merged["strength_training"].append(train_info)
            merged["summary"]["workout_count"] += 1

    # Orphan Coros strength
    for ca in coros_strength:
        merged["strength_training"].append({
            "title": ca.get("name", "Strength"),
            "start_time": (ca.get("start_ts") or 0) * 1000,
            "end_time": (ca.get("end_ts") or 0) * 1000,
            "duration_minutes": (ca.get("duration_seconds", 0) // 60),
            "calories": round(_num(ca.get("calories")) / 1000, 1),
            "training_load": _num(ca.get("training_load")),
            "movements": []
        })
        merged["summary"]["workout_count"] += 1

    # Coros cardio activities
    for activity in coros_cardio:
        merged["cardio_activities"].append({
            "name": activity.get("name", "Unknown"),
            "sport_type": activity.get("sport_type"),
            "duration_seconds": activity.get("duration_seconds", 0),
            "distance_meters": activity.get("distance_meters", 0),
            "avg_hr": activity.get("avg_hr"),
            "calories": round(_num(activity.get("calories")) / 1000, 1),
            "training_load": _num(activity.get("training_load"))
        })
        merged["summary"]["total_duration"] += activity.get("duration_seconds", 0)
        merged["summary"]["workout_count"] += 1

    all_coros = (coros_data.get("activities", []) if coros_data else [])
    for a in all_coros:
        merged["summary"]["total_calories"] += _num(a.get("calories")) / 1000
        merged["summary"]["total_load"] += _num(a.get("training_load"))
    merged["summary"]["total_calories"] = round(merged["summary"]["total_calories"], 1)

    if coros_data:
        merged["daily_metrics"] = coros_data.get("daily_metrics", {})
        merged["sleep_data"] = coros_data.get("sleep_data", {})

    merged["summary"]["total_duration"] = merged["summary"]["total_duration"] // 60

    return merged


def save_json_data(merged_data, date_str):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_file = DATA_DIR / f"{date_str}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, indent=2, ensure_ascii=False)
    print(f"[OK] JSON data saved: {json_file}")
    return json_file


# ── HTML generation ──

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: #0d0d0d;
    color: #f5f5f7;
    min-height: 100vh;
    padding: 40px 20px;
}
.container { max-width: 900px; margin: 0 auto; }
.header { text-align: center; margin-bottom: 32px; }
.header .date { color: #8e8e93; font-size: 14px; margin-bottom: 6px; }
.header h1 { font-size: 32px; font-weight: 700; letter-spacing: 1px; }
.header .subtitle { color: #00d4aa; font-size: 16px; margin-top: 8px; }

.global-toggle { text-align: center; margin-bottom: 24px; }
.global-toggle button {
    background: #1c1c1e; color: #00d4aa; border: 1px solid #00d4aa;
    padding: 8px 20px; border-radius: 20px; cursor: pointer; font-size: 14px; margin: 0 6px;
}
.global-toggle button:hover { background: #232326; }

.overview-card { background: #1c1c1e; border-radius: 20px; padding: 28px; margin-bottom: 24px; }
.overview-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.overview-title { font-size: 20px; font-weight: 600; }
.ring-wrap { position: relative; width: 110px; height: 110px; }
.ring-bg, .ring-fill { fill: none; stroke-width: 10; stroke-linecap: round; }
.ring-bg { stroke: #2c2c2e; }
.ring-fill { stroke: #00d4aa; stroke-dasharray: 259 345; }
.ring-text { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); text-align: center; }
.ring-text .num { font-size: 26px; font-weight: 700; }
.ring-text .label { font-size: 12px; color: #8e8e93; }
.overview-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.overview-item { text-align: center; }
.overview-item .value { font-size: 26px; font-weight: 700; color: #fff; }
.overview-item .label { font-size: 12px; color: #8e8e93; margin-top: 4px; }

.section { margin-bottom: 16px; }
.fold-card { background: #1c1c1e; border-radius: 16px; overflow: hidden; }
.fold-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 18px 20px; cursor: pointer; user-select: none;
}
.fold-header:hover { background: #232326; }
.fold-title { display: flex; align-items: center; gap: 10px; }
.fold-title .icon-emoji { font-size: 22px; }
.fold-title .text { font-size: 18px; font-weight: 700; }
.fold-title .badge {
    background: #2c2c2e; color: #8e8e93; font-size: 12px;
    padding: 3px 10px; border-radius: 12px; margin-left: 8px;
}
.fold-arrow { font-size: 14px; color: #8e8e93; transition: transform 0.2s; }
.fold-card.open .fold-arrow { transform: rotate(180deg); }
.fold-body { display: none; padding: 0 20px 20px; }
.fold-card.open .fold-body { display: block; }

.card { background: #1c1c1e; border-radius: 16px; padding: 20px; margin-bottom: 16px; }
.detail-row { font-size: 12px; color: #8e8e93; margin-top: 6px; display: flex; gap: 16px; flex-wrap: wrap; }
.detail-row span { color: #f5f5f7; }

.strength-summary { display: flex; gap: 24px; font-size: 13px; color: #8e8e93; margin-bottom: 16px; flex-wrap: wrap; }
.strength-summary span { color: #00d4aa; font-weight: 600; }
.movement { display: flex; align-items: flex-start; gap: 14px; padding: 16px 0; border-bottom: 1px solid #2c2c2e; }
.movement:last-child { border-bottom: none; padding-bottom: 0; }
.movement:first-child { padding-top: 0; }
.movement-num { width: 36px; height: 36px; border-radius: 50%; background: #2c2c2e; color: #00d4aa; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; flex-shrink: 0; }
.movement-info { flex: 1; }
.movement-name { font-size: 16px; font-weight: 600; margin-bottom: 10px; }
.sets-row { display: flex; flex-wrap: wrap; gap: 8px; }
.set-tag { background: #2c2c2e; padding: 8px 12px; border-radius: 10px; font-size: 13px; color: #f5f5f7; }
.set-tag .main { font-weight: 600; }
.set-tag .type { color: #ff9f0a; font-size: 11px; margin-right: 4px; }
.set-tag .rest { color: #8e8e93; font-size: 11px; margin-left: 4px; }
.set-tag .extra { color: #8e8e93; font-size: 11px; margin-left: 4px; }
.mov-detail { color: #8e8e93; font-size: 12px; margin-top: 8px; }
.mov-detail span { color: #f5f5f7; }

.activity-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 14px; }
.activity-grid .item .num { font-size: 22px; font-weight: 700; }
.activity-grid .item .label { font-size: 12px; color: #8e8e93; margin-top: 2px; }

.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.metric-card { background: #1c1c1e; border-radius: 16px; padding: 18px; }
.metric-card .label { font-size: 12px; color: #8e8e93; margin-bottom: 8px; }
.metric-card .value { font-size: 24px; font-weight: 700; }
.metric-card .unit { font-size: 12px; color: #8e8e93; margin-left: 2px; }
.metric-card .sub { font-size: 11px; color: #8e8e93; margin-top: 6px; }

.sleep-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.sleep-time { font-size: 32px; font-weight: 700; }
.sleep-quality { background: #2c2c2e; padding: 6px 14px; border-radius: 20px; font-size: 14px; color: #00d4aa; }
.sleep-bar { height: 14px; border-radius: 7px; background: #2c2c2e; overflow: hidden; display: flex; margin-bottom: 14px; }
.sleep-bar span { height: 100%; }
.deep { background: #0a84ff; }
.light { background: #64d2ff; }
.rem { background: #bf5af2; }
.awake { background: #ff9f0a; }
.sleep-legend { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.legend-item { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; }
.sleep-hr { color: #8e8e93; font-size: 13px; margin-top: 16px; }

.analysis-card { background: #161618; border-radius: 14px; padding: 18px; margin-bottom: 12px; }
.analysis-card h3 { font-size: 15px; color: #00d4aa; margin-bottom: 10px; }
.analysis-card ul { list-style: none; }
.analysis-card li { padding: 5px 0; font-size: 13px; line-height: 1.6; color: #d1d1d6; border-bottom: 1px solid #2c2c2e; }
.analysis-card li:last-child { border-bottom: none; }
.analysis-card .highlight { color: #fff; font-weight: 600; }
.analysis-card .warn { color: #ff9f0a; }
.analysis-card .good { color: #00d4aa; }

.score-card { display: flex; align-items: center; justify-content: space-between; background: #1c1c1e; border-radius: 20px; padding: 28px; margin-bottom: 24px; }
.score-left { flex: 1; }
.score-title { font-size: 20px; font-weight: 600; margin-bottom: 8px; }
.score-desc { color: #8e8e93; font-size: 13px; }
.score-right { text-align: right; }
.score-value { font-size: 48px; font-weight: 700; line-height: 1; }
.score-value.good { color: #00d4aa; }
.score-value.warn { color: #ff9f0a; }
.score-value.danger { color: #ff453a; }
.score-value.neutral { color: #0a84ff; }
.readiness-badge { display: inline-block; margin-top: 10px; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; }
.readiness-badge.good { background: rgba(0, 212, 170, 0.15); color: #00d4aa; }
.readiness-badge.warn { background: rgba(255, 159, 10, 0.15); color: #ff9f0a; }
.readiness-badge.danger { background: rgba(255, 69, 58, 0.15); color: #ff453a; }
.readiness-badge.neutral { background: rgba(10, 132, 255, 0.15); color: #0a84ff; }
.metric-row { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 14px; }
.metric-row .metric { background: #2c2c2e; padding: 8px 12px; border-radius: 10px; font-size: 13px; }
.metric-row .metric .label { color: #8e8e93; margin-right: 6px; }
.metric-row .metric .value { color: #f5f5f7; font-weight: 600; }
.trend-up { color: #00d4aa; }
.trend-down { color: #ff453a; }
.trend-flat { color: #8e8e93; }

.footer { text-align: center; color: #8e8e93; font-size: 12px; margin-top: 40px; }
"""

SCRIPT = """
function toggleCard(card) { card.classList.toggle('open'); }
function toggleAll(open) {
    document.querySelectorAll('[data-fold="section"]').forEach(c => {
        if (open) c.classList.add('open'); else c.classList.remove('open');
    });
}
"""


def generate_html_report(merged_data, date_str):
    weekday = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a").replace("Mon", "周一").replace("Tue", "周二").replace("Wed", "周三").replace("Thu", "周四").replace("Fri", "周五").replace("Sat", "周六").replace("Sun", "周日")

    overview_ring_value = merged_data['summary']['total_load']

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>训练分析报告 - {date_str}</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="date">{date_str} {weekday}</div>
            <h1>训练分析报告</h1>
            <div class="subtitle">今天有 {merged_data['summary']['workout_count']} 练</div>
        </div>

        <div class="global-toggle">
            <button onclick="toggleAll(true)">全部展开</button>
            <button onclick="toggleAll(false)">全部折叠</button>
        </div>

        <div class="overview-card">
            <div class="overview-top">
                <div>
                    <div class="overview-title">今日训练负荷</div>
                    <div style="color:#8e8e93;font-size:13px;margin-top:8px">力量 + 有氧综合</div>
                </div>
                <div class="ring-wrap">
                    <svg width="110" height="110" viewBox="0 0 120 120">
                        <circle class="ring-bg" cx="60" cy="60" r="50" transform="rotate(135 60 60)"></circle>
                        <circle class="ring-fill" cx="60" cy="60" r="50" transform="rotate(135 60 60)"></circle>
                    </svg>
                    <div class="ring-text">
                        <div class="num">{overview_ring_value}</div>
                        <div class="label">负荷</div>
                    </div>
                </div>
            </div>
            <div class="overview-grid">
                <div class="overview-item"><div class="value">{merged_data['summary']['workout_count']}</div><div class="label">训练场次</div></div>
                <div class="overview-item"><div class="value">{_fmt_hms(merged_data['summary']['total_duration'])}</div><div class="label">训练时长</div></div>
                <div class="overview-item"><div class="value">{merged_data['summary']['total_calories']:.0f}</div><div class="label">消耗热量(千卡)</div></div>
                <div class="overview-item"><div class="value">{sum(t.get('training_load') or 0 for t in merged_data['strength_training'])}</div><div class="label">力量负荷</div></div>
            </div>
        </div>

        {generate_strength_html(merged_data)}
        {_generate_score_html(merged_data, date_str)}
        {generate_cardio_html(merged_data)}
        {generate_metrics_html(merged_data)}
        {generate_sleep_html(merged_data)}
        {generate_analysis_html(merged_data, date_str)}

        <div class="footer">
            <p>数据来源: 训记 App + 高驰 App · 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
    <script>{SCRIPT}</script>
</body>
</html>"""


def generate_strength_html(merged_data):
    if not merged_data["strength_training"]:
        return ""

    total_sets = sum(len(m["sets"]) for t in merged_data["strength_training"] for m in t["movements"])
    section_badge = f"{len(merged_data['strength_training'])} 场训练 · {total_sets} 组"

    inner_html = ""
    for train in merged_data["strength_training"]:
        if not train["movements"]:
            inner_html += f"""
            <div class="fold-card open" style="margin-bottom:14px" data-fold="inner">
                <div class="fold-header" onclick="toggleCard(this.parentElement)">
                    <div class="fold-title"><span class="text" style="font-size:16px">{_esc(train['title'])}</span><span class="badge">无明细</span></div>
                    <div class="fold-arrow">▼</div>
                </div>
                <div class="fold-body"><p style="color:#8e8e93;font-size:13px">仅高驰记录，无动作明细</p></div>
            </div>"""
            continue

        train_sets = sum(len(m["sets"]) for m in train["movements"])
        train_volume = sum(
            _num(s.get("weight")) * _num(s.get("reps"))
            for m in train["movements"] for s in m["sets"]
        )

        movements_html = ""
        for idx, movement in enumerate(train["movements"], 1):
            sets_html = ""
            mov_volume = 0
            mov_max_weight = 0
            total_reps = 0
            for s in movement["sets"]:
                w = _num(s.get("weight"))
                r = _num(s.get("reps"))
                mov_volume += w * r
                mov_max_weight = max(mov_max_weight, w)
                total_reps += r

                left_w = s.get("left_weight")
                left_str = f'<span class="extra">左{left_w}</span>' if left_w else ""
                rest = s.get("rest_seconds")
                rest_str = f'<span class="rest">休{rest}s</span>' if rest else ""
                stype = s.get("set_type", "")
                type_str = f'<span class="type">{stype}</span>' if stype else ""
                weight_val = s.get("weight")
                if weight_val in (None, "", 0):
                    main = "自重"
                else:
                    main = f"{weight_val}{s.get('unit','kg')}"
                reps_val = s.get("reps")
                reps_str = f"×{reps_val}" if reps_val else ""
                sets_html += f'<div class="set-tag">{type_str}<span class="main">{main}{reps_str}</span>{left_str}{rest_str}</div>'

            detail_items = []
            if mov_volume:
                detail_items.append(f"容量 <span>{mov_volume:.0f} kg</span>")
            if mov_max_weight:
                detail_items.append(f"最大重量 <span>{mov_max_weight:.0f} kg</span>")
            if not mov_volume and total_reps:
                detail_items.append(f"总次数 <span>{total_reps} 次</span>")
            detail_html = " &nbsp;·&nbsp; ".join(detail_items) if detail_items else ""

            movements_html += f"""
            <div class="movement">
                <div class="movement-num">{idx}</div>
                <div class="movement-info">
                    <div class="movement-name">{_esc(movement['name'])}</div>
                    <div class="sets-row">{sets_html}</div>
                    <div class="mov-detail">{detail_html}</div>
                </div>
            </div>"""

        extra = []
        if train.get("calories") is not None:
            extra.append(f"消耗 <span>{train['calories']:.0f} 千卡</span>")
        if train.get("training_load") is not None:
            extra.append(f"负荷 <span>{train['training_load']}</span>")
        extra_html = " &nbsp;·&nbsp; ".join(extra)

        inner_html += f"""
        <div class="fold-card open" style="margin-bottom:14px" data-fold="inner">
            <div class="fold-header" onclick="toggleCard(this.parentElement)">
                <div class="fold-title">
                    <span class="text" style="font-size:16px">{_esc(train['title'])}</span>
                    <span class="badge">{train['duration_minutes']}分钟 · {train_sets}组 · {train_volume:.0f}kg</span>
                </div>
                <div class="fold-arrow">▼</div>
            </div>
            <div class="fold-body">
                <div class="detail-row" style="margin-bottom:12px">
                    开始 <span>{_fmt_ts(train['start_time'])}</span> &nbsp;·&nbsp; 结束 <span>{_fmt_ts(train['end_time'])}</span>
                </div>
                <div class="strength-summary">总重量 <span>{train_volume:.0f} kg</span> &nbsp;·&nbsp; {extra_html}</div>
                {movements_html}
            </div>
        </div>"""

    return f"""
    <div class="section fold-card open" data-fold="section">
        <div class="fold-header" onclick="toggleCard(this.parentElement)">
            <div class="fold-title">
                <span class="icon-emoji">💪</span>
                <span class="text">力量训练</span>
                <span class="badge">{section_badge}</span>
            </div>
            <div class="fold-arrow">▼</div>
        </div>
        <div class="fold-body">{inner_html}</div>
    </div>"""


def generate_cardio_html(merged_data):
    if not merged_data["cardio_activities"]:
        return ""

    # Group activities by sport category
    groups = {}
    for activity in merged_data["cardio_activities"]:
        sport_type = activity.get("sport_type")
        category = SPORT_TYPE_CATEGORIES.get(sport_type, "其他运动")
        groups.setdefault(category, []).append(activity)

    sections = []
    for category, activities in groups.items():
        emoji = {"跑步": "🏃", "骑行": "🚴", "游泳": "🏊", "飞盘": "🥏", "乒乓球": "🏓", "越野跑": "⛰️"}.get(category, "🏃")

        cards = []
        for activity in activities:
            distance_km = activity["distance_meters"] / 1000
            duration_min = activity["duration_seconds"] / 60
            pace = _pace(duration_min, distance_km)
            hr = activity.get("avg_hr")
            hr_str = f"{hr} bpm" if hr else "-"

            cards.append(f"""
            <div class="card">
                <div style="font-size:18px;font-weight:700;margin-bottom:8px">{_esc(activity['name'])}</div>
                <div class="detail-row" style="margin-bottom:12px">
                    运动类型 <span>{category} ({activity.get('sport_type','')})</span> &nbsp;·&nbsp; 训练负荷 <span>{activity.get('training_load') or 0}</span>
                </div>
                <div class="activity-grid">
                    <div class="item"><div class="num">{distance_km:.2f}</div><div class="label">距离 km</div></div>
                    <div class="item"><div class="num">{_fmt_hms(duration_min)}</div><div class="label">时长</div></div>
                    <div class="item"><div class="num">{pace}</div><div class="label">平均配速 min/km</div></div>
                    <div class="item"><div class="num">{hr_str}</div><div class="label">平均心率</div></div>
                    <div class="item"><div class="num">{activity['calories']:.0f}</div><div class="label">消耗 kcal</div></div>
                </div>
            </div>""")

        total_duration = sum(a["duration_seconds"] for a in activities) // 60
        total_kcal = sum(a["calories"] for a in activities)
        badge = f"{len(activities)} 场 · {_fmt_hms(total_duration)} · {total_kcal:.0f}千卡"

        sections.append(f"""
        <div class="section fold-card open" data-fold="section">
            <div class="fold-header" onclick="toggleCard(this.parentElement)">
                <div class="fold-title">
                    <span class="icon-emoji">{emoji}</span>
                    <span class="text">{category}</span>
                    <span class="badge">{badge}</span>
                </div>
                <div class="fold-arrow">▼</div>
            </div>
            <div class="fold-body">{''.join(cards)}</div>
        </div>""")

    return "".join(sections)


def generate_metrics_html(merged_data):
    m = merged_data.get("daily_metrics") or {}
    if not m:
        return ""

    cards = [
        ("HRV", m.get("avg_sleep_hrv"), "ms", f"基线 {m.get('baseline')}ms" if m.get("baseline") else ""),
        ("静息心率", m.get("rhr"), "bpm", ""),
        ("训练负荷", m.get("training_load"), "", f"负荷比 {m.get('training_load_ratio')}" if m.get("training_load_ratio") else ""),
        ("最大摄氧量", m.get("vo2max"), "", ""),
        ("体能水平", m.get("stamina_level"), "", f"7日 {m.get('stamina_level_7d')}" if m.get("stamina_level_7d") else ""),
        ("疲劳度", m.get("tired_rate"), "%", ""),
        ("有氧能力 ATI", m.get("ati"), "", ""),
        ("无氧能力 CTI", m.get("cti"), "", ""),
        ("乳酸阈值心率", m.get("lthr"), "bpm", ""),
        ("乳酸阈值配速", m.get("ltsp"), "", ""),
        ("表现指数", m.get("performance"), "", ""),
    ]

    metric_html = ""
    for label, value, unit, sub in cards:
        if value is None:
            continue
        unit_html = f'<span class="unit">{unit}</span>' if unit else ""
        sub_html = f'<div class="sub">{sub}</div>' if sub else ""
        metric_html += f"""
        <div class="metric-card">
            <div class="label">{label}</div>
            <div class="value">{value}{unit_html}</div>
            {sub_html}
        </div>"""

    if m.get("interval_list"):
        metric_html += f"""
        <div class="metric-card">
            <div class="label">HRV 区间</div>
            <div class="value" style="font-size:14px">{'-'.join(str(v) for v in m['interval_list'])}</div>
        </div>"""

    return f"""
    <div class="section fold-card open" data-fold="section">
        <div class="fold-header" onclick="toggleCard(this.parentElement)">
            <div class="fold-title">
                <span class="icon-emoji">❤️</span>
                <span class="text">生理指标</span>
                <span class="badge">{len([c for c in cards if c[1] is not None])} 项数据</span>
            </div>
            <div class="fold-arrow">▼</div>
        </div>
        <div class="fold-body">
            <div class="metric-grid">{metric_html}</div>
        </div>
    </div>"""


def generate_sleep_html(merged_data):
    s = merged_data.get("sleep_data") or {}
    if not s:
        return ""

    total = s.get("total_duration_minutes", 0)
    phases = s.get("phases") or {}
    deep = phases.get("deep_minutes", 0) or 0
    light = phases.get("light_minutes", 0) or 0
    rem = phases.get("rem_minutes", 0) or 0
    awake = phases.get("awake_minutes", 0) or 0
    total_phase = deep + light + rem + awake or 1

    deep_pct = deep / total_phase * 100
    light_pct = light / total_phase * 100
    rem_pct = rem / total_phase * 100
    awake_pct = awake / total_phase * 100

    quality_text = "睡眠正常"
    if total < 360:
        quality_text = "睡眠偏短"
    elif awake_pct > 20:
        quality_text = "清醒偏多"

    avg_hr = s.get("avg_hr")
    min_hr = s.get("min_hr")
    max_hr = s.get("max_hr")
    hr_text = ""
    if avg_hr is not None:
        hr_text = f"睡眠心率 {min_hr or '-'}-{max_hr or '-'} bpm，平均 {avg_hr} bpm"

    return f"""
    <div class="section fold-card open" data-fold="section">
        <div class="fold-header" onclick="toggleCard(this.parentElement)">
            <div class="fold-title">
                <span class="icon-emoji">🌙</span>
                <span class="text">睡眠</span>
                <span class="badge">{_fmt_duration(total)}</span>
            </div>
            <div class="fold-arrow">▼</div>
        </div>
        <div class="fold-body">
            <div class="sleep-top">
                <div class="sleep-time">{_fmt_duration(total)}</div>
                <div class="sleep-quality">{quality_text}</div>
            </div>
            <div class="sleep-bar">
                <span class="deep" style="width:{deep_pct:.1f}%"></span>
                <span class="light" style="width:{light_pct:.1f}%"></span>
                <span class="rem" style="width:{rem_pct:.1f}%"></span>
                <span class="awake" style="width:{awake_pct:.1f}%"></span>
            </div>
            <div class="sleep-legend">
                <div class="legend-item"><div class="legend-dot deep"></div>深睡 {deep}分</div>
                <div class="legend-item"><div class="legend-dot light"></div>浅睡 {light}分</div>
                <div class="legend-item"><div class="legend-dot rem"></div>REM {rem}分</div>
                <div class="legend-item"><div class="legend-dot awake"></div>清醒 {awake}分</div>
            </div>
            <div class="sleep-hr">{hr_text}</div>
        </div>
    </div>"""


def _load_llm_analysis(date_str):
    """Load LLM-generated analysis sidecar if it exists."""
    analysis_file = DATA_DIR / f"{date_str}.analysis.json"
    if not analysis_file.exists():
        return None
    try:
        with open(analysis_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load LLM analysis: {e}")
        return None


def _load_historical_data(date_str, days=14):
    """Load previous N days of merged data for comparison."""
    history = []
    base_date = datetime.strptime(date_str, "%Y-%m-%d")
    for i in range(1, days + 1):
        d = (base_date - timedelta(days=i)).strftime("%Y-%m-%d")
        f = DATA_DIR / f"{d}.json"
        if not f.exists():
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                if data:
                    history.append(data)
        except Exception:
            continue
    return history


def _analyze_strength(train, agg, muscle_freq):
    items = []
    if not train.get("movements"):
        return items

    total_sets = sum(len(m["sets"]) for m in train["movements"])
    total_volume = sum(
        _num(s.get("weight")) * _num(s.get("reps"))
        for m in train["movements"] for s in m["sets"]
    )
    total_reps = sum(
        _num(s.get("reps"))
        for m in train["movements"] for s in m["sets"]
    )
    mov_count = len(train["movements"])

    items.append(
        f"今日<span class='highlight'>{_esc(train['title'])}</span>训练共 "
        f"<span class='highlight'>{mov_count} 个动作 {total_sets} 组</span>，"
        f"总容量约 <span class='highlight'>{total_volume / 1000:.1f} 吨</span>，"
        f"训练时长 {_fmt_hms(train['duration_minutes'])}。"
    )

    hist_volumes = [v["strength_volume"] for v in agg.values() if v["strength_volume"] > 0]
    if hist_volumes:
        pct_str, arrow, state = _trend_pct(total_volume, hist_volumes)
        trend = _trend_span(pct_str, arrow, state)
        avg_volume = sum(hist_volumes) / len(hist_volumes)
        if trend:
            items.append(
                f"总容量 {total_volume/1000:.1f} 吨，较 14 日单日均值 {avg_volume/1000:.1f} 吨 {trend}。"
            )
        elif total_volume > avg_volume * 1.15:
            items.append("总容量高于近期平均，训练刺激较强。")
        elif total_volume < avg_volume * 0.85:
            items.append("总容量低于近期平均，可适当加量。")
        else:
            items.append("总容量与近期平均基本持平，训练量适中。")

    max_weight = 0
    for movement in train["movements"]:
        weights = [_num(s.get("weight")) for s in movement["sets"] if _num(s.get("weight"))]
        mw = max(weights) if weights else 0
        max_weight = max(max_weight, mw)
        stable = len(set(weights)) == 1 and len(weights) >= 3
        if stable and mw:
            items.append(
                f"<span class='highlight'>{_esc(movement['name'])}</span>稳定在 "
                f"<span class='highlight'>{mw:.0f}kg</span> 做组，力量维持良好。"
            )
        elif mw:
            items.append(
                f"<span class='highlight'>{_esc(movement['name'])}</span>最大重量 "
                f"<span class='highlight'>{mw:.0f}kg</span>，可根据状态尝试渐进超负荷。"
            )

    if total_volume > 0 and total_reps > 0 and max_weight > 0:
        avg_weight = total_volume / total_reps
        intensity_ratio = max_weight / avg_weight if avg_weight else 0
        if intensity_ratio > 1.15 and total_sets >= 15:
            nature = "高强度综合训练"
        elif total_sets >= 15:
            nature = "增肌容量训练"
        elif intensity_ratio > 1.15:
            nature = "力量维持训练"
        else:
            nature = "恢复性/技术训练"
        items.append(f"本次训练性质判断为：<span class='highlight'>{nature}</span>。")

    group = _muscle_group(train.get("title", ""))
    if group:
        freq = muscle_freq.get(group, 0)
        if freq >= 3:
            items.append(
                f"<span class='warn'>近 7 天 {group} 部训练已达 {freq} 次，注意恢复，避免连续高强度刺激。</span>"
            )
        elif freq == 2:
            items.append(
                f"近 7 天 {group} 部训练 {freq} 次，建议下次训练轮换其他肌群。"
            )

    items.append("整体训练强度适中，建议关键动作逐步增加 2.5-5kg 或 1-2 次重复。")
    return items


def _analyze_cardio(category, activities, agg, lthr):
    items = []
    total_distance = sum(a["distance_meters"] for a in activities) / 1000
    total_duration = sum(a["duration_seconds"] for a in activities) / 60
    total_kcal = sum(a["calories"] for a in activities)

    items.append(
        f"今日{category}共 <span class='highlight'>{len(activities)} 场</span>，"
        f"总距离 <span class='highlight'>{total_distance:.2f} km</span>，"
        f"总时长 <span class='highlight'>{_fmt_hms(total_duration)}</span>，"
        f"消耗 <span class='highlight'>{total_kcal:.0f} 千卡</span>。"
    )

    hist_durations = [v["cardio_by_type"].get(category, 0) for v in agg.values()]
    hist_durations = [d for d in hist_durations if d > 0]
    if hist_durations and total_duration > 0:
        pct_str, arrow, state = _trend_pct(total_duration, hist_durations)
        trend = _trend_span(pct_str, arrow, state)
        avg_dur = sum(hist_durations) / len(hist_durations)
        if trend:
            items.append(
                f"{category}总时长较 14 日同类型日均 {avg_dur:.0f} 分钟 {trend}。"
            )

    if category in ("跑步", "越野跑") and lthr:
        recent_paces = []
        for v in agg.values():
            for a in v.get("__raw_activities", []):
                if SPORT_TYPE_CATEGORIES.get(a.get("sport_type")) in ("跑步", "越野跑") and a.get("distance_meters", 0) > 0:
                    dmin = a["duration_seconds"] / 60
                    dkm = a["distance_meters"] / 1000
                    p = _pace_to_seconds(_pace(dmin, dkm))
                    if p:
                        recent_paces.append(p)

        for a in activities:
            hr = a.get("avg_hr")
            duration = a["duration_seconds"] / 60
            distance = a["distance_meters"] / 1000
            pace = _pace(duration, distance)
            zone = _running_hr_zones(hr, lthr)
            if zone:
                items.append(f"{a['name']} 平均心率 {hr} bpm，处于{zone}；平均配速 {pace}。")
            else:
                items.append(f"{a['name']} 平均配速 {pace}。")

            cur_pace = _pace_to_seconds(pace)
            if cur_pace and len(recent_paces) >= 2:
                avg_pace = sum(recent_paces) / len(recent_paces)
                if cur_pace < avg_pace * 0.97:
                    items.append("配速较近期同类型平均更快，同样努力下速度能力提升。")
                elif cur_pace > avg_pace * 1.03:
                    items.append("配速较近期同类型平均偏慢，可能受疲劳、气温或地形影响。")
                else:
                    items.append("配速与近期同类型平均相当，状态稳定。")
    else:
        for a in activities:
            hr = a.get("avg_hr")
            duration = a["duration_seconds"] / 60
            distance = a["distance_meters"] / 1000
            pace = _pace(duration, distance)
            if hr:
                intensity = "低强度" if hr < 120 else "中等强度" if hr < 150 else "高强度"
                items.append(f"{a['name']} 平均心率 {hr} bpm，属于{intensity}有氧；平均配速 {pace}。")
    return items


def _analyze_metrics(m, agg):
    items = []
    hrv = _num(m.get("avg_sleep_hrv"))
    baseline = _num(m.get("baseline"))
    if hrv and baseline:
        pct_str, arrow, state = _trend_pct(hrv, [v.get("hrv") for v in agg.values() if v.get("hrv")])
        trend = _trend_span(pct_str, arrow, state)
        if hrv < baseline * 0.9:
            items.append(
                f"HRV 为 <span class='highlight'>{hrv} ms</span>，低于基线 {baseline} ms，提示恢复状态一般。{trend}"
            )
        else:
            items.append(
                f"HRV 为 <span class='highlight'>{hrv} ms</span>，接近或高于基线，恢复状态良好。{trend}"
            )

    rhr = _num(m.get("rhr"))
    if rhr:
        pct_str, arrow, state = _trend_pct(rhr, [v.get("rhr") for v in agg.values() if v.get("rhr")], lower_better=True)
        trend = _trend_span(pct_str, arrow, state)
        items.append(
            f"静息心率 <span class='highlight'>{rhr} bpm</span>，"
            f"{'较低，心肺基础较好' if rhr < 55 else '正常范围'}。{trend}"
        )

    load = m.get("training_load")
    ratio = m.get("training_load_ratio")
    if load and ratio:
        state_label = "维持区间" if 0.8 <= ratio <= 1.3 else "负荷偏高" if ratio > 1.3 else "负荷较低"
        items.append(f"训练负荷 {load}，负荷比 {ratio}，处于<span class='highlight'>{state_label}</span>。")

    tired = m.get("tired_rate")
    stamina = m.get("stamina_level")
    if tired is not None and stamina:
        items.append(
            f"疲劳度 {tired}%，体能水平 {stamina}，整体处于可训练状态，需结合睡眠判断恢复质量。"
        )

    vo2max = m.get("vo2max")
    if vo2max:
        items.append(f"最大摄氧量 <span class='highlight'>{vo2max}</span>，可作为有氧能力长期跟踪指标。")

    ati = m.get("ati")
    cti = m.get("cti")
    if ati and cti:
        items.append(f"有氧能力 ATI {ati}，无氧能力 CTI {cti}。")

    return items


def _analyze_sleep(s, agg):
    total = s.get("total_duration_minutes", 0)
    phases = s.get("phases") or {}
    deep = phases.get("deep_minutes", 0) or 0
    light = phases.get("light_minutes", 0) or 0
    rem = phases.get("rem_minutes", 0) or 0
    awake = phases.get("awake_minutes", 0) or 0
    nap = phases.get("nap_minutes", 0) or 0
    # Exclude nap from phase-ratio denominator; nap is already part of total sleep.
    total_phase = sum(p for k, p in phases.items() if p and k != "nap_minutes") or 1
    deep_pct = deep / total_phase * 100
    awake_pct = awake / total_phase * 100

    items = []
    if total < 360:
        items.append(f"睡眠总时长 <span class='warn'>{_fmt_duration(total)}</span>，明显偏短，可能影响恢复。")
    else:
        items.append(f"睡眠总时长 <span class='good'>{_fmt_duration(total)}</span>，时长充足。")

    hist_sleep = [v["sleep_minutes"] for v in agg.values() if v["sleep_minutes"] > 0]
    if hist_sleep:
        pct_str, arrow, state = _trend_pct(total, hist_sleep)
        trend = _trend_span(pct_str, arrow, state)
        avg_sleep = sum(hist_sleep) / len(hist_sleep)
        if trend:
            items.append(
                f"睡眠时长较 14 日均值 {avg_sleep:.0f} 分钟 {trend}，注意调整作息。"
            )

    items.append(
        f"深睡占比约 <span class='highlight'>{deep_pct:.0f}%</span>，"
        f"{'比例正常' if 15 <= deep_pct <= 35 else '需关注'}；清醒占比 {awake_pct:.0f}%。"
    )
    if nap:
        items.append(f"含零星小睡 <span class='highlight'>{nap} 分钟</span>。")
    avg_hr = s.get("avg_hr")
    if avg_hr:
        items.append(
            f"睡眠平均心率 {avg_hr} bpm，夜间心率"
            f"{'平稳' if 45 <= avg_hr <= 60 else '偏高/偏低，需关注'}。"
        )
    items.append("建议根据睡眠情况调整今日训练强度，睡眠不足时避免高强度训练。")
    return items


def _recovery_advice(merged_data, agg):
    items = []
    strength_count = len(merged_data.get("strength_training", []))
    cardio_count = len(merged_data.get("cardio_activities", []))
    total_load = merged_data.get("summary", {}).get("total_load", 0)

    hist_loads = [v["total_load"] for v in agg.values() if v["total_load"] > 0]
    if hist_loads:
        pct_str, arrow, state = _trend_pct(total_load, hist_loads)
        trend = _trend_span(pct_str, arrow, state)
        avg_load = sum(hist_loads) / len(hist_loads)
        if trend:
            items.append(
                f"今日综合负荷 {total_load}，较 14 日均值 {avg_load:.0f} {trend}。"
            )
        elif total_load > avg_load * 1.3:
            items.append(
                f"今日综合负荷 <span class='warn'>{total_load}</span> 明显高于近期平均 {avg_load:.0f}，建议明天安排恢复性训练或休息。"
            )
        elif total_load < avg_load * 0.7:
            items.append(
                f"今日综合负荷 <span class='good'>{total_load}</span> 低于近期平均 {avg_load:.0f}，状态允许时可适当增加训练量。"
            )
        else:
            items.append(
                f"今日综合负荷 {total_load} 与近期平均 {avg_load:.0f} 相当，维持当前节奏即可。"
            )

    sleep = merged_data.get("sleep_data") or {}
    sleep_total = sleep.get("total_duration_minutes", 0)
    if sleep_total and sleep_total < 360:
        items.append("睡眠不足，明天如训练建议选择低强度有氧或技术练习，避免大重量力量训练。")
    elif strength_count and cardio_count:
        items.append("今日力量与有氧均有安排，明天建议以主动恢复、拉伸或低强度有氧为主。")
    elif strength_count >= 2:
        items.append("今日安排了多场力量训练，注意肌群轮换，给目标肌群 48 小时恢复时间。")
    elif cardio_count >= 2:
        items.append("今日有氧场次较多，注意补充糖原与水分，避免连续高强度有氧造成过度训练。")
    else:
        items.append("今日训练量适中，保持规律作息即可。")

    return items


def _generate_score_html(merged_data, date_str):
    """Render recovery score and readiness badge at top of analysis section."""
    m = merged_data.get("daily_metrics") or {}
    s = merged_data.get("sleep_data") or {}
    if not m and not s:
        return ""

    score = _recovery_score(m, s)
    label, state = _readiness_label(score)

    contributors = []
    hrv = _num(m.get("avg_sleep_hrv"))
    baseline = _num(m.get("baseline"))
    if hrv and baseline:
        contributors.append(("HRV", f"{hrv}/{baseline}"))
    rhr = _num(m.get("rhr"))
    if rhr:
        contributors.append(("RHR", f"{rhr} bpm"))
    sleep_min = _num(s.get("total_duration_minutes"))
    if sleep_min:
        contributors.append(("睡眠", _fmt_duration(sleep_min)))
    tired = m.get("tired_rate")
    if tired is not None:
        contributors.append(("疲劳", f"{tired}%"))

    metric_html = "".join(
        f'<div class="metric"><span class="label">{k}:</span><span class="value">{v}</span></div>'
        for k, v in contributors
    )

    return f'''
    <div class="score-card">
        <div class="score-left">
            <div class="score-title">恢复评分</div>
            <div class="score-desc">基于 HRV、静息心率、睡眠、疲劳度与负荷比综合计算</div>
            <div class="metric-row">{metric_html}</div>
        </div>
        <div class="score-right">
            <div class="score-value {state}">{score}</div>
            <div class="readiness-badge {state}">{label}</div>
        </div>
    </div>'''


def generate_analysis_html(merged_data, date_str):
    """Generate analysis section. Prefer LLM sidecar if present, fallback to rule-based."""
    llm = _load_llm_analysis(date_str)
    if llm:
        sections = llm.get("sections", [])
        if sections:
            cards_html = ""
            for sec in sections:
                title = sec.get("title", "分析")
                li_html = "".join(f"<li>{_esc(item)}</li>" for item in sec.get("items", []))
                cards_html += f'''
                <div class="analysis-card">
                    <h3>{_esc(title)}</h3>
                    <ul>{li_html}</ul>
                </div>'''
            return f'''
            <div class="section fold-card open" data-fold="section">
                <div class="fold-header" onclick="toggleCard(this.parentElement)">
                    <div class="fold-title">
                        <span class="icon-emoji">📊</span>
                        <span class="text">数据分析</span>
                        <span class="badge">LLM 分析</span>
                    </div>
                    <div class="fold-arrow">▼</div>
                </div>
                <div class="fold-body">{cards_html}</div>
            </div>'''

    history = _load_historical_data(date_str, days=14)
    agg = _aggregate_history(history)
    parts = []

    m = merged_data.get("daily_metrics") or {}
    s = merged_data.get("sleep_data") or {}
    if m or s:
        score = _recovery_score(m, s)
        label, state = _readiness_label(score)
        parts.append(("恢复状态", [
            f"综合恢复评分 <span class='highlight'>{score}</span>，今日训练建议：<span class='{state}'>{label}</span>。"
        ]))

    if merged_data["strength_training"]:
        base_date = datetime.strptime(date_str, "%Y-%m-%d")
        recent_7 = [h for h in history if (base_date - datetime.strptime(h.get("date", date_str), "%Y-%m-%d")).days <= 7]
        muscle_freq = {}
        for h in recent_7:
            for t in h.get("strength_training", []):
                g = _muscle_group(t.get("title", ""))
                if g:
                    muscle_freq[g] = muscle_freq.get(g, 0) + 1
        for t in merged_data["strength_training"]:
            g = _muscle_group(t.get("title", ""))
            if g:
                muscle_freq[g] = muscle_freq.get(g, 0) + 1

        for train in merged_data["strength_training"]:
            items = _analyze_strength(train, agg, muscle_freq)
            if items:
                parts.append(("力量训练分析", items))

    groups = {}
    for activity in merged_data["cardio_activities"]:
        sport_type = activity.get("sport_type")
        category = SPORT_TYPE_CATEGORIES.get(sport_type, "其他运动")
        groups.setdefault(category, []).append(activity)

    lthr = _num(m.get("lthr"))
    for category, activities in groups.items():
        items = _analyze_cardio(category, activities, agg, lthr)
        if items:
            parts.append((f"{category}分析", items))

    if m:
        items = _analyze_metrics(m, agg)
        if items:
            parts.append(("生理指标分析", items))

    if s:
        items = _analyze_sleep(s, agg)
        if items:
            parts.append(("睡眠分析", items))

    recovery_items = _recovery_advice(merged_data, agg)
    if recovery_items:
        parts.append(("恢复建议", recovery_items))

    if not parts:
        return ""

    cards_html = ""
    for title, items in parts:
        li_html = "".join(f"<li>{item}</li>" for item in items)
        cards_html += f'''
        <div class="analysis-card">
            <h3>{title}</h3>
            <ul>{li_html}</ul>
        </div>'''

    return f'''
    <div class="section fold-card open" data-fold="section">
        <div class="fold-header" onclick="toggleCard(this.parentElement)">
            <div class="fold-title">
                <span class="icon-emoji">📊</span>
                <span class="text">数据分析</span>
                <span class="badge">{len(parts)} 项分析</span>
            </div>
            <div class="fold-arrow">▼</div>
        </div>
        <div class="fold-body">{cards_html}</div>
    </div>'''



def save_html_report(html_content, date_str):
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    html_file = PUBLIC_DIR / f"{date_str}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[OK] HTML report generated: {html_file}")
    return html_file


def update_summary(date_str):
    summary_file = DATA_DIR / "summary.json"
    summary_data = {"dates": []}

    if DATA_DIR.exists():
        for json_file in sorted(DATA_DIR.glob("*.json")):
            if json_file.name == "summary.json" or json_file.name.endswith(".analysis.json"):
                continue
            date = json_file.stem
            if date not in summary_data["dates"]:
                summary_data["dates"].append(date)

    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Summary updated: {summary_file}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate training analysis report")
    parser.add_argument("--date", type=str, help="Target date (format: YYYY-MM-DD)")
    args = parser.parse_args()

    date_str = args.date if args.date else datetime.now().strftime("%Y-%m-%d")

    print(f"\n[START] Generating training report for {date_str}...\n")
    print("[INFO] Fetching Xunji data...")
    xunji_data = fetch_xunji_data(date_str)
    print("[INFO] Fetching Coros data...")
    coros_data = fetch_coros_data(date_str)
    print("[INFO] Merging data...")
    merged_data = merge_training_data(xunji_data, coros_data, date_str)
    save_json_data(merged_data, date_str)
    print("[INFO] Generating HTML report...")
    html_content = generate_html_report(merged_data, date_str)
    save_html_report(html_content, date_str)
    update_summary(date_str)
    print(f"\n[DONE] Report generation completed!\n")
    print(f"JSON: data/daily/{date_str}.json")
    print(f"HTML: public/daily/{date_str}.html")


if __name__ == "__main__":
    main()
