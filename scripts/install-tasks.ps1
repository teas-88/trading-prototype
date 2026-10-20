# Registers the trading jobs in Windows Task Scheduler (re-run to update; -Remove to delete).
# Times are Sydney local, Tue-Sat, chosen to work through both US and Australian DST changes:
#   05:00 wheel  -> 13:00-15:00 New York, while US options are trading
#   09:00 daily  -> after the US close, before the next open (orders queue for the open)
#   09:30 sync   -> pull the day's fills into the trade log
# Tasks run while you're logged in (a locked screen is fine) and catch up if a run was missed.
param([switch]$Remove)

$folder = "\trading-prototype\"
$jobs = [ordered]@{ wheel = "05:00"; daily = "09:00"; sync = "09:30" }

if ($Remove) {
    foreach ($job in $jobs.Keys) {
        Unregister-ScheduledTask -TaskPath $folder -TaskName $job -Confirm:$false -ErrorAction SilentlyContinue
    }
    "Removed trading-prototype tasks."
    return
}

$script = Join-Path $PSScriptRoot "run-job.ps1"
$days = "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

foreach ($job in $jobs.Keys) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Job $job"
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $jobs[$job]
    Register-ScheduledTask -TaskPath $folder -TaskName $job -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    "Registered $folder$job at $($jobs[$job]) Tue-Sat"
}
