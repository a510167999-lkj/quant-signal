# Install a Windows Scheduled Task for reclaim independent OOS daily roll.
# Default: weekdays 18:30 local time.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_path_a_reclaim_oos_roll_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_path_a_reclaim_oos_roll_task.ps1 -Uninstall

param(
    [string]$TaskName = "quant-signal-lkj-path-a-reclaim-oos-roll",
    [string]$Time = "18:30",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Wrapper = Join-Path $RepoRoot "scripts\run_path_a_protocol_reclaim_oos_roll_wrapper.ps1"
$LogDir = Join-Path $RepoRoot "data\research_runs\path_a_protocol_reclaim_oos_roll"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $RepoRoot "scripts\run_path_a_protocol_reclaim_oos_roll.py"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Uninstalled task: $TaskName"
    exit 0
}

if (-not (Test-Path $Python)) { throw "Missing venv python: $Python" }
if (-not (Test-Path $Runner)) { throw "Missing runner: $Runner" }
if (-not (Test-Path $Wrapper)) { throw "Missing wrapper: $Wrapper" }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Wrapper`""
$Trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "  When: weekdays at $Time (local)"
Write-Host "  Repo: $RepoRoot"
Write-Host "  Log:  $(Join-Path $LogDir 'scheduled_task.log')"
Write-Host "  Due:  2027-07-04 (12-month independent OOS)"
Write-Host "  Uninstall: powershell -File scripts\install_path_a_reclaim_oos_roll_task.ps1 -Uninstall"
