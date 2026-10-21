# Shared helpers for push_configs.ps1 and config_diff.ps1 (dot-source this file).
# Both load the generated config into an EOS configuration session that starts from
# a clean config, so the session holds exactly what NetBox generated: committing it
# is a full replace (things removed from NetBox are removed from the switch), and
# its diff against the running config is exactly what a push would change.

function Get-GeneratedConfig([string]$Device) {
    $file = "output\configs\$Device.cfg"
    if (-not (Test-Path $file)) { return $null }
    return (Get-Content $file | Where-Object { $_ -notmatch '^\s*end\s*$' }) -join "`n"
}

function Invoke-EosSession([string]$Device, [string]$Config, [string]$Finish) {
    $session = "netbox$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    $script = "configure session $session`nrollback clean-config`n$Config`n$Finish`n"
    return @($script | docker exec -i "clab-netbox-arista-lab-$Device" Cli -p 15 2>&1)
}

# Lines that would change (diff body only). A plaintext password is re-hashed with a
# new salt on every load, so that line always differs and is not a real change.
function Get-ConfigDrift([string]$Device, [string]$Config) {
    $out = Invoke-EosSession $Device $Config "show session-config diffs`nabort"
    $header = $out | Where-Object { $_ -match '^\+\+\+ ' } | Select-Object -First 1
    if (-not $header) { return @() }
    $start = [array]::IndexOf($out, $header) + 1
    if ($start -ge $out.Count) { return @() }
    return @($out[$start..($out.Count - 1)] | Where-Object {
        $_ -and $_ -notmatch '^[+-]username \S+ .*secret sha512 '
    })
}

function Test-HasChanges([string[]]$Drift) {
    return @($Drift | Where-Object { $_ -match '^[+-]' }).Count -gt 0
}
