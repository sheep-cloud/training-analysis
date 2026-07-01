# PowerShell 每日训练报告生成脚本
# 用法: .\run_daily.ps1 [日期]
#      .\run_daily.ps1 2026-06-30
#      .\run_daily.ps1  # 默认使用今天

param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd")
)

$ErrorActionPreference = "Stop"

# coros-mcp 可执行文件(与 .mcp.json 一致);如已在 PATH 可改为 "coros-mcp"
$CorosMcp = "C:/Develop/Workspaces/AIProjects/cygnusb/coros-mcp/.venv/Scripts/coros-mcp.exe"

Write-Host "🚀 开始生成 $Date 的训练报告..." -ForegroundColor Green
Write-Host ""

# 1. 同步高驰数据到本地缓存(失败不中断,容忍离线/已同步)
$DayCompact = $Date -replace "-", ""
Write-Host "🔄 正在同步高驰数据 ($DayCompact)..." -ForegroundColor Cyan
try {
    & $CorosMcp sync --from $DayCompact --to $DayCompact
} catch {
    Write-Host "⚠️  高驰同步失败,使用现有缓存继续: $_" -ForegroundColor Yellow
}

# 2. 运行 Python 脚本
Write-Host ""
Write-Host "⏳ 正在运行脚本..." -ForegroundColor Cyan
py scripts/generate_report.py --date $Date

# 3. 提交到 GitHub
Write-Host ""
Write-Host "📤 正在提交到 GitHub..." -ForegroundColor Cyan

# data/daily/*.json 被 .gitignore 忽略,需 -f 强制加入
git add -f "public/daily/$Date.html" "data/daily/$Date.json" "data/daily/summary.json"

# 分析 sidecar 是可选的(会话生成),存在才加入
$AnalysisFile = "data/daily/$Date.analysis.json"
if (Test-Path $AnalysisFile) {
    git add -f $AnalysisFile
    Write-Host "📎 已纳入分析文件:$AnalysisFile" -ForegroundColor Cyan
}

$stagedFiles = git diff --cached --name-only
if (-not $stagedFiles) {
    Write-Host "ℹ️  没有新的文件需要提交" -ForegroundColor Yellow
}
else {
    git commit -m "chore: daily training report - $Date"
    git push origin master
    Write-Host "✅ 已推送到 GitHub" -ForegroundColor Green
}

Write-Host ""
Write-Host "✨ 完成！" -ForegroundColor Green
Write-Host ""
Write-Host "📊 访问地址: https://training-analysis.vercel.app/public/daily/$Date.html" -ForegroundColor Cyan
