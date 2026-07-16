param(
    [string]$Terminal = "",
    [switch]$List
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $VenvPython) {
    $PythonExe = $VenvPython
} else {
    $PythonExe = "python"
}

$Terminals = [ordered]@{
    "1" = @{
        Title = "Terminal 1 - MQTT monitor"
        Script = "tools/mqtt_monitor.py"
        Args = @()
        Hint = "README: start first; watches uplink and downlink MQTT topics."
    }
    "2" = @{
        Title = "Terminal 2 - bridge main service"
        Script = "src/main.py"
        Args = @()
        Hint = "README: start bridge service; then start Terminal 3 before PubSub retries run out."
    }
    "4" = @{
        Title = "Terminal 4 - simulated MQTT devices"
        Script = "tools/sim_device.py"
        Args = @()
        Hint = "README: type 'auto' here when Terminal 3 asks you to register devices."
    }
    "3" = @{
        Title = "Terminal 3 - E2E verifier"
        Script = "tools/verify_e2e.py"
        Args = @()
        Hint = "README: starts business VSOA server on 3000 and runs end-to-end checks."
    }
    "2-offline" = @{
        Title = "Terminal 2 - bridge offline mode"
        Script = "src/main.py"
        Args = @("--no-mqtt")
        Hint = "Offline variant: VSOA + TCP 9090 only, no MQTT broker connection."
    }
}

$Aliases = @{
    "monitor" = "1"
    "mqtt" = "1"
    "bridge" = "2"
    "main" = "2"
    "sim" = "4"
    "device" = "4"
    "verify" = "3"
    "e2e" = "3"
    "offline" = "2-offline"
    "no-mqtt" = "2-offline"
}

function Quote-PowerShellSingleString {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Show-Menu {
    Write-Host ""
    Write-Host "MQTT-VSOA bridge launcher"
    Write-Host "Project: $ProjectRoot"
    Write-Host "Python : $PythonExe"
    Write-Host ""
    Write-Host "README startup order: 1 -> 2 -> 4 -> 3"
    Write-Host "Tip: after Terminal 2 starts, launch Terminal 3 before PubSub retries are exhausted."
    Write-Host ""

    foreach ($key in $Terminals.Keys) {
        $item = $Terminals[$key]
        Write-Host ("  {0,-9} {1}" -f $key, $item.Title)
        Write-Host ("            {0}" -f $item.Hint)
    }

    Write-Host ""
    Write-Host "Aliases: monitor, bridge, sim, verify, offline"
    Write-Host "Enter q to quit."
    Write-Host ""
}

function Resolve-TerminalKey {
    param([string]$Value)

    $normalized = $Value.Trim().ToLowerInvariant()
    if ($Terminals.Contains($normalized)) {
        return $normalized
    }
    if ($Aliases.ContainsKey($normalized)) {
        return $Aliases[$normalized]
    }
    return ""
}

function Start-BridgeTerminal {
    param([string]$Key)

    $item = $Terminals[$Key]
    $title = $item.Title
    $scriptPath = Join-Path $ProjectRoot $item.Script

    if (-not (Test-Path -LiteralPath $scriptPath)) {
        throw "Script not found: $scriptPath"
    }

    $quotedRoot = Quote-PowerShellSingleString $ProjectRoot
    $quotedPython = Quote-PowerShellSingleString $PythonExe
    $quotedScript = Quote-PowerShellSingleString $scriptPath
    $quotedTitle = Quote-PowerShellSingleString $title
    $quotedArgs = @()

    foreach ($arg in $item.Args) {
        $quotedArgs += Quote-PowerShellSingleString $arg
    }

    $argText = ($quotedArgs -join " ")
    $command = @(
        "`$Host.UI.RawUI.WindowTitle = $quotedTitle"
        "Set-Location -LiteralPath $quotedRoot"
        "Write-Host $quotedTitle"
        "Write-Host ('cwd: ' + (Get-Location))"
        "Write-Host ''"
        "& $quotedPython $quotedScript $argText"
        "Write-Host ''"
        "Write-Host 'Command exited. This terminal remains open.'"
    ) -join "; "

    Start-Process `
        -FilePath "powershell.exe" `
        -WorkingDirectory $ProjectRoot `
        -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)

    Write-Host ("Started in a new terminal: {0}" -f $title)
}

if ($List) {
    Show-Menu
    exit 0
}

if ($Terminal.Trim()) {
    $key = Resolve-TerminalKey $Terminal
    if (-not $key) {
        Write-Error "Unknown terminal: $Terminal"
        Show-Menu
        exit 1
    }
    Start-BridgeTerminal $key
    exit 0
}

while ($true) {
    Show-Menu
    $choice = Read-Host "Select one terminal to open"
    if ($choice.Trim().ToLowerInvariant() -in @("q", "quit", "exit")) {
        break
    }

    $key = Resolve-TerminalKey $choice
    if (-not $key) {
        Write-Warning "Unknown selection: $choice"
        continue
    }

    Start-BridgeTerminal $key
}
