$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $projectRoot "runtime.ps1")
try {
    $runtime = Get-JpwRuntime
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Install Python 3.11+, Node.js 22+, and Microsoft Edge, then try again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "Starting JingPriceWatch..." -ForegroundColor Cyan
$pythonPrefix = @($runtime.PythonPrefix)
& $runtime.Python $pythonPrefix -B "$projectRoot\app.py" --open-browser
