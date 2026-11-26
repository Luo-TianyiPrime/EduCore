param(
    [int]$Port = 8765,
    [switch]$SkipFrontend
)

# 开发启动脚本：安装依赖（按需）、构建前端、启动本地服务。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "安装 Python 依赖..." -ForegroundColor Cyan
    uv sync --all-extras
}

if (-not $SkipFrontend -and -not (Test-Path "frontend\node_modules")) {
    Write-Host "安装前端依赖..." -ForegroundColor Cyan
    Push-Location frontend
    npm install
    Pop-Location
}

if (-not $SkipFrontend -and -not (Test-Path "frontend\dist\index.html")) {
    Write-Host "构建前端..." -ForegroundColor Cyan
    Push-Location frontend
    npm run build
    Pop-Location
}

Write-Host "启动 EduCore (http://127.0.0.1:$Port)" -ForegroundColor Green
uv run python -m backend.main --port $Port --open-browser
