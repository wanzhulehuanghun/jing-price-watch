$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backgroundScript = Join-Path $projectRoot "start-background.ps1"
$taskName = "JingPriceWatch"
$arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$backgroundScript`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Start JingPriceWatch at Windows logon" -Force | Out-Null
Write-Host "Windows logon startup is enabled: $taskName" -ForegroundColor Green
Write-Host "It will run in the background after your next logon."
Read-Host "Press Enter to close"
