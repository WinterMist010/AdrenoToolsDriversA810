#!/usr/bin/env python3
# From: Vauzi-17
"""A8XX: set FORCE_BI_DIR_LRZ_DISABLE in sysmem and LRZ-disabled passes.

On Adreno 8xx, hardware traces show GRAS_SC_BIN_CNTL (0x8231) sets bit 14
(FORCE_BI_DIR_LRZ_DISABLE) during sysmem rendering, and also while LRZ is
disabled, to keep the LRZ cache coherent when depth is updated from both
directions across draws in a pass.

A6xx/A7xx do not have this bit, so every edit is gated on CHIP >= A8XX.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "61-a8xx-lrz-bidir-disable.py"
MARKER = "force_bi_dir_lrz_disable"
CMD_BUF = "src/freedreno/vulkan/tu_cmd_buffer.cc"
LRZ_CC = "src/freedreno/vulkan/tu_lrz.cc"

EDITS = [
    (
        CMD_BUF,
        "   bool cons_vis_in_binning;\n};\n",
        "   bool cons_vis_in_binning;\n"
        "   bool force_bi_dir_lrz_disable;\n};\n",
    ),
    (
        CMD_BUF,
        "                                 .cons_vis_in_binning = p.cons_vis_in_binning, ));\n",
        "                                 .cons_vis_in_binning = p.cons_vis_in_binning,\n"
        "                                 .force_bi_dir_lrz_disable = p.force_bi_dir_lrz_disable, ));\n",
    ),
    (
        CMD_BUF,
        "         .force_lrz_dis = CHIP >= A7XX && disable_lrz,\n      });\n",
        "         .force_lrz_dis = CHIP >= A7XX && disable_lrz,\n"
        "         .force_bi_dir_lrz_disable = CHIP >= A8XX && disable_lrz,\n      });\n",
    ),
    (
        CMD_BUF,
        "         cmd->device->physical_device->info->props.has_lrz_feedback\n"
        "            ? LRZ_FEEDBACK_EARLY_Z_OR_EARLY_Z_LATE_Z\n"
        "            : LRZ_FEEDBACK_NONE,\n"
        "   });\n",
        "         cmd->device->physical_device->info->props.has_lrz_feedback\n"
        "            ? LRZ_FEEDBACK_EARLY_Z_OR_EARLY_Z_LATE_Z\n"
        "            : LRZ_FEEDBACK_NONE,\n"
        "      .force_bi_dir_lrz_disable = CHIP >= A8XX,\n"
        "   });\n",
    ),
    (
        LRZ_CC,
        "      const struct tu_reg_value reg = GRAS_SC_BIN_CNTL(CHIP, .force_lrz_dis = true);\n",
        "      const struct tu_reg_value reg = GRAS_SC_BIN_CNTL(CHIP, .force_lrz_dis = true,\n"
        "                                                             .force_bi_dir_lrz_disable = CHIP >= A8XX);\n",
    ),
]


def main():
    files = {}
    for path, _, _ in EDITS:
        if path in files:
            continue
        try:
            files[path] = open(path, "r", encoding="utf-8").read()
        except OSError as e:
            print(f"{NAME}: WARNING cannot read {path}: {e}; skipped")
            return 0

    if all(MARKER in s for s in files.values()):
        print(f"{NAME}: already applied ({MARKER} present)")
        return 0

    for path, old, _ in EDITS:
        found = files[path].count(old)
        if found != 1:
            print(f"{NAME}: WARNING anchor matched {found}x (want 1) in {path}; skipped")
            return 0

    for path, old, new in EDITS:
        files[path] = files[path].replace(old, new, 1)

    try:
        for path, content in files.items():
            open(path, "w", encoding="utf-8", newline="\n").write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write source files: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (A8XX FORCE_BI_DIR_LRZ_DISABLE)")
    return 0


if __name__ == "__main__":
    sys.exit(main())