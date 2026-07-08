# Problem 2 — "Abysmal" async offloading (DIMM thermal throttling)

## Symptom

ComfyUI **async offloading** (streaming weights between host RAM and GPU while
computing) was catastrophically slow on RipperPC. The tell-tale pattern:

- After a **cold boot** (machine off for a while), the first few runs were fine.
- After a handful of successful runs, offloading slowed **~5–10×** and **never
  recovered** — once the machine was warm it stayed slow.

Host↔GPU transfer bandwidth was stuck at roughly **~4 GB/s**, far below the
~24–26 GB/s a Gen4 link should give.

## What was ruled out

A long investigation eliminated the obvious suspects one by one:

- **PCIe link speed/width** — link trained fine; the risers weren't the cause of
  the *sustained* slowness (P2P itself stayed ~24 GB/s after the problem-1 fix).
- **IOMMU mode** — `iommu=pt` vs `amd_iommu=off` made no difference to this.
- **Memory encryption** — TSME / SMEE / transparent SME toggles: no effect.
- **Data Fabric / C-state / power settings** — no effect.
- **SWIOTLB bounce buffering** — the kernel message was a dormant fallback;
  `io_tlb_used=0`, so it was not actually bouncing.
- **Linux-specific?** No — a **Windows** cross-check showed the **same
  catastrophic DRAM collapse**, proving it's a hardware/firmware problem, not an
  OS issue. The Windows measurements used the scripts in
  [`tools/windows/`](../tools/windows/):
  - [`winsat_mem.ps1`](../tools/windows/winsat_mem.ps1) — Windows' official
    WinSAT memory benchmark (neutral, non-custom): ~3,368 MB/s throttled vs
    51,716 MB/s (MemoryScore 8.7) once all 8 DIMMs had a fan — a ~15× swing.
  - [`mem_bw.ps1`](../tools/windows/mem_bw.ps1) — the same cache-vs-DRAM scan as
    `mem_bw.py`, showing cache fast / DRAM collapsed.
  - [`mem_heatload.ps1`](../tools/windows/mem_heatload.ps1) — the sustained load
    that made the throttling visible over time (see below).

## The key measurement

The breakthrough was measuring **system DRAM bandwidth directly**, separately
from cache, with [`tools/linux/mem_bw.py`](../tools/linux/mem_bw.py):

```bash
python tools/linux/mem_bw.py
```

This distinguishes:
- **cache bandwidth** — fast if the cores/uncore are healthy, and
- **DRAM bandwidth** — the thing that was actually broken.

### Broken (warm / heat-soaked)

- cache: ~96 GiB/s (**healthy** — cores are fine)
- DRAM: **~1–3 GiB/s** (should be ~170 GB/s theoretical for 8-channel DDR4-2667;
  the dual-channel x570 box does ~40 GiB/s)

Cache fast + DRAM collapsed ⇒ the **memory subsystem** is the bottleneck. And
because H2D/D2H transfers stage through host DRAM, the GPUs inherited the stall
— that's why "async offloading" looked broken.

## Root cause

**DIMM thermal throttling.** The board carries **8× 64 GB quad-rank LRDIMMs**
(Samsung M386A8K40BM2-CTD) packed densely with essentially **no airflow** over
them. Quad-rank LRDIMMs run hot. As they heat-soak (~66 °C+), the memory
controller throttles hard and DRAM bandwidth collapses to a crawl — and it
doesn't recover until they cool. This matches the observed behavior *exactly*:
cold = a few good runs, then permanent slowdown once heat-soaked.

Supporting evidence:
- Single-DIMM / DIMM-pair configs tested healthy (~16 / 33 GB/s); the collapse
  only appeared in the **dense, fully-populated** config **at temperature**.
- A **BIOS update (1602 → 1801) did NOT fix it** — it's thermal, not firmware.

## Fix

**Add active airflow over the DIMMs** (a fan directed across the memory banks).
With the RAM kept cool, bandwidth stays high and does not collapse under
sustained load.

## Verification — AFTER cooling

`python tools/linux/mem_bw.py`:
- cache: **~92.5 GiB/s**
- DRAM 1 thread: **31.4 GiB/s**
- DRAM 4 thread: **56.9 GiB/s**
- DRAM 8 thread: **69.6 GiB/s**

`python tools/linux/pcie_bw.py --p2p`:
- H2D / D2H: **~25–26 GB/s** on both GPUs
- P2P integrity OK, P2P bandwidth **~24.8 GB/s**

Async offloading now performs as expected and stays fast under sustained load.

## Update — CPU upgrade 5955WX → 5995WX

Later the CPU was swapped from a **5955WX (16-core, 2 CCDs)** to a **5995WX
(64-core, 8 CCDs)**, same board / RAM / cooling. More CCDs means far more GMI
(Infinity Fabric) bandwidth from the core complexes to the I/O die, so aggregate
multi-threaded DRAM bandwidth roughly **doubled**. Single-thread and cache
numbers are unchanged, as expected — those are per-core / latency-bound, not
limited by fabric width.

`python tools/linux/mem_bw.py`:

| Metric | 5955WX (cooled) | 5995WX |
|---|---|---|
| cache | 92.5 GiB/s | 94.7 GiB/s |
| DRAM 1 thread | 31.4 GiB/s | 31.3 GiB/s |
| DRAM 4 thread | 56.9 GiB/s | **101.1 GiB/s** |
| DRAM 8 thread | 69.6 GiB/s | **134.3 GiB/s** |

`python tools/linux/pcie_bw.py --p2p` (unchanged — link-bound):
- H2D 23–26 GB/s, D2H ~26 GB/s on both GPUs, Gen4 x16 under load
- P2P integrity OK, P2P bandwidth **~24.6 GB/s**, IOMMU domain `identity`
- `iommu=pt` still active, so the problem-1 P2P fix remains intact

No regressions from the swap; the higher DRAM bandwidth directly benefits async
offloading throughput. (GPU1 may show a Gen2 idle link in the report — that's
ASPM power-down; it ramps to Gen4 x16 under load.)

## Takeaways

- **"Slow GPU offloading" can actually be slow *system RAM*.** Host↔GPU
  transfers go through DRAM; if DRAM is throttled, transfers are throttled.
- Measure **DRAM separately from cache** — a fast cache number hides a dead DRAM
  subsystem.
- A **"fast when cold, slow when warm, never recovers"** pattern is the
  fingerprint of **thermal throttling**. Suspect it early.
- Dense **quad-rank LRDIMM** builds need real airflow; server DIMMs assume
  chassis wind that a workstation/open-air/riser build may not provide.
- Cross-check against another OS to cheaply confirm hardware vs. software.
