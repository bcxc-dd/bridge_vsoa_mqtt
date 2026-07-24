$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = $null

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($PythonCommand) {
    $Python = $PythonCommand.Source
}

$Vite = Join-Path $Root "frontend\node_modules\.bin\vite.cmd"
$BackendLog = Join-Path $Root "backend.log"
$FrontendLog = Join-Path $Root "frontend.log"

if (-not $Python) {
    throw "Python executable not found. Please activate your conda base environment or install Python first."
}

if (-not (Test-Path $Vite)) {
    throw "Frontend dependencies are missing. Please run corepack pnpm --dir .\frontend install first."
}

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

if (-not (Test-PortListening 8000)) {
    Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000" -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $BackendLog -RedirectStandardError (Join-Path $Root "backend-error.log")
}
if (-not (Test-PortListening 5173)) {
    Start-Process -FilePath $Vite -ArgumentList "--host", "0.0.0.0", "--port", "5173" -WorkingDirectory (Join-Path $Root "frontend") -WindowStyle Hidden -RedirectStandardOutput $FrontendLog -RedirectStandardError (Join-Path $Root "frontend-error.log")
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
