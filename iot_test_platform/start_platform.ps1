$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Root
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Vite = Join-Path $Root "frontend\node_modules\vite\bin\vite.js"
$BundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$Node = if ($env:IOT_PLATFORM_NODE -and (Test-Path $env:IOT_PLATFORM_NODE)) {
    $env:IOT_PLATFORM_NODE
} elseif (Test-Path $BundledNode) {
    $BundledNode
} else {
    (Get-Command node -ErrorAction Stop).Source
}

if (-not (Test-Path $Python)) {
    throw "仓库 Python 环境不存在：$Python"
}
if (-not (Test-Path $Vite)) {
    throw "前端依赖不存在，请先在 frontend 目录执行 npm install。"
}

$BackendLog = Join-Path $Root "backend.log"
$FrontendLog = Join-Path $Root "frontend.log"

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

if (-not (Test-PortListening 8000)) {
    Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000" -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $BackendLog -RedirectStandardError (Join-Path $Root "backend-error.log")
}
if (-not (Test-PortListening 5173)) {
    Start-Process -FilePath $Node -ArgumentList $Vite, "--host", "0.0.0.0", "--port", "5173" -WorkingDirectory (Join-Path $Root "frontend") -WindowStyle Hidden -RedirectStandardOutput $FrontendLog -RedirectStandardError (Join-Path $Root "frontend-error.log")
}

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$IsAdministrator = ([Security.Principal.WindowsPrincipal]$Identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($IsAdministrator) {
    foreach ($Port in 5173, 8000) {
        $RuleName = "ACOINFO IoT Platform TCP $Port"
        if (-not (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
        }
    }
} else {
    Write-Warning "未使用管理员权限启动；如果其他电脑无法访问，请以管理员身份运行一次本脚本以放行防火墙。"
}

Start-Sleep -Seconds 2
$LanAddress = Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    ForEach-Object { $_.IPv4Address.IPAddress } |
    Where-Object { $_ -and $_ -notlike "169.254.*" } |
    Select-Object -First 1

Write-Host "智慧环境设备管理平台已启动"
Write-Host "本机访问：http://127.0.0.1:5173"
if ($LanAddress) {
    Write-Host "局域网访问：http://${LanAddress}:5173"
} else {
    Write-Warning "未找到可用的局域网 IPv4 地址。"
}
