# Wrapper used by Windows Scheduled Task. Appends logs under F3 output root.
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$env:VPS_RUNTIME_ROLE = "local_research"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $RepoRoot "scripts\run_path_a_f3_oos_daily_roll.py"
$LogDir = Join-Path $RepoRoot "data\research_runs\path_a_f3_oos_daily_roll"
$LogFile = Join-Path $LogDir "scheduled_task.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $LogFile -Value "==== F3 roll $stamp ===="
& $Python -u $Runner --skip-if-no-new-session *>> $LogFile 2>&1
Add-Content -LiteralPath $LogFile -Value "exit=$LASTEXITCODE"
exit $LASTEXITCODE
