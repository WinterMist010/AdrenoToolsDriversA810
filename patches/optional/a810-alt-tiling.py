#!/usr/bin/env python3
# From: Vauzi-17 (A810 blob tiling), A/B toggle
"""OPT-IN A/B: switch A810 between the current config and the blob tiling.

Vauzi-17's A810 blob analysis (51-fd810-blob.py) uses num_ccu = 1 with
tile_align 32x16, i.e. more, smaller bins. This repo's a8xx_810 uses
num_ccu = 2 with 64x32, which is what VPC/GMEM offsets were tuned against.

On a 1-slice / 576 KiB-GMEM part this genuinely moves both the per-bin
overhead and the tile-space/CCU partitioning, so it is worth measuring rather
than guessing. Running this script flips the values; run it again to flip back.

Not applied by either builder. To try it:

    cp patches/optional/a810-alt-tiling.py patches/base/

Runs from the Mesa source root. Warns + skips if the A810 block is not found.
"""
import sys

NAME = "a810-alt-tiling.py"
TARGET = "src/freedreno/common/freedreno_devices.py"

HEAD = "        [a7xx_base, a7xx_gen3, a8xx_base, a8xx_810],\n"
CURRENT = (
    HEAD +
    "        num_ccu = 2,\n"
    "        num_slices = 1,\n"
    "        tile_align_w = 64,\n"
    "        tile_align_h = 32,\n"
)
ALT = (
    HEAD +
    "        num_ccu = 1,\n"
    "        num_slices = 1,\n"
    "        tile_align_w = 32,\n"
    "        tile_align_h = 16,\n"
)


def main():
    try:
        content = open(TARGET, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read {TARGET}: {e}; skipped")
        return 0

    cur = content.count(CURRENT)
    alt = content.count(ALT)

    if cur == 1 and alt == 0:
        new, msg = content.replace(CURRENT, ALT, 1), "current (2 ccu, 64x32) -> alt (1 ccu, 32x16)"
    elif alt == 1 and cur == 0:
        new, msg = content.replace(ALT, CURRENT, 1), "alt (1 ccu, 32x16) -> current (2 ccu, 64x32)"
    else:
        print(f"{NAME}: WARNING A810 block matched current={cur} alt={alt} "
              f"(want exactly one variant); skipped")
        return 0

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied ({msg})")
    return 0


if __name__ == "__main__":
    sys.exit(main())