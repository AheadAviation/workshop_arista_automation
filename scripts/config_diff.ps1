# Preview what a push would change on each running cEOS switch, without applying it.
# Usage: .\scripts\config_diff.ps1 [spine-01 leaf-01 ...]

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Devices
)
if (-not $Devices) { $Devices = @("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04") }
. "$PSScriptRoot\eos_session.ps1"

$changed = 0
foreach ($dev in $Devices) {
    $config = Get-GeneratedConfig $dev
    if ($null -eq $config) {
        Write-Host "${dev}: no generated config (run .\run.ps1 generate)" -ForegroundColor Red
        continue
    }
    $drift = Get-ConfigDrift $dev $config
    if (-not (Test-HasChanges $drift)) {
        Write-Host "${dev}: no change" -ForegroundColor Green
        continue
    }
    $changed++
    $count = @($drift | Where-Object { $_ -match '^[+-]' }).Count
    Write-Host "${dev}: $count line(s) change" -ForegroundColor Yellow
    foreach ($line in $drift) {
        $color = if ($line -match '^\+') { "Green" } elseif ($line -match '^-') { "Red" } else { "Gray" }
        Write-Host "    $line" -ForegroundColor $color
    }
}
Write-Host ""
if ($changed -eq 0) {
    Write-Host "The switches already match NetBox." -ForegroundColor Green
} else {
    Write-Host "$changed switch(es) would change. Nothing was applied. Push with .\run.ps1 push" -ForegroundColor Cyan
}
