#!/usr/bin/env python3
# From: Vauzi-17 (methodology), extended
"""A8XX: complete the Shader-Processor hysteresis set (HS/DS/GS).

54-a8xx-sp-hysteresis.py initialises PS/VS/CS. The A8xx register file also
defines SP_HS_HYSTERESIS (0xa83e), SP_DS_HYSTERESIS (0xa866) and
SP_GS_HYSTERESIS (0xa897) with the same a8xx_sp_xs_hysteresis type, and
a6xx.xml notes "UMD needs to write [these] in some cases".

Leaving them at 0 lets the HS/DS/GS ALU clusters power-gate between draws the
same way PS/VS/CS used to, which costs wake-up latency in tessellation- and
geometry-heavy workloads (common in Switch/PC emulation).

Runs from the Mesa source root. Idempotent; warns and skips if the anchor
moved (upstream refactor) so it can never fail the build.
"""
import sys

NAME = "55-a8xx-sp-hysteresis-gs-hs-ds.py"
TARGET = "src/freedreno/vulkan/tu_cmd_buffer.cc"
MARKER = "SP_GS_HYSTERESIS(CHIP"

OLD = (
    "      tu_cs_emit_regs(cs, SP_CS_HYSTERESIS(CHIP, .dword = 0x00000400));\n"
    "   }\n"
)

NEW = (
    "      tu_cs_emit_regs(cs, SP_CS_HYSTERESIS(CHIP, .dword = 0x00000400));\n"
    "      tu_cs_emit_regs(cs, SP_HS_HYSTERESIS(CHIP, .dword = 0x00000400));\n"
    "      tu_cs_emit_regs(cs, SP_DS_HYSTERESIS(CHIP, .dword = 0x00000400));\n"
    "      tu_cs_emit_regs(cs, SP_GS_HYSTERESIS(CHIP, .dword = 0x00000400));\n"
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

    found = content.count(OLD)
    if found != 1:
        print(f"{NAME}: WARNING anchor matched {found}x (want 1) in {TARGET}; skipped")
        return 0

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content.replace(OLD, NEW, 1))
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (SP_HS/DS/GS_HYSTERESIS = 0x00000400)")
    return 0


if __name__ == "__main__":
    sys.exit(main())