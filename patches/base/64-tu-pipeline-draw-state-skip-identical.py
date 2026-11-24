#!/usr/bin/env python3
# From: WinterMist010 (A81X perf work), building on 59-tu-draw-state-skip-identical.py
"""tu: skip re-emitting unchanged pipeline draw-state groups on rebind.

59-tu-draw-state-skip-identical.py deduplicates the *dynamic* draw states
(viewport, scissor, blend, ...) emitted by tu_emit_draw_state(). The other
half of CP_SET_DRAW_STATE traffic on the bind path is tu_CmdBindPipeline:
unless a full re-emit is already pending, every graphics pipeline bind
emits all 10 program groups (PROGRAM_CONFIG, VS, VS_BINNING, HS, DS, GS,
GS_BINNING, FS, VPC, PRIM_MODE_GMEM) plus the pipeline's baked dynamic
states — a 3-dword CP_SET_DRAW_STATE packet per group, 30-60+ dwords per
bind.

Emulators rebind the *same* pipeline object constantly: DXVK and the
Switch emulators re-bind the current pipeline around state-block flushes,
subpass transitions and clears, and a rebound same-object pipeline hits
this path with every group bit-identical. The baked states live in
pipeline-owned, immutable memory, so {iova,size} equality implies the GPU
already has exactly these groups; re-emitting them is pure CP traffic and
re-fetch work.

CP_SET_DRAW_STATE has no such comparison in hardware, so this patch
shadows the last-emitted {iova,size} per group in tu_cmd_state and skips
the packets that would write identical values. The shadow is invalidated
wherever the groups can change behind the bind path's back:

  * tu_CmdExecuteCommands (secondaries emit their own groups),
  * tu_disable_draw_states() (CmdClearAttachments 3D fallback disables
    all groups),
  * the end of the binning pass (CONST and, for FDM, RAST groups are
    disabled there),
  * the full re-emit path in tu6_emit_draw_state() (it re-emits every
    group, including the render-mode-dependent PRIM_MODE_GMEM, from
    cmd->state rather than the bound pipeline),
  * command buffer begin (tu_cmd_buffer_begin() memsets cmd->state).

Note the shadow covers graphics binds only; compute binds do not emit
these groups. Different pipeline objects never share baked-state iovas,
so only same-object rebinds can be skipped — that is the common case
described above, and A->B->A sequences correctly re-emit.

Gated on TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL (default true) so it can
be A/B'd against upstream behaviour on device.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "64-tu-pipeline-draw-state-skip-identical.py"
MARKER = "TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL"
CMD_H = "src/freedreno/vulkan/tu_cmd_buffer.h"
CMD_CC = "src/freedreno/vulkan/tu_cmd_buffer.cc"

H_OLD = (
    "   bool pipeline_blend_lrz, pipeline_bandwidth, pipeline_disable_fs;\n"
    "   uint32_t pipeline_draw_states;\n"
)
H_NEW = (
    "   bool pipeline_blend_lrz, pipeline_bandwidth, pipeline_disable_fs;\n"
    "   uint32_t pipeline_draw_states;\n"
    "   /* TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL: last {iova,size} emitted\n"
    "    * for each draw-state group by tu_CmdBindPipeline, to skip groups\n"
    "    * that would re-write identical values.\n"
    "    */\n"
    "   struct tu_draw_state pipeline_draw_state_shadow[TU_DRAW_STATE_COUNT];\n"
)

# tu_disable_draw_states() kills all groups on the GPU.
DISABLE_OLD = (
    "   tu_cs_emit_pkt7(cs, CP_SET_DRAW_STATE, 3);\n"
    "   tu_cs_emit(cs, CP_SET_DRAW_STATE__0_COUNT(0) |\n"
    "                     CP_SET_DRAW_STATE__0_DISABLE_ALL_GROUPS |\n"
    "                     CP_SET_DRAW_STATE__0_GROUP_ID(0));\n"
    "   tu_cs_emit(cs, CP_SET_DRAW_STATE__1_ADDR_LO(0));\n"
    "   tu_cs_emit(cs, CP_SET_DRAW_STATE__2_ADDR_HI(0));\n"
    "\n"
    "   cmd->state.dirty |= TU_CMD_DIRTY_DRAW_STATE;\n"
    "}"
)
DISABLE_NEW = (
    "   tu_cs_emit_pkt7(cs, CP_SET_DRAW_STATE, 3);\n"
    "   tu_cs_emit(cs, CP_SET_DRAW_STATE__0_COUNT(0) |\n"
    "                     CP_SET_DRAW_STATE__0_DISABLE_ALL_GROUPS |\n"
    "                     CP_SET_DRAW_STATE__0_GROUP_ID(0));\n"
    "   tu_cs_emit(cs, CP_SET_DRAW_STATE__1_ADDR_LO(0));\n"
    "   tu_cs_emit(cs, CP_SET_DRAW_STATE__2_ADDR_HI(0));\n"
    "\n"
    "   /* TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL: all groups were disabled\n"
    "    * on the GPU, the shadow no longer matches.\n"
    "    */\n"
    "   memset(cmd->state.pipeline_draw_state_shadow, 0,\n"
    "          sizeof(cmd->state.pipeline_draw_state_shadow));\n"
    "\n"
    "   cmd->state.dirty |= TU_CMD_DIRTY_DRAW_STATE;\n"
    "}"
)

# End of the binning pass: the CONST group (and, for FDM pipelines, the
# RAST group) was disabled above, so the shadow no longer matches.
BINEND_OLD = (
    "   tu_emit_event_write<CHIP>(cmd, cs, FD_VSC_BINNING_END);\n"
)
BINEND_NEW = (
    "   tu_emit_event_write<CHIP>(cmd, cs, FD_VSC_BINNING_END);\n"
    "\n"
    "   /* TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL: the binning pass disabled\n"
    "    * the CONST group (and the FDM RAST group), the last-bind shadow no\n"
    "    * longer matches the GPU.\n"
    "    */\n"
    "   memset(cmd->state.pipeline_draw_state_shadow, 0,\n"
    "          sizeof(cmd->state.pipeline_draw_state_shadow));\n"
)

# The full re-emit path re-emits every group from cmd->state, including
# the render-mode-dependent PRIM_MODE_GMEM.
DIRTY_OLD = (
    "   if (dirty & TU_CMD_DIRTY_DRAW_STATE) {\n"
    "      tu_pipeline_update_rp_state(&cmd->state);\n"
)
DIRTY_NEW = (
    "   if (dirty & TU_CMD_DIRTY_DRAW_STATE) {\n"
    "      tu_pipeline_update_rp_state(&cmd->state);\n"
    "\n"
    "      /* TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL: this path re-emits\n"
    "       * every group (including the render-mode-dependent\n"
    "       * PRIM_MODE_GMEM) from cmd->state; the last-bind shadow no\n"
    "       * longer matches what is on the GPU.\n"
    "       */\n"
    "      memset(cmd->state.pipeline_draw_state_shadow, 0,\n"
    "             sizeof(cmd->state.pipeline_draw_state_shadow));\n"
)

BIND_OLD = (
    "   if (!(cmd->state.dirty & TU_CMD_DIRTY_DRAW_STATE)) {\n"
    "      struct tu_cs *cs = &cmd->draw_cs;\n"
    "\n"
    "      tu_cs_emit_pkt7(cs, CP_SET_DRAW_STATE, 3 * (10 + util_bitcount(set_state_mask)));\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_PROGRAM_CONFIG, pipeline->program.config_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_VS, pipeline->program.vs_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_VS_BINNING, pipeline->program.vs_binning_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_HS, pipeline->program.hs_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_DS, pipeline->program.ds_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_GS, pipeline->program.gs_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_GS_BINNING, pipeline->program.gs_binning_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_FS, pipeline->program.fs_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_VPC, pipeline->program.vpc_state);\n"
    "      tu_cs_emit_draw_state(cs, TU_DRAW_STATE_PRIM_MODE_GMEM, pipeline->prim_order.state_gmem);\n"
    "\n"
    "      u_foreach_bit(i, set_state_mask)\n"
    "         tu_cs_emit_draw_state(cs, TU_DRAW_STATE_DYNAMIC + i, pipeline->dynamic_state[i]);\n"
    "   }\n"
)
BIND_NEW = (
    "   if (!(cmd->state.dirty & TU_CMD_DIRTY_DRAW_STATE)) {\n"
    "      struct tu_cs *cs = &cmd->draw_cs;\n"
    "\n"
    "      /* TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL: the baked states live\n"
    "       * in pipeline-owned immutable memory, so {iova,size} equality\n"
    "       * means the GPU already has this group. Skip the packets that\n"
    "       * would re-write identical values; the shadow is invalidated\n"
    "       * everywhere the groups can change behind our back.\n"
    "       */\n"
    "      static int skip_identical = -1;\n"
    "      if (skip_identical < 0)\n"
    "         skip_identical = debug_get_bool_option(\n"
    "            \"TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL\", true);\n"
    "\n"
    "      struct { uint32_t id; struct tu_draw_state state; }\n"
    "         emit[10 + TU_DYNAMIC_STATE_COUNT];\n"
    "      unsigned emit_count = 0;\n"
    "      bool emit_all = !skip_identical;\n"
    "\n"
    "      auto add_emit = [&](uint32_t id, struct tu_draw_state state) {\n"
    "         if (emit_all ||\n"
    "             cmd->state.pipeline_draw_state_shadow[id].iova != state.iova ||\n"
    "             cmd->state.pipeline_draw_state_shadow[id].size != state.size)\n"
    "            emit[emit_count++] = { id, state };\n"
    "         cmd->state.pipeline_draw_state_shadow[id] = state;\n"
    "      };\n"
    "\n"
    "      add_emit(TU_DRAW_STATE_PROGRAM_CONFIG, pipeline->program.config_state);\n"
    "      add_emit(TU_DRAW_STATE_VS, pipeline->program.vs_state);\n"
    "      add_emit(TU_DRAW_STATE_VS_BINNING, pipeline->program.vs_binning_state);\n"
    "      add_emit(TU_DRAW_STATE_HS, pipeline->program.hs_state);\n"
    "      add_emit(TU_DRAW_STATE_DS, pipeline->program.ds_state);\n"
    "      add_emit(TU_DRAW_STATE_GS, pipeline->program.gs_state);\n"
    "      add_emit(TU_DRAW_STATE_GS_BINNING, pipeline->program.gs_binning_state);\n"
    "      add_emit(TU_DRAW_STATE_FS, pipeline->program.fs_state);\n"
    "      add_emit(TU_DRAW_STATE_VPC, pipeline->program.vpc_state);\n"
    "      add_emit(TU_DRAW_STATE_PRIM_MODE_GMEM, pipeline->prim_order.state_gmem);\n"
    "\n"
    "      u_foreach_bit(i, set_state_mask)\n"
    "         add_emit(TU_DRAW_STATE_DYNAMIC + i, pipeline->dynamic_state[i]);\n"
    "\n"
    "      if (emit_count > 0) {\n"
    "         tu_cs_emit_pkt7(cs, CP_SET_DRAW_STATE, 3 * emit_count);\n"
    "         for (unsigned i = 0; i < emit_count; i++)\n"
    "            tu_cs_emit_draw_state(cs, emit[i].id, emit[i].state);\n"
    "      }\n"
    "   }\n"
)

EXEC_OLD = (
    "   cmd->state.dirty = ~0u; /* TODO: set dirty only what needs to be */\n"
)
EXEC_NEW = (
    "   /* TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL: secondaries overwrote the\n"
    "    * draw state groups on the GPU.\n"
    "    */\n"
    "   memset(cmd->state.pipeline_draw_state_shadow, 0,\n"
    "          sizeof(cmd->state.pipeline_draw_state_shadow));\n"
    "\n"
    "   cmd->state.dirty = ~0u; /* TODO: set dirty only what needs to be */\n"
)


def warn(msg):
    print(f"{NAME}: WARNING {msg}; skipped")
    return 0


def main():
    try:
        h = open(CMD_H, "r", encoding="utf-8").read()
        cc = open(CMD_CC, "r", encoding="utf-8").read()
    except OSError as e:
        return warn(f"cannot read source files: {e}")

    if MARKER in h and MARKER in cc:
        print(f"{NAME}: already applied")
        return 0
    if MARKER in h or MARKER in cc:
        return warn("partially applied; skipped")

    for path, text, old, what in (
        (CMD_H, h, H_OLD, "tu_cmd_state fields"),
        (CMD_CC, cc, DISABLE_OLD, "tu_disable_draw_states"),
        (CMD_CC, cc, BINEND_OLD, "binning-pass end"),
        (CMD_CC, cc, DIRTY_OLD, "full re-emit path"),
        (CMD_CC, cc, BIND_OLD, "tu_CmdBindPipeline emit block"),
        (CMD_CC, cc, EXEC_OLD, "tu_CmdExecuteCommands dirty line"),
    ):
        found = text.count(old)
        if found != 1:
            return warn(f"{what} anchor matched {found}x (want 1) in {path}")

    # tu_cmd_buffer.cc needs util/u_debug.h for debug_get_bool_option.
    if '#include "util/u_debug.h"' not in cc:
        inc_old = '#include "tu_trace_bin_layout.h"\n'
        if cc.count(inc_old) != 1:
            return warn("include anchor not found in tu_cmd_buffer.cc")
        cc = cc.replace(inc_old, inc_old + '\n#include "util/u_debug.h"\n', 1)

    h = h.replace(H_OLD, H_NEW, 1)
    cc = cc.replace(DISABLE_OLD, DISABLE_NEW, 1)
    cc = cc.replace(BINEND_OLD, BINEND_NEW, 1)
    cc = cc.replace(DIRTY_OLD, DIRTY_NEW, 1)
    cc = cc.replace(BIND_OLD, BIND_NEW, 1)
    cc = cc.replace(EXEC_OLD, EXEC_NEW, 1)

    if MARKER not in h or cc.count(MARKER) < 6:
        return warn("replacement did not take; skipped")

    try:
        open(CMD_H, "w", encoding="utf-8", newline="\n").write(h)
        open(CMD_CC, "w", encoding="utf-8", newline="\n").write(cc)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write source files: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (TU_PIPELINE_DRAW_STATE_SKIP_IDENTICAL, default true)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
