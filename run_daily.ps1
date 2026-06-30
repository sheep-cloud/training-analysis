# PowerShell 每日训练报告生成脚本
# 用法: .\run_daily.ps1 [日期]
#      .\run_daily.ps1 2026-06-30
#      .\run_daily.ps1  # 默认使用今天

param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd")
)

$ErrorActionPreference = "Stop"

Write-Host "🚀 开始生成 $Date 的训练报告..." -ForegroundColor Green
Write-Host ""

# 1. 运行 Python 脚本
Write-Host "⏳ 正在运行脚本..." -ForegroundColor Cyan
python scripts/generate_report.py --date $Date

# 2. 提交到 Gitee
Write-Host ""
Write-Host "📤 正在提交到 Gitee..." -ForegroundColor Cyan

$stagedFiles = git diff --cached --name-only

if ($stagedFiles.Count -eq 0 -and -not $stagedFiles) {
    Write-Host "ℹ️  没有新的文件需要提交" -ForegroundColor Yellow
}
else {
    git add "public/daily/$Date.html" "data/daily/$Date.json" "data/daily/summary.json"
    git commit -m "chore: daily training report - $Date"
    git push origin main
    Write-Host "✅ 已推送到 Gitee" -ForegroundColor Green
}

Write-Host ""
Write-Host "✨ 完成！" -ForegroundColor Green
Write-Host ""
Write-Host "📊 访问地址: https://training-analysis.vercel.app/public/daily/$Date.html" -ForegroundColor Cyan
