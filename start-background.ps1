$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataDir = Join-Path $projectRoot "data"
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$stdoutLog = Join-Path $dataDir "service.log"
$stderrLog = Join-Path $dataDir "service-error.log"
. (Join-Path $projectRoot "runtime.ps1")
try {
    $runtime = Get-JpwRuntime
} catch {
    $_.Exception.Message | Set-Content -LiteralPath $stderrLog -Encoding UTF8
    exit 1
}
$appPath = Join-Path $projectRoot "app.py"
$argumentParts = @($runtime.PythonPrefix) + @("-B", "`"$appPath`"")
$arguments = $argumentParts -join " "
Start-Process `
    -FilePath $runtime.Python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog
