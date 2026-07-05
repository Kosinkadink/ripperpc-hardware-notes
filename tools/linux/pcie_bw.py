"""
PCIe host<->GPU bandwidth + link diagnostic.

Re-run this after each BIOS change / riser swap to see whether host<->device
transfer bandwidth improves. On a healthy Gen4 x16 link expect ~20-25 GB/s
pinned H2D/D2H; ~12-13 GB/s on Gen4 x8. RipperPC's fault shows as ~4 GB/s
(Gen1 x16-class) on every GPU.

For each GPU it reports:
  - PCIe link speed/width when idle and during a sustained transfer
  - IOMMU domain type (identity = passthrough, DMA/DMA-FQ = translating)
  - pinned H2D and D2H bandwidth (CUDA-event timed)
  - optional P2P integrity + cross-GPU copy bandwidth (--p2p)

Usage:
  python notes/pcie_bw.py            # bandwidth + link report
  python notes/pcie_bw.py --p2p      # also test direct GPU<->GPU P2P
  python notes/pcie_bw.py --size 512 # transfer buffer size in MiB (default 256)
"""
import argparse, subprocess, sys, time
import torch

GEN = {"2.5 GT/s PCIe": "Gen1", "5.0 GT/s PCIe": "Gen2", "8.0 GT/s PCIe": "Gen3",
       "16.0 GT/s PCIe": "Gen4", "32.0 GT/s PCIe": "Gen5"}

def bus_ids():
    """index -> sysfs BDF (e.g. '0000:2e:00.0')."""
    out = subprocess.run(["nvidia-smi", "--query-gpu=index,pci.bus_id",
                          "--format=csv,noheader"], capture_output=True, text=True).stdout
    m = {}
    for line in out.strip().splitlines():
        idx, bus = [s.strip() for s in line.split(",")]
        # nvidia-smi: 00000000:2E:00.0  ->  sysfs: 0000:2e:00.0
        m[int(idx)] = ("0000:" + bus.split(":", 1)[1]).lower()
    return m

def readf(bdf, name):
    try:
        return open(f"/sys/bus/pci/devices/{bdf}/{name}").read().strip()
    except OSError:
        return "?"

def link(bdf):
    spd = readf(bdf, "current_link_speed")
    return f"{GEN.get(spd, spd)} x{readf(bdf, 'current_link_width')}"

def iommu_domain(bdf):
    return readf(bdf, "iommu_group/type")

def timed(dev, fn, iters=20):
    for _ in range(3):
        fn()
    torch.cuda.synchronize(dev)
    s = torch.cuda.Event(True); e = torch.cuda.Event(True)
    s.record(torch.cuda.current_stream(dev))
    for _ in range(iters):
        fn()
    e.record(torch.cuda.current_stream(dev)); torch.cuda.synchronize(dev)
    return s.elapsed_time(e) / iters  # ms

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=256, help="buffer MiB")
    ap.add_argument("--p2p", action="store_true", help="also test GPU<->GPU P2P")
    args = ap.parse_args()

    n = (args.size * 1024 * 1024) // 4
    gib = n * 4 / (1024 ** 3)
    ids = bus_ids()
    print(f"torch {torch.__version__}  cuda {torch.version.cuda}  "
          f"buffer {args.size} MiB  devices {torch.cuda.device_count()}")
    print(f"{'GPU':4} {'BDF':14} {'idle-link':10} {'iommu':9} "
          f"{'H2D GB/s':>9} {'D2H GB/s':>9} {'load-link':10}")

    host = torch.empty(n, dtype=torch.float32).pin_memory()
    for i in range(torch.cuda.device_count()):
        dev = f"cuda:{i}"
        bdf = ids.get(i, "?")
        idle = link(bdf)
        g = torch.empty(n, dtype=torch.float32, device=dev)
        h2d = gib / (timed(dev, lambda: g.copy_(host, non_blocking=True)) / 1000)
        load = link(bdf)  # sampled right after a burst
        d2h = gib / (timed(dev, lambda: host.copy_(g, non_blocking=True)) / 1000)
        print(f"{i:<4} {bdf:14} {idle:10} {iommu_domain(bdf):9} "
              f"{h2d:9.1f} {d2h:9.1f} {load:10}")
        del g
        torch.cuda.empty_cache()

    if args.p2p and torch.cuda.device_count() >= 2:
        print("\nP2P (GPU0 <-> GPU1):")
        print("  can_device_access_peer 0->1:", torch.cuda.can_device_access_peer(0, 1))
        a = torch.arange(n, dtype=torch.float32, device="cuda:0")
        torch.cuda.synchronize(0)
        b = a.to("cuda:1"); torch.cuda.synchronize(1)
        ok = torch.equal(b.cpu(), a.cpu())
        ms = timed("cuda:0", lambda: a.to("cuda:1"))
        print(f"  integrity: {'OK' if ok else 'CORRUPT (data does not match!)'}")
        print(f"  0->1 copy bandwidth: {gib / (ms / 1000):.1f} GB/s")

if __name__ == "__main__":
    main()
