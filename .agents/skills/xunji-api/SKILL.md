---
name: xunji-api
description: 读取、整理、导入、导出或写回训记（Xunji）训练数据。输出为 Markdown 表格，写回前需确认。
trigger: 当用户提到“训记”“Xunji”“训练数据”“训练记录”“训练日志”“workout”“导入/导出/整理/写回训练”等意图时触发。即使只问“帮我看看今天的训练”“整理一下最近的健身记录”“把这条训练写回训记”，只要上下文涉及训记 App 的训练数据，也应调用本 skill。
---

# 训记训练数据 Open API Skill

## 原则

- 只在用户明确要求读取、整理或写回训练数据时调用接口。
- 写回前必须先展示变更摘要，并等待用户确认。
- 按 `datestr` 缓存读取结果；同一天不要重复请求。

## 鉴权

- API Key 从环境变量 `XUNJI_API_KEY` 读取，格式如 `xjllm_...`。
- 也兼容请求头 `x-api-key`。
- 不要把 Key 写入日志、skill 文件、输出文件或展示给第三方。
- 如果环境变量未设置，提示用户在 `.env` 文件中设置 `XUNJI_API_KEY` 或临时粘贴（仅本次会话使用，不保存）。

## 接口

- Base URL: `https://trains.xunjiapp.cn`
- 读取训练: `POST /api_trains_for_llm_v2`
- 写回训练: `POST /api_upsert_trains_for_llm_v2`
- 成功时 `success === true`，核心数据在 `res`。
- 标准动作中文名参考: `https://github.com/Foveluy/Xunji-movements`

## 读取训练

```http
POST https://trains.xunjiapp.cn/api_trains_for_llm_v2
Authorization: Bearer <XUNJI_API_KEY>
Content-Type: application/json

{
  "schema_version": "train_open_api_v2",
  "datestr": "2026-04-02",
  "include_full_data": false
}
```

- 默认 `include_full_data: false`，只返回轻量数据，接近 v1。
- 需要未打勾组、RPE、备注、完成感受、左右侧重量、实练秒数或每组休息秒数时，传 `include_full_data: true`。
- 有氧、计时、Tabata、苹果健康等记录型动作会在 `sets[].metrics` 返回 distance/kcal/calories/workoutTime/avgHeartRate/maxHeartRate 等摘要指标。
- 苹果健康训练的 `name` 返回运动类型，例如 `Running`；老数据会尽量从训练标题推断。
- 超级组/递减组会在 `sets[].items[]` 返回子动作；每个子项的 `set` 里有 weight/unit/reps/time/metrics。
- 返回里的训练在 `res.trains`；写回旧训练时保留 `localid`、`start`、`end`。
- 动作不会暴露内部 key；需要标准动作名时读取 GitHub 动作名表。

### 读取执行方式

优先使用脚本：

```bash
python .Codex/skills/xunji-api/scripts/fetch_trains.py read --date 2026-04-02
python .Codex/skills/xunji-api/scripts/fetch_trains.py read --date 2026-04-02 --full
```

脚本会自动处理鉴权、限频和缓存。如果缓存存在且未过期，直接返回缓存。

## 整理输出

读取到训练数据后，默认以 **Markdown 表格** 展示给用户。表格应包含：

- 训练标题
- 动作名
- 每组：weight + unit、reps、time、done 状态
- 完整模式下额外展示：RPE、备注、左右侧重量、实练秒数、组间休息秒数
- 记录型动作展示 metrics（distance/kcal/workoutTime/心率等）

如果用户要求导出为文件，可保存为 JSON 或 CSV，但默认用 Markdown 表格。

## 写回训练

```http
POST https://trains.xunjiapp.cn/api_upsert_trains_for_llm_v2
Authorization: Bearer <XUNJI_API_KEY>
Content-Type: application/json

{
  "schema_version": "train_open_api_v2",
  "client_request_id": "unique-id-from-agent",
  "dry_run": false,
  "include_full_data": false,
  "res": [
    {
      "datestr": "2026-04-02",
      "localid": 123456,
      "title": "胸部训练",
      "start": 1744010000000,
      "end": 1744013600000,
      "movements": [
        { "name": "杠铃卧推", "sets": [
          { "done": true, "weight": "60", "unit": "kg", "reps": "10" }
        ] }
      ]
    }
  ]
}
```

### 写回规则

- 写回动作只传中文 `name`，不要传 `key`；服务端会按中文名查找并回填内部 key。
- 不确定中文名时，先读取 `https://github.com/Foveluy/Xunji-movements`，只从表里的中文名里选择。
- `res` 可以是训练数组，也可以是 `{ "trains": [...] }`；单次最多 4 条训练，且必须属于同一天。
- 每条训练最多 15 个动作；每个动作最多 20 组，超过会被服务端拒绝。
- 有 `localid` 时更新原训练；没有 `localid` 时新建训练；不要因为列表里缺少旧训练就删除旧训练。
- 更新旧训练时保留 `localid`、`start`、`end`，除非用户明确要改时间。
- 组至少包含 `weight`/`weight_kg`、`reps`、`time`/`duration_s`、`selfWeight` 之一。
- 未完成组用 `done: false`；不要把完整模式读到的未完成组擅自删掉。
- 写回成功后，用服务端返回的标准化 `res` 覆盖缓存。

### 写回执行方式

1. 把待写回的数据保存为 JSON 文件。
2. 先执行 dry-run：
   ```bash
   python .Codex/skills/xunji-api/scripts/fetch_trains.py upsert --file trains.json --dry-run
   ```
3. 向用户展示变更摘要（新增/更新/删除的训练、动作、组变化）。
4. 等待用户明确确认后再执行真实写回：
   ```bash
   python .Codex/skills/xunji-api/scripts/fetch_trains.py upsert --file trains.json
   ```

## 限频与错误

- 同一用户同一训练日：默认读取 15 秒一次，`include_full_data: true` 读取 30 秒一次，写回 45 秒一次；`too frequent` 时等待提示的 retry 时间。
- 脚本会自动记录并遵守这些限频规则，必要时提示用户等待。
- 不确定动作名时不要编造；让用户确认中文动作名后再写回。
- `apikey missing` / `apikey invalid`: 让用户回 App 复制或重新申请 Key。
- `仅VIP可用`: 当前账号需要会员权限。

## 动作名表

标准动作中文名表在 `https://github.com/Foveluy/Xunji-movements`。可以通过脚本获取：

```bash
python .Codex/skills/xunji-api/scripts/fetch_trains.py movements
```

不确定动作名时，必须先查表，只从表中选择。
