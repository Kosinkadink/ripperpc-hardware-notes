"""
Minimal reproduction of suspected cuda:0 <-> cuda:1 P2P transfer corruption.

Mirrors what ComfyUI MultiGPU CFG Split does: allocate a tensor on one GPU,
copy it to the other GPU, do work, copy back, and compare against a CPU-side
reference. On a healthy machine every check passes. On a machine where P2P over
PCIe is silently corrupted (e.g. AMD IOMMU in translation mode) the copied data
does not match.
"""
import torch

def hr(t): print("-" * 60); print(t)

def can_access_peer():
    hr("can_device_access_peer (driver's opinion)")
    for a in range(torch.cuda.device_count()):
        for b in range(torch.cuda.device_count()):
            if a != b:
                print(f"  {a}->{b}: {torch.cuda.can_device_access_peer(a, b)}")

def integrity_test(numel=1 << 24, trials=20, dtype=torch.float32):
    """numel*4 bytes per tensor; 1<<24 float32 = 64 MiB."""
    hr(f"P2P integrity: {numel} elts, {trials} trials, {dtype}")
    bad = 0
    for i in range(trials):
        # Reference on CPU, deterministic per-trial
        cpu = torch.arange(numel, dtype=torch.float32).add_(i * 7.0)
        if dtype != torch.float32:
            cpu = cpu.to(dtype)

        src = cpu.to("cuda:0", non_blocking=False)
        torch.cuda.synchronize(0)

        # The critical operation: direct GPU0 -> GPU1 copy (P2P if enabled)
        dst = src.to("cuda:1", non_blocking=False)
        torch.cuda.synchronize(1)

        # Pull back down to CPU from GPU1 (goes through host, generally safe)
        back = dst.cpu()

        if not torch.equal(back, cpu):
            bad += 1
            mism = (back != cpu)
            n_mis = int(mism.sum())
            first = int(mism.nonzero()[0][0]) if n_mis else -1
            print(f"  trial {i:2d}: CORRUPT  mismatches={n_mis}/{numel} "
                  f"first_idx={first} got={back[first].item()} want={cpu[first].item()}")
        else:
            print(f"  trial {i:2d}: ok")
    hr(f"RESULT: {bad}/{trials} trials corrupted")
    return bad

def bidirectional_test(numel=1 << 22):
    """Copy 0->1->0 and compare to original (round trip)."""
    hr(f"Round-trip 0->1->0: {numel} elts")
    cpu = torch.randn(numel)
    g0 = cpu.to("cuda:0"); torch.cuda.synchronize(0)
    g1 = g0.to("cuda:1"); torch.cuda.synchronize(1)
    g0b = g1.to("cuda:0"); torch.cuda.synchronize(0)
    back = g0b.cpu()
    ok = torch.equal(back, cpu)
    print(f"  round-trip match: {ok}")
    if not ok:
        mism = (back != cpu)
        print(f"  mismatches={int(mism.sum())}/{numel}")
    return ok

if __name__ == "__main__":
    print("torch", torch.__version__, "cuda", torch.version.cuda)
    can_access_peer()
    integrity_test()
    bidirectional_test()
