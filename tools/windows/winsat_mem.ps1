<#
.SYNOPSIS
  Run Windows' built-in memory benchmark (WinSAT) and print the bandwidth.

.DESCRIPTION
  WinSAT is the official Windows System Assessment Tool. `winsat mem` measures
  system memory bandwidth (MB/s) and derives a MemoryScore. It requires
  elevation, so this script relaunches winsat elevated (UAC prompt), waits for
  the XML result, and parses out the bandwidth + score.

  On RipperPC this was the neutral, non-custom cross-check that confirmed the
  DRAM collapse was real and hardware-level (not a quirk of the custom C#
  benchmark): throttled ~3,368 MB/s (MemoryScore 5.x) vs all-8-DIMMs-with-fan
  51,716 MB/s (MemoryScore 8.7) — roughly a 15x swing from cooling alone.

.PARAMETER Name  Label used in the output XML filename (default "current").

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools/windows/winsat_mem.ps1 -Name all8-fan
#>
param(
    [string]$Name = "current"
)

$out = Join-Path $env:TEMP "amp-winsat-mem-$Name.xml"

# Launch elevated WinSAT (UAC prompt). Runs in its own window.
Get-Process winsat, consent -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item $out -ErrorAction SilentlyContinue
$cmd = "C:\Windows\System32\winsat.exe mem -xml `"$out`""
Start-Process -FilePath "C:\Windows\System32\cmd.exe" -ArgumentList "/c $cmd" -Verb RunAs -WindowStyle Normal
Write-Host "Started elevated WinSAT '$Name'; waiting for: $out"

# Poll for the result (up to ~3 minutes).
for ($i = 0; $i -lt 36; $i++) {
    if (Test-Path $out) { break }
    Start-Sleep -Seconds 5
}

if (Test-Path $out) {
    Get-Item $out | Format-List FullName, Length, LastWriteTime
    [xml]$x = Get-Content $out
    Write-Host "`n--- MemoryMetrics.Bandwidth (units: MB/s) ---"
    $x.WinSAT.Metrics.MemoryMetrics.Bandwidth | Format-List *
    Write-Host "--- WinSPR (scores) ---"
    $x.WinSAT.WinSPR | Format-List *
} else {
    Write-Host "No WinSAT XML produced yet at $out"
    Get-Process winsat, consent, cmd -ErrorAction SilentlyContinue |
        Select-Object ProcessName, Id, MainWindowTitle | Format-Table -AutoSize
}
