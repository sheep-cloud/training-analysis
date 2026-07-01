# Training Analysis Platform

每日训练数据自动分析与展示平台。整合训记 App (力量训练精确数据) 与高驰 App (有氧运动与生理指标)，生成美观的 HTML 报告。

## 功能特性

✅ **数据融合** - 自动合并训记 + 高驰的训练数据  
✅ **自动生成** - 每日报告自动生成为 HTML 和 JSON  
✅ **版本管理** - 历史记录保存在 Git  
✅ **外网访问** - 通过 GitHub Pages 发布  
✅ **渐进式** - 从日报告逐步扩展到周月分析  

## 项目结构

```
training-analysis/
├── scripts/
│   └── generate_report.py        # 核心脚本：数据获取 + 融合 + 生成报告
├── data/
│   └── daily/                    # 每日 JSON 备份
│       ├── summary.json
│       └── .gitkeep
├── public/
│   └── daily/                    # 每日 HTML 报告
│       ├── index.html            # 首页（待实现）
│       └── .gitkeep
├── run_daily.sh                  # Linux/Mac 自动化脚本
├── run_daily.ps1                 # Windows 自动化脚本
└── README.md
```

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/sheep-cloud/training-analysis.git
cd training-analysis
```

### 2. 配置环境变量

创建 `.env` 文件，填入训记 API Key：

```
XUNJI_API_KEY=xjllm_...
```

### 3. 生成今天的报告

#### Windows (PowerShell)
```powershell
.\run_daily.ps1
# 或指定日期
.\run_daily.ps1 2026-06-30
```

#### Linux/Mac
```bash
bash run_daily.sh
# 或指定日期
bash run_daily.sh 2026-06-30
```

#### 直接运行 Python
```bash
python scripts/generate_report.py
# 或指定日期
python scripts/generate_report.py --date 2026-06-30
```

### 4. 访问报告

生成完成后，文件位置：
- **JSON 数据**: `data/daily/2026-06-30.json`
- **HTML 报告**: `public/daily/2026-06-30.html`

推送到 GitHub 后，通过 GitHub Pages 访问：
```
https://sheep-cloud.github.io/training-analysis/public/daily/2026-06-30.html
```

## 数据源

### 训记 App (xunji-api skill)
- 力量训练数据（完全精确）
- 每个动作：重量、组数、次数、RPE
- 支持超级组、递减组等高级结构

### 高驰 App (Coros MCP)
- 有氧运动数据（跑步、飞盘等）
- 生理指标（HRV、训练负荷、心率等）
- 睡眠数据（深睡、浅睡、REM）

## 部署（GitHub Pages）

### 第一步：开启 GitHub Pages

1. 打开仓库 https://github.com/sheep-cloud/training-analysis
2. 进入 **Settings → Pages**
3. **Source** 选择 `Deploy from a branch`
4. Branch 选择 `master`，目录选择 `/(root)`
5. 点击 **Save**

### 第二步：自动部署

每次 `git push` 到 GitHub，GitHub Pages 会自动重新构建并部署。

1-2 分钟后即可通过以下地址访问：
```
https://sheep-cloud.github.io/training-analysis/public/daily/2026-06-30.html
```

## 开发路线图

### 阶段 1（已完成）
- [x] 项目结构
- [x] 数据融合脚本
- [x] HTML 报告生成
- [x] JSON 备份

### 阶段 2（进行中）
- [ ] 首页索引（列表所有历史记录）
- [ ] 周总结
- [ ] 月分析

### 阶段 3（计划中）
- [ ] 训练趋势图表（Chart.js）
- [ ] 体能指数分析
- [ ] 恢复状态预测
- [ ] 移动端优化

## 常见问题

### Q: 如何定时自动生成？
**A:**
- **Windows**: 用任务计划程序定时运行 `run_daily.ps1`
- **Mac/Linux**: 用 crontab 定时运行 `run_daily.sh`
- **云端**: GitHub Actions（待配置）

### Q: 力量训练数据为空？
**A:** 检查 `.env` 里 `XUNJI_API_KEY` 是否存在且有效（格式 `xjllm_...`）。

### Q: 数据不更新？
**A:** 检查：
1. `XUNJI_API_KEY` 是否有效
2. Coros 认证状态
3. 日期格式是否正确 (YYYY-MM-DD)
4. 清除训记缓存：删除 `.claude/skills/xunji-api/cache/` 目录

### Q: 如何访问旧报告？
**A:** 访问 `https://sheep-cloud.github.io/training-analysis/public/daily/日期.html`

## 技术栈

- **数据源**: xunji-api skill + Coros MCP
- **处理**: Python 3（纯标准库，无第三方依赖）
- **存储**: Git (GitHub)
- **部署**: GitHub Pages
- **展示**: HTML + CSS + JSON

## 许可证

MIT
