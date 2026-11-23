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

Runs from the Mesa source root, after 64-a810-vpc-buffer-sizes.py if both
are used (the builder applies .py scripts in name order). Idempotent;
warns and skips on anchor drift.
"""
import sys

NAME = "65-a8xx-8bpp-ubwc.py"
MARKER = "65-a8xx-8bpp-ubwc"
TARGET = "src/freedreno/common/freedreno_devices.py"

OLD = (
    "            # This is possibly also needed for a830 (and all of a8xx),\n"
    "            # move to a8xx_base if confirmed needed for a830.\n"
    "            has_fs_tex_prefetch = False,\n"
)
NEW = (
    "            # This is possibly also needed for a830 (and all of a8xx),\n"
    "            # move to a8xx_base if confirmed needed for a830.\n"
    "            has_fs_tex_prefetch = False,\n"
    "            # 65-a8xx-8bpp-ubwc.py: single-channel 8-bit formats get\n"
    "            # UBWC on A810. TU_DEBUG=noubwc disables at runtime.\n"
    "            has_8bpp_ubwc = True,\n"
)


def main():
    try:
        content = open(TARGET, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read {TARGET}: {e}; skipped")
        return 0

    if MARKER in content:
        print(f"{NAME}: already applied ({MARKER} present)")
        return 0

    found = content.count(OLD)
    if found != 1:
        print(f"{NAME}: WARNING A810 fs_tex_prefetch anchor matched {found}x (want 1) "
              f"in {TARGET}; skipped (a8xx bundle rework present?)")
        return 0

    content = content.replace(OLD, NEW, 1)

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (A810 has_8bpp_ubwc = True)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
