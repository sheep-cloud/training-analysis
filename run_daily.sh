#!/bin/bash
# 每日训练报告生成脚本
# 用法: bash run_daily.sh [日期]
#      bash run_daily.sh 2026-06-30
#      bash run_daily.sh  # 默认使用今天

set -e

cd "$(dirname "$0")"

DATE=${1:-$(date +%Y-%m-%d)}
DAY_COMPACT=${DATE//-/}

# coros-mcp 可执行文件;如已在 PATH 可直接用 "coros-mcp"
COROS_MCP=${COROS_MCP:-/c/Develop/Workspaces/AIProjects/cygnusb/coros-mcp/.venv/Scripts/coros-mcp.exe}

echo "🚀 开始生成 $DATE 的训练报告..."
echo ""

# 1. 同步高驰数据到本地缓存(失败不中断,容忍离线/已同步)
echo "🔄 正在同步高驰数据 ($DAY_COMPACT)..."
"$COROS_MCP" sync --from "$DAY_COMPACT" --to "$DAY_COMPACT" || echo "⚠️  高驰同步失败,使用现有缓存继续"

# 2. 运行 Python 脚本
echo ""
python scripts/generate_report.py --date "$DATE"

# 3. 提交到 GitHub
echo ""
echo "📤 正在提交到 GitHub..."

# data/daily/*.json 被 .gitignore 忽略,需 -f 强制加入
git add -f public/daily/"$DATE".html data/daily/"$DATE".json data/daily/summary.json

# 分析 sidecar 是可选的(会话生成),存在才加入
ANALYSIS_FILE="data/daily/$DATE.analysis.json"
if [ -f "$ANALYSIS_FILE" ]; then
    git add -f "$ANALYSIS_FILE"
    echo "📎 已纳入分析文件:$ANALYSIS_FILE"
fi

if git diff --cached --quiet; then
    echo "ℹ️  没有新的文件需要提交"
else
    git commit -m "chore: daily training report - $DATE"
    git push origin master
    echo "✅ 已推送到 GitHub"
fi

echo ""
echo "✨ 完成！"
echo ""
echo "📊 访问地址: https://training-analysis.vercel.app/public/daily/$DATE.html"
