# PowerShell 每日训练报告生成脚本
# 用法: .\run_daily.ps1 [日期]
#      .\run_daily.ps1 2026-06-30
#      .\run_daily.ps1  # 默认使用今天

param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd")
)

Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"

Write-Host "🚀 开始生成 $Date 的训练报告..." -ForegroundColor Green
Write-Host ""

# 1. 运行 Python 脚本（脚本内部会自动同步高驰数据并刷新 token）
Write-Host "⏳ 正在运行脚本..." -ForegroundColor Cyan

# 兼容不同环境：优先使用 py，其次 python/python3
$pythonCmd = $null
foreach ($cmd in @("py", "python", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $pythonCmd = $cmd
        break
    }
}
if (-not $pythonCmd) {
    Write-Host "❌ 找不到可用的 Python 命令（py/python/python3）" -ForegroundColor Red
    exit 1
}

& $pythonCmd scripts/generate_report.py --date $Date

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
    Write-Host "[i] 没有新的文件需要提交" -ForegroundColor Yellow
}
else {
    git commit -m "chore(训练报告): $Date"
    git push origin master
    Write-Host "✅ 已推送到 GitHub" -ForegroundColor Green
}

Write-Host ""
Write-Host "✨ 完成！" -ForegroundColor Green
Write-Host ""
Write-Host "📊 访问地址: https://sheep-cloud.github.io/training-analysis/public/daily/$Date.html" -ForegroundColor Cyan
