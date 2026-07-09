#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""训记训练数据 Open API 客户端。

只从环境变量 XUNJI_API_KEY 读取鉴权信息，不保存 key。
支持读取、写回、动作名表查询，以及按 datestr 缓存和限频。
"""

import argparse
import gzip
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib import request, error as urllib_error

BASE_URL = "https://trains.xunjiapp.cn"
READ_ENDPOINT = f"{BASE_URL}/api_trains_for_llm_v2"
UPSERT_ENDPOINT = f"{BASE_URL}/api_upsert_trains_for_llm_v2"
MOVEMENTS_URL = "https://raw.githubusercontent.com/Foveluy/Xunji-movements/main/README.md"

# 限频间隔（秒）
RATE_LIMITS = {
    "read": 15,
    "read_full": 30,
    "upsert": 45,
}


def skill_dir() -> Path:
    """返回 skill 根目录（脚本位于 scripts/ 下）。"""
    return Path(__file__).resolve().parent.parent


def cache_dir() -> Path:
    d = skill_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def meta_path() -> Path:
    return cache_dir() / "_meta.json"


def load_meta() -> dict:
    p = meta_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_meta(meta: dict) -> None:
    meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_env_from_dotenv() -> None:
    """尝试从项目根目录的 .env 文件加载 XUNJI_API_KEY。"""
    env_file = skill_dir().parent.parent / ".env"
    if not env_file.exists():
        env_file = Path.cwd() / ".env"
    if not env_file.exists():
        return
    try:
        content = env_file.read_text(encoding="utf-8")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "XUNJI_API_KEY" and value and "XUNJI_API_KEY" not in os.environ:
            os.environ[key] = value


def get_api_key() -> str:
    load_env_from_dotenv()
    key = os.environ.get("XUNJI_API_KEY", "").strip()
    if not key:
        print("未找到 XUNJI_API_KEY 环境变量。", file=sys.stderr)
        print("请在 .env 文件中设置 XUNJI_API_KEY=xjllm_...，或输入临时 key（仅本次会话有效）：", file=sys.stderr)
        try:
            key = input().strip()
        except EOFError:
            key = ""
        if not key:
            print("错误：缺少 API key，无法继续。", file=sys.stderr)
            sys.exit(1)
    return key


def make_headers(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "xunji-api-skill/1.0",
    }


def decode_text(raw: bytes) -> str:
    """尝试 UTF-8 解码，失败则回退到 GBK。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def http_post(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(decode_text(raw))
    except urllib_error.HTTPError as e:
        body = e.read()
        try:
            encoding = e.headers.get("Content-Encoding", "").lower()
            if encoding == "gzip":
                body = gzip.decompress(body)
            return json.loads(decode_text(body))
        except (json.JSONDecodeError, OSError):
            return {"success": False, "message": f"HTTP {e.code}: {decode_text(body)}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def http_get(url: str) -> str:
    req = request.Request(url, headers={"User-Agent": "xunji-api-skill/1.0"}, method="GET")
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            return decode_text(raw)
    except Exception as e:
        return ""


def check_rate_limit(action: str, datestr: str) -> None:
    meta = load_meta()
    key = f"last_{action}_{datestr}"
    last = meta.get(key, 0)
    limit = RATE_LIMITS.get(action, 15)
    elapsed = time.time() - last
    if elapsed < limit:
        wait = limit - elapsed
        print(f"触发限频：{action} 操作需等待 {wait:.1f} 秒。", file=sys.stderr)
        time.sleep(wait)


def update_rate_limit(action: str, datestr: str) -> None:
    meta = load_meta()
    meta[f"last_{action}_{datestr}"] = time.time()
    save_meta(meta)


def cache_file(datestr: str, full: bool) -> Path:
    suffix = "-full" if full else ""
    return cache_dir() / f"{datestr}{suffix}.json"


def has_success_result(result: dict) -> bool:
    """接口成功返回可能只有 res，不一定有 success 字段。"""
    return bool(result.get("success")) or "res" in result


def cmd_read(args: argparse.Namespace) -> int:
    key = get_api_key()
    datestr = args.date
    full = bool(args.full)
    action = "read_full" if full else "read"

    cache = cache_file(datestr, full)
    if cache.exists():
        # 即使缓存存在，也检查是否过期？这里按“同一天不要重复请求”处理：
        # 如果缓存存在，直接返回，不重复请求。
        print(cache.read_text(encoding="utf-8"))
        return 0

    check_rate_limit(action, datestr)
    payload = {
        "schema_version": "train_open_api_v2",
        "datestr": datestr,
        "include_full_data": full,
    }
    result = http_post(READ_ENDPOINT, payload, make_headers(key))
    # 读取接口返回 {"res": {...}}，不一定有 success 字段
    if has_success_result(result):
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        update_rate_limit(action, datestr)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


def cmd_upsert(args: argparse.Namespace) -> int:
    key = get_api_key()
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"错误：文件不存在 {file_path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"错误：JSON 解析失败 {e}", file=sys.stderr)
        return 1

    include_full_data = bool(args.full)
    if isinstance(data, dict):
        include_full_data = include_full_data or bool(data.get("include_full_data"))

    # 规范化 payload
    if isinstance(data, list):
        res = data
    elif isinstance(data, dict):
        res = data.get("res", data.get("trains", data))
        if isinstance(res, dict):
            res = res.get("trains", res)
    else:
        res = data
    if not isinstance(res, list):
        print("错误：res 必须是训练数组或包含 trains 字段的对象。", file=sys.stderr)
        return 1

    # 检查同一天
    datestrs = {t.get("datestr") for t in res if t.get("datestr")}
    if len(datestrs) > 1:
        print("错误：单次写回的训练必须属于同一天。", file=sys.stderr)
        return 1
    datestr = next(iter(datestrs)) if datestrs else "unknown"

    # 检查数量限制
    if len(res) > 4:
        print("错误：单次最多写回 4 条训练。", file=sys.stderr)
        return 1
    for train in res:
        movements = train.get("movements", [])
        if len(movements) > 15:
            print(f"错误：训练 '{train.get('title', '未知')}' 超过 15 个动作。", file=sys.stderr)
            return 1
        for movement in movements:
            sets = movement.get("sets", [])
            if len(sets) > 20:
                print(f"错误：动作 '{movement.get('name', '未知')}' 超过 20 组。", file=sys.stderr)
                return 1

    check_rate_limit("upsert", datestr)
    payload = {
        "schema_version": "train_open_api_v2",
        "client_request_id": str(uuid.uuid4()),
        "dry_run": bool(args.dry_run),
        "include_full_data": include_full_data,
        "res": res,
    }
    result = http_post(UPSERT_ENDPOINT, payload, make_headers(key))
    if has_success_result(result):
        update_rate_limit("upsert", datestr)
        # 写回成功后覆盖缓存
        if not args.dry_run and datestr != "unknown":
            cache = cache_file(datestr, include_full_data)
            cache_payload = result if "res" in result else {"res": res}
            cache.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


def cmd_movements(args: argparse.Namespace) -> int:
    cache = cache_dir() / "movements.md"
    if cache.exists():
        print(cache.read_text(encoding="utf-8"))
        return 0
    text = http_get(MOVEMENTS_URL)
    if not text:
        print("错误：无法获取动作名表。", file=sys.stderr)
        return 1
    cache.write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    # Windows 上默认 stdout 可能是 cp936，强制使用 utf-8 以便正确输出中文
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(description="训记训练数据 Open API 客户端")
    sub = parser.add_subparsers(dest="command", required=True)

    read_parser = sub.add_parser("read", help="读取某日期训练")
    read_parser.add_argument("--date", required=True, help="日期，格式 2026-04-02")
    read_parser.add_argument("--full", action="store_true", help="返回完整数据")

    upsert_parser = sub.add_parser("upsert", help="写回训练")
    upsert_parser.add_argument("--file", required=True, help="待写回的 JSON 文件路径")
    upsert_parser.add_argument("--dry-run", action="store_true", help="仅 dry-run，不真正写回")
    upsert_parser.add_argument(
        "--full",
        "--include-full-data",
        action="store_true",
        dest="full",
        help="写回请求传 include_full_data=true，用于 RPE、动作完成难度或完整标准化返回",
    )

    movements_parser = sub.add_parser("movements", help="获取标准动作中文名表")

    args = parser.parse_args()
    if args.command == "read":
        return cmd_read(args)
    if args.command == "upsert":
        return cmd_upsert(args)
    if args.command == "movements":
        return cmd_movements(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
