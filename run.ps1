param(
    [switch]$Reload,
    [switch]$Setup,
    [switch]$Check,
    [switch]$Test,
    [switch]$Smoke,
    [ValidateSet('demo','integration')][string]$Profile = 'demo',
    [ValidateRange(1024,65535)][int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$projectPython = Join-Path $PSScriptRoot '.venv-dev\Scripts\python.exe'
if ($Setup) {
    & (Join-Path $PSScriptRoot 'setup-dev.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    exit 0
}
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Local environment is missing. Run .\run.ps1 -Setup first (Python 3.11 recommended).'
}
if (([int]$Check.IsPresent + [int]$Test.IsPresent + [int]$Smoke.IsPresent) -gt 1) { throw 'Choose only one of -Check, -Test, -Smoke.' }
$command = if ($Check) {'check'} elseif ($Test) {'test'} elseif ($Smoke) {'smoke'} else {'run'}
$launchArgs = @('-X','utf8','-m','tools.dev',$command,'--profile',$Profile,'--port',[string]$Port)
if ($Reload) { $launchArgs += '--reload' }
Push-Location $PSScriptRoot
try {
    & $projectPython @launchArgs
    $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
