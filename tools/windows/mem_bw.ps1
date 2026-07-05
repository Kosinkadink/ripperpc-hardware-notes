<#
.SYNOPSIS
  Cache-vs-DRAM memory bandwidth scan (Windows equivalent of tools/linux/mem_bw.py).

.DESCRIPTION
  Compiles a tiny unsafe C# copy loop at runtime (Buffer.MemoryCopy in a timed
  Task loop) and sweeps buffer size x worker count. Small (~1 MiB) buffers stay
  in cache and report a high number even on a broken box; larger (8-256 MiB)
  buffers hit DRAM and expose the real memory subsystem.

  On RipperPC with heat-soaked DIMMs this showed the classic fingerprint:
  the 1 MiB point stayed fast (cache) while every DRAM-sized point collapsed to
  ~2-4 GiB/s. With DIMM airflow, DRAM-sized points recover to ~70-90 GiB/s.

  Numbers reported:
    payload_copy = bytes actually copied / s
    approx_rw    = 2 x payload_copy (each copy reads src + writes dst)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools/windows/mem_bw.ps1
#>

$src = @"
using System;
using System.Diagnostics;
using System.Threading.Tasks;

public static class AmpMemBw {
    public unsafe static double RunOnce(int workers, int mib, int seconds) {
        long[] bytes = new long[workers];
        int len = mib * 1024 * 1024;
        Task[] tasks = new Task[workers];
        var swAll = Stopwatch.StartNew();
        for (int w = 0; w < workers; w++) {
            int idx = w;
            tasks[w] = Task.Run(() => {
                byte[] src = new byte[len];
                byte[] dst = new byte[len];
                for (int i = 0; i < len; i += 4096) src[i] = (byte)i;
                fixed (byte* ps = src)
                fixed (byte* pd = dst) {
                    Buffer.MemoryCopy(ps, pd, len, len); // warmup / fault-in
                    var sw = Stopwatch.StartNew();
                    long local = 0;
                    while (sw.Elapsed.TotalSeconds < seconds) {
                        Buffer.MemoryCopy(ps, pd, len, len);
                        local += len;
                    }
                    bytes[idx] = local;
                }
            });
        }
        Task.WaitAll(tasks);
        swAll.Stop();
        long total = 0; foreach (var b in bytes) total += b;
        return total / 1073741824.0 / swAll.Elapsed.TotalSeconds;
    }
    public static void Run() {
        Console.WriteLine($"host={Environment.MachineName} logical_cpus={Environment.ProcessorCount}");
        int[] sizes   = new int[]{ 1, 8, 32, 128, 256 };  // 1 MiB = cache, rest = DRAM
        int[] workers = new int[]{ 1, 4, 16 };
        foreach (int m in sizes) {
            foreach (int w in workers) {
                double g = RunOnce(w, m, 3);
                Console.WriteLine($"size={m,3}MiB workers={w,2} payload_copy={g,8:F2} GiB/s approx_rw={2*g,8:F2} GiB/s");
            }
        }
    }
}
"@

Add-Type -TypeDefinition $src -CompilerOptions '-unsafe', '-optimize'
[AmpMemBw]::Run()
