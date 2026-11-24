#!/usr/bin/env python3
# From: WinterMist010 (A81X perf work)
"""Autotune: charge GMEM for per-tile overhead and a margin on tiny-GMEM parts.

Upstream's BANDWIDTH algorithm (the default for native-Vulkan apps, i.e.
Switch emulators) compares estimated sysmem vs gmem traffic:

    gmem_bandwidth = (gmem_bandwidth * 11 + draw_call_bandwidth) / 10

The 10% uplift is meant to capture per-tile state-change overhead. It does
not model the fixed per-tile cost at all: every tile re-runs the binning
visibility stream, the VSC overflow test, the render-mode transition and
per-tile LRZ handling. On the bigger A8XX parts (A825's 2 MiB GMEM, the
multi-MB A830/840) a 1080p pass fits in a handful of tiles, so that is
noise. On the A810/812 (576 KiB GMEM) a 1080p pass is dozens to low
hundreds of tiles and the per-tile fixed cost becomes a first-order term
the flat 10% cannot represent - GMEM gets picked for passes where it is
actually slower.

This adds two GPU props (both env-tunable via FD_DEV_FEATURES) so only
tiny-GMEM parts change behaviour:

    gmem_bandwidth += tile_count * gmem_tile_overhead_bytes
    gmem_bandwidth = gmem_bandwidth * gmem_margin_percent / 100

  * gmem_tile_overhead_bytes (default 0 = upstream): fixed per-tile
    CP/VSC cost in bytes, scaled by the pass' actual tile count and
    layer/view count. a8xx_810 activates the 8192 value sketched
    (commented out) by 0000-a8xx.patch.
  * gmem_margin_percent (100, or 0 = unset, means upstream): blunt
    uniform margin on the GMEM estimate, for quick on-device sweeps.
    a8xx_810 sets the neutral 100; the sketched 135 can be tried with
    FD_DEV_FEATURES=gmem_margin_percent=135 without a rebuild.

Env TU_AT_GMEM_TILE_OVERHEAD (bytes) overrides the tile-overhead prop
(read once, cached); 0 disables. Every other GPU keeps bit-identical
upstream behaviour with the default props.

The A810 defaults go into the a8xx_810 GPUProps block from
0000-a8xx.patch. While 0000's A810-entry rework hunk is drifted (the
a8xx_810 template is defined but not referenced by any add_gpus()), the
props are additionally set in the live inline GPUProps of the A810/812
entry, so the default is effective in both the current and the repaired
tree.

Runs from the Mesa source root. Idempotent; warns and skips on anchor drift.
"""
import sys

NAME = "63-autotune-gmem-tile-overhead.py"
MARKER = "gmem_tile_overhead_bytes"
DEV_INFO = "src/freedreno/common/freedreno_dev_info.h"
AUTOTUNE = "src/freedreno/vulkan/tu_autotune.cc"
DEVICES = "src/freedreno/common/freedreno_devices.py"

DEV_INFO_OLD = "      uint32_t vsc_unknown_0d08;\n"
DEV_INFO_NEW = (
    "      uint32_t vsc_unknown_0d08;\n"
    "\n"
    "      /* Autotune: fixed per-tile CP/VSC overhead charged to GMEM\n"
    "       * rendering (bytes), and a uniform GMEM margin (percent, 100 =\n"
    "       * none). 0 / 100 = upstream behaviour. Tunable via\n"
    "       * FD_DEV_FEATURES.\n"
    "       */\n"
    "      uint32_t gmem_tile_overhead_bytes;\n"
    "      uint32_t gmem_margin_percent;\n"
)

# bandwidth_algo is nested in rp_history and has no device access, so the
# overhead is computed by the caller (tu_autotune::get_optimal_mode, which
# has the device) and handed in as extra arguments.
SIG_OLD = (
    "      render_mode get_optimal_mode(rp_history &history,\n"
    "                                   const struct tu_cmd_state *cmd_state,\n"
    "                                   const struct tu_render_pass *pass,\n"
    "                                   const struct tu_framebuffer *framebuffer,\n"
    "                                   const struct tu_render_pass_state *rp_state)\n"
)
SIG_NEW = (
    "      render_mode get_optimal_mode(rp_history &history,\n"
    "                                   const struct tu_cmd_state *cmd_state,\n"
    "                                   const struct tu_render_pass *pass,\n"
    "                                   const struct tu_framebuffer *framebuffer,\n"
    "                                   const struct tu_render_pass_state *rp_state,\n"
    "                                   uint64_t gmem_tile_overhead = 0,\n"
    "                                   uint32_t gmem_margin_percent = 100)\n"
)

MODEL_OLD = (
    "         gmem_bandwidth = (gmem_bandwidth * 11 + total_draw_call_bandwidth) / 10;\n"
    "\n"
    "         bool select_sysmem = sysmem_bandwidth <= gmem_bandwidth;\n"
)
MODEL_NEW = (
    "         gmem_bandwidth = (gmem_bandwidth * 11 + total_draw_call_bandwidth) / 10;\n"
    "\n"
    "         /* A81X perf: tiny-GMEM parts pay a fixed per-tile cost (VSC\n"
    "          * restart, overflow test, render-mode transition, per-tile\n"
    "          * state) that the flat 10% uplift cannot model. Charge GMEM\n"
    "          * mode for it, scaled by the actual tile count, plus an\n"
    "          * optional uniform margin. 0 / 100 keep upstream behaviour\n"
    "          * on every other part. See 63-autotune-gmem-tile-overhead.py.\n"
    "          */\n"
    "         if (gmem_tile_overhead && cmd_state->tiling) {\n"
    "            uint64_t tile_count =\n"
    "               (uint64_t)cmd_state->tiling->vsc.tile_count.width *\n"
    "               cmd_state->tiling->vsc.tile_count.height *\n"
    "               MAX2(cmd_state->pass->num_views,\n"
    "                    cmd_state->framebuffer->layers);\n"
    "            gmem_bandwidth += tile_count * gmem_tile_overhead;\n"
    "         }\n"
    "         /* 0 means unset (C zero-init) - treat like 100. */\n"
    "         if (gmem_margin_percent && gmem_margin_percent != 100)\n"
    "            gmem_bandwidth = gmem_bandwidth * gmem_margin_percent / 100;\n"
    "\n"
    "         bool select_sysmem = sysmem_bandwidth <= gmem_bandwidth;\n"
)

CALL_OLD = (
    "   if (config.is_enabled(algorithm::BANDWIDTH))\n"
    "      return history.bandwidth.get_optimal_mode(history, cmd_state, pass, framebuffer, rp_state);\n"
)
CALL_NEW = (
    "   if (config.is_enabled(algorithm::BANDWIDTH)) {\n"
    "      /* A81X perf: per-tile GMEM overhead + margin for tiny-GMEM\n"
    "       * parts, see 63-autotune-gmem-tile-overhead.py. The prop\n"
    "       * defaults keep every other GPU bit-identical; the env var\n"
    "       * allows on-device measurement without a rebuild.\n"
    "       */\n"
    "      static int64_t tile_overhead = -1;\n"
    "      if (tile_overhead < 0) {\n"
    "         tile_overhead = debug_get_num_option(\n"
    "            \"TU_AT_GMEM_TILE_OVERHEAD\",\n"
    "            device->physical_device->info->props.gmem_tile_overhead_bytes);\n"
    "      }\n"
    "      return history.bandwidth.get_optimal_mode(\n"
    "         history, cmd_state, pass, framebuffer, rp_state,\n"
    "         (uint64_t) tile_overhead,\n"
    "         device->physical_device->info->props.gmem_margin_percent);\n"
    "   }\n"
)

INCLUDE_OLD = '#include "tu_pass.h"\n'
INCLUDE_NEW = (
    '#include "tu_pass.h"\n'
    '\n'
    '#include "util/u_debug.h"\n'
)

A810_ANCHOR_PREFIX = "enable_tp_ubwc_flag_hint"
# 0000-a8xx.patch ships these autotune hints commented out; activate them.
A810_HINT_TILE = "    #autotune_gmem_tile_overhead_bytes = 8192,\n"
A810_HINT_MARGIN = "    #autotune_gmem_margin_percent = 135,\n"
A810_TILE_LINES = (
    "    # Autotune: per-tile CP/VSC overhead charged to GMEM mode (bytes),\n"
    "    # activates the sketch from 0000-a8xx.patch. Tune with\n"
    "    # TU_AT_GMEM_TILE_OVERHEAD on-device before changing.\n"
    "    gmem_tile_overhead_bytes = 8192,"
)
A810_MARGIN_LINES = (
    "    # Autotune: uniform GMEM margin in percent (100 = none). Kept\n"
    "    # neutral; try the sketched 135 with\n"
    "    # FD_DEV_FEATURES=gmem_margin_percent=135.\n"
    "    gmem_margin_percent = 100,"
)
# While the a8xx_810 template is not referenced by any add_gpus() (the
# 0000 hunk that reworks the A810 entry has drifted), also set the props
# in the live inline GPUProps of the A810/812 entry so the default is
# effective. Once 0000 applies cleanly again the a8xx_810 block is used
# and the inline entry (with this addition) is dropped by it.
A810_LIVE_ANCHOR = (
    "            # This is possibly also needed for a830 (and all of a8xx),\n"
    "            # move to a8xx_base if confirmed needed for a830.\n"
    "            has_fs_tex_prefetch = False,\n"
)
A810_LIVE_LINES = (
    "            # 63-autotune-gmem-tile-overhead.py: also set here while\n"
    "            # the a8xx_810 template is unreferenced (0000 hunk drift).\n"
    "            gmem_tile_overhead_bytes = 8192,\n"
    "            gmem_margin_percent = 100,"
)


def warn(msg):
    print(f"{NAME}: WARNING {msg}; skipped")
    return 0


def prop_is_set(block, name):
    """True when the GPUProps block sets name on a non-comment line."""
    needle = name + " ="
    for ln in block.split("\n"):
        if not ln.lstrip().startswith("#") and needle in ln:
            return True
    return False


def insert_after_anchor(block, lines):
    """Insert lines after the enable_tp_ubwc_flag_hint prop; None on failure."""
    out = None
    for i, ln in enumerate(block.split("\n")):
        if ln.lstrip().startswith(A810_ANCHOR_PREFIX):
            if out is not None:
                return None
            ls = block.split("\n")
            ls[i + 1:i + 1] = lines.split("\n")
            out = "\n".join(ls)
    return out


def main():
    try:
        dev_info = open(DEV_INFO, "r", encoding="utf-8").read()
        autotune = open(AUTOTUNE, "r", encoding="utf-8").read()
        devices = open(DEVICES, "r", encoding="utf-8").read()
    except OSError as e:
        return warn(f"cannot read source files: {e}")

    if MARKER in autotune:
        print(f"{NAME}: already applied ({MARKER} present)")
        return 0

    # 1. GPU props.
    if "gmem_tile_overhead_bytes;" not in dev_info:
        if dev_info.count(DEV_INFO_OLD) != 1:
            return warn(f"dev_info anchor matched {dev_info.count(DEV_INFO_OLD)}x (want 1)")
        dev_info = dev_info.replace(DEV_INFO_OLD, DEV_INFO_NEW, 1)

    # 2. u_debug include for debug_get_num_option.
    if '#include "util/u_debug.h"' not in autotune:
        if autotune.count(INCLUDE_OLD) != 1:
            return warn("tu_pass.h include anchor not found")
        autotune = autotune.replace(INCLUDE_OLD, INCLUDE_NEW, 1)

    # 3. Extra arguments on bandwidth_algo::get_optimal_mode.
    if autotune.count(SIG_OLD) != 1:
        return warn(f"bandwidth get_optimal_mode signature matched {autotune.count(SIG_OLD)}x (want 1)")
    autotune = autotune.replace(SIG_OLD, SIG_NEW, 1)

    # 4. Per-tile overhead term + margin in the bandwidth model.
    if autotune.count(MODEL_OLD) != 1:
        return warn(f"bandwidth model anchor matched {autotune.count(MODEL_OLD)}x (want 1)")
    autotune = autotune.replace(MODEL_OLD, MODEL_NEW, 1)

    # 5. Compute and pass the knobs from the caller (has the device).
    if autotune.count(CALL_OLD) != 1:
        return warn(f"bandwidth call site matched {autotune.count(CALL_OLD)}x (want 1)")
    autotune = autotune.replace(CALL_OLD, CALL_NEW, 1)

    # 6. A810/812 defaults inside the a8xx_810 GPUProps block: activate the
    #    autotune hints that 0000-a8xx.patch ships commented out. Setting
    #    them (even to neutral values) also makes them FD_DEV_FEATURES
    #    tunable, because the generator only emits overrides for props
    #    used by at least one GPUProps().
    start = devices.find("a8xx_810 = GPUProps(")
    if start < 0:
        return warn("a8xx_810 GPUProps block not found in freedreno_devices.py")
    end = devices.find("\n)\n", start)
    if end < 0:
        return warn("a8xx_810 GPUProps block has no terminator")
    end += 3
    block = devices[start:end]
    changed = False
    if not prop_is_set(block, "gmem_tile_overhead_bytes"):
        if block.count(A810_HINT_TILE) == 1:
            block = block.replace(A810_HINT_TILE, A810_TILE_LINES + "\n", 1)
            changed = True
        else:
            new_block = insert_after_anchor(block, A810_TILE_LINES)
            if new_block is None:
                return warn("cannot place gmem_tile_overhead_bytes in a8xx_810")
            block = new_block
            changed = True
    if not prop_is_set(block, "gmem_margin_percent"):
        if block.count(A810_HINT_MARGIN) == 1:
            block = block.replace(A810_HINT_MARGIN, A810_MARGIN_LINES + "\n", 1)
            changed = True
        else:
            print(f"{NAME}: margin hint not found; gmem_margin_percent left unset (defaults to 0 = neutral)")
    if changed:
        devices = devices[:start] + block + devices[end:]

    # 7. Fallback: while no add_gpus() references the a8xx_810 template
    #    (0000's A810-entry rework hunk has drifted), the block above is
    #    dead code. Set the props in the live inline GPUProps of the
    #    A810/812 entry so the default is effective right now; when 0000
    #    applies cleanly again that entry is replaced wholesale and the
    #    a8xx_810 block takes over.
    template_used = "a8xx_810]" in devices or "a8xx_810," in devices
    live_marker = "63-autotune-gmem-tile-overhead.py: also set here"
    if not template_used and live_marker not in devices:
        if devices.count(A810_LIVE_ANCHOR) == 1:
            devices = devices.replace(
                A810_LIVE_ANCHOR,
                A810_LIVE_ANCHOR + A810_LIVE_LINES + "\n", 1)
            print(f"{NAME}: a8xx_810 template unreferenced (0000 drift); "
                  "props also set in the live A810/812 inline GPUProps")
        else:
            print(f"{NAME}: WARNING a8xx_810 template unreferenced and the live "
                  "A810 inline anchor was not found; only the env knobs "
                  "(TU_AT_GMEM_TILE_OVERHEAD / FD_DEV_FEATURES) can set the "
                  "autotune props")

    try:
        open(DEV_INFO, "w", encoding="utf-8", newline="\n").write(dev_info)
        open(AUTOTUNE, "w", encoding="utf-8", newline="\n").write(autotune)
        open(DEVICES, "w", encoding="utf-8", newline="\n").write(devices)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write source files: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (per-tile GMEM overhead + margin in autotune, A810 tile overhead 8192B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
