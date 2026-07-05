# Problem 1 — MultiGPU CFG Split produces black / noise images

## Symptom

Running any ComfyUI workflow that uses **MultiGPU CFG Split**
(the `MultiGPU_WorkUnits` node — splits the positive and negative CFG passes
across two GPUs) produced **random noise or a black image**. This happened for
*any* CFG>1 workflow where the split can actually apply, with *any* model —
including something as light as SD1.5.

Critically:
- **Single-GPU (MultiGPU off) worked perfectly**, same seed.
- The **same ComfyUI on Windows** on the same box worked.
- A **second Ubuntu machine with dual RTX 4090s** (`kosin-x570-aorus-ultra`)
  worked. That machine represents how the feature behaves on ~everyone else's
  hardware.

So it was Linux-specific *and* machine-specific — a strong hint of a
platform/firmware issue rather than a ComfyUI bug. Strengthening the MultiGPU
feature upstream never fixed it here, and it persisted across multiple Python
and PyTorch versions.

## Hypothesis

MultiGPU CFG Split copies tensors directly between `cuda:0` and `cuda:1`. The
suspicion was that a **direct GPU→GPU transfer over PCIe (P2P DMA) was silently
corrupting the tensor** on this machine, while other machines were unaffected.

## Minimal reproduction (no ComfyUI needed)

[`tools/p2p_repro.py`](../tools/p2p_repro.py) reproduces exactly what the
MultiGPU split does, in ~40 lines of PyTorch:

1. Build a deterministic reference tensor on the CPU.
2. Upload it to `cuda:0`.
3. **Copy `cuda:0` → `cuda:1`** (this is the P2P DMA step under suspicion).
4. Pull it back to the CPU and compare against the reference.

```bash
python tools/p2p_repro.py
```

### Result on RipperPC — BEFORE the fix

- `can_device_access_peer 0->1: True` — the driver *claimed* P2P worked.
- Integrity test: **20/20 trials corrupted.** The data arriving on `cuda:1` did
  not match — it came back as zeros / garbage.
- A host-staged copy (GPU0 → CPU → GPU1) was **correct**.

That is the whole bug: direct P2P DMA silently returned bad data, and ComfyUI's
MultiGPU split relies on exactly that path, so the denoised latents were
garbage → noise/black output.

### Why the other machines were fine

On the dual-4090 x570 box, `can_device_access_peer` returns **`False`** — the
consumer 4090 driver does not expose P2P at all, so PyTorch/ComfyUI silently
**bounces the copy through host RAM**, which is correct (if slightly slower). It
wasn't healthy because of a special fix; it was healthy because it never took
the broken P2P path. Windows on RipperPC similarly did not hit the corrupt path.

## Root cause

**The AMD IOMMU (AMD-Vi) in translation mode was corrupting GPU↔GPU P2P DMA
transfers.** When the IOMMU actively translates addresses for peer-to-peer PCIe
traffic on this WRX80 + Blackwell + Linux combination, the transferred payload
is silently mangled. The driver's `can_device_access_peer` optimistically
reports `True`, so the framework happily uses the broken path.

## Fix

Boot the kernel with the IOMMU in **passthrough** mode so it does not translate
DMA for these devices:

1. Edit `/etc/default/grub`, add `iommu=pt` to `GRUB_CMDLINE_LINUX_DEFAULT`:
   ```
   GRUB_CMDLINE_LINUX_DEFAULT="quiet splash iommu=pt"
   ```
2. `sudo update-grub`
3. Reboot.
4. Confirm: `cat /proc/cmdline` shows `... quiet splash iommu=pt`.

`iommu=pt` keeps the IOMMU enabled (so features that need it still work) but
puts devices in identity-mapped / passthrough domains, avoiding the translation
step that corrupted P2P.

## Verification — AFTER the fix

- `cat /proc/cmdline` → includes `iommu=pt`.
- `python tools/p2p_repro.py` → **0/20 corrupted**, round-trip `0→1→0` matches.
- `python tools/pcie_bw.py --p2p` → P2P integrity **OK**, ~24 GB/s.
- End-to-end: ComfyUI Desktop (SD1.5, `MultiGPU_WorkUnits`) produced a coherent
  image with **MultiGPU ON and OFF at the same seed** — no more noise/black.

## Takeaways

- If multi-GPU output is silently wrong (not crashing) on an AMD workstation,
  suspect **IOMMU-vs-P2P** before blaming the application.
- `can_device_access_peer == True` does **not** mean P2P is *correct* — only that
  the driver thinks it's available. Always integrity-check, not just benchmark.
- A tiny PyTorch repro that mirrors the suspect data path is far faster to
  iterate on than the full application.
