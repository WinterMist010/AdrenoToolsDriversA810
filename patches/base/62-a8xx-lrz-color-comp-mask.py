#!/usr/bin/env python3
# From: Vauzi-17
"""A8XX: emit GRAS_LRZ_COLOR_COMP_MASK with the MRT blend state.

On Adreno 8xx (0x810c) the rasterizer LRZ block tracks per-MRT component write
enables through GRAS_LRZ_COLOR_COMP_MASK. Emitting it together with the
SP/RB MRT control packets keeps early-Z LRZ culling coherent across multi-MRT
and disabled-color-attachment draws, instead of falling back to conservative
behaviour.

A6xx/A7xx do not have the register, so the emission (and the extra size dwords)
are gated on CHIP >= A8XX.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "62-a8xx-lrz-color-comp-mask.py"
MARKER = "GRAS_LRZ_COLOR_COMP_MASK"
TARGET = "src/freedreno/vulkan/tu_pipeline.cc"

EDITS = [
    (
        "      MAX2(cb->attachment_count, 1) : cb->attachment_count;\n"
        "   return 8 + 5 * num_rts;\n",
        "      MAX2(cb->attachment_count, 1) : cb->attachment_count;\n"
        "   return 8 + (CHIP >= A8XX ? 2 : 0) + 5 * num_rts;\n",
    ),
    (
        "   unsigned num_remapped_rts = 0;\n"
        "   for (unsigned i = 0; i < num_rts; i++) {\n",
        "   unsigned num_remapped_rts = 0;\n"
        "   uint32_t lrz_color_comp_mask = 0;\n"
        "   for (unsigned i = 0; i < num_rts; i++) {\n",
    ),
    (
        "      if ((cb->color_write_enables & (1u << i)) && i < cb->attachment_count) {\n",
        "      if ((cb->color_write_enables & (1u << i)) && i < cb->attachment_count) {\n"
        "         if (CHIP >= A8XX && remapped_idx < 8)\n"
        "            lrz_color_comp_mask |= (att->write_mask & 0xf) << (remapped_idx * 4);\n",
    ),
    (
        "   tu_cs_emit_regs(cs, A6XX_SP_PS_MRT_CNTL(.mrt = num_remapped_rts));\n"
        "   tu_cs_emit_regs(cs, A6XX_RB_PS_MRT_CNTL(.mrt = num_remapped_rts));\n"
        "}\n",
        "   tu_cs_emit_regs(cs, A6XX_SP_PS_MRT_CNTL(.mrt = num_remapped_rts));\n"
        "   tu_cs_emit_regs(cs, A6XX_RB_PS_MRT_CNTL(.mrt = num_remapped_rts));\n"
        "   if (CHIP >= A8XX) {\n"
        "      tu_cs_emit_regs(cs, GRAS_LRZ_COLOR_COMP_MASK(CHIP, .dword = lrz_color_comp_mask));\n"
        "   }\n"
        "}\n",
    ),
]


def main():
    try:
        content = open(TARGET, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read {TARGET}: {e}; skipped")
        return 0

    if MARKER in content:
        print(f"{NAME}: already applied ({MARKER} present)")
        return 0

    for old, _ in EDITS:
        found = content.count(old)
        if found != 1:
            print(f"{NAME}: WARNING anchor matched {found}x (want 1) in {TARGET}; skipped")
            return 0

    for old, new in EDITS:
        content = content.replace(old, new, 1)

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (A8XX GRAS_LRZ_COLOR_COMP_MASK emitted with blend state)")
    return 0


if __name__ == "__main__":
    sys.exit(main())