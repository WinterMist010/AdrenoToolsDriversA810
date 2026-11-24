#!/usr/bin/env python3
# From: WinterMist010 (A81X perf work)
"""tu: skip re-emitting graphics descriptor sets when nothing changed.

tu_CmdBindPipeline() unconditionally sets TU_CMD_DIRTY_DESC_SETS (and
TU_CMD_DIRTY_SHADER_CONSTS) even when the new pipeline uses the same
descriptor set layout and the same sets are still bound. At the next
draw, tu6_emit_descriptor_sets() then allocates a fresh sub_cs draw
state, re-records the SP_GFX_BINDLESS_BASE packet into it and emits a
CP_SET_DRAW_STATE for it, and tu6_emit_draw_state re-emits the
DESC_SETS_LOAD group — pure CPU and CS traffic per pipeline bind, plus
an SP_UPDATE_CNTL invalidation that makes the GPU re-fetch descriptors.

Emulators rebind the same pipeline object (or a clone with an identical
descriptor layout) very often per frame. On the A810, whose CPU and CS
bandwidth are both constrained, that is measurable.

The emitted dwords depend only on the bound set_iova array, the dynamic
reserved set and the pipeline's load_state, so this patch shadows those
and skips the re-emit entirely when they are all unchanged. The shadow
is invalidated whenever descriptor sets are (re-)bound, a secondary
command buffer ran, or the command buffer was begun (tu_cmd_buffer_begin
memsets cmd->state) — i.e. exactly the places that can change them.

Compute (TU_CMD_DIRTY_COMPUTE_DESC_SETS) keeps upstream behaviour: it is
emitted directly into cmd->cs at dispatch time.

Gated on TU_DESC_SETS_SKIP_IDENTICAL (default true) for on-device A/B.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "66-tu-desc-sets-skip-identical.py"
MARKER = "TU_DESC_SETS_SKIP_IDENTICAL"
CMD_CC = "src/freedreno/vulkan/tu_cmd_buffer.cc"
CMD_H = "src/freedreno/vulkan/tu_cmd_buffer.h"

H_OLD = (
    "   /* saved states to re-emit in TU_CMD_DIRTY_DRAW_STATE case */\n"
    "   struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];\n"
)
H_NEW = (
    "   /* saved states to re-emit in TU_CMD_DIRTY_DRAW_STATE case */\n"
    "   struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];\n"
    "   /* TU_DESC_SETS_SKIP_IDENTICAL: shadow of the inputs of the last\n"
    "    * emitted graphics descriptor-set state (bindless base array,\n"
    "    * reserved set, pipeline load_state); valid = false = re-emit.\n"
    "    */\n"
    "   struct {\n"
    "      bool valid;\n"
    "      uint64_t set_iova[MAX_SETS];\n"
    "      uint32_t max_sets_bound;\n"
    "      uint32_t max_dynamic_offset_size;\n"
    "      struct tu_draw_state load_state;\n"
    "   } desc_sets_shadow;\n"
)

# Early-out at the top of tu6_emit_descriptor_sets for the graphics path.
EMIT_OLD = (
    "tu6_emit_descriptor_sets(struct tu_cmd_buffer *cmd,\n"
    "                         VkPipelineBindPoint bind_point)\n"
    "{\n"
    "   struct tu_descriptor_state *descriptors_state =\n"
    "      tu_get_descriptors_state(cmd, bind_point);\n"
)
EMIT_NEW = (
    "tu6_emit_descriptor_sets(struct tu_cmd_buffer *cmd,\n"
    "                         VkPipelineBindPoint bind_point)\n"
    "{\n"
    "   struct tu_descriptor_state *descriptors_state =\n"
    "      tu_get_descriptors_state(cmd, bind_point);\n"
    "\n"
    "   /* TU_DESC_SETS_SKIP_IDENTICAL: the emitted dwords depend only on\n"
    "    * the set_iova array, the dynamic reserved set and the pipeline's\n"
    "    * load_state, so compare those against the last emission and skip\n"
    "    * the sub-stream alloc + draw-state emit when nothing changed.\n"
    "    * Graphics only; compute is emitted inline at dispatch time.\n"
    "    */\n"
    "   if (bind_point == VK_PIPELINE_BIND_POINT_GRAPHICS) {\n"
    "      static int skip_identical = -1;\n"
    "      if (skip_identical < 0)\n"
    "         skip_identical = debug_get_bool_option(\n"
    "            \"TU_DESC_SETS_SKIP_IDENTICAL\", true);\n"
    "\n"
    "      struct tu_cmd_state *st = &cmd->state;\n"
    "      if (skip_identical && st->desc_sets_shadow.valid) {\n"
    "         uint32_t n = descriptors_state->max_sets_bound;\n"
    "         bool same = st->desc_sets_shadow.max_sets_bound == n &&\n"
    "            st->desc_sets_shadow.max_dynamic_offset_size ==\n"
    "               descriptors_state->max_dynamic_offset_size &&\n"
    "            st->desc_sets_shadow.load_state.iova ==\n"
    "               st->load_state.iova &&\n"
    "            st->desc_sets_shadow.load_state.size ==\n"
    "               st->load_state.size;\n"
    "         for (uint32_t i = 0; same && i < n; i++)\n"
    "            same = st->desc_sets_shadow.set_iova[i] ==\n"
    "               descriptors_state->set_iova[i];\n"
    "         if (same) {\n"
    "            /* Keep the previous draw state alive; clear the dirty bit\n"
    "             * so the draw path does not re-emit the group.\n"
    "             */\n"
    "            cmd->state.dirty &= ~TU_CMD_DIRTY_DESC_SETS;\n"
    "            return;\n"
    "         }\n"
    "      }\n"
    "   }\n"
)

# Record the shadow right before the DESC_SETS draw state is registered.
RECORD_OLD = (
    "   if (bind_point == VK_PIPELINE_BIND_POINT_GRAPHICS) {\n"
    "      assert(cs->cur == cs->end); /* validate draw state size */\n"
)
RECORD_NEW = (
    "   if (bind_point == VK_PIPELINE_BIND_POINT_GRAPHICS) {\n"
    "      /* TU_DESC_SETS_SKIP_IDENTICAL: remember what we emitted. */\n"
    "      cmd->state.desc_sets_shadow.valid = true;\n"
    "      cmd->state.desc_sets_shadow.max_sets_bound =\n"
    "         descriptors_state->max_sets_bound;\n"
    "      cmd->state.desc_sets_shadow.max_dynamic_offset_size =\n"
    "         descriptors_state->max_dynamic_offset_size;\n"
    "      cmd->state.desc_sets_shadow.load_state = cmd->state.load_state;\n"
    "      memcpy(cmd->state.desc_sets_shadow.set_iova,\n"
    "             descriptors_state->set_iova,\n"
    "             sizeof(uint64_t) * descriptors_state->max_sets_bound);\n"
    "\n"
    "      assert(cs->cur == cs->end); /* validate draw state size */\n"
)

# tu_CmdBindDescriptorSets (via tu_dirty_desc_sets) invalidates the shadow.
DIRTY_OLD = (
    "   if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_COMPUTE) {\n"
    "      cmd->state.dirty |= TU_CMD_DIRTY_COMPUTE_DESC_SETS;\n"
)
DIRTY_NEW = (
    "   /* TU_DESC_SETS_SKIP_IDENTICAL: sets are (re-)bound, forget the\n"
    "    * shadow so the next draw re-emits them.\n"
    "    */\n"
    "   cmd->state.desc_sets_shadow.valid = false;\n"
    "\n"
    "   if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_COMPUTE) {\n"
    "      cmd->state.dirty |= TU_CMD_DIRTY_COMPUTE_DESC_SETS;\n"
)

# Secondaries may rebind descriptors; their draw-state groups ran on the GPU.
EXEC_OLD = (
    "   cmd->state.dirty = ~0u; /* TODO: set dirty only what needs to be */\n"
)
EXEC_NEW = (
    "   /* TU_DESC_SETS_SKIP_IDENTICAL */\n"
    "   cmd->state.desc_sets_shadow.valid = false;\n"
    "\n"
    "   cmd->state.dirty = ~0u; /* TODO: set dirty only what needs to be */\n"
)

INC_OLD = '#include "tu_trace_bin_layout.h"\n'
INC_NEW = (
    '#include "tu_trace_bin_layout.h"\n'
    '\n'
    '#include "util/u_debug.h"\n'
)


def warn(msg):
    print(f"{NAME}: WARNING {msg}; skipped")
    return 0


def main():
    try:
        cc = open(CMD_CC, "r", encoding="utf-8").read()
        h = open(CMD_H, "r", encoding="utf-8").read()
    except OSError as e:
        return warn(f"cannot read source files: {e}")

    if MARKER in cc or MARKER in h:
        print(f"{NAME}: already applied")
        return 0

    for path, text, old, what in (
        (CMD_H, h, H_OLD, "dynamic_state member"),
        (CMD_CC, cc, EMIT_OLD, "tu6_emit_descriptor_sets head"),
        (CMD_CC, cc, RECORD_OLD, "desc_sets draw state assert"),
        (CMD_CC, cc, DIRTY_OLD, "tu_dirty_desc_sets"),
        (CMD_CC, cc, EXEC_OLD, "tu_CmdExecuteCommands dirty line"),
    ):
        found = text.count(old)
        if found != 1:
            return warn(f"{what} anchor matched {found}x (want 1) in {path}")

    if '#include "util/u_debug.h"' not in cc:
        if cc.count(INC_OLD) != 1:
            return warn("include anchor not found")
        cc = cc.replace(INC_OLD, INC_NEW, 1)

    h = h.replace(H_OLD, H_NEW, 1)
    cc = cc.replace(EMIT_OLD, EMIT_NEW, 1)
    cc = cc.replace(RECORD_OLD, RECORD_NEW, 1)
    cc = cc.replace(DIRTY_OLD, DIRTY_NEW, 1)
    cc = cc.replace(EXEC_OLD, EXEC_NEW, 1)

    if MARKER not in h or cc.count(MARKER) < 4:
        return warn("replacement did not take; skipped")

    try:
        open(CMD_CC, "w", encoding="utf-8", newline="\n").write(cc)
        open(CMD_H, "w", encoding="utf-8", newline="\n").write(h)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write source files: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (TU_DESC_SETS_SKIP_IDENTICAL, default true)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
