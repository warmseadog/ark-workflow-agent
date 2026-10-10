param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$venv = Join-Path $PSScriptRoot '.venv-dev'
$projectPython = Join-Path $venv 'Scripts\python.exe'
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath $projectPython)) {
        & $Python -c 'import sys; assert (3,10) <= sys.version_info[:2] < (3,13), "Install Python 3.11 (supported: 3.10-3.12)"'
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -m venv $venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $configuration = Get-Content -LiteralPath (Join-Path $venv 'pyvenv.cfg') -Raw
    if ($configuration -match 'include-system-site-packages\s*=\s*true') {
        throw '.venv-dev must be isolated from global packages. Use a fresh virtual environment.'
    }
    & $projectPython -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $projectPython -m pip check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $projectPython -X utf8 -m tools.dev check
    exit $LASTEXITCODE
} finally { Pop-Location }
