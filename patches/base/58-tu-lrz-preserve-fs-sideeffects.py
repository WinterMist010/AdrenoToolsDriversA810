#!/usr/bin/env python3
# From: Vauzi-17
"""LRZ: do not permanently kill depth culling for an FS-side-effect draw.

Upstream Turnip permanently invalidates LRZ for the rest of the render pass when
a fragment shader writes depth (or has side effects) while the depth direction
is still TU_LRZ_UNKNOWN under GPU direction tracking. Every later draw in that
pass then loses early-Z.

This patch instead temporarily disables LRZ for the current draw only, letting
subsequent draws use LRZ again. It is a heuristic: with an unknown direction the
conservative choice really is to invalidate, so it is OFF by default and left as
an opt-in experiment.

Env TU_LRZ_PRESERVE_FS_SIDEEFFECTS (default false):
  true   temporarily disable LRZ for the current draw only
  false  permanently invalidate LRZ for the render pass (upstream behaviour)

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "58-tu-lrz-preserve-fs-sideeffects.py"
MARKER = "TU_LRZ_PRESERVE_FS_SIDEEFFECTS"
TARGET = "src/freedreno/vulkan/tu_lrz.cc"

INCLUDE_OLD = '#include "tu_image.h"\n'
INCLUDE_NEW = (
    '#include "tu_image.h"\n'
    '\n'
    '#include "util/u_debug.h"\n'
    '\n'
    'DEBUG_GET_ONCE_BOOL_OPTION(tu_lrz_preserve_fs_sideeffects, "TU_LRZ_PRESERVE_FS_SIDEEFFECTS", false)\n'
)

BLOCK_OLD = (
    "   if (disable_lrz_due_to_fs) {\n"
    "      if (cmd->state.lrz.prev_direction != TU_LRZ_UNKNOWN || !cmd->state.lrz.gpu_dir_tracking) {\n"
    "         perf_debug(cmd->device, \"Skipping LRZ due to FS\");\n"
    "         temporary_disable_lrz = true;\n"
    "      } else {\n"
    "         tu_lrz_invalidate(cmd, \"FS writes depth or has side-effects (TODO: fix for gpu-direction-tracking case)\");\n"
    "      }\n"
    "   }\n"
)
BLOCK_NEW = (
    "   if (disable_lrz_due_to_fs) {\n"
    "      if (debug_get_option_tu_lrz_preserve_fs_sideeffects() ||\n"
    "          cmd->state.lrz.prev_direction != TU_LRZ_UNKNOWN || !cmd->state.lrz.gpu_dir_tracking) {\n"
    "         perf_debug(cmd->device, \"Skipping LRZ due to FS\");\n"
    "         temporary_disable_lrz = true;\n"
    "      } else {\n"
    "         tu_lrz_invalidate(cmd, \"FS writes depth or has side-effects (TODO: fix for gpu-direction-tracking case)\");\n"
    "      }\n"
    "   }\n"
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

    for old, what in ((INCLUDE_OLD, "include"), (BLOCK_OLD, "disable_lrz_due_to_fs block")):
        found = content.count(old)
        if found != 1:
            print(f"{NAME}: WARNING {what} anchor matched {found}x (want 1) in {TARGET}; skipped")
            return 0

    content = content.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
    content = content.replace(BLOCK_OLD, BLOCK_NEW, 1)

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (TU_LRZ_PRESERVE_FS_SIDEEFFECTS, default false)")
    return 0


if __name__ == "__main__":
    sys.exit(main())