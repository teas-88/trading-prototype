# Runs one scheduled job and appends its output to data/logs/<date>-<job>.log.
# Called by Windows Task Scheduler (see scripts/install-tasks.ps1).
# Alerts (Windows notification, plus phone via ntfy.sh if NTFY_TOPIC is set in .env) when a
# command fails, and the sync job checks that the earlier jobs ran today.
param(
    [Parameter(Mandatory)][ValidateSet("wheel", "daily", "sync", "test-alert")][string]$Job
)
$ErrorActionPreference = "Continue"  # stderr from Python (warnings) must not abort the job

$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
}
$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$today = Get-Date -Format yyyy-MM-dd
$log = Join-Path $logDir "$today-$Job.log"

function Get-EnvValue([string]$Name) {
    $envFile = Join-Path $repo ".env"
    if (-not (Test-Path $envFile)) { return $null }
    $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=\s*(.+?)\s*$" } | Select-Object -First 1
    if ($line -and $line -match "^\s*$Name\s*=\s*(.+?)\s*$") { return $Matches[1] }
    return $null
}

function Send-Alert([string]$Title, [string]$Message, [string]$Priority = "high") {
    "$(Get-Date -Format s) [$Priority] $Title - $Message" | Out-File (Join-Path $logDir "alerts.log") -Append -Encoding utf8
    if ($Priority -ne "low") {
        try {
            $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
            $toast = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
                [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $text = $toast.GetElementsByTagName("text")
            $null = $text.Item(0).AppendChild($toast.CreateTextNode($Title))
            $null = $text.Item(1).AppendChild($toast.CreateTextNode($Message))
            $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
                [Windows.UI.Notifications.ToastNotification]::new($toast))
        } catch { "toast failed: $_" | Out-File (Join-Path $logDir "alerts.log") -Append -Encoding utf8 }
    }
    $topic = Get-EnvValue "NTFY_TOPIC"
    if ($topic) {
        try {
            $body = [Text.Encoding]::UTF8.GetBytes($Message)
            $null = Invoke-RestMethod -Method Post -Uri "https://ntfy.sh/$topic" -Body $body -TimeoutSec 20 `
                -Headers @{ Title = $Title; Priority = $Priority; Tags = $(if ($Priority -eq "low") { "chart_with_upwards_trend" } else { "warning" }) }
        } catch { "ntfy failed: $_" | Out-File (Join-Path $logDir "alerts.log") -Append -Encoding utf8 }
    }
}

function Invoke-Trader([string[]]$TraderArgs) {
    $cmd = "trader $($TraderArgs -join ' ')"
    "`n=== $(Get-Date -Format s) $cmd" | Out-File $log -Append -Encoding utf8
    $output = & $uv run trader @TraderArgs 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $output | Out-File $log -Append -Encoding utf8
    "=== exit code $code" | Out-File $log -Append -Encoding utf8
    if ($code -ne 0) {
        $tail = ($output | Where-Object { $_ -and $_ -notmatch "^\s*(File|~|\^)" } | Select-Object -Last 3) -join " | "
        Send-Alert "Trading job failed: $Job" "$cmd exited $code. $tail (log: $today-$Job.log)"
    }
    return , $output
}

# A job that never started can't report itself, so the last job of the day checks the others.
function Test-EarlierJobs {
    foreach ($earlier in "wheel", "daily") {
        $path = Join-Path $logDir "$today-$earlier.log"
        if (-not (Test-Path $path)) {
            Send-Alert "Trading job missing: $earlier" "No $earlier run today ($today). Was the laptop asleep, off or logged out?"
        } elseif (Select-String -Path $path -Pattern "=== exit code [^0]" -Quiet) {
            # Already alerted when it failed; this repeats it in case that alert was missed.
            Send-Alert "Trading job had errors: $earlier" "See $today-$earlier.log" "default"
        }
    }
}

switch ($Job) {
    "wheel" { $null = Invoke-Trader @("wheel", "--execute") }
    "daily" {
        $null = Invoke-Trader @("run", "trend-spy", "--execute")
        $null = Invoke-Trader @("run", "swing-us-stocks", "--execute")
    }
    "sync" {
        $null = Invoke-Trader @("sync")
        $report = Invoke-Trader @("report")
        Test-EarlierJobs
        $total = $report | Where-Object { $_ -like "Total P&L*" } | Select-Object -First 1
        if ($total) { Send-Alert "Trading daily summary" $total "low" }
    }
    "test-alert" { Send-Alert "Trading alerts test" "If you can see this, alerts are working." }
}
