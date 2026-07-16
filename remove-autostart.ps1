$ErrorActionPreference = "Stop"
$taskName = "JingPriceWatch"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Windows logon startup is disabled: $taskName" -ForegroundColor Green
} else {
    Write-Host "No logon startup task was found."
}
Read-Host "Press Enter to close"
