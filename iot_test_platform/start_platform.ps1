$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Vite = Join-Path $Root "frontend\node_modules\.bin\vite.cmd"

if (-not (Test-Path $Python)) {
    throw "平台 Python 环境不存在，请先安装 backend/requirements.txt。"
}
if (-not (Test-Path $Vite)) {
    throw "前端依赖不存在，请先在 frontend 目录执行 pnpm install。"
}

$BackendLog = Join-Path $Root "backend.log"
$FrontendLog = Join-Path $Root "frontend.log"

Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000" -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $BackendLog -RedirectStandardError (Join-Path $Root "backend-error.log")
Start-Process -FilePath $Vite -ArgumentList "--host", "0.0.0.0", "--port", "5173" -WorkingDirectory (Join-Path $Root "frontend") -WindowStyle Hidden -RedirectStandardOutput $FrontendLog -RedirectStandardError (Join-Path $Root "frontend-error.log")

Start-Sleep -Seconds 2
Write-Host "智慧环境设备管理平台已启动：http://127.0.0.1:5173"
