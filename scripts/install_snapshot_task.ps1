param(
    [string]$Python = "python",
    [string]$TaskName = "Equity Research Daily Snapshot",
    [string]$Watchlist = "default",
    [string]$At = "08:00",
    [ValidateSet("auto", "on", "off")]
    [string]$Wisburg = "auto"
)

$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "snapshot_consensus.py"
$arguments = "`"$script`" --watchlist `"$Watchlist`" --wisburg $Wisburg"
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Snapshots point-in-time consensus, daily prices, and optional capped Wisburg research context. The script skips US market holidays." `
    -Force

Write-Output "Installed '$TaskName' for $At local time (watchlist=$Watchlist, wisburg=$Wisburg)."
