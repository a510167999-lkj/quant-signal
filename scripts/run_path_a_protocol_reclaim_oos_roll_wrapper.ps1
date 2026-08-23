# Wrapper used by Windows Scheduled Task. Appends logs under roll output root.
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$env:VPS_RUNTIME_ROLE = "local_research"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $RepoRoot "scripts\run_path_a_protocol_reclaim_oos_roll.py"
$LogDir = Join-Path $RepoRoot "data\research_runs\path_a_protocol_reclaim_oos_roll"
$LogFile = Join-Path $LogDir "scheduled_task.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $LogFile -Value "==== reclaim OOS roll $stamp ===="
& $Python -u $Runner *>> $LogFile 2>&1
$rollExit = $LASTEXITCODE
Add-Content -LiteralPath $LogFile -Value "roll_exit=$rollExit"

$Bounce = Join-Path $RepoRoot "scripts\run_path_a_protocol_bounce_daily.py"
Add-Content -LiteralPath $LogFile -Value "==== bounce daily $stamp ===="
& $Python -u $Bounce *>> $LogFile 2>&1
$bounceExit = $LASTEXITCODE
Add-Content -LiteralPath $LogFile -Value "bounce_exit=$bounceExit"

$Personal = Join-Path $RepoRoot "scripts\run_personal_book.py"
Add-Content -LiteralPath $LogFile -Value "==== personal book $stamp ===="
& $Python -u $Personal *>> $LogFile 2>&1
Add-Content -LiteralPath $LogFile -Value "personal_exit=$LASTEXITCODE"

$Daily = Join-Path $RepoRoot "scripts\run_personal_daily_sleeve.py"
Add-Content -LiteralPath $LogFile -Value "==== personal daily sleeve $stamp ===="
& $Python -u $Daily *>> $LogFile 2>&1
Add-Content -LiteralPath $LogFile -Value "daily_sleeve_exit=$LASTEXITCODE"

$Sync = Join-Path $RepoRoot "scripts\sync_bounce_daily_to_vps.py"
if (Test-Path $Sync) {
    Add-Content -LiteralPath $LogFile -Value "==== bounce daily sync $stamp ===="
    & $Python -u $Sync *>> $LogFile 2>&1
    Add-Content -LiteralPath $LogFile -Value "sync_exit=$LASTEXITCODE"
}

if ($rollExit -ne 0) { exit $rollExit }
if ($bounceExit -ne 0) { exit $bounceExit }
exit 0
