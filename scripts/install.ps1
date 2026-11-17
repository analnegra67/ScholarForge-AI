# ScholarForge installer for Windows (user profile only; no administrator rights needed).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-Tools] [-Claude] [-Codex]
param([switch]$Tools, [switch]$Claude, [switch]$Codex)
$ErrorActionPreference = 'Stop'
$py = $null
foreach ($candidate in @('py -3', 'python')) {
    try {
        $v = & cmd /c "$candidate -c ""import sys; print(sys.version_info >= (3, 12))""" 2>$null
        if ($v -eq 'True') { $py = $candidate; break }
    } catch {}
}
if (-not $py) { Write-Error 'Python 3.12+ not found. Install it from https://www.python.org (tick "Add python.exe to PATH").'; exit 1 }
$argsList = @()
if ($Tools) { $argsList += '--tools' }
if ($Claude) { $argsList += '--claude' }
if ($Codex) { $argsList += '--codex' }
$script = Join-Path $PSScriptRoot 'install.py'
& cmd /c "$py ""$script"" $($argsList -join ' ')"
exit $LASTEXITCODE
