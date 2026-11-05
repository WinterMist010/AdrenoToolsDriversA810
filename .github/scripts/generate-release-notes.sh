#!/usr/bin/env bash
#
# generate-release-notes.sh - build the GitHub release body for the "Turnip
# A81X" AdrenoTools driver packages.
#
# Run it from CI (see .github/workflows/Tu_bd_new.yml and
# .github/workflows/tubdupspatch.yml) so the release body lives in version
# control instead of being duplicated inside every workflow file. The script
# writes a Markdown file that is handed to softprops/action-gh-release via
# "body_path".
#
# Usage:
#   VARIANT=new BUILD_VERSION=3 .github/scripts/generate-release-notes.sh [out.md]
#
# Environment:
#   VARIANT           "new"      -> turnip_builder_new.sh (-N, new-a810 branch)
#                     "upstream" -> upstream_patches.sh   (-U, upstream Mesa)
#   BUILD_VERSION     Version string used for the zip names and the release tag.
#   RELEASE_CHANGES   Optional Markdown for the Highlights section. Overrides
#                     .github/release-notes/changes.md when non-empty.
#   RELEASE_PREV_TAG  Optional previous tag for the changelog. Defaults to the
#                     nearest tag reachable from HEAD (git describe).
#   ARTIFACTS         Optional space separated list of built .zip files; each
#                     one that exists is listed with its size and SHA-256.
#   MESA_VERSION      Optional Mesa version string, e.g. 26.3.0.
#   MESA_COMMIT       Optional Mesa source commit (short sha).
#   BUILD_DATE        Optional build date (defaults to today, UTC).
#
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

VARIANT="${VARIANT:-new}"
BUILD_VERSION="${BUILD_VERSION:-dev}"
RELEASE_CHANGES="${RELEASE_CHANGES:-}"
RELEASE_PREV_TAG="${RELEASE_PREV_TAG:-}"
ARTIFACTS="${ARTIFACTS:-}"
MESA_VERSION="${MESA_VERSION:-}"
MESA_COMMIT="${MESA_COMMIT:-}"
BUILD_DATE="${BUILD_DATE:-$(date -u +%Y-%m-%d)}"
OUT="${1:-$repo_root/release-notes.md}"
CHANGES_FILE="$repo_root/.github/release-notes/changes.md"

# ---------------------------------------------------------------------------
# Patch catalogue: file name -> what the patch does (with credit).
#
# Keep this in sync with patches/base and patches/sync. Every file a builder can
# apply should have an entry, otherwise the "Patches applied" section lists it
# with no explanation. Values may span multiple lines.
# ---------------------------------------------------------------------------
declare -A DESC=(
  # --- patches/base - applied to upstream Mesa by upstream_patches.sh ------
  ["0000-a8xx.patch"]="One \`git format-patch\` bundle that carries the whole A8XX bring-up stack (16 commits rebased onto the current Mesa base). It contains:
- DECK_EMU: advertise as a Steam Deck / AMD RADV VANGOGH (vendor 0x1002) so titles that refuse an unknown GPU still start - \`TU_DEBUG=deck_emu\`
- \`disable_gmem\` GPU property and the plumbing to force system-memory rendering
- drm-shim support for Adreno (host-side tooling)
- A825 enablement and A8XX family device-config updates
- A810 / A812 device configs: VPC and CCU sizing, depth-CCU fraction, A825/829 offset fixes
- A810 workarounds: a few features disabled, custom resolve path, MSAA and FDM off, ir3 UBO-promotion range coalescing, A812 treated like A810
- \`TU_DEBUG=nocb\` enforced by default (concurrent binning off)
- \`vsc_unknown_0d08\` GPU property (A810 blob traces emit \`VSC_UNKNOWN_0D08=1\`)
(whitebelyash, DVD, Karmjit Mahil / Igalia, WinterMist010)"
  ["0001-tu-Emulate-VK_EXT_mesh_shader-with-compute.patch"]="Emulate \`VK_EXT_mesh_shader\` with compute shaders - runs titles that require mesh shaders on hardware without native support (mesh series)"
  ["0001A-tu-Mark-compute-state-stale-when-a-secondary-execute.patch"]="mesh: mark the primary's compute state stale after a secondary \`vkCmdExecuteCommands\` so the next dispatch re-emits its state (mesh series)"
  ["0001B-tu-mesh-Size-chunk-tables-per-pipeline-and-advertise.patch"]="mesh: size the chunk tables per pipeline and advertise the resulting limits (mesh series)"
  ["0001C-tu-mesh-Only-flush-for-the-CP-after-the-setup-dispat.patch"]="mesh: only flush for the CP after the setup dispatch instead of unconditionally (mesh series)"
  ["0001D-tu-mesh-Skip-vertex-and-primitive-fetches-for-dead-c.patch"]="mesh: skip vertex/primitive fetches for dead clusters - avoids needless work (mesh series)"
  ["0002-tu-ir3-Support-a-required-subgroup-size-of-half-a-wa.patch"]="tu, ir3: support a required subgroup size of half a wave - prerequisite for mesh-shader emulation (Max's Tech Review)"
  ["0003-ir3-Sanitize-cube-map-directions-on-A8XX.patch"]="ir3: sanitize cube-map directions on A8XX - fixes cubemap sampling (Max's Tech Review)"
  ["0004-tu-Invalidate-bindless-descriptors-through-the-A8XX-.patch"]="tu: invalidate bindless descriptors through the A8XX descriptor path (Max's Tech Review)"
  ["0005-tu-kgsl-Fetch-A8XX-command-streams-through-a-virtual.patch"]="tu/kgsl: fetch A8XX command streams through a virtual BO - fixes command-stream access on KGSL (Max's Tech Review)"
  ["0006-tu-sample-shading-gate.patch"]="tu: only force per-sample interpolation for shaders that actually use sample shading - small perf win (Hugo223)"
  ["0007-u_gralloc-always-use-ubwc-detection.patch"]="u_gralloc: always use the UBWC detection path - correct tiling on A8XX (whitebelyash)"
  ["0008-tu-mesh-Experimental-indexed-draw-for-mesh-shaders-w.patch"]="mesh: experimental indexed draw for mesh shaders with a direct index buffer (mesh series)"
  ["0009-tu-mesh-Wait-for-the-ME-before-indirect-chunk-draws.patch"]="mesh: wait for the ME before indirect chunk draws - fixes GPU hangs (mesh series)"
  ["51-fd810-vsc0d08.py"]="Python patch: emit \`VSC_UNKNOWN_0D08\` from the per-GPU \`vsc_unknown_0d08\` property (A810 blob traces write 1). Runs after \`0000-a8xx.patch\` (WinterMist010)"
  ["52-a8xx-resolve-cr-cntl.py"]="Python patch: emit \`REG_A8XX_RB_RESOLVE_CR_CNTL=1\` in the context preamble, matching proprietary A8XX blob captures (initialises the color/clear resolve block) (Vauzi-17)"
  ["54-a8xx-sp-hysteresis.py"]="Python patch: initialise the Shader-Processor hysteresis thresholds. Stops SP ALU clusters power-gating between draws, cutting wake-up latency in draw-heavy workloads (Vauzi-17)"
  ["55-a8xx-sp-hysteresis-gs-hs-ds.py"]="Python patch: complete the A8XX Shader-Processor hysteresis set by initialising SP_HS/SP_DS/SP_GS_HYSTERESIS (54 only covered PS/VS/CS). Keeps tessellation/geometry ALU clusters warm, same latency win as 54 for tess/GS-heavy titles"
  ["56-a8xx-sp-hysteresis-tune.py"]="Python patch: make the SP hysteresis thresholds per-GPU props and tune A810 to the blob-observed upper range (PS 0x3000, others 0x1000). Other A8xx parts keep the driver defaults"
  ["57-tu-sysmem-wfi-relax.py"]="Python patch: on A8XX, use CP_WAIT_FOR_ME instead of a full WFI on the sysmem subpass-slice wait, so the CP does not drain the shader cores on every subpass (\`TU_SYSMEM_RELAX_WFI=false\` restores WFI) (Vauzi-17)"
  ["58-tu-lrz-preserve-fs-sideeffects.py"]="Python patch: opt-in LRZ heuristic - temporarily disable LRZ for a single FS-depth-write draw instead of invalidating it for the whole pass. Off by default (\`TU_LRZ_PRESERVE_FS_SIDEEFFECTS=true\` enables it) (Vauzi-17)"
  ["59-tu-draw-state-skip-identical.py"]="Python patch: skip re-emitting a dynamic draw state (and its CP_SET_DRAW_STATE) when the emitted words are byte-identical to the active one. Cuts CP/packet churn in draw-call-heavy workloads (\`TU_DRAW_STATE_SKIP_IDENTICAL\`) (Vauzi-17)"
  ["60-ir3-sched-prefer-lower-live.py"]="Python patch: ir3 scheduler prefers ready instructions with a lower live-register effect when breaking ties under pressure, reducing spikes past the small A810/A812 GPR budget (\`TU_IR3_SCHED_LOWER_LIVE\`) (Vauzi-17)"
  ["61-a8xx-lrz-bidir-disable.py"]="Python patch: set GRAS_SC_BIN_CNTL.FORCE_BI_DIR_LRZ_DISABLE on A8XX sysmem and LRZ-disabled passes, matching blob-captured LRZ coherence (Vauzi-17)"
  ["62-a8xx-lrz-color-comp-mask.py"]="Python patch: emit GRAS_LRZ_COLOR_COMP_MASK with the MRT write masks on A8XX so early-Z LRZ culling stays coherent across multi-MRT / disabled-attachment draws (Vauzi-17)"
  ["a8xx_shared_mem.py"]="Python patch: increase `\`cs_shared_mem_size\`` from 32*1024 to 64*1024 in `\`freedreno_devices.py\`` - robust global replace of whitebelyash's mesa-unified shared-mem bump, idempotent against context drift (WinterMist010)"

  # --- patches/sync - timeline/KGSL sync, used by the -N sync artifact -----
  ["39751_3.diff"]="tu/kgsl: timeline-based synchronisation (binary sync -> timeline). Needed by some Zink/DXVK paths; upstream Mesa MR !39751 (Mesa)"
  ["tu-kgsl-timeline-improvements.diff"]="Fixes on top of the timeline sync: signalled-fence import, empty-submit fast path, profiling leak fix and a \`TU_KGSL_NO_TIMELINE=1\` escape hatch (follow-up)"

  # --- patches/sync2 - KGSL sync fixes, used by the -U sync artifact ------
  ["kgsl-syncobj-merge-ts-fd.patch"]="tu/kgsl: fix \`kgsl_syncobj_merge\` when mixing timestamp- and fd-state syncobjs on different queues. The wrong syncobj was converted to a fence (reading a queue it did not have), producing an invalid fd; convert the correct one before \`sync_merge\`"
  ["kgsl-zero-timeout-poll.patch"]="tu/kgsl: do not pass a zero timeout to the wait ioctl. KGSL treats zero as wait-forever, so a poll (e.g. timeline-semaphore GC while holding a lock on every submit) would block until the GPU finished; read the retired timestamp instead via a new \`kgsl_timestamp_retired\` helper"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
describe() { printf '%s' "${DESC[$1]:-}"; }

# Any .patch/.diff/.py in the directory? (skips the *.off.txt parked patches)
has_patches() {
  local dir="$1" f
  for f in "$dir"/*.patch "$dir"/*.diff "$dir"/*.py; do
    [ -e "$f" ] && return 0
  done
  return 1
}

# Print "- `name`" bullets with an indented description, sorted by name across
# all extensions so numbered series stay in order.
list_patches() {
  local dir="$1" f name desc
  while IFS= read -r f; do
    [ -e "$f" ] || continue
    case "$f" in *Zone.Identifier*) continue ;; esac
    name="$(basename "$f")"
    desc="$(describe "$name")"
    printf -- '- `%s`\n' "$name"
    if [ -n "$desc" ]; then
      printf '%s\n' "$desc" | sed 's/^/  /'
    fi
  done < <(ls -1 "$dir"/*.patch "$dir"/*.diff "$dir"/*.py 2>/dev/null | sort)
}

count_patches() {
  local dir="$1" f n=0
  while IFS= read -r f; do
    [ -e "$f" ] || continue
    case "$f" in *Zone.Identifier*) continue ;; esac
    n=$((n + 1))
  done < <(ls -1 "$dir"/*.patch "$dir"/*.diff "$dir"/*.py 2>/dev/null | sort)
  printf '%s' "$n"
}

# git remote ("git@github.com:o/r.git", "ssh://git@host/o/r", "https://...")
# -> a browsable URL for commit links.
remote_to_web() {
  local url="$1" host path
  url="${url%.git}"
  case "$url" in
    git@*:*)   host="${url#git@}"; host="${host%%:*}"; path="${url#*:}"; url="https://$host/$path" ;;
    ssh://git@*) url="https://${url#ssh://git@}" ;;
  esac
  printf '%s' "$url"
}

# ---------------------------------------------------------------------------
# Per-variant configuration
# ---------------------------------------------------------------------------
case "$VARIANT" in
  upstream)
    VARIANT_LABEL="upstream Mesa + patches (\`-U\`)"
    BUILDER="upstream_patches.sh"
    SOURCE="[upstream Mesa](https://gitlab.freedesktop.org/mesa/mesa) + \`patches/base\`"
    MESA_BASE="Mesa 27.0.0 (newest upstream)"
    VULKAN="1.4.363"
    PRIMARY_TITLE="Base patches - \`patches/base\`"
    PRIMARY_DIR="$repo_root/patches/base"
    PRIMARY_NOTE="Applied to upstream Mesa in file-name order: \`*.patch\` / \`*.diff\` first (via \`git am --3way\`, falling back to \`patch -p1 --fuzz=4\`), then the \`*.py\` scripts."
    SYNC_DIR_NAME="patches/sync2"
    SYNC_DIR="$repo_root/patches/sync2"
    SYNC_NOTE="Applied only to the **sync** artifact (\`Turnip-A81X-sync-V${BUILD_VERSION}.zip\`). The \`-U\` builder takes these from \`patches/sync2\`; the old \`patches/sync\` folder is used by the \`-N\` builder instead."
    BRANCH_EQUIV="no"
    ZIP_NAMES="\`Turnip-A81X-V${BUILD_VERSION}.zip\`, \`Turnip-A81X-sync-V${BUILD_VERSION}.zip\`"
    ;;
  new|*)
    VARIANT="new"
    VARIANT_LABEL="fork / \`new-a810\` (\`-N\`)"
    BUILDER="turnip_builder_new.sh"
    SOURCE="\`new-a810\` branch of [mist-mesa-unified](https://github.com/WinterMist010/mist-mesa-unified)"
    MESA_BASE="Mesa 26.3.0"
    VULKAN="1.4.363"
    PRIMARY_TITLE="Fork patches - \`patches/fork\`"
    PRIMARY_DIR="$repo_root/patches/fork"
    PRIMARY_NOTE="Applied on top of the \`new-a810\` branch with \`git apply\`. Python (\`*.py\`) patches are only used by the \`-U\` builder; this branch carries its workarounds in-tree."
    SYNC_DIR_NAME="patches/sync"
    SYNC_DIR="$repo_root/patches/sync"
    SYNC_NOTE="\`-sync\` is a **separate** artifact (\`Turnip-A81X-sync-V${BUILD_VERSION}.zip\`) built from the same branch with the sync patches below added on top."
    BRANCH_EQUIV="yes"
    ZIP_NAMES="\`Turnip-A81X-V${BUILD_VERSION}.zip\`, \`Turnip-A81X-sync-V${BUILD_VERSION}.zip\`"
    ;;
esac

# ---------------------------------------------------------------------------
# Build the patch lists
# ---------------------------------------------------------------------------
PRIMARY_COUNT="$(count_patches "$PRIMARY_DIR")"
if [ "$PRIMARY_COUNT" -gt 0 ]; then
  PRIMARY_LIST="$(list_patches "$PRIMARY_DIR")"
elif [ "$VARIANT" = "new" ]; then
  PRIMARY_LIST="_No \`patches/fork\` patches - the A8XX device configs and workarounds ship inside the \`new-a810\` branch, see **Baked into the new-a810 branch** below._"
else
  PRIMARY_LIST="_No base patches found._"
fi

SYNC_COUNT="$(count_patches "$SYNC_DIR")"
if [ "$SYNC_COUNT" -gt 0 ]; then
  SYNC_LIST="$(list_patches "$SYNC_DIR")"
else
  SYNC_LIST="_No sync patches present._"
fi

BRANCH_LIST=""
if [ "$BRANCH_EQUIV" = "yes" ]; then
  BRANCH_LIST="$(list_patches "$repo_root/patches/base")"
fi

# Parked patches (*.off.txt) are shipped but deliberately not applied.
DISABLED_LIST=""
while IFS= read -r f; do
  [ -e "$f" ] || continue
  DISABLED_LIST="${DISABLED_LIST}- \`patches/base/$(basename "$f")\`"$'\n'
done < <(ls -1 "$repo_root/patches/base/"*.off.txt 2>/dev/null | sort)

# ---------------------------------------------------------------------------
# Editable highlights
# ---------------------------------------------------------------------------
CHANGES="$RELEASE_CHANGES"
if [ -z "$CHANGES" ] && [ -f "$CHANGES_FILE" ]; then
  CHANGES="$(cat "$CHANGES_FILE")"
fi
if [ -z "${CHANGES//[[:space:]]/}" ]; then
  CHANGES="_No curated highlights for this build - see **Patches applied** and **Changelog** below._"
fi
# drop trailing whitespace so section spacing stays stable regardless of source
CHANGES="${CHANGES%"${CHANGES##*[![:space:]]}"}"

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
REPO_SHA="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)"
REPO_URL=""
if git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
  REPO_URL="$(remote_to_web "$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)")"
fi

MESA_BASE_DISPLAY="${MESA_VERSION:-$MESA_BASE}"
[ -n "$MESA_COMMIT" ] && MESA_BASE_DISPLAY="${MESA_BASE_DISPLAY} \`${MESA_COMMIT}\`"

# ---------------------------------------------------------------------------
# Changelog: commits to main since the previous release
# ---------------------------------------------------------------------------
PREV_TAG="$RELEASE_PREV_TAG"
if [ -z "$PREV_TAG" ] && git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
  PREV_TAG="$(git -C "$repo_root" describe --tags --abbrev=0 \
      --exclude "tu_v${BUILD_VERSION}" --exclude "tu_v${BUILD_VERSION}-*" 2>/dev/null || true)"
fi

COMMITS=""
COMMIT_COUNT=0
CHANGELOG_HEADING="Repository changelog"
if git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
  log_args=(--no-merges --pretty='%h%x09%s')
  if [ -n "$PREV_TAG" ] && git -C "$repo_root" rev-parse -q --verify "${PREV_TAG}^{commit}" >/dev/null 2>&1; then
    CHANGELOG_HEADING="Commits to \`main\` since \`${PREV_TAG}\`"
    log_args+=("${PREV_TAG}..HEAD")
  else
    CHANGELOG_HEADING="Recent commits to \`main\` (no previous tag found)"
    log_args+=(-n 15 HEAD)
  fi
  while IFS=$'\t' read -r sha subject; do
    [ -n "$sha" ] || continue
    COMMIT_COUNT=$((COMMIT_COUNT + 1))
    if [ -n "$REPO_URL" ]; then
      COMMITS="${COMMITS}- [\`${sha}\`](${REPO_URL}/commit/${sha}) ${subject}"$'\n'
    else
      COMMITS="${COMMITS}- \`${sha}\` ${subject}"$'\n'
    fi
  done < <(git -C "$repo_root" log "${log_args[@]}" 2>/dev/null)
fi
if [ -n "$COMMITS" ]; then
  CHANGELOG_HEADING="${CHANGELOG_HEADING} (${COMMIT_COUNT})"
else
  COMMITS="_Changelog unavailable - not a git checkout, or a shallow clone. Use \`fetch-depth: 0\` in CI._"
fi

# ---------------------------------------------------------------------------
# Artifacts + checksums
# ---------------------------------------------------------------------------
ARTIFACT_ROWS=""
for a in $ARTIFACTS; do
  [ -f "$a" ] || continue
  size="$(du -h "$a" 2>/dev/null | cut -f1)"
  [ -n "$size" ] || size="?"
  sum="$(sha256sum "$a" 2>/dev/null | awk '{print $1}')"
  [ -n "$sum" ] || sum="unavailable"
  ARTIFACT_ROWS="${ARTIFACT_ROWS}| \`$(basename "$a")\` | ${size} | \`${sum}\` |"$'\n'
done
if [ -z "$ARTIFACT_ROWS" ]; then
  ARTIFACT_ROWS="| _(no artifacts checksummed)_ | - | - |"$'\n'
fi

# ---------------------------------------------------------------------------
# Emit the release body
# ---------------------------------------------------------------------------
{
  cat <<'MARKDOWN'
> [!WARNING]
> **Experimental build.** Expect bugs, glitches, regressions and the occasional
> broken build. This project is **not affiliated with** Mesa, Qualcomm, Google,
> or any application used to test it. Please **do not** report issues caused by
> these builds to the Mesa project or to whitebelyash.

MARKDOWN

  printf '# Turnip A81X - V%s\n\n' "$BUILD_VERSION"
  printf 'AdrenoTools driver package (`libvulkan_freedreno.so` + `meta.json`) that brings up **Adreno 810 / 812** and the wider **Adreno 8XX** family on Vulkan through the Mesa **Turnip** (Freedreno) driver.\n\n'

  printf '| | |\n|---|---|\n'
  printf '| **Variant** | %s |\n' "$VARIANT_LABEL"
  printf '| **Builder** | `%s` |\n' "$BUILDER"
  printf '| **Source** | %s |\n' "$SOURCE"
  printf '| **Mesa base** | %s |\n' "$MESA_BASE_DISPLAY"
  printf '| **Vulkan** | 1.4.363 |\n'
  printf '| **Build date** | %s |\n' "$BUILD_DATE"
  printf '| **Release tag** | `tu_v%s` |\n' "$BUILD_VERSION"
  printf '| **Min Android API** | 28 |\n'
  printf '| **Artifacts** | %s |\n' "$ZIP_NAMES"
  printf '| **Patch set revision** | `%s` |\n' "$REPO_SHA"
  printf '\n'

  printf '**Contents:** [Downloads](#downloads) - [Highlights](#highlights) - [Patches applied](#patches-applied) - [Changelog](#changelog) - [Build variants](#build-variants) - [Supported GPUs](#supported-gpus) - [Installation](#installation) - [Usage and debug options](#usage-and-debug-options) - [Known issues](#known-issues) - [Reporting issues](#reporting-issues)\n\n'

  printf '## Downloads\n\n'
  printf '| File | Size | SHA-256 |\n|---|---|---|\n'
  printf '%s' "$ARTIFACT_ROWS"
  printf '\n'

  printf '## Highlights\n\n'
  printf '%s\n\n' "$CHANGES"

  printf '## Patches applied\n\n'
  printf '%s\n\n' "$PRIMARY_NOTE"
  printf '### %s\n\n' "$PRIMARY_TITLE"
  printf '%s\n\n' "$PRIMARY_LIST"

  if [ "$BRANCH_EQUIV" = "yes" ] && [ -n "$BRANCH_LIST" ]; then
    printf '### Baked into the new-a810 branch\n\n'
    printf 'The `-N` build does not run `patches/base`. The equivalent A8XX change set - the one the `-U` build applies on top of upstream Mesa from `patches/base` - is listed below. Contents can differ slightly between the branch and these patch files.\n\n'
    printf '%s\n\n' "$BRANCH_LIST"
  fi

  printf '### Sync patches - `%s`\n\n' "$SYNC_DIR_NAME"
  printf '%s\n\n' "$SYNC_NOTE"
  printf '%s\n\n' "$SYNC_LIST"

  if [ -n "$DISABLED_LIST" ]; then
    printf '### Disabled / parked patches\n\n'
    printf 'These files ship in the repository but are deliberately **not** applied (`.off.txt` suffix):\n\n'
    printf '%s' "$DISABLED_LIST"
    printf '\n'
  fi

  printf '## Changelog\n\n'
  printf '### %s\n\n' "$CHANGELOG_HEADING"
  printf '%s\n' "$COMMITS"

  cat <<'MARKDOWN'
## Build variants

The build variant is carried in the release version as a **suffix**, e.g. `tu_v3N` / `tu_v3U`.

| Suffix | Builder | Source | Mesa base | Use it? |
|:------:|---------|--------|-----------|:-------:|
| **`-N`** | `turnip_builder_new.sh` | `new-a810` branch of mist-mesa-unified | Mesa 26.3.0 | yes - best A810/812 support |
| **`-U`** | `upstream_patches.sh` | upstream Mesa + `patches/base` | Mesa 27.0.0 / newest upstream | yes - closest to Mesa |
| **`-M`** / other | legacy `turnip_builder.sh` | older `turnip/gen8` | out of date | no - legacy, ignore |

Prefer `-N` or `-U`; anything else is legacy and out of date.

## Supported GPUs

- **Adreno 8XX:** 840, 830, 829, 825, 812 (untested - expected to behave like an overclocked 810), 810.
- **Adreno 7XX:** whatever upstream Turnip supports (710/720/722 sysmem patches where applicable).
- **Adreno 6XX:** whatever upstream Turnip supports.

Adreno 830/840 have the most stable configs. Adreno 810/812 may have better configs here than upstream, but may also have more bugs.

## Installation

1. Download the `.zip` from the assets **below** - do **not** extract it.
2. Open your app's driver manager (Eden, Skyline, Winlator, Yuzu forks, ...) and import the `.zip` as a custom Turnip / AdrenoTools driver.
3. Select the imported driver and launch. Some apps want the extracted `libvulkan_freedreno.so` + `meta.json` instead - follow that app's own instructions.

## Usage and debug options

| Option | Effect |
|---|---|
| `TU_DEBUG=deck_emu` | Spoof an AMD / Steam Deck GPU (RADV VANGOGH) for titles that refuse Qualcomm GPUs. |
| `TU_DEBUG=sysmem` | Force system-memory rendering; works around GMEM glitches (e.g. the A830 write page faults). |
| `TU_DEBUG=nocb` | Disable concurrent binning. Already the default in these builds. |
| `TU_KGSL_NO_TIMELINE=1` | In `-sync` builds, fall back to the old syncobj (non-timeline) implementation. |
| `DXVK_HUD` | Disable it if you see weird black artifacts. |

## Known issues

- Experimental overall; expect rendering glitches and crashes in some titles.
- Timeline sync (`-sync`) is still experimental; the traditional syncobj build is the safe default.
- Adreno 810/812 configs are best-effort and may regress between Mesa versions.
- Swizzling glitches in Eden and other emulators: try increasing the render scale.

## Reporting issues

- Discord: **voided.exe_**
- Open an issue on this repository.
- **Do not** report bugs from these builds to Mesa, whitebelyash, or any other upstream maintainer.

## Credits

Built on the work of the Mesa / Freedreno contributors, whitebelyash, WinterMist010, ilhan-athn7, bylaws (libadrenotools), The412Banner, Max's Tech Review, Hugo223, Vauzi-17 and Karmjit Mahil (Igalia). See the README for the full attribution table.

MARKDOWN

  printf -- '---\n'
  printf '_Generated by `.github/scripts/generate-release-notes.sh` for the %s build at `%s`. Release notes are editable after publishing._\n' "$VARIANT_LABEL" "$REPO_SHA"
} > "$OUT"

printf 'Release notes written to %s\n' "$OUT"
