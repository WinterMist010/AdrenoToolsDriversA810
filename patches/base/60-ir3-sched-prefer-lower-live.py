#!/usr/bin/env python3
# From: Vauzi-17
"""ir3: prefer instructions with a lower live-register effect under pressure.

In choose_instr_inc, when every ready instruction increases register pressure,
upstream breaks ties purely on distance to the nearest use. That lets large
register consumers (vec4 ops) get scheduled early, spiking pressure past the
GPR budget of the small parts (A810/A812 and A710-class), which costs occupancy
and spills.

This patch also compares live_effect() and only falls back to nearest-use when
the live effect ties.

Env TU_IR3_SCHED_LOWER_LIVE (default true):
  true   prefer lower live_effect during pressure increase
  false  rank purely by nearest_use (upstream behaviour)

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "60-ir3-sched-prefer-lower-live.py"
MARKER = "TU_IR3_SCHED_LOWER_LIVE"
TARGET = "src/freedreno/ir3/ir3_sched.c"

INC_OLD = '#include "util/u_math.h"\n'
INC_NEW = (
    '#include "util/u_math.h"\n'
    '#include "util/u_debug.h"\n'
)

OLD_DECL = (
    "   unsigned chosen_distance = 0;\n"
    "\n"
    "   /* Pick the max delay of the remaining ready set. */\n"
)
NEW_DECL = (
    "   unsigned chosen_distance = 0;\n"
    "   int chosen_live = 0;\n"
    "\n"
    "   /* Pick the max delay of the remaining ready set. */\n"
)

OLD_PICK = (
    "      unsigned distance = nearest_use(n->instr);\n"
    "\n"
    "      if (!chosen || rank > chosen_rank ||\n"
    "          (rank == chosen_rank && distance < chosen_distance)) {\n"
    "         chosen = n;\n"
    "         chosen_distance = distance;\n"
    "         chosen_rank = rank;\n"
    "      }\n"
)
NEW_PICK = (
    "      unsigned distance = nearest_use(n->instr);\n"
    "      int live = live_effect(n->instr);\n"
    "\n"
    "      static int prefer_lower_live = -1;\n"
    "      if (prefer_lower_live < 0)\n"
    "         prefer_lower_live = debug_get_bool_option(\"TU_IR3_SCHED_LOWER_LIVE\", true);\n"
    "\n"
    "      bool better = !chosen || rank > chosen_rank;\n"
    "      if (!better && rank == chosen_rank) {\n"
    "         if (prefer_lower_live) {\n"
    "            if (live < chosen_live)\n"
    "               better = true;\n"
    "            else if (live == chosen_live && distance < chosen_distance)\n"
    "               better = true;\n"
    "         } else {\n"
    "            if (distance < chosen_distance)\n"
    "               better = true;\n"
    "         }\n"
    "      }\n"
    "\n"
    "      if (better) {\n"
    "         chosen = n;\n"
    "         chosen_distance = distance;\n"
    "         chosen_rank = rank;\n"
    "         chosen_live = live;\n"
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

    for old, what in ((INC_OLD, "include"), (OLD_DECL, "chosen_distance decl"),
                      (OLD_PICK, "choose_instr_inc pick loop")):
        found = content.count(old)
        if found != 1:
            print(f"{NAME}: WARNING {what} anchor matched {found}x (want 1) in {TARGET}; skipped")
            return 0

    content = content.replace(INC_OLD, INC_NEW, 1)
    content = content.replace(OLD_DECL, NEW_DECL, 1)
    content = content.replace(OLD_PICK, NEW_PICK, 1)

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (TU_IR3_SCHED_LOWER_LIVE)")
    return 0


if __name__ == "__main__":
    sys.exit(main())