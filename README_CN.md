# Training Analysis Platform

每日训练数据自动分析与展示平台。整合训记 App (力量训练精确数据) 与高驰 App (有氧运动与生理指标)，生成美观的 HTML 报告。

## 功能特性

✅ **数据融合** - 自动合并训记 + 高驰的训练数据  
✅ **自动生成** - 每日报告自动生成为 HTML 和 JSON  
✅ **版本管理** - 历史记录保存在 Git  
✅ **外网访问** - 通过 Vercel + CDN 全球加速  
✅ **渐进式** - 从日报告逐步扩展到周月分析  

## 项目结构

```
training-analysis/
├── scripts/
│   └── generate_report.py        # 核心脚本：数据获取 + 融合 + 生成报告
├── data/
│   └── daily/                    # 每日 JSON 备份
│       ├── 2026-06-30.json
│       ├── summary.json
│       └── .gitkeep
├── public/
│   └── daily/                    # 每日 HTML 报告
│       ├── 2026-06-30.html
│       ├── index.html            # 首页（待实现）
│       └── .gitkeep
├── run_daily.sh                  # Linux/Mac 自动化脚本
├── run_daily.ps1                 # Windows 自动化脚本
└── README.md
```

## 快速开始

### 1. 克隆仓库

```bash
git clone https://gitee.com/your-username/training-analysis.git
cd training-analysis
```

### 2. 生成今天的报告

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

### 3. 访问报告

生成完成后，文件位置：
- **JSON 数据**: `data/daily/2026-06-30.json`
- **HTML 报告**: `public/daily/2026-06-30.html`

推送到 Gitee 后，通过 Vercel 访问：
```
https://training-analysis.vercel.app/public/daily/2026-06-30.html
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

## 集成步骤（Vercel + Gitee）

### 第一步：连接 Vercel

1. 访问 https://vercel.com
2. **Add New** → **Project**
3. 选择 **Import Git Repository**
4. 选择 **Gitee** 标签
5. 授权并选择 `training-analysis` 仓库
6. Deploy

### 第二步：自动部署

每次 `git push` 到 Gitee，Vercel 会自动：
1. 检测新推送
2. 构建项目（无需额外配置）
3. 部署到 CDN
4. 更新 URL

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

### Q: 数据不更新？
**A:** 检查：
1. `xunji-api` skill 是否可用
2. Coros 认证状态
3. 日期格式是否正确 (YYYY-MM-DD)

### Q: 如何访问旧报告？
**A:** 访问 `https://training-analysis.vercel.app/public/daily/日期.html`  
或查看 Gitee 仓库中的 `data/daily/` 目录

## 技术栈

- **数据源**: xunji-api skill + Coros MCP
- **处理**: Python 3
- **存储**: Git (Gitee)
- **部署**: Vercel
- **展示**: HTML + CSS + JSON

## 许可证

MIT

## 作者

Training Analysis Team

---

**最后更新**: 2026-06-30  
**仓库**: https://gitee.com/your-username/training-analysis
