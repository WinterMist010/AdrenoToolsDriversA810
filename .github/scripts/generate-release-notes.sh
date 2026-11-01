#!/usr/bin/env bash
#
# generate-release-notes.sh - build the GitHub release body for the
# Turnip A81X AdrenoTools driver packages.
#
# It produces a single Markdown file that is fed to softprops/action-gh-release
# via `body_path`, so the release body can stay in version control instead of
# being duplicated inside every workflow file.
#
# Usage:
#   VARIANT=new BUILD_VERSION=3 .github/scripts/generate-release-notes.sh [out.md]
#
# Environment:
#   VARIANT            "new"      -> turnip_builder_new.sh (-N, new-a810 branch)
#                      "upstream" -> upstream_patches.sh    (-U, upstream Mesa + patches/base)
#   BUILD_VERSION      Version string used for the zip names and the release tag.
#   RELEASE_CHANGES    Optional Markdown for the "Highlights" section. Overrides
#                      .github/release-notes/changes.md when non-empty.
#   RELEASE_PREV_TAG   Optional previous tag for the changelog. Defaults to the
#                      nearest tag reachable from HEAD (git describe).
#   ARTIFACTS          Optional space separated list of built .zip files; each one
#                      that exists is listed with a SHA-256 checksum.
#
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

VARIANT="${VARIANT:-new}"
BUILD_VERSION="${BUILD_VERSION:-dev}"
RELEASE_CHANGES="${RELEASE_CHANGES:-}"
RELEASE_PREV_TAG="${RELEASE_PREV_TAG:-}"
ARTIFACTS="${ARTIFACTS:-}"
OUT="${1:-$repo_root/release-notes.md}"
CHANGES_FILE="$repo_root/.github/release-notes/changes.md"

# ---------------------------------------------------------------------------
# Patch catalogue: filename -> one line description (with credit).
# Keep in sync with patches/base and patches/sync.
# ---------------------------------------------------------------------------
declare -A DESC=(
  # --- patches/base -------------------------------------------------------
  ["a8xx.patch"]="Combined A8XX stack: A810/812/825/829 device configs, custom resolve, UBO-promotion coalescing, \`TU_DEBUG=nocb\` default, DECK_EMU Steam Deck spoof, \`disable_gmem\` prop, drm-shim support and the \`vsc_unknown_0d08\` prop (whitebelyash, WinterMist010)"
  ["0001-tu-Emulate-VK_EXT_mesh_shader-with-compute.patch"]="Emulate \`VK_EXT_mesh_shader\` with compute shaders - mesh-shader support for games that require it (Max's Tech Review)"
  ["0001A-tu-Mark-compute-state-stale-when-a-secondary-execute.diff"]="mesh: mark compute state stale after a secondary \`vkCmdExecuteCommands\` (Max's Tech Review)"
  ["0001B-tu-mesh-Size-chunk-tables-per-pipeline-and-advertise.diff"]="mesh: size chunk tables per pipeline and advertise the resulting limits (Max's Tech Review)"
  ["0001C-tu-mesh-Only-flush-for-the-CP-after-the-setup-dispat.diff"]="mesh: only flush for the CP after the setup dispatch (Max's Tech Review)"
  ["0001D-tu-mesh-Skip-vertex-and-primitive-fetches-for-dead-c.diff"]="mesh: skip vertex/primitive fetches for dead clusters (Max's Tech Review)"
  ["0002-tu-ir3-Support-a-required-subgroup-size-of-half-a-wa.patch"]="tu,ir3: support a required subgroup size of half a wave - prerequisite for mesh emulation (Max's Tech Review)"
  ["0003-ir3-Sanitize-cube-map-directions-on-A8XX.patch"]="ir3: sanitize cube-map directions on A8XX - fixes cubemap sampling (Max's Tech Review)"
  ["0004-tu-Invalidate-bindless-descriptors-through-the-A8XX-.patch"]="tu: invalidate bindless descriptors through the A8XX descriptor path (Max's Tech Review)"
  ["0005-tu-kgsl-Fetch-A8XX-command-streams-through-a-virtual.patch"]="tu/kgsl: fetch A8XX command streams through a virtual BO - fixes command-stream access on KGSL (Max's Tech Review)"
  ["0006-tu-sample-shading-gate.patch"]="tu: only force per-sample interpolation for shaders that actually use sample shading - perf (Hugo223)"
  ["0007-u_gralloc-always-use-ubwc-detection.patch"]="u_gralloc: always use the UBWC detection path - correct tiling on A8XX (whitebelyash)"
  ["0008-tu-mesh-Experimental-indexed-draw-for-mesh-shaders-w.diff"]="mesh: experimental indexed draw for mesh shaders with a direct index buffer (mesh series)"
  ["0009-tu-mesh-Wait-for-the-ME-before-indirect-chunk-draws.diff"]="mesh: wait for the ME before indirect chunk draws - fixes GPU hangs (mesh series)"
  ["51-fd810-vsc0d08.py"]="Emit \`VSC_UNKNOWN_0D08\` from a per-GPU prop (A810 blob traces write 1) - python patch (WinterMist010)"
  ["54-a8xx-sp-hysteresis.py"]="Initialise Shader-Processor hysteresis thresholds - stops SP clusters power-gating between draws, less wake-up latency in draw-heavy workloads - python patch (Vauzi-17)"
  # --- patches/sync -------------------------------------------------------
  ["39751_3.diff"]="tu/kgsl: timeline-based synchronisation (binary sync -> timeline); needed by some Zink/DXVK paths (Mesa MR !39751)"
  ["tu-kgsl-timeline-improvements.diff"]="Fixes on top of the timeline sync: signalled-fence import, empty-submit fast path, profiling leak, \`TU_KGSL_NO_TIMELINE=1\` escape hatch (follow-up)"
)

describe() { printf '%s' "${DESC[$1]:-}"; }

has_patches() {
  local dir="$1" f
  for f in "$dir"/*.patch "$dir"/*.diff "$dir"/*.py; do
    [ -e "$f" ] && return 0
  done
  return 1
}

# List the patches in a directory as "- `name` - description" bullets.
# Files are sorted by name across all extensions so numbered series stay in order.
list_patches() {
  local dir="$1" f name desc
  while IFS= read -r f; do
    [ -e "$f" ] || continue
    case "$f" in *Zone.Identifier*) continue ;; esac
    name="$(basename "$f")"
    desc="$(describe "$name")"
    if [ -n "$desc" ]; then
      printf -- '- `%s`\n  %s\n' "$name" "$desc"
    else
      printf -- '- `%s`\n' "$name"
    fi
  done < <(ls -1 "$dir"/*.patch "$dir"/*.diff "$dir"/*.py 2>/dev/null | sort)
}

# ---------------------------------------------------------------------------
# Per-variant configuration
# ---------------------------------------------------------------------------
case "$VARIANT" in
  upstream)
    VARIANT_LABEL="upstream Mesa + patches (\`-U\`)"
    BUILDER="upstream_patches.sh"
    SOURCE="[upstream Mesa](https://gitlab.freedesktop.org/mesa/mesa) + \`patches/base\`"
    MESA_BASE="Mesa 27.0.0 / newest upstream"
    VULKAN="1.4.363"
    PRIMARY_TITLE="Base patches - \`patches/base\`"
    PRIMARY_EMPTY="No base patches found."
    SYNC_NOTE="The \`-sync\` variant is currently **disabled** in \`upstream_patches.sh\` (timeline sync is still experimental), so this section is informational only."
    ZIP_NAMES="\`Turnip-A81X-V${BUILD_VERSION}.zip\`"
    PRIMARY_DIR="$repo_root/patches/base"
    ;;
  new|*)
    VARIANT="new"
    VARIANT_LABEL="fork / \`new-a810\` (\`-N\`)"
    BUILDER="turnip_builder_new.sh"
    SOURCE="\`new-a810\` branch of [mist-mesa-unified](https://github.com/WinterMist010/mist-mesa-unified)"
    MESA_BASE="Mesa 26.3.0"
    VULKAN="1.4.363"
    PRIMARY_TITLE="Fork patches - \`patches/fork\`"
    PRIMARY_EMPTY="No \`patches/fork\` patches. The A8XX device configs and workarounds for this build ship inside the \`new-a810\` branch itself; the equivalent stack the \`-U\` build applies on top of upstream Mesa lives in \`patches/base\`."
    SYNC_NOTE="\`-sync\` is a **separate** artifact (\`Turnip-A81X-sync-V${BUILD_VERSION}.zip\`) built from the same branch with the sync patches below added on top."
    ZIP_NAMES="\`Turnip-A81X-V${BUILD_VERSION}.zip\`, \`Turnip-A81X-sync-V${BUILD_VERSION}.zip\`"
    PRIMARY_DIR="$repo_root/patches/fork"
    ;;
esac
SYNC_DIR="$repo_root/patches/sync"

PRIMARY_LIST=""
if has_patches "$PRIMARY_DIR"; then
  PRIMARY_LIST="$(list_patches "$PRIMARY_DIR")"
else
  PRIMARY_LIST="_${PRIMARY_EMPTY}_"
fi

SYNC_LIST=""
if has_patches "$SYNC_DIR"; then
  SYNC_LIST="$(list_patches "$SYNC_DIR")"
else
  SYNC_LIST="_No sync patches present._"
fi

# ---------------------------------------------------------------------------
# Editable highlights
# ---------------------------------------------------------------------------
CHANGES="$RELEASE_CHANGES"
if [ -z "$CHANGES" ] && [ -f "$CHANGES_FILE" ]; then
  CHANGES="$(cat "$CHANGES_FILE")"
fi
if [ -z "${CHANGES//[[:space:]]/}" ]; then
  CHANGES="- No curated highlights for this build. See **Patches applied** and **Changelog** below."
fi

# ---------------------------------------------------------------------------
# Changelog: commits to main since the previous release
# ---------------------------------------------------------------------------
PREV_TAG="$RELEASE_PREV_TAG"
if [ -z "$PREV_TAG" ] && git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
  PREV_TAG="$(git -C "$repo_root" describe --tags --abbrev=0 \
      --exclude "tu_v${BUILD_VERSION}" --exclude "tu_v${BUILD_VERSION}-*" 2>/dev/null || true)"
fi

COMMITS=""
CHANGELOG_HEADING="Repository changelog"
if git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$PREV_TAG" ] && git -C "$repo_root" rev-parse -q --verify "${PREV_TAG}^{commit}" >/dev/null 2>&1; then
    CHANGELOG_HEADING="Commits to \`main\` since \`${PREV_TAG}\`"
    COMMITS="$(git -C "$repo_root" log --no-merges --pretty='- `%h` %s' "${PREV_TAG}..HEAD" 2>/dev/null)"
  else
    CHANGELOG_HEADING="Recent commits to \`main\` (no previous tag found)"
    COMMITS="$(git -C "$repo_root" log --no-merges -n 15 --pretty='- `%h` %s' HEAD 2>/dev/null)"
  fi
fi
if [ -z "${COMMITS//[[:space:]]/}" ]; then
  COMMITS="_Changelog unavailable - not a git checkout, or a shallow clone. Use \`fetch-depth: 0\` in CI._"
fi

REPO_SHA="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# ---------------------------------------------------------------------------
# Artifacts + checksums
# ---------------------------------------------------------------------------
ARTIFACT_ROWS=""
for a in $ARTIFACTS; do
  [ -f "$a" ] || continue
  sum="$(sha256sum "$a" 2>/dev/null | awk '{print $1}')"
  [ -n "$sum" ] || sum="unavailable"
  ARTIFACT_ROWS="${ARTIFACT_ROWS}| \`$(basename "$a")\` | \`${sum}\` |"$'\n'
done
if [ -z "$ARTIFACT_ROWS" ]; then
  ARTIFACT_ROWS="| _(no artifacts checksummed)_ | - |"$'\n'
fi

# ---------------------------------------------------------------------------
# Emit the release body
# ---------------------------------------------------------------------------
{
  cat <<'MARKDOWN'
> [!WARNING]
> **Experimental build.** Expect bugs, glitches, regressions and the occasional broken build.
> This project is **not affiliated with** Mesa, Qualcomm, Google, or any application used to test it.
> Please **do not** report issues caused by these builds to the Mesa project or to whitebelyash.

MARKDOWN

  printf '## Turnip A81X - V%s\n\n' "$BUILD_VERSION"
  printf 'AdrenoTools driver package (`libvulkan_freedreno.so` + `meta.json`) bringing up **Adreno 810 / 812** and the wider **Adreno 8XX** family on Vulkan.\n\n'

  printf '| | |\n|---|---|\n'
  printf '| **Variant** | %s |\n' "$VARIANT_LABEL"
  printf '| **Builder** | `%s` |\n' "$BUILDER"
  printf '| **Source** | %s |\n' "$SOURCE"
  printf '| **Mesa base** | %s |\n' "$MESA_BASE"
  printf '| **Vulkan** | %s |\n' "$VULKAN"
  printf '| **Release tag** | `tu_v%s` |\n' "$BUILD_VERSION"
  printf '| **Min Android API** | %s |\n' "28"
  printf '| **Artifacts** | %s |\n' "$ZIP_NAMES"
  printf '| **Patch set revision** | `%s` |\n' "$REPO_SHA"
  printf '\n'

  printf '## Highlights\n\n'
  printf '%s\n\n' "$CHANGES"

  printf '## Patches applied\n\n'
  printf '### %s\n\n' "$PRIMARY_TITLE"
  printf '%s\n\n' "$PRIMARY_LIST"
  printf '### Sync patches - `patches/sync`\n\n'
  printf '%s\n\n' "$SYNC_NOTE"
  printf '%s\n\n' "$SYNC_LIST"

  printf '## Changelog\n\n'
  printf '### %s\n\n' "$CHANGELOG_HEADING"
  printf '%s\n\n' "$COMMITS"

  cat <<'MARKDOWN'
## Build variants

| Suffix | Builder | Source | Mesa base |
|---|---|---|---|
| **`-N`** | `turnip_builder_new.sh` | `new-a810` branch of mist-mesa-unified | Mesa 26.3.0 |
| **`-U`** | `upstream_patches.sh` | upstream Mesa + `patches/base` | newest upstream |
| **`-M`** / other | legacy `turnip_builder.sh` | older `turnip/gen8` | out of date |

The suffix is carried in the version, e.g. `V3N` / `V3U`. Prefer `-N` or `-U`; anything else is legacy and out of date.

## Supported GPUs

- **Adreno 8XX:** 840, 830, 829, 825, 812 (untested, expected to behave like an overclocked 810), 810.
- **Adreno 7XX:** whatever upstream Turnip supports (710/720/722 sysmem patches where applicable).
- **Adreno 6XX:** whatever upstream Turnip supports.

Adreno 830/840 have the most stable configs. Adreno 810/812 may have better configs than upstream here, but may also have more bugs.

## Installation

1. Download the `.zip` from the assets below - do **not** extract it.
2. Open your emulator / app's driver manager (Eden, Skyline, Winlator, Yuzu forks, ...) and import the `.zip` as a custom Turnip / AdrenoTools driver.
3. Select the imported driver and launch. Some apps want the extracted `libvulkan_freedreno.so` + `meta.json` instead - use the app's own instructions.

## Usage notes

- Disable `DXVK_HUD` if you see weird black artifacts.
- Swizzling glitches in Eden and other emulators: try increasing the render scale.
- Games that demand an AMD/NVIDIA GPU: a Steam Deck spoof patch is included - set `TU_DEBUG=deck_emu`.
- GMEM rendering produces a glitchy picture on A830 (write page faults); disable it with `TU_DEBUG=sysmem`. It is mostly useless for PC emulation anyway.
- `-sync` builds (timeline / KGSL sync) target Zink and can regress performance - test both artifacts.

## Known issues

- Experimental overall; expect rendering glitches and crashes in some titles.
- Timeline sync (`-sync`) is still experimental; traditional syncobj builds are the safe default.
- Adreno 810/812 configs are best-effort and may regress between Mesa versions.

## Artifacts & checksums

| File | SHA-256 |
|---|---|
MARKDOWN

  printf '%s\n' "$ARTIFACT_ROWS"

  cat <<'MARKDOWN'
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