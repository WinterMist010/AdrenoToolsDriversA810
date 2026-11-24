#!/usr/bin/env python3
# From: WinterMist010 (A81X perf work), extending 57-tu-sysmem-wfi-relax.py
"""A8XX: relax the sysmem resolve/unresolve WFI to CP_WAIT_FOR_ME.

57-tu-sysmem-wfi-relax.py relaxed the sysmem subpass-slice wait (the wait
after FD_CACHE_INVALIDATE in tu6_emit_flushes). There are two more full
WFI pipeline drains on the A810/812 hot path, both in sysmem mode where
the small-GMEM parts spend nearly all their time:

  * tu6_emit_sysmem_resolves(): "Wait for the flushes to land before
    using the 2D engine" — the CCU color/depth clean + UCHE invalidate
    before CP_BLIT resolves.
  * tu6_emit_sysmem_unresolves(): the same flush, plus a trailing WFI
    after FD_CCU_INVALIDATE_* / FD_CACHE_INVALIDATE before drawing
    resumes.

The CP_BLIT (2D) engine consumes its inputs through UCHE, like the draw
pipeline; ordering the stream only requires the CP to have caught up with
the event writes, which CP_WAIT_FOR_ME provides without draining the
shader cores. The blob uses WAIT_FOR_ME in the equivalent spots in its
sysmem batches. On a part that renders nearly everything in sysmem, each
avoided WFI saves the full pipeline-drain bubble per subpass boundary
with resolves (MSAA titles, DXVK backbuffer resolves, the mesh-emulation
resolve steps).

Gated to CHIP >= A8XX with the existing 57 patch's env knob reused:
TU_SYSMEM_RELAX_WFI (default true) controls all three sites together so
there is a single A/B switch for the whole WFI-relaxation family.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
Requires 57-tu-sysmem-wfi-relax.py to be applied first (for the env knob);
warns and skips otherwise.
"""
import sys

NAME = "65-a8xx-sysmem-resolve-wfi-relax.py"
KNOB = "debug_get_option_tu_sysmem_relax_wfi()"
MARKER = "TU_SYSMEM_RELAX_WFI"
CMD_CC = "src/freedreno/vulkan/tu_cmd_buffer.cc"

RESOLVE_OLD = (
    "      tu_emit_event_write<CHIP>(cmd, cs, FD_CACHE_INVALIDATE);\n"
    "\n"
    "      /* Wait for the flushes to land before using the 2D engine */\n"
    "      tu_cs_emit_wfi(cs);\n"
    "\n"
    "      for (unsigned i = 0; i < subpass->resolve_count; i++) {\n"
)
RESOLVE_NEW = (
    "      tu_emit_event_write<CHIP>(cmd, cs, FD_CACHE_INVALIDATE);\n"
    "\n"
    "      /* Wait for the flushes to land before using the 2D engine.\n"
    "       * A81X perf: on A8XX the 2D engine is a UCHE client like the\n"
    "       * draw pipeline, ordering the stream is enough - see\n"
    "       * 65-a8xx-sysmem-resolve-wfi-relax.py.\n"
    "       */\n"
    "      if (CHIP >= A8XX && debug_get_option_tu_sysmem_relax_wfi())\n"
    "         tu_cs_emit_pkt7(cs, CP_WAIT_FOR_ME, 0);\n"
    "      else\n"
    "         tu_cs_emit_wfi(cs);\n"
    "\n"
    "      for (unsigned i = 0; i < subpass->resolve_count; i++) {\n"
)

UNRESOLVE_HEAD_OLD = (
    "      tu_emit_event_write<CHIP>(cmd, cs, FD_CCU_CLEAN_COLOR);\n"
    "      tu_emit_event_write<CHIP>(cmd, cs, FD_CACHE_INVALIDATE);\n"
    "\n"
    "      /* Wait for the flushes to land before using the 2D engine */\n"
    "      tu_cs_emit_wfi(cs);\n"
    "\n"
    "      bool unresolve_ds = false;\n"
)
UNRESOLVE_HEAD_NEW = (
    "      tu_emit_event_write<CHIP>(cmd, cs, FD_CCU_CLEAN_COLOR);\n"
    "      tu_emit_event_write<CHIP>(cmd, cs, FD_CACHE_INVALIDATE);\n"
    "\n"
    "      /* Wait for the flushes to land before using the 2D engine.\n"
    "       * A81X perf: see 65-a8xx-sysmem-resolve-wfi-relax.py.\n"
    "       */\n"
    "      if (CHIP >= A8XX && debug_get_option_tu_sysmem_relax_wfi())\n"
    "         tu_cs_emit_pkt7(cs, CP_WAIT_FOR_ME, 0);\n"
    "      else\n"
    "         tu_cs_emit_wfi(cs);\n"
    "\n"
    "      bool unresolve_ds = false;\n"
)

UNRESOLVE_TAIL_OLD = (
    "      if (unresolve_ds) {\n"
    "         tu_emit_event_write<CHIP>(cmd, cs, FD_CCU_CLEAN_DEPTH);\n"
    "         tu_emit_event_write<CHIP>(cmd, cs, FD_CCU_INVALIDATE_DEPTH);\n"
    "      }\n"
    "      tu_cs_emit_wfi(cs);\n"
    "   }\n"
    "}\n"
)
UNRESOLVE_TAIL_NEW = (
    "      if (unresolve_ds) {\n"
    "         tu_emit_event_write<CHIP>(cmd, cs, FD_CCU_CLEAN_DEPTH);\n"
    "         tu_emit_event_write<CHIP>(cmd, cs, FD_CCU_INVALIDATE_DEPTH);\n"
    "      }\n"
    "      /* A81X perf: the draw pipeline re-reads these through UCHE,\n"
    "       * stream ordering suffices on A8XX - see\n"
    "       * 65-a8xx-sysmem-resolve-wfi-relax.py.\n"
    "       */\n"
    "      if (CHIP >= A8XX && debug_get_option_tu_sysmem_relax_wfi())\n"
    "         tu_cs_emit_pkt7(cs, CP_WAIT_FOR_ME, 0);\n"
    "      else\n"
    "         tu_cs_emit_wfi(cs);\n"
    "   }\n"
    "}\n"
)


def warn(msg):
    print(f"{NAME}: WARNING {msg}; skipped")
    return 0


def main():
    try:
        cc = open(CMD_CC, "r", encoding="utf-8").read()
    except OSError as e:
        return warn(f"cannot read {CMD_CC}: {e}")

    if "65-a8xx-sysmem-resolve-wfi-relax" in cc:
        print(f"{NAME}: already applied")
        return 0

    if KNOB not in cc:
        return warn("57-tu-sysmem-wfi-relax.py not applied (no TU_SYSMEM_RELAX_WFI knob); skipped")

    for old, what in (
        (RESOLVE_OLD, "sysmem resolves WFI"),
        (UNRESOLVE_HEAD_OLD, "sysmem unresolves head WFI"),
        (UNRESOLVE_TAIL_OLD, "sysmem unresolves tail WFI"),
    ):
        found = cc.count(old)
        if found != 1:
            return warn(f"{what} anchor matched {found}x (want 1)")

    cc = cc.replace(RESOLVE_OLD, RESOLVE_NEW, 1)
    cc = cc.replace(UNRESOLVE_HEAD_OLD, UNRESOLVE_HEAD_NEW, 1)
    cc = cc.replace(UNRESOLVE_TAIL_OLD, UNRESOLVE_TAIL_NEW, 1)

    try:
        open(CMD_CC, "w", encoding="utf-8", newline="\n").write(cc)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {CMD_CC}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (A8XX sysmem resolve/unresolve WFI -> CP_WAIT_FOR_ME)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
