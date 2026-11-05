#!/usr/bin/env python3
# From: Vauzi-17
"""tu: skip re-emitting a dynamic draw state whose words are unchanged.

A dynamic-state dirty bit (pipeline bind, or a vkCmdSet* with identical values)
makes Turnip emit the state packet into the sub_cs and set
dirty_draw_states |= (1u << id), which later emits CP_SET_DRAW_STATE even when
the register contents did not change. On draw-call-heavy workloads (emulators)
that is pure CP/packet churn.

This patch remembers the first dword of the active IB for each dynamic state and,
when the just-emitted packet is byte-identical, rewinds the sub-allocation and
leaves the previously bound state in place (no dirty bit).

Env TU_DRAW_STATE_SKIP_IDENTICAL (default true):
  true   skip identical dynamic draw states
  false  always re-emit (upstream behaviour)

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "59-tu-draw-state-skip-identical.py"
MARKER = "TU_DRAW_STATE_SKIP_IDENTICAL"
CMD_H = "src/freedreno/vulkan/tu_cmd_buffer.h"
PIPELINE_CC = "src/freedreno/vulkan/tu_pipeline.cc"

H_OLD = (
    "   /* saved states to re-emit in TU_CMD_DIRTY_DRAW_STATE case */\n"
    "   struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];\n"
)
H_NEW = (
    "   /* saved states to re-emit in TU_CMD_DIRTY_DRAW_STATE case */\n"
    "   struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];\n"
    "   /* TU_DRAW_STATE_SKIP_IDENTICAL: first dword of the active IB per state */\n"
    "   const uint32_t *dynamic_state_map[TU_DYNAMIC_STATE_COUNT];\n"
)

INC_OLD = '#include "tu_rmv.h"\n'
INC_NEW = (
    '#include "tu_rmv.h"\n'
    '\n'
    '#include <string.h>\n'
    '\n'
    'DEBUG_GET_ONCE_BOOL_OPTION(tu_draw_state_skip_identical, "TU_DRAW_STATE_SKIP_IDENTICAL", true)\n'
)


def macro(lines):
    out = [ln.ljust(76) + " \\" for ln in lines[:-1]]
    out.append(lines[-1])
    return out


COND_LINES = [
    "#define DRAW_STATE_COND(name, id, extra_cond, ...)",
    "   if ((EMIT_STATE(name) || (extra_cond)) &&",
    "       !(cmd->state.pipeline_draw_states & (1u << id))) {",
    "      unsigned size = tu6_##name##_size<CHIP>(cmd->device, __VA_ARGS__);",
    "      if (size > 0) {",
    "         tu_cs_begin_sub_stream(&cmd->sub_cs, size, &cs);",
    "         tu6_emit_##name<CHIP>(&cs, __VA_ARGS__);",
    "         uint32_t emit_size = tu_cs_get_size(&cs);",
    "         bool identical =",
    "            debug_get_option_tu_draw_state_skip_identical() &&",
    "            cmd->state.dynamic_state_map[id] != NULL &&",
    "            cmd->state.dynamic_state[id].size == emit_size &&",
    "            emit_size > 0 &&",
    "            memcmp(cmd->state.dynamic_state_map[id], cs.start,",
    "                   emit_size * sizeof(uint32_t)) == 0;",
    "         if (identical) {",
    "            cs.cur = cs.start;",
    "            (void)tu_cs_end_draw_state(&cmd->sub_cs, &cs);",
    "         } else {",
    "            cmd->state.dynamic_state_map[id] = cs.start;",
    "            cmd->state.dynamic_state[id] =",
    "               tu_cs_end_draw_state(&cmd->sub_cs, &cs);",
    "            dirty_draw_states |= (1u << id);",
    "         }",
    "      } else {",
    "         cmd->state.dynamic_state[id] = {};",
    "         cmd->state.dynamic_state_map[id] = NULL;",
    "         dirty_draw_states |= (1u << id);",
    "      }",
    "   }",
]

FDM_LINES = [
    "#define DRAW_STATE_FDM(name, id, ...)",
    "   if ((EMIT_STATE(name) || (cmd->state.dirty &",
    "                             (TU_CMD_DIRTY_FDM |",
    "                              TU_CMD_DIRTY_PER_VIEW_VIEWPORT))) &&",
    "       !(cmd->state.pipeline_draw_states & (1u << id))) {",
    "      if (cmd->state.has_fdm || cmd->state.per_layer_viewport) {",
    "         tu_cs_set_writeable(&cmd->sub_cs, true);",
    "         tu6_emit_##name##_fdm(&cs, cmd, __VA_ARGS__);",
    "         cmd->state.dynamic_state[id] =",
    "            tu_cs_end_draw_state(&cmd->sub_cs, &cs);",
    "         tu_cs_set_writeable(&cmd->sub_cs, false);",
    "         cmd->state.dynamic_state_map[id] = NULL;",
    "         dirty_draw_states |= (1u << id);",
    "      } else {",
    "         unsigned size = tu6_##name##_size<CHIP>(cmd->device, __VA_ARGS__);",
    "         if (size > 0) {",
    "            tu_cs_begin_sub_stream(&cmd->sub_cs, size, &cs);",
    "            tu6_emit_##name<CHIP>(&cs, __VA_ARGS__);",
    "            uint32_t emit_size = tu_cs_get_size(&cs);",
    "            bool identical =",
    "               debug_get_option_tu_draw_state_skip_identical() &&",
    "               cmd->state.dynamic_state_map[id] != NULL &&",
    "               cmd->state.dynamic_state[id].size == emit_size &&",
    "               emit_size > 0 &&",
    "               memcmp(cmd->state.dynamic_state_map[id], cs.start,",
    "                      emit_size * sizeof(uint32_t)) == 0;",
    "            if (identical) {",
    "               cs.cur = cs.start;",
    "               (void)tu_cs_end_draw_state(&cmd->sub_cs, &cs);",
    "            } else {",
    "               cmd->state.dynamic_state_map[id] = cs.start;",
    "               cmd->state.dynamic_state[id] =",
    "                  tu_cs_end_draw_state(&cmd->sub_cs, &cs);",
    "               dirty_draw_states |= (1u << id);",
    "            }",
    "         } else {",
    "            cmd->state.dynamic_state[id] = {};",
    "            cmd->state.dynamic_state_map[id] = NULL;",
    "            dirty_draw_states |= (1u << id);",
    "         }",
    "      }",
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

    new = (lines[:cond] + macro(COND_LINES) +
           macro(FDM_LINES) + lines[end:])
    return "\n".join(new) + "\n", None


def main():
    try:
        h = open(CMD_H, "r", encoding="utf-8").read()
        cc = open(PIPELINE_CC, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read source files: {e}; skipped")
        return 0

    if MARKER in cc and "dynamic_state_map" in h:
        print(f"{NAME}: already applied")
        return 0

    if "dynamic_state_map" in cc:
        print(f"{NAME}: WARNING pipeline already references dynamic_state_map; skipped")
        return 0
    if h.count(H_OLD) != 1:
        print(f"{NAME}: WARNING header anchor matched {h.count(H_OLD)}x (want 1); skipped")
        return 0
    if cc.count(INC_OLD) != 1:
        print(f"{NAME}: WARNING include anchor matched {cc.count(INC_OLD)}x (want 1); skipped")
        return 0

    new_cc, err = patch_pipeline(cc)
    if err:
        print(f"{NAME}: WARNING {err}; skipped")
        return 0

    h = h.replace(H_OLD, H_NEW, 1)
    new_cc = new_cc.replace(INC_OLD, INC_NEW, 1)

    try:
        open(CMD_H, "w", encoding="utf-8", newline="\n").write(h)
        open(PIPELINE_CC, "w", encoding="utf-8", newline="\n").write(new_cc)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write source files: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (TU_DRAW_STATE_SKIP_IDENTICAL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())