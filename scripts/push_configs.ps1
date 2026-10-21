# Push configs to running ContainerLab cEOS devices
# Usage: .\scripts\push_configs.ps1

Write-Host "Pushing configs to ContainerLab devices..." -ForegroundColor Cyan

$devices = @("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")

foreach ($dev in $devices) {
    $configFile = "output\configs\$dev.cfg"
    $containerName = "clab-netbox-arista-lab-$dev"

    if (Test-Path $configFile) {
        Write-Host "  Loading config on $dev..." -ForegroundColor Yellow
        $cfg = Get-Content -Raw $configFile
        $out = "configure`n$cfg" | docker exec -i $containerName Cli -p 15 2>&1
        if ("$out" -match "Invalid input|% Error") {
            Write-Host "  FAIL: $dev : $out" -ForegroundColor Red
        } else {
            Write-Host "  OK: $dev configured" -ForegroundColor Green
        }
    } else {
        Write-Host "  MISSING: Config file not found: $configFile" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Done! Checking BGP status on spine-01..." -ForegroundColor Cyan
Start-Sleep -Seconds 5
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"
