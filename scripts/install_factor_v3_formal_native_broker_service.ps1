[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Apply,
    [string]$BrokerBinary = "",
    [string]$RuntimeRoot = "",
    [string]$SecretRoot = ""
)

$ErrorActionPreference = "Stop"
$serviceName = "QuantSignalLkjFactorV3FormalBroker"

Write-Output "External TCB: Windows administrator, SCM, fixed service SID, ACL, and CNG KSP."
if (-not $Apply) {
    Write-Output "DRY-RUN: no service, ACL, or CNG state was changed."
    exit 0
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdministrator) {
    Write-Error "A Windows administrator is required; deployment failed closed."
    exit 1
}
if (
    [string]::IsNullOrWhiteSpace($BrokerBinary) -or
    [string]::IsNullOrWhiteSpace($RuntimeRoot) -or
    [string]::IsNullOrWhiteSpace($SecretRoot)
) {
    Write-Error "Explicit broker, runtime, and secret paths are required."
    exit 1
}
foreach ($path in @($BrokerBinary, $RuntimeRoot, $SecretRoot)) {
    if (-not [IO.Path]::IsPathFullyQualified($path) -or -not (Test-Path -LiteralPath $path)) {
        Write-Error "Every deployment path must already exist and be absolute."
        exit 1
    }
}

$manifestPath = Join-Path (Split-Path -Parent $PSScriptRoot) `
    "native\factor_v3_formal_native_broker\factor_v3_formal_native_broker_manifest.h"
$manifest = Get-Content -LiteralPath $manifestPath -Raw
if ($manifest -notmatch "#define F3_BROKER_PRODUCTION_HANDOFF_READY 1") {
    Write-Error "The reviewed production manifest is not provisioned; deployment failed closed."
    exit 1
}

if (-not $PSCmdlet.ShouldProcess($serviceName, "install formal native broker service")) {
    Write-Output "DRY-RUN: ShouldProcess declined all mutations."
    exit 0
}

throw "SCM/ACL/CNG mutation is intentionally unavailable until reviewed deployment policy is checked in."
