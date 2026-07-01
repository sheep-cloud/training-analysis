# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

每日训练数据分析平台。核心是单个 Python 脚本,把两个数据源(力量训练 + 有氧/生理指标)融合成结构化 JSON 与 HTML 报告,按日期归档,推送到 GitHub 后由 Vercel 部署展示。

纯 Python 3 标准库,**无第三方依赖、无构建步骤、无测试套件、无 lint 配置**。

## 常用命令

```bash
# 生成今天的报告
python scripts/generate_report.py

# 生成指定日期的报告
python scripts/generate_report.py --date 2026-06-30
```

自动化包装脚本(生成报告 + git add + commit + push 一步完成):

```powershell
.\run_daily.ps1 [YYYY-MM-DD]      # Windows,默认今天
```

```bash
bash run_daily.sh [YYYY-MM-DD]    # Linux/Mac,默认今天
```

## 架构

全部逻辑在 `scripts/generate_report.py` 的一条线性管线里(`main()` 顺序调用):

1. **`fetch_xunji_data(date)`** — 通过 `subprocess` 调用外部 skill 脚本 `.claude/skills/xunji-api/scripts/fetch_trains.py`,取力量训练数据,只取返回 JSON 的 `res` 字段。
2. **`fetch_coros_data(date)`** — 取有氧运动 + 生理/睡眠指标。
3. **`merge_training_data(xunji, coros, date)`** — 融合两源:训记 → `strength_training`,高驰 → `cardio_activities` / `daily_metrics` / `sleep_data`,并累加 `summary`。**这是唯一定义输出数据结构的地方**,改字段先从这里入手。
4. **`save_json_data()`** → `data/daily/{date}.json`
5. **`generate_html_report()`** → `public/daily/{date}.html`。纯 f-string 字符串拼接,无模板引擎;表格部分拆到 `generate_strength_html()` / `generate_cardio_html()` 两个子函数。
6. **`update_summary()`** — 扫描 `data/daily/` 下所有 json(除 `summary.json`),重建 `summary.json` 的日期索引,供首页列表使用。

### 两个数据源的分工
- **训记 App**(xunji-api skill,经 subprocess 调用):精确力量训练 —— 每个动作的重量 / 组数 / 次数 / RPE。时间戳为**毫秒**(merge 时 `(end-start)//60000` 转分钟)。
- **高驰 App**(Coros MCP):有氧运动 + HRV / 训练负荷 / 心率 / 睡眠。

### 当前状态(重要)
两个数据源接入进度不同,别再当成"全是空壳":

- **训记(xunji)链路已打通**:`fetch_trains.py` 已存在,`fetch_xunji_data` 经 subprocess 调它取真实数据。**前提是 `.env` 里有 `XUNJI_API_KEY`**(格式 `xjllm_...`)。缺 key 时该脚本会交互式 `input()` 索取,但在 `capture_output=True` 的 subprocess 下 stdin 非 TTY → `EOFError` → 脚本退出 → `fetch_xunji_data` 静默返回 `None`,力量训练数据变空。**"力量训练为空"的第一嫌疑就是 key 缺失或失效**,而非代码问题。
- **高驰(coros)链路尚未接代码**:Coros MCP 已在 `.mcp.json` 配好、`mcp__coros__*` 工具可用,但 `fetch_coros_data`(`generate_report.py:52-69`)仍硬编码返回空 mock,没调用任何 MCP 工具。这是当前**唯一真正的接入缺口**。接入时要把 MCP 工具(`list_activities` / `get_daily_metrics` / `get_sleep_data` 等)的返回适配成 `merge_training_data` 期望的形状:`{"activities": [...], "daily_metrics": {...}, "sleep_data": {...}}`(注意 merge 读的 key 是 `activities`,不是输出字段名 `cardio_activities`)。睡眠分期数据还需额外的 mobile 认证(`authenticate_coros_mobile`)。
- 净效果:报告里力量训练可能有数据,有氧 / 生理 / 睡眠目前恒为空(参见 `data/daily/2026-06-30.json`)。

## 注意事项

- **分支不一致**:仓库当前分支为 `master`,但 `run_daily.{sh,ps1}` 硬编码 `git push origin main`。直接运行会推错分支或失败,改脚本或建分支前先确认目标分支。
- **.gitignore 与提交冲突**:`.gitignore` 忽略 `data/daily/*.json`(仅保留 `.gitkeep`),而 `run_daily` 脚本与 README 又要 `git add data/daily/{date}.json`。对**新日期**的 json,不带 `-f` 的 `git add` 会被忽略规则拒绝。
- **xunji skill 自带按日缓存**:`fetch_trains.py` 把结果缓存在 `.claude/skills/xunji-api/cache/`(按 `datestr`),同一天重复生成会命中缓存、不重新请求接口。若在训记 App 里改了数据但报告没更新,先清该目录再重跑。
- **部署链路**:`git push` 到 GitHub → Vercel 自动构建(无需额外配置),报告经 `public/daily/{date}.html` 直接访问。
- README 只保留 `README.md`，内容已更新为正确的项目文档。
