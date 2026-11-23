#!/usr/bin/env python3
# From: WinterMist010 (candidate, needs an A/B measurement before promotion)
"""OPT-IN: give A810 UBWC for single-channel 8-bit formats (R8 class).

Upstream inherits has_8bpp_ubwc = False for A810 through the a7xx chain
(only a6xx_base sets it True). Every R8/R8_SNORM/R8_UINT image and render
target is therefore uncompressed on A810 today, while the proprietary
driver compresses them - DXVK and the console emulators use R8-class
resources heavily (masks, font/alpha atlases, metadata planes).

A810 is bandwidth-starved, so 8bpp UBWC is a plausible win, but it has to
be confirmed on hardware before it becomes a default:

  1. A/B it with no driver change at all:
       FD_DEV_FEATURES=has_8bpp_ubwc=1   (same knob this patch defaults)
  2. Check an A810 blob capture actually enables UBWC on 8bpp images.
  3. If it wins and renders clean, copy this script into patches/base/.

Escape hatches once applied: TU_DEBUG=noubwc, or
FD_DEV_FEATURES=has_8bpp_ubwc=0 (the env wins over the built-in value).

Runs from the Mesa source root, after 0000-a8xx.patch (which moves the A810
props into the shared a8xx_810 GPUProps set). Also handles the pre-0000
layout (inline GPUProps in the A810 add_gpus entry). Idempotent; warns and
skips on anchor drift.
"""
import re
import sys

NAME = "65-a8xx-8bpp-ubwc.py"
MARKER = "65-a8xx-8bpp-ubwc"
TARGET = "src/freedreno/common/freedreno_devices.py"

# Post-0000 layout: shared a8xx_810 GPUProps set.
BLOCK_START = "a8xx_810 = GPUProps("
BLOCK_END = "\n)\n"
FS_PREFETCH = re.compile(r"^([ \t]*)has_fs_tex_prefetch = False,[^\n]*\n", re.M)

# Pre-0000 layout: inline GPUProps in the A810 entry.
LEGACY_OLD = (
    "            # This is possibly also needed for a830 (and all of a8xx),\n"
    "            # move to a8xx_base if confirmed needed for a830.\n"
    "            has_fs_tex_prefetch = False,\n"
)
LEGACY_NEW = (
    LEGACY_OLD
    + "            # 65-a8xx-8bpp-ubwc.py: single-channel 8-bit formats get\n"
    "            # UBWC on A810. TU_DEBUG=noubwc disables at runtime.\n"
    "            has_8bpp_ubwc = True,\n"
)


def patch_a8xx_810(content):
    if content.count(BLOCK_START) != 1:
        return None, f"{BLOCK_START!r} matched {content.count(BLOCK_START)}x (want 1)"
    start = content.index(BLOCK_START)
    end = content.find(BLOCK_END, start)
    if end < 0:
        return None, "a8xx_810 block terminator not found"
    end += 1
    block = content[start:end]
    hits = list(FS_PREFETCH.finditer(block))
    if len(hits) != 1:
        return None, f"has_fs_tex_prefetch matched {len(hits)}x in a8xx_810 (want 1)"
    m = hits[0]
    line = (
        f"{m.group(1)}has_8bpp_ubwc = True,"
        f"                 # {MARKER}.py: R8-class UBWC on A810; "
        "TU_DEBUG=noubwc disables at runtime\n"
    )
    block = block[: m.end()] + line + block[m.end():]
    return content[:start] + block + content[end:], None


def patch_legacy(content):
    found = content.count(LEGACY_OLD)
    if found != 1:
        return None, f"legacy fs_tex_prefetch anchor matched {found}x (want 1)"
    return content.replace(LEGACY_OLD, LEGACY_NEW, 1), None


def main():
    try:
        content = open(TARGET, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read {TARGET}: {e}; skipped")
        return 0

    if MARKER in content:
        print(f"{NAME}: already applied ({MARKER} present)")
        return 0

    new, err = patch_a8xx_810(content)
    if new is None:
        new, err_legacy = patch_legacy(content)
        if new is None:
            print(f"{NAME}: WARNING a8xx_810: {err}; legacy: {err_legacy}; "
                  f"skipped in {TARGET} (a8xx bundle rework present?)")
            return 0

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (A810 has_8bpp_ubwc = True)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
