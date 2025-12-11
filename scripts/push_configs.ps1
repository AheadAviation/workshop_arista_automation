# Push configs to running ContainerLab cEOS devices
# Usage: .\scripts\push_configs.ps1

Write-Host "Pushing configs to ContainerLab devices..." -ForegroundColor Cyan

$devices = @("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")

foreach ($dev in $devices) {
    $configFile = "output\configs\$dev.cfg"
    $containerName = "clab-netbox-arista-lab-$dev"
    
    if (Test-Path $configFile) {
        Write-Host "  Loading config on $dev..." -ForegroundColor Yellow
        
        # Copy config to container's flash
        Get-Content $configFile | docker exec -i $containerName bash -c "cat > /mnt/flash/startup-config"
        
        # Apply config using configure replace
        docker exec $containerName bash -c "Cli -p 15 -c 'configure replace flash:startup-config'" 2>&1 | Out-Null
        
        Write-Host "  ✓ $dev configured" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Config file not found: $configFile" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Done! Checking BGP status on spine-01..." -ForegroundColor Cyan
Start-Sleep -Seconds 5
docker exec clab-netbox-arista-lab-spine-01 bash -c "Cli -p 15 -c 'show ip bgp summary'"

