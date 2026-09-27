param(
    [switch]$Tunnel,
    [ValidateRange(1024, 65535)][int]$LocalPort = 18081,
    [switch]$BindPhysical,
    [string]$Command
)
$ErrorActionPreference = 'Stop'
$profilePath = Join-Path $PSScriptRoot 'ssh/ECS-YRXT.conf'
$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$sshArgs = @('-F', $profilePath)
if ($BindPhysical) {
    $indices = @(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -ExpandProperty ifIndex)
    $routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' |
        Where-Object { $indices -contains $_.InterfaceIndex -and $_.NextHop -ne '0.0.0.0' } |
        Sort-Object RouteMetric)
    $address = $null
    foreach ($route in $routes) {
        $address = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 |
            Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } |
            Select-Object -First 1 -ExpandProperty IPAddress
        if ($address) { break }
    }
    if (-not $address) { throw 'No active physical IPv4 adapter with a default route.' }
    $sshArgs += @('-b', $address)
}
if ($Tunnel) {
    if ($Command) { throw 'Use either -Tunnel or -Command.' }
    $sshArgs += @('-N', '-T', '-o', 'ExitOnForwardFailure=yes', '-L', "127.0.0.1:${LocalPort}:127.0.0.1:18080")
    Write-Host "Server workspace: http://127.0.0.1:$LocalPort/ (keep this process running)"
}
$sshArgs += 'ECS-YRXT'
if ($Command) { $sshArgs += $Command }
& $sshPath @sshArgs
exit $LASTEXITCODE
