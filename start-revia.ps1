[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$backendPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$packageJson = Join-Path $projectRoot "package.json"
$frontendUrl = "http://localhost:3000"

function Test-ListeningPort {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return [bool]($listeners | Where-Object { $_.Port -eq $Port } | Select-Object -First 1)
}

function ConvertTo-EncodedPowerShellCommand {
    param([Parameter(Mandatory = $true)][string]$Command)

    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
}

function Start-ServiceWindow {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$Command
    )

    $windowCommand = "`$Host.UI.RawUI.WindowTitle = '$($Title.Replace("'", "''"))'`r`n$Command"
    $encodedCommand = ConvertTo-EncodedPowerShellCommand -Command $windowCommand
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoLogo",
        "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", $encodedCommand
    ) | Out-Null
}

if (-not (Test-Path -LiteralPath $backendPython -PathType Leaf)) {
    throw "Backend virtual environment was not found: $backendPython"
}

if (-not (Test-Path -LiteralPath $packageJson -PathType Leaf)) {
    throw "Frontend package.json was not found: $packageJson"
}

if (-not (Get-Command "npm.cmd" -ErrorAction SilentlyContinue)) {
    throw "npm.cmd was not found. Install Node.js or add it to PATH first."
}

$escapedBackendRoot = $backendRoot.Replace("'", "''")
$escapedBackendPython = $backendPython.Replace("'", "''")
$escapedProjectRoot = $projectRoot.Replace("'", "''")

if (Test-ListeningPort -Port 8000) {
    Write-Warning "Port 8000 is already in use. Skipping the FastAPI server."
}
else {
    $backendCommand = @"
Set-Location -LiteralPath '$escapedBackendRoot'
& '$escapedBackendPython' -m alembic upgrade head
if (`$LASTEXITCODE -ne 0) { throw 'Alembic migration failed.' }
& '$escapedBackendPython' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"@
    Start-ServiceWindow -Title "Revia Backend :8000" -Command $backendCommand
    Write-Host "Started the FastAPI server window: http://127.0.0.1:8000" -ForegroundColor Green
}

if (Test-ListeningPort -Port 3000) {
    Write-Warning "Port 3000 is already in use. Skipping the Next.js server."
}
else {
    $frontendCommand = @"
Set-Location -LiteralPath '$escapedProjectRoot'
& npm.cmd run dev -- --hostname 127.0.0.1 --port 3000
"@
    Start-ServiceWindow -Title "Revia Frontend :3000" -Command $frontendCommand
    Write-Host "Started the Next.js server window: $frontendUrl" -ForegroundColor Green
}

$frontendReady = $false
$readyDeadline = [DateTime]::UtcNow.AddSeconds(90)
while ([DateTime]::UtcNow -lt $readyDeadline) {
    try {
        $response = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            $frontendReady = $true
            break
        }
    }
    catch {
        Start-Sleep -Milliseconds 750
    }
}

if (-not $frontendReady) {
    Write-Warning "The frontend did not respond within 90 seconds. Opening the browser anyway; check the frontend log window."
}

Start-Process $frontendUrl
Write-Host "Opened $frontendUrl" -ForegroundColor Cyan
