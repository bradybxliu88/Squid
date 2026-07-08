<#
.SYNOPSIS
  Produce a clean, shippable copy of Squid_BL for a collaborator.

.DESCRIPTION
  Robocopy-mirrors the repo to a destination, EXCLUDING bulk/junk (brady_images ~1.3 GB,
  __pycache__, *.pyc, Linux *.so, caches, scratch files, .git), then VERIFIES that the
  must-have files survived the copy (the machine .ini config, merfish_config, the
  fluidics_v2 submodule CODE, the pinned requirements, and this setup guide).

  Deliver a DIRECTORY COPY -- NOT a git clone. A clone is missing the machine .ini config
  (gitignored, *.ini) and the fluidics_v2 submodule code (broken gitlink), so the app would
  refuse to launch and fluidics would be silently unavailable. A directory copy carries them.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File make_shippable_copy.ps1 -Destination D:\Squid_BL_ship

.EXAMPLE
  # Keep the bundled camera vendor SDKs (~290 MB) if the collaborator's camera needs them:
  powershell -ExecutionPolicy Bypass -File make_shippable_copy.ps1 -Destination D:\Squid_BL_ship -IncludeCameraSDKs
#>
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [switch]$IncludeCameraSDKs   # keep 'drivers and libraries' (~290 MB); excluded by default
)

$ErrorActionPreference = 'Stop'
$Source = $PSScriptRoot
Write-Host "Source:      $Source"
Write-Host "Destination: $Destination`n"

# Junk dir names to drop anywhere in the tree:
$excludeDirs = @('__pycache__', '.pytest_cache', '.venv', '.git', '.claude')
# Bulk/machine-specific dirs at known locations (full paths so we don't over-match):
$excludeDirs += (Join-Path $Source 'brady_images')       # ~1.3 GB acquisition dumps
$excludeDirs += (Join-Path $Source 'conductor')          # agent worktrees
$excludeDirs += (Join-Path $Source 'software\cache')     # this-machine absolute path files
if (-not $IncludeCameraSDKs) {
    $excludeDirs += (Join-Path $Source 'software\drivers and libraries')  # ~290 MB vendor SDKs
}
$excludeFiles = @('*.pyc', '*.pyo', '*.so', '*.o', 'scratch_*.txt', 'dependency_analysis.log')

$roboArgs = @($Source, $Destination, '/MIR', '/NFL', '/NDL', '/NJH', '/NP', '/R:1', '/W:1')
$roboArgs += '/XD'; $roboArgs += $excludeDirs
$roboArgs += '/XF'; $roboArgs += $excludeFiles

Write-Host "Copying (excluding bulk/junk)..."
robocopy @roboArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }   # 0-7 = success

Write-Host "`nVerifying must-have files survived:"
$ok = $true
$mustHave = @(
    'software\src\main_hcs.py',
    'software\merfish_config\MERFISH_config.json',
    'software\fluidics_v2\software\fluidics\control\controller.py',
    'software\requirements-pinned.txt',
    'COLLABORATOR_SETUP.md'
)
foreach ($rel in $mustHave) {
    if (Test-Path (Join-Path $Destination $rel)) { Write-Host "  OK   $rel" }
    else { Write-Host "  MISS $rel" -ForegroundColor Red; $ok = $false }
}
# The active machine config: any configuration*.ini directly in software/ (gitignored, so a
# clone would not have it; a copy should).
$ini = Get-ChildItem -Path (Join-Path $Destination 'software') -Filter 'configuration*.ini' -File -ErrorAction SilentlyContinue
if ($ini) { Write-Host "  OK   software\$($ini[0].Name)  (machine config)" }
else {
    Write-Host "  MISS software\configuration*.ini  (machine config -- app will NOT launch without it!)" -ForegroundColor Red
    $ok = $false
}

$sizeGB = "{0:N2}" -f ((Get-ChildItem $Destination -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB)
Write-Host "`nCopy size: $sizeGB GB"
if ($ok) {
    Write-Host "`nSHIPPABLE COPY READY -> $Destination" -ForegroundColor Green
    Write-Host "Hand it over together with COLLABORATOR_SETUP.md (it is inside the copy)."
    exit 0   # robocopy leaves $LASTEXITCODE=1 on success; return a clean 0 to callers
}
else {
    Write-Host "`nWARNING: must-have files missing -- do NOT ship until resolved." -ForegroundColor Red
    exit 1
}
