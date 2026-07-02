#!/bin/bash
# 每日训练报告生成脚本
# 用法: bash run_daily.sh [日期]
#      bash run_daily.sh 2026-06-30
#      bash run_daily.sh  # 默认使用今天

set -e

cd "$(dirname "$0")"

DATE=${1:-$(date +%Y-%m-%d)}

echo "🚀 开始生成 $DATE 的训练报告..."
echo ""

# 1. 运行 Python 脚本（脚本内部会自动同步高驰数据并刷新 token）
echo "⏳ 正在运行脚本..."

# 兼容不同环境：优先使用 python3，其次 python/py
python_cmd=""
for cmd in python3 python py; do
    if command -v "$cmd" >/dev/null 2>&1; then
        python_cmd=$cmd
        break
    fi
done
if [ -z "$python_cmd" ]; then
    echo "❌ 找不到可用的 Python 命令（python3/python/py）"
    exit 1
fi

$python_cmd scripts/generate_report.py --date "$DATE"

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
    git commit -m "chore(训练报告): $DATE"
    git push origin master
    echo "✅ 已推送到 GitHub"
fi

echo ""
echo "✨ 完成！"
echo ""
echo "📊 访问地址: https://sheep-cloud.github.io/training-analysis/public/daily/$DATE.html"
