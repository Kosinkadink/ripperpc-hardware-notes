"""
System DRAM bandwidth check.

Distinguishes cache bandwidth (fast if cores are healthy) from DRAM bandwidth
(the thing that was found broken on RipperPC: ~3 GiB/s vs an expected ~170 GB/s
for 8-channel DDR4-2667, and vs ~40 GiB/s on the dual-channel x570).

Re-run after BIOS changes / CPU reseat / memory retraining:
  python notes/mem_bw.py

Healthy reference (x570, dual-channel DDR4-3200):
  1 thread DRAM ~40 GiB/s, 8 thread ~37 GiB/s, cache ~106 GiB/s
Broken (RipperPC, 8-channel DDR4-2667):
  1 thread DRAM ~2 GiB/s, 8 thread ~3 GiB/s, cache ~96 GiB/s
"""
import numpy as np, time, threading

def _worker(a, b, iters):
    for _ in range(iters):
        np.copyto(b, a)

def dram(nthreads, mib=256, iters=3):
    sz = mib * 1024 * 1024
    bufs = [(np.ones(sz, dtype=np.uint8), np.empty(sz, dtype=np.uint8)) for _ in range(nthreads)]
    for a, b in bufs:
        np.copyto(b, a)  # fault-in / warmup
    t = time.perf_counter()
    ths = [threading.Thread(target=_worker, args=(a, b, iters)) for a, b in bufs]
    for x in ths: x.start()
    for x in ths: x.join()
    dt = time.perf_counter() - t
    return nthreads * iters * 2 * sz / (1024 ** 3) / dt  # R+W GiB/s

def cache(mib=8, iters=200):
    sz = mib * 1024 * 1024
    a = np.ones(sz, dtype=np.uint8); b = np.empty(sz, dtype=np.uint8)
    np.copyto(b, a)
    t = time.perf_counter()
    for _ in range(iters):
        np.copyto(b, a)
    return iters * 2 * sz / (1024 ** 3) / (time.perf_counter() - t)

if __name__ == "__main__":
    print(f"cache ({8} MiB, resident): {cache():6.1f} GiB/s  (should be ~100)")
    for nt in (1, 4, 8):
        print(f"DRAM {nt:2d} thread(s):        {dram(nt):6.1f} GiB/s aggregate (R+W)")
    print("\nIf DRAM stays low while cache is high, the memory subsystem is the bottleneck.")
