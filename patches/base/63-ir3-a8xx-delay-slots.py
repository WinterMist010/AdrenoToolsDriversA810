#!/usr/bin/env python3
# From: WinterMist010 (research), values proven on A810 by the -N A8XX codegen profile (DVD)
"""ir3: use the a7xx instruction delay slots on a8xx as well.

ir3 inserts hardware NOPs between instructions based on compiler->delay_slots:
alu_to_alu=3, non_alu=6, cat3_src2_read=2 by default, reduced to 2/5/1 for
a7xx in ir3_compiler_create(). A8XX keeps the conservative a6xx timings, so
every a8xx shader pays extra ir3-legalize-inserted NOPs (see ir3_legalize.c:
ir3_required_delay() -> compiler->delay_slots) that a7xx does not.

The -N new-a810 tree already ships these reduced latencies for A8XX (the
"optimized delay slots alu_to_alu=2, non_alu=5, cat3_src2_read=1" codegen
profile), which is the precedent for this patch on -U.

Fewer NOPs = shorter shaders (fewer issue slots, less icache), a direct win
in ALU-heavy content.

Env TU_A8XX_DELAY_SLOTS (default true):
  true   a8xx uses the a7xx delay slots (2/5/1)
  false  a8xx keeps the upstream a6xx timings (3/6/2)

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "63-ir3-a8xx-delay-slots.py"
MARKER = "TU_A8XX_DELAY_SLOTS"
TARGET = "src/freedreno/ir3/ir3_compiler.c"

INC_OLD = '#include "util/ralloc.h"\n'
INC_NEW = (
    '#include "util/ralloc.h"\n'
    '#include "util/u_debug.h"\n'
)

SLOTS_OLD = (
    "      if (compiler->gen == 7) {\n"
    "         compiler->delay_slots.alu_to_alu = 2;\n"
    "         compiler->delay_slots.non_alu = 5;\n"
    "         compiler->delay_slots.cat3_src2_read = 1;\n"
    "      }\n"
)
SLOTS_NEW = (
    "      /* A8XX tolerates the a7xx instruction latencies too - the -N\n"
    "       * new-a810 codegen profile ships these values on A810. Fewer\n"
    "       * legalize-inserted NOPs make every a8xx shader shorter.\n"
    "       * TU_A8XX_DELAY_SLOTS=0 restores the upstream a6xx timings.\n"
    "       */\n"
    "      if (compiler->gen == 7 ||\n"
    "          (compiler->gen >= 8 &&\n"
    "           debug_get_bool_option(\"TU_A8XX_DELAY_SLOTS\", true))) {\n"
    "         compiler->delay_slots.alu_to_alu = 2;\n"
    "         compiler->delay_slots.non_alu = 5;\n"
    "         compiler->delay_slots.cat3_src2_read = 1;\n"
    "      }\n"
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

    for old, what in ((INC_OLD, "ralloc include"), (SLOTS_OLD, "gen==7 delay-slot block")):
        found = content.count(old)
        if found != 1:
            print(f"{NAME}: WARNING {what} anchor matched {found}x (want 1) in {TARGET}; skipped")
            return 0

    content = content.replace(INC_OLD, INC_NEW, 1)
    content = content.replace(SLOTS_OLD, SLOTS_NEW, 1)

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (a8xx uses a7xx delay slots, TU_A8XX_DELAY_SLOTS gate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
