# RipperPC hardware debugging notes (Threadripper PRO / WRX80 / dual Blackwell)

Documentation, minimal reproductions, benchmarks, and fixes for two nasty
hardware/firmware issues found on a Threadripper PRO workstation running
ComfyUI. Both issues were **silent** — no crashes, no error messages — and both
were initially assumed to be software/ComfyUI bugs. Both turned out to be
platform-level.

If you have a similar box (AMD WRX80/TRX40 workstation, GPUs on PCIe risers,
many densely-packed LRDIMMs) and see corrupt multi-GPU output or mysteriously
slow host↔GPU transfers, this may save you a week.

## The machine ("RipperPC")

| Component     | Detail                                                        |
|---------------|---------------------------------------------------------------|
| CPU           | AMD Ryzen Threadripper PRO 5955WX                             |
| Motherboard   | ASUS Pro WS WRX80E-SAGE SE WIFI II                            |
| GPUs          | 2× NVIDIA RTX PRO 6000 (Blackwell), each on a PCIe riser      |
| Memory        | 8× Samsung M386A8K40BM2-CTD — 64 GB quad-rank DDR4-2667 LRDIMM (512 GB) |
| OS            | Ubuntu 24.04 (bug also reproduced on Windows for problem 2)   |

Both GPUs are mounted on **risers** (relevant: risers change PCIe signal
integrity and airflow), and the eight LRDIMMs are densely packed with limited
airflow.

## TL;DR

| Problem | Symptom | Root cause | Fix |
|---------|---------|------------|-----|
| **1. MultiGPU CFG Split → black/noise images** | Any CFG>1 workflow (even SD1.5) produces random-noise or black images when ComfyUI's MultiGPU CFG Split (`MultiGPU_WorkUnits`) is used. Single-GPU is fine. Windows and a dual-4090 box are fine. | AMD IOMMU (AMD-Vi) in **translation mode** silently corrupts GPU→GPU P2P DMA over PCIe. The destination GPU receives zeros/garbage; `can_device_access_peer` still reports `True`. | Boot with kernel param **`iommu=pt`** (IOMMU passthrough). |
| **2. "Abysmal" async offloading / slow host↔GPU** | Async offloading and H2D/D2H transfers are OK for a few runs after a cold boot, then collapse ~5–10× and never recover. | **DIMM thermal throttling.** The dense quad-rank LRDIMMs heat-soak with no airflow; DRAM bandwidth collapses from ~90 GiB/s to ~1–3 GiB/s and everything (including PCIe transfers that stage through host RAM) inherits the stall. | **Active DIMM airflow** (a fan over the memory). |

Full write-ups:
- [docs/01-multigpu-cfg-split-p2p-corruption.md](docs/01-multigpu-cfg-split-p2p-corruption.md)
- [docs/02-memory-bandwidth-dimm-thermal-throttling.md](docs/02-memory-bandwidth-dimm-thermal-throttling.md)

## Diagnostic tools

### Linux — [`tools/linux/`](tools/linux/) (needs `torch` + `numpy`)

| Script | Purpose |
|--------|---------|
| [`tools/linux/p2p_repro.py`](tools/linux/p2p_repro.py) | Minimal reproduction of the GPU→GPU P2P corruption (problem 1). Isolates the bug without booting ComfyUI. |
| [`tools/linux/pcie_bw.py`](tools/linux/pcie_bw.py) | PCIe host↔GPU bandwidth + link/IOMMU report, optional P2P integrity/bandwidth (`--p2p`). |
| [`tools/linux/mem_bw.py`](tools/linux/mem_bw.py) | System DRAM vs cache bandwidth (problem 2 — surfaces the thermal collapse). |

```bash
python tools/linux/p2p_repro.py      # is P2P silently corrupt?
python tools/linux/pcie_bw.py --p2p  # link speed + H2D/D2H/P2P bandwidth + integrity
python tools/linux/mem_bw.py         # DRAM vs cache bandwidth
```

### Windows — [`tools/windows/`](tools/windows/) (built-in PowerShell, no deps)

These are the memory tests used for the Windows cross-check that proved the DRAM
collapse was hardware/firmware, not Linux-specific.

| Script | Purpose |
|--------|---------|
| [`tools/windows/mem_bw.ps1`](tools/windows/mem_bw.ps1) | Cache-vs-DRAM bandwidth scan (Windows twin of `mem_bw.py`). Small buffers = cache, large = DRAM. |
| [`tools/windows/mem_heatload.ps1`](tools/windows/mem_heatload.ps1) | Sustained multi-minute load with per-interval bandwidth — **the script that surfaced the thermal collapse**. Watch DIMM temps alongside it. |
| [`tools/windows/winsat_mem.ps1`](tools/windows/winsat_mem.ps1) | Runs Windows' official WinSAT memory benchmark (elevated) and parses the MB/s + score — a neutral, non-custom confirmation. |

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\mem_bw.ps1
powershell -ExecutionPolicy Bypass -File tools\windows\mem_heatload.ps1 -Label "no fan"
powershell -ExecutionPolicy Bypass -File tools\windows\winsat_mem.ps1 -Name all8-fan
```
