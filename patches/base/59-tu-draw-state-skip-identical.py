#!/usr/bin/env python3
# From: Vauzi-17
"""tu: skip re-emitting a dynamic draw state whose words are unchanged.

A dynamic-state dirty bit (pipeline bind, or a vkCmdSet* with identical values)
makes Turnip emit the state packet into the sub_cs and set
dirty_draw_states |= (1u << id), which later emits CP_SET_DRAW_STATE even when
the register contents did not change. On draw-call-heavy workloads (emulators)
that is pure CP/packet churn.

The comparison is done entirely in L1-cached host memory: the packet is first
emitted into a stack buffer, compared against a shadow copy kept in the command
buffer, and only copied into the (write-combined, uncached) sub_cs when it
actually differs. This avoids reading WC memory, which drains the write-
combining buffers and stalls the CPU whenever a state changes.

The shadow copy is invalidated whenever the GPU state can be overwritten behind
our back: a pipeline that bakes the state statically, an FDM viewport/scissor
emit, an empty state, or executing secondary command buffers.

Env TU_DRAW_STATE_SKIP_IDENTICAL (default true):
  true   skip identical dynamic draw states
  false  always re-emit (upstream behaviour)

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "59-tu-draw-state-skip-identical.py"
MARKER = "TU_DRAW_STATE_SKIP_IDENTICAL"
CMD_H = "src/freedreno/vulkan/tu_cmd_buffer.h"
CMD_CC = "src/freedreno/vulkan/tu_cmd_buffer.cc"
PIPELINE_CC = "src/freedreno/vulkan/tu_pipeline.cc"

# The shadow must fit the largest packet that DRAW_STATE_COND()/DRAW_STATE_FDM()
# can emit.  Viewport is the biggest: A8XX emits 1 + 2 * (12 * count + 1)
# dwords, and count is bounded by MAX_VIEWPORTS.  The next largest emitters are
# vertex_input/vertex_stride (1 + 2 * MAX_VBS = 65), blend (8 + 2 + 5 * MAX_RTS
# on A8XX = 50) and scissor (1 + 2 * MAX_SCISSORS = 33).
H_DEFINE_OLD = "struct tu_cmd_state\n{\n"
H_DEFINE_NEW = (
    "/* TU_DRAW_STATE_SKIP_IDENTICAL: dwords in the L1-cached per-state shadow.\n"
    " * Sized for the largest dynamic state packet (A8XX viewport).\n"
    " */\n"
    "#define TU_DRAW_STATE_SHADOW_MAX_DWORDS (1 + 2 * (12 * MAX_VIEWPORTS + 1))\n"
    "\n"
    "struct tu_cmd_state\n{\n"
)

H_OLD = (
    "   /* saved states to re-emit in TU_CMD_DIRTY_DRAW_STATE case */\n"
    "   struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];\n"
)
H_NEW = (
    "   /* saved states to re-emit in TU_CMD_DIRTY_DRAW_STATE case */\n"
    "   struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];\n"
    "   /* TU_DRAW_STATE_SKIP_IDENTICAL: L1-cached copy of the dwords emitted for\n"
    "    * each dynamic draw state.  \"iova\" records which IB the shadow belongs\n"
    "    * to: if anything else overwrites dynamic_state[id] (e.g. a pipeline\n"
    "    * with the state baked in) the match fails and the state is re-emitted.\n"
    "    */\n"
    "   struct {\n"
    "      uint64_t iova;\n"
    "      uint16_t size;\n"
    "      bool valid;\n"
    "      uint32_t dwords[TU_DRAW_STATE_SHADOW_MAX_DWORDS];\n"
    "   } dynamic_state_shadow[TU_DYNAMIC_STATE_COUNT];\n"
)

INC_OLD = '#include "tu_rmv.h"\n'
INC_NEW = (
    '#include "tu_rmv.h"\n'
    '\n'
    '#include <string.h>\n'
    '\n'
    'DEBUG_GET_ONCE_BOOL_OPTION(tu_draw_state_skip_identical, "TU_DRAW_STATE_SKIP_IDENTICAL", true)\n'
)

# tu_CmdExecuteCommands() lets secondaries overwrite draw state slots on the GPU
# without touching the primary's shadow.
EXEC_OLD = (
    "   assert(commandBufferCount > 0);\n"
    "\n"
    "   /* Emit any pending flushes. */\n"
)
EXEC_NEW = (
    "   assert(commandBufferCount > 0);\n"
    "\n"
    "   /* TU_DRAW_STATE_SKIP_IDENTICAL: executing a secondary command buffer\n"
    "    * overwrites dynamic draw state slots on the GPU without updating this\n"
    "    * command buffer's shadow, so it can no longer be trusted.\n"
    "    */\n"
    "   for (unsigned i = 0; i < TU_DYNAMIC_STATE_COUNT; i++)\n"
    "      cmd->state.dynamic_state_shadow[i].valid = false;\n"
    "\n"
    "   /* Emit any pending flushes. */\n"
)


def macro(lines):
    out = [ln.ljust(76) + " \\" for ln in lines[:-1]]
    out.append(lines[-1])
    return out


# Plain (non-continued) lines emitted before the macros, inside
# tu_emit_draw_state(): scratch space for emitting a packet in cached memory.
BUFFER_LINES = [
    "   /* TU_DRAW_STATE_SKIP_IDENTICAL: emit into cached memory first so the",
    "    * comparison never reads back the WC sub_cs.",
    "    */",
    "   uint32_t draw_state_tmp[TU_DRAW_STATE_SHADOW_MAX_DWORDS];",
    "",
]

# Shared by DRAW_STATE_COND() and the non-FDM path of DRAW_STATE_FDM().
HELPER_LINES = [
    "#define DRAW_STATE_EMIT_OR_SKIP(name, id, ...)",
    "   do {",
    "      unsigned size = tu6_##name##_size<CHIP>(cmd->device, __VA_ARGS__);",
    "      if (size > 0) {",
    "         assert(size <= TU_DRAW_STATE_SHADOW_MAX_DWORDS);",
    "         tu_cs_init_external(&cs, cmd->device, draw_state_tmp,",
    "                             draw_state_tmp + size, 0, false);",
    "         tu_cs_begin(&cs);",
    "         tu_cs_reserve_space(&cs, size);",
    "         tu6_emit_##name<CHIP>(&cs, __VA_ARGS__);",
    "         uint32_t emit_size = tu_cs_get_size(&cs);",
    "         bool identical =",
    "            debug_get_option_tu_draw_state_skip_identical() &&",
    "            cmd->state.dynamic_state_shadow[id].valid &&",
    "            cmd->state.dynamic_state_shadow[id].size == emit_size &&",
    "            cmd->state.dynamic_state[id].iova ==",
    "               cmd->state.dynamic_state_shadow[id].iova &&",
    "            cmd->state.dynamic_state[id].size == emit_size &&",
    "            emit_size > 0 &&",
    "            memcmp(cmd->state.dynamic_state_shadow[id].dwords,",
    "                   draw_state_tmp, emit_size * sizeof(uint32_t)) == 0;",
    "         if (identical) {",
    "            /* The GPU already has these words: keep the active IB and",
    "             * skip the sub-allocation and CP_SET_DRAW_STATE.",
    "             */",
    "         } else if (emit_size == 0) {",
    "            cmd->state.dynamic_state[id] = {};",
    "            cmd->state.dynamic_state_shadow[id].valid = false;",
    "            dirty_draw_states |= (1u << id);",
    "         } else {",
    "            VkResult result =",
    "               tu_cs_begin_sub_stream(&cmd->sub_cs, emit_size, &cs);",
    "            if (result == VK_SUCCESS) {",
    "               memcpy(cs.start, draw_state_tmp,",
    "                      emit_size * sizeof(uint32_t));",
    "               cs.cur += emit_size;",
    "            }",
    "            cmd->state.dynamic_state[id] =",
    "               tu_cs_end_draw_state(&cmd->sub_cs, &cs);",
    "            cmd->state.dynamic_state_shadow[id].valid =",
    "               result == VK_SUCCESS;",
    "            if (result == VK_SUCCESS) {",
    "               cmd->state.dynamic_state_shadow[id].iova =",
    "                  cmd->state.dynamic_state[id].iova;",
    "               cmd->state.dynamic_state_shadow[id].size = emit_size;",
    "               memcpy(cmd->state.dynamic_state_shadow[id].dwords,",
    "                      draw_state_tmp, emit_size * sizeof(uint32_t));",
    "            }",
    "            dirty_draw_states |= (1u << id);",
    "         }",
    "      } else {",
    "         cmd->state.dynamic_state[id] = {};",
    "         cmd->state.dynamic_state_shadow[id].valid = false;",
    "         dirty_draw_states |= (1u << id);",
    "      }",
    "   } while (0)",
]

COND_LINES = [
    "#define DRAW_STATE_COND(name, id, extra_cond, ...)",
    "   if ((EMIT_STATE(name) || (extra_cond)) &&",
    "       !(cmd->state.pipeline_draw_states & (1u << id))) {",
    "      DRAW_STATE_EMIT_OR_SKIP(name, id, __VA_ARGS__);",
    "   } else if (cmd->state.pipeline_draw_states & (1u << id)) {",
    "      /* A static pipeline overwrote this register on the GPU. */",
    "      cmd->state.dynamic_state_shadow[id].valid = false;",
    "   }",
]

FDM_LINES = [
    "#define DRAW_STATE_FDM(name, id, ...)",
    "   if ((EMIT_STATE(name) || (cmd->state.dirty &",
    "                             (TU_CMD_DIRTY_FDM |",
    "                              TU_CMD_DIRTY_PER_VIEW_VIEWPORT))) &&",
    "       !(cmd->state.pipeline_draw_states & (1u << id))) {",
    "      if (cmd->state.has_fdm || cmd->state.per_layer_viewport) {",
    "         /* FDM state is patched per bin, so it cannot be compared. */",
    "         cmd->state.dynamic_state_shadow[id].valid = false;",
    "         tu_cs_set_writeable(&cmd->sub_cs, true);",
    "         tu6_emit_##name##_fdm(&cs, cmd, __VA_ARGS__);",
    "         cmd->state.dynamic_state[id] =",
    "            tu_cs_end_draw_state(&cmd->sub_cs, &cs);",
    "         tu_cs_set_writeable(&cmd->sub_cs, false);",
    "         dirty_draw_states |= (1u << id);",
    "      } else {",
    "         DRAW_STATE_EMIT_OR_SKIP(name, id, __VA_ARGS__);",
    "      }",
    "   } else if (cmd->state.pipeline_draw_states & (1u << id)) {",
    "      /* A static pipeline overwrote this register on the GPU. */",
    "      cmd->state.dynamic_state_shadow[id].valid = false;",
    "   }",
]


def patch_pipeline(text):
    lines = text.splitlines()

    def find(prefix, start=0):
        for i in range(start, len(lines)):
            if lines[i].startswith(prefix):
                return i
        return -1

    # The runtime family is the DRAW_STATE_COND whose body uses dirty_draw_states.
    cond = -1
    i = 0
    while True:
        i = find("#define DRAW_STATE_COND(", i)
        if i < 0:
            break
        if "dirty_draw_states" in "\n".join(lines[i:i + 25]):
            cond = i
            break
        i += 1
    if cond < 0:
        return None, "runtime DRAW_STATE_COND macro not found"

    fdm = find("#define DRAW_STATE_FDM(", cond)
    end = find("#define DRAW_STATE(name, id, ...)", fdm + 1 if fdm >= 0 else cond)
    if fdm < 0 or end < 0 or not (cond < fdm < end):
        return None, "DRAW_STATE_FDM / DRAW_STATE boundaries not found"

    # The undef sits right after the runtime macros we replace; the pipeline
    # builder has an identical 3-line block earlier, so include the following
    # statement to keep the anchor unique.
    undef_old = (
        "#undef DRAW_STATE\n"
        "#undef DRAW_STATE_COND\n"
        "#undef EMIT_STATE\n"
        "\n"
        "   return dirty_draw_states;\n"
    )
    undef_new = (
        "#undef DRAW_STATE\n"
        "#undef DRAW_STATE_COND\n"
        "#undef DRAW_STATE_EMIT_OR_SKIP\n"
        "#undef EMIT_STATE\n"
        "\n"
        "   return dirty_draw_states;\n"
    )

    new = (lines[:cond] + BUFFER_LINES + macro(HELPER_LINES) +
           macro(COND_LINES) + macro(FDM_LINES) + lines[end:])
    result = "\n".join(new) + "\n"

    if result.count(undef_old) != 1:
        return None, "DRAW_STATE undef anchor not found"
    result = result.replace(undef_old, undef_new, 1)
    return result, None


def main():
    try:
        h = open(CMD_H, "r", encoding="utf-8").read()
        pipeline = open(PIPELINE_CC, "r", encoding="utf-8").read()
        cmd_cc = open(CMD_CC, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read source files: {e}; skipped")
        return 0

    applied = [
        path
        for path, text in ((CMD_H, h), (PIPELINE_CC, pipeline), (CMD_CC, cmd_cc))
        if "dynamic_state_shadow" in text
    ]
    if len(applied) == 3:
        print(f"{NAME}: already applied")
        return 0
    if applied:
        print(f"{NAME}: WARNING partially applied ({', '.join(applied)}); skipped")
        return 0
    if "dynamic_state_map" in h or "dynamic_state_map" in pipeline:
        print(f"{NAME}: WARNING older patch version present; skipped")
        return 0

    anchors = (
        (CMD_H, h.count(H_DEFINE_OLD), "tu_cmd_state definition"),
        (CMD_H, h.count(H_OLD), "dynamic_state member"),
        (PIPELINE_CC, pipeline.count(INC_OLD), "tu_rmv.h include"),
        (CMD_CC, cmd_cc.count(EXEC_OLD), "tu_CmdExecuteCommands flush block"),
    )
    for path, found, what in anchors:
        if found != 1:
            print(f"{NAME}: WARNING {what} anchor matched {found}x (want 1) in {path}; skipped")
            return 0

    new_pipeline, err = patch_pipeline(pipeline)
    if err:
        print(f"{NAME}: WARNING {err}; skipped")
        return 0

    h = h.replace(H_DEFINE_OLD, H_DEFINE_NEW, 1)
    h = h.replace(H_OLD, H_NEW, 1)
    new_pipeline = new_pipeline.replace(INC_OLD, INC_NEW, 1)
    cmd_cc = cmd_cc.replace(EXEC_OLD, EXEC_NEW, 1)

    if not (MARKER in h and MARKER in new_pipeline and MARKER in cmd_cc):
        print(f"{NAME}: WARNING replacement did not take; skipped")
        return 0

    try:
        open(CMD_H, "w", encoding="utf-8", newline="\n").write(h)
        open(PIPELINE_CC, "w", encoding="utf-8", newline="\n").write(new_pipeline)
        open(CMD_CC, "w", encoding="utf-8", newline="\n").write(cmd_cc)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write source files: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (TU_DRAW_STATE_SKIP_IDENTICAL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())