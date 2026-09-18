param(
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$args = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
if ($Reload) { $args += "--reload" }
python @args
