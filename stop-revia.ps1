[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ports = @(3000, 8000)
$stoppedAny = $false

foreach ($port in $ports) {
    $processIds = @(
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )

    if ($processIds.Count -eq 0) {
        Write-Host "No process is listening on port $port." -ForegroundColor DarkGray
        continue
    }

    foreach ($processId in $processIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }

        Write-Host "Stopping $($process.ProcessName) (PID $processId) on port $port..." -ForegroundColor Yellow
        Stop-Process -Id $processId -Force -ErrorAction Stop
        $stoppedAny = $true
    }
}

Start-Sleep -Milliseconds 500

$remainingPorts = @()
foreach ($port in $ports) {
    $remaining = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($remaining) {
        $remainingPorts += $port
    }
}

if ($remainingPorts.Count -gt 0) {
    Write-Warning "These ports are still in use: $($remainingPorts -join ', ')"
    exit 1
}

if ($stoppedAny) {
    Write-Host "The Revia frontend and backend services have been stopped." -ForegroundColor Green
}
else {
    Write-Host "No Revia services need to be stopped." -ForegroundColor Green
}
