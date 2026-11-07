#!/usr/bin/env python3
# From: Vauzi-17 (methodology), extended
"""A8XX: make the Shader-Processor hysteresis thresholds per-GPU and tune A810.

54-a8xx-sp-hysteresis.py writes the *minimum* blob-observed thresholds
(PS 0xc00, VS/CS 0x400). a6xx.xml documents the values the blob actually uses
across captures: 0x400, 0xc00, 0x1000, 0x1c00, 0x1000, 0x2000, 0x3000.

Higher thresholds keep SP ALU clusters powered/clocked longer, which lowers
wake-up latency in draw-call-heavy workloads (exactly what the original
hysteresis patch fixed, just further). The RT workaround already uses 0x10000
temporarily, so the hardware tolerates much larger values than 0x3000.

This patch:
  1. adds per-stage `sp_*_hysteresis` GPU props (0 means "use the driver
     default", so every other A8xx part keeps its current behaviour),
  2. rewrites the A8XX init block to emit from those props, and
  3. sets the blob-observed upper range for A810 in freedreno_devices.py.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import re
import sys

NAME = "56-a8xx-sp-hysteresis-tune.py"
DEV_INFO = "src/freedreno/common/freedreno_dev_info.h"
CMD_BUF = "src/freedreno/vulkan/tu_cmd_buffer.cc"
DEVICES = "src/freedreno/common/freedreno_devices.py"

MARKER = "hp.sp_ps_hysteresis"

DEV_INFO_OLD = "      uint32_t vsc_unknown_0d08;\n"
DEV_INFO_NEW = (
    "      uint32_t vsc_unknown_0d08;\n"
    "\n"
    "      /* Shader Processor hysteresis thresholds. 0 = driver default. */\n"
    "      uint32_t sp_ps_hysteresis;\n"
    "      uint32_t sp_vs_hysteresis;\n"
    "      uint32_t sp_cs_hysteresis;\n"
    "      uint32_t sp_hs_hysteresis;\n"
    "      uint32_t sp_ds_hysteresis;\n"
    "      uint32_t sp_gs_hysteresis;\n"
)

BLOCK_RE = re.compile(
    r"   if \(CHIP >= A8XX\) \{\n"
    r"      tu_cs_emit_regs\(cs, SP_ALPHA_TEST_CNTL\(CHIP\)\);\n"
    r"(?:      tu_cs_emit_regs\(cs, SP_(?:PS|VS|CS|HS|DS|GS)_HYSTERESIS\(CHIP[^\n]*\n)+"
    r"   \}\n"
)

CANON = (
    "   if (CHIP >= A8XX) {\n"
    "      const auto &hp = phys_dev->info->props;\n"
    "      tu_cs_emit_regs(cs, SP_ALPHA_TEST_CNTL(CHIP));\n"
    "      tu_cs_emit_regs(cs, SP_PS_HYSTERESIS(CHIP, .dword = hp.sp_ps_hysteresis ? hp.sp_ps_hysteresis : 0x00000c00));\n"
    "      tu_cs_emit_regs(cs, SP_VS_HYSTERESIS(CHIP, .dword = hp.sp_vs_hysteresis ? hp.sp_vs_hysteresis : 0x00000400));\n"
    "      tu_cs_emit_regs(cs, SP_CS_HYSTERESIS(CHIP, .dword = hp.sp_cs_hysteresis ? hp.sp_cs_hysteresis : 0x00000400));\n"
    "      tu_cs_emit_regs(cs, SP_HS_HYSTERESIS(CHIP, .dword = hp.sp_hs_hysteresis ? hp.sp_hs_hysteresis : 0x00000400));\n"
    "      tu_cs_emit_regs(cs, SP_DS_HYSTERESIS(CHIP, .dword = hp.sp_ds_hysteresis ? hp.sp_ds_hysteresis : 0x00000400));\n"
    "      tu_cs_emit_regs(cs, SP_GS_HYSTERESIS(CHIP, .dword = hp.sp_gs_hysteresis ? hp.sp_gs_hysteresis : 0x00000400));\n"
    "   }\n"
)

A810_ANCHOR = "    enable_tp_ubwc_flag_hint = True,"
A810_TUNED = (
    "    # Shader-Processor hysteresis thresholds (blob-observed upper range).\n"
    "    # 0 means \"use the driver default\" for any other A8xx part.\n"
    "    sp_ps_hysteresis = 0x00003000,\n"
    "    sp_vs_hysteresis = 0x00001000,\n"
    "    sp_cs_hysteresis = 0x00001000,\n"
    "    sp_hs_hysteresis = 0x00001000,\n"
    "    sp_ds_hysteresis = 0x00001000,\n"
    "    sp_gs_hysteresis = 0x00001000,\n"
)


def warn(msg):
    print(f"{NAME}: WARNING {msg}; skipped")
    return 0


def main():
    try:
        dev_info = open(DEV_INFO, "r", encoding="utf-8").read()
        cmd_buf = open(CMD_BUF, "r", encoding="utf-8").read()
        devices = open(DEVICES, "r", encoding="utf-8").read()
    except OSError as e:
        return warn(f"cannot read source files: {e}")

    if MARKER in cmd_buf:
        print(f"{NAME}: already applied ({MARKER} present)")
        return 0

    # 1. GPU props
    if "uint32_t sp_ps_hysteresis;" not in dev_info:
        if dev_info.count(DEV_INFO_OLD) != 1:
            return warn(f"dev_info anchor matched {dev_info.count(DEV_INFO_OLD)}x (want 1)")
        dev_info = dev_info.replace(DEV_INFO_OLD, DEV_INFO_NEW, 1)

    # 2. Emit from props (works with or without 55-a8xx-sp-hysteresis-gs-hs-ds.py)
    matches = BLOCK_RE.findall(cmd_buf)
    if len(matches) < 1:
        return warn("A8XX SP hysteresis init block not found in tu_cmd_buffer.cc")
    cmd_buf = BLOCK_RE.sub(CANON, cmd_buf, count=1)

    # 3. A810 tuning, scoped to the a8xx_810 GPUProps block
    start = devices.find("a8xx_810 = GPUProps(")
    if start < 0:
        return warn("a8xx_810 GPUProps block not found in freedreno_devices.py")
    end = devices.find("\n)\n", start)
    if end < 0:
        return warn("a8xx_810 GPUProps block has no terminator")
    end += 3
    block = devices[start:end]
    if "sp_ps_hysteresis" not in block:
        # Anchor line may carry a trailing inline comment (e.g. the UBWC one),
        # so match on the line prefix rather than an exact full-line string.
        lines = block.split("\n")
        anchor_idx = None
        for i, ln in enumerate(lines):
            if ln.startswith(A810_ANCHOR):
                if anchor_idx is not None:
                    return warn("A810 GPUProps anchor (enable_tp_ubwc_flag_hint) not unique")
                anchor_idx = i
        if anchor_idx is None:
            return warn("A810 GPUProps anchor (enable_tp_ubwc_flag_hint) not found")
        # Insert the hysteresis block right after the anchor line.
        lines.insert(anchor_idx + 1, "\n" + A810_TUNED.rstrip("\n"))
        block = "\n".join(lines)
        devices = devices[:start] + block + devices[end:]

    try:
        open(DEV_INFO, "w", encoding="utf-8", newline="\n").write(dev_info)
        open(CMD_BUF, "w", encoding="utf-8", newline="\n").write(cmd_buf)
        open(DEVICES, "w", encoding="utf-8", newline="\n").write(devices)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write source files: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (per-GPU SP hysteresis props, A810 PS=0x3000 others=0x1000)")
    return 0


if __name__ == "__main__":
    sys.exit(main())