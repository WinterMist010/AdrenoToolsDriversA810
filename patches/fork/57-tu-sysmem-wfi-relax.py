#!/usr/bin/env python3
# From: Vauzi-17
"""A8XX: relax the sysmem subpass-slice wait from WFI to CP_WAIT_FOR_ME.

In sysmem mode Turnip emits a full WFI (wait-for-idle) pipeline drain on the
subpass-slice wait. WFI stalls the command processor until every active shader
wave has finished. In sysmem rendering CP_WAIT_FOR_ME is enough to order the
command stream, without draining the shader cores.

This matters for the small-GMEM parts (A810/A812) which spend most of their
time in sysmem mode, where the sysmem subpass slice fence is on the hot path of
every subpass.

Env TU_SYSMEM_RELAX_WFI (default true):
  true   emit CP_WAIT_FOR_ME on the A8XX sysmem subpass-slice wait
  false  emit tu_cs_emit_wfi (upstream behaviour)

Only CHIP >= A8XX is affected; A6xx/A7xx keep upstream behaviour regardless.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "57-tu-sysmem-wfi-relax.py"
MARKER = "TU_SYSMEM_RELAX_WFI"
TARGET = "src/freedreno/vulkan/tu_cmd_buffer.cc"

INCLUDE_OLD = '#include "tu_trace_bin_layout.h"\n'
INCLUDE_NEW = (
    '#include "tu_trace_bin_layout.h"\n'
    '\n'
    '#include "util/u_debug.h"\n'
    '\n'
    'DEBUG_GET_ONCE_BOOL_OPTION(tu_sysmem_relax_wfi, "TU_SYSMEM_RELAX_WFI", true)\n'
)

WAIT_OLD = (
    "      if (emit_subpass_slice_wait)\n"
    "         tu_cs_emit_wfi(cs);\n"
)
WAIT_NEW = (
    "      if (emit_subpass_slice_wait) {\n"
    "         if (CHIP >= A8XX && debug_get_option_tu_sysmem_relax_wfi())\n"
    "            tu_cs_emit_pkt7(cs, CP_WAIT_FOR_ME, 0);\n"
    "         else\n"
    "            tu_cs_emit_wfi(cs);\n"
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

    for old, what in ((INCLUDE_OLD, "include"), (WAIT_OLD, "subpass-slice wait")):
        found = content.count(old)
        if found != 1:
            print(f"{NAME}: WARNING {what} anchor matched {found}x (want 1) in {TARGET}; skipped")
            return 0

    content = content.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
    content = content.replace(WAIT_OLD, WAIT_NEW, 1)

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (A8XX sysmem subpass-slice wait uses CP_WAIT_FOR_ME)")
    return 0


if __name__ == "__main__":
    sys.exit(main())