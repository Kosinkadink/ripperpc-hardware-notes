<#
.SYNOPSIS
  Sustained memory heat-load — the script that actually diagnosed the DIMM
  thermal throttling on RipperPC.

.DESCRIPTION
  Runs a continuous multi-worker DRAM copy for several minutes and prints the
  bandwidth for every 5-second interval plus the running average. A healthy /
  cooled system holds a flat number for the whole run; a thermally throttled one
  starts fine, then collapses once the DIMMs heat-soak (on RipperPC the
  collapse to ~0-1.2 GiB/s coincided with DIMM temps hitting ~66.5 C), and may
  oscillate as the memory controller's thermal loop backs off and recovers.

  Watch per-DIMM temperatures in parallel (HWiNFO64 / BMC web UI on Windows;
  `ipmitool sensor list | grep -i dimm` on Linux) to correlate the collapse
  with temperature.

  Default working set: 8 workers x (1024 MiB src + 1024 MiB dst) = ~16 GiB, 300 s.

.PARAMETER Workers    Number of parallel copy threads (default 8).
.PARAMETER Mib        Per-worker src/dst buffer size in MiB (default 1024).
.PARAMETER Seconds    Total run duration in seconds (default 300).
.PARAMETER Label      Free-text banner note, e.g. "no fan" / "with airflow".

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools/windows/mem_heatload.ps1 -Label "no fan"

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools/windows/mem_heatload.ps1 -Label "with airflow"
#>
param(
    [int]$Workers = 8,
    [int]$Mib     = 1024,
    [int]$Seconds = 300,
    [string]$Label = ""
)

$src = @"
using System;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;

public static class AmpMemHeatLoad {
    public unsafe static void Run(int workers, int mib, int seconds, string label) {
        Console.WriteLine($"Starting sustained memory load ({label}): workers={workers}, per_worker={mib}MiB src + {mib}MiB dst, duration={seconds}s");
        Console.WriteLine($"Approx allocated working set: {workers * mib * 2 / 1024.0:F1} GiB");
        long[] bytes = new long[workers];
        Task[] tasks = new Task[workers];
        bool stop = false;
        for (int w = 0; w < workers; w++) {
            int idx = w;
            tasks[w] = Task.Run(() => {
                int len = mib * 1024 * 1024;
                byte[] src = new byte[len];
                byte[] dst = new byte[len];
                for (int i = 0; i < len; i += 4096) src[i] = (byte)(i + idx);
                fixed (byte* ps = src)
                fixed (byte* pd = dst) {
                    Buffer.MemoryCopy(ps, pd, len, len);
                    while (!Volatile.Read(ref stop)) {
                        Buffer.MemoryCopy(ps, pd, len, len);
                        Interlocked.Add(ref bytes[idx], len);
                    }
                }
            });
        }
        var sw = Stopwatch.StartNew();
        long lastTotal = 0;
        double lastT = 0;
        while (sw.Elapsed.TotalSeconds < seconds) {
            Thread.Sleep(5000);
            long total = 0; foreach (var b in bytes) total += b;
            double t = sw.Elapsed.TotalSeconds;
            double intervalGiBs = (total - lastTotal) / 1073741824.0 / (t - lastT);
            double totalGiBs = total / 1073741824.0 / t;
            Console.WriteLine($"t={t,6:F1}s interval_payload={intervalGiBs,8:F2} GiB/s interval_rw={2*intervalGiBs,8:F2} GiB/s avg_rw={2*totalGiBs,8:F2} GiB/s");
            lastTotal = total;
            lastT = t;
        }
        stop = true;
        Task.WaitAll(tasks);
        sw.Stop();
        long finalTotal = 0; foreach (var b in bytes) finalTotal += b;
        double finalPayload = finalTotal / 1073741824.0 / sw.Elapsed.TotalSeconds;
        Console.WriteLine($"DONE elapsed={sw.Elapsed.TotalSeconds:F1}s payload={finalPayload:F2} GiB/s approx_rw={2*finalPayload:F2} GiB/s");
    }
}
"@

Add-Type -TypeDefinition $src -CompilerOptions '-unsafe', '-optimize'
[AmpMemHeatLoad]::Run($Workers, $Mib, $Seconds, $Label)
