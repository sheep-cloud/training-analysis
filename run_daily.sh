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

# 1. 运行 Python 脚本
python scripts/generate_report.py --date "$DATE"

# 2. 提交到 Gitee
echo ""
echo "📤 正在提交到 Gitee..."

git add public/daily/"$DATE".html data/daily/"$DATE".json data/daily/summary.json

if git diff --cached --quiet; then
    echo "ℹ️  没有新的文件需要提交"
else
    git commit -m "chore: daily training report - $DATE"
    git push origin main
    echo "✅ 已推送到 Gitee"
fi

echo ""
echo "✨ 完成！"
echo ""
echo "📊 访问地址: https://training-analysis.vercel.app/public/daily/$DATE.html"
