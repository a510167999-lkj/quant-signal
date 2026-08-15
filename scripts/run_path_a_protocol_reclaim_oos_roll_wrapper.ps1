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
Add-Content -LiteralPath $LogFile -Value "exit=$LASTEXITCODE"
exit $LASTEXITCODE
