# AdrenoTools Turnip drivers for Adreno 810 / 812 (A81X)

Driver packages (`libvulkan_freedreno.so` + `meta.json`) for **AdrenoTools** loaders, bringing up
**Adreno 810 / 812** — and the wider **Adreno 8XX** family — on Vulkan through Mesa's **Turnip**
Freedreno Vulkan driver.

> [!WARNING]
> **Experimental.** There might be rendering glitches, crashes and performance regressions in some titles.
> This project is **not affiliated with** Mesa, Qualcomm, Google, or any of the applications used to
> test it. **Do not** report bugs caused by these builds to Mesa or to whitebelyash — report them to
> the maintainer (see *Contact*).

## Which download do I want? (build suffixes)

The build variant is carried in the release version as a **suffix** — e.g. `tu_v3N` and `tu_v3U`.

| Suffix | Builder | Source | Mesa base | Use it? |
|:------:|---------|--------|-----------|:-------:|
| **`-N`** | `turnip_builder_new.sh` | `new-a810` branch of [mist-mesa-unified](https://github.com/WinterMist010/mist-mesa-unified) — a fork of whitebelyash's **`turnip/gen8`** | **Mesa 26.3.0** | ✅ best A810/812 support |
| **`-U`** | `upstream_patches.sh` | [upstream Mesa](https://gitlab.freedesktop.org/mesa/mesa) + `patches/base` | **Mesa 27.0.0 or newest upstream** | ✅ closest to Mesa |
| **`-M`** / anything else | legacy `turnip_builder.sh` | old `turnip/gen8` | out of date | ❌ legacy, ignore |

### `-N` — the `new-a810` build (recommended for Adreno 810/812)

Built by `turnip_builder_new.sh` from the **`new-a810`** branch of
[mist-mesa-unified](https://github.com/WinterMist010/mist-mesa-unified).

**`new-a810` is not some random branch:** it is whitebelyash's **`turnip/gen8`** Turnip tree,
rebased onto **Mesa 26.3.0**, with the custom A810/812 (and wider A8XX) device configs and
workarounds living directly in the branch. Two artifacts are produced:

- `Turnip-A81X-V<version>.zip` — the main build.
- `Turnip-A81X-sync-V<version>.zip` — the same branch with the timeline-sync patches from
  `patches/sync` added on top. ⚠️ **On `-N` this is a performance regression in most titles** — the
  timeline/KGSL sync rework is slower than the stock syncobj path on this tree, so use the plain build
  unless you specifically need timeline sync (mainly Zink, see below).

### `-U` — upstream Mesa + patches

Built by `upstream_patches.sh` from **upstream Mesa** — currently the **Mesa 27.0.0** development
cycle, or whatever is newest — with the patches in `patches/base` applied on top. This is the build
to use if you want to track upstream as closely as possible.

Two artifacts are produced:

- `Turnip-A81X-V<version>.zip` — the main build.
- `Turnip-A81X-sync-V<version>.zip` — the same build with the KGSL sync fixes from `patches/sync2`
  added on top. Unlike the `-N` sync build this is a **straight performance win** rather than a
  rework, and it is the better choice for **DXVK 2.5 and newer** (see below).

### `-M` / anything else — legacy (do not use)

Any other suffix (including `-M`) comes from the **old** `turnip_builder.sh`
(`turnip_builder_haks.sh` for the "haks" experiments) and is **out of date**. It builds the older
`turnip/gen8` tree without the current fixes — safe to ignore.

### The `-sync` artifacts — `-N` vs `-U`

Both builders emit a second `.zip` with a `-sync` suffix, but they are **not the same thing** and they
behave in opposite ways:

| Artifact | Patches | Effect |
|----------|---------|--------|
| `-N` `Turnip-A81X-sync-*.zip` | `patches/sync` — the timeline/KGSL sync rework (Mesa MR !39751) plus fixes | ❌ **Performance regression.** Timeline sync is slower than the stock syncobj path on the `-N` tree in most titles; only use it if you specifically need timeline sync (mainly Zink). |
| `-U` `Turnip-A81X-sync-*.zip` | `patches/sync2` — small fixes to the **existing** KGSL syncobj path | ✅ **Straight win.** Correctness/perf fixes rather than a rework, so they do not suffer from the `-N` regression and they **improve performance for DXVK 2.5 and newer**. |

In short:

- **`-N`:** use the plain `Turnip-A81X-V<version>.zip` — the `-sync` build is a regression here.
- **`-U`:** the `-sync` build is the better pick, especially with **DXVK 2.5+**, because it benefits from
  the sync fixes without the `-N` downsides.

## Supported GPUs (-U)

- **Adreno 8XX:** 840, 840 (8 Elite Gen 5V version), 830, 829, 825, 812 (untested — expected to behave like an overclocked 810),
  810.
- **Adreno 7XX:** 710 to 750.
- **Adreno 6XX:** whatever upstream Turnip supports.

## Supported GPUs (-N)

- **Adreno 8XX:** 840, 830, 829, 825, 812 (untested — expected to behave like an overclocked 810),
  810.
- **Adreno 7XX:** whatever upstream Turnip supports (+710/720/722 sysmem only).
- **Adreno 6XX:** whatever upstream Turnip supports.

Adreno 830/840 have the most stable configs. Adreno 810/812 may have **better** configs here than
upstream, but may also have more bugs.

## Installation

1. Download the `.zip` from the releases page — **do not** extract it.
2. Open your app's driver manager (Eden, Skyline, Winlator, Yuzu forks, …) and import the `.zip` as
   a custom Turnip / AdrenoTools driver.
3. Select the imported driver and launch. Some apps want the extracted `libvulkan_freedreno.so` +
   `meta.json` instead — follow that app's own instructions.

## Usage notes

- Disable `DXVK_HUD` if you see weird black artifacts.
- Swizzling glitches in Eden and other emulators: try increasing the render scale.
- Games that demand an AMD/NVIDIA GPU: a Steam Deck spoof patch is included — set
  `TU_DEBUG=deck_emu`.
- GMEM rendering produces a glitchy picture on A830 (write page faults); disable it with
  `TU_DEBUG=sysmem`. It is mostly useless for PC emulation anyway.
- Runtime knobs added by the performance patch set (defaults are the perf-friendly choice
  unless noted):
  - `TU_SYSMEM_RELAX_WFI=false` — restore the upstream full WFI on A8XX sysmem subpass-slice
    waits (the default uses `CP_WAIT_FOR_ME`).
  - `TU_DRAW_STATE_SKIP_IDENTICAL=false` — always re-emit dynamic draw state.
  - `TU_IR3_SCHED_LOWER_LIVE=false` — restore the upstream ir3 scheduling order.
  - `TU_LRZ_PRESERVE_FS_SIDEEFFECTS=true` — **opt-in** (off by default): keep LRZ across
    fragment-shader depth-write/side-effect draws instead of invalidating it for the pass.
    Can misrender when depth direction is genuinely unknown.
- `-sync` artifacts are **not** interchangeable between build suffixes: on `-N` the sync build (timeline
  sync) regresses performance in most titles, while on `-U` the sync build (KGSL syncobj fixes) is a win —
  best with **DXVK 2.5 and above**. Choose per build suffix; see *Which download do I want?*.

## Building locally

Run from the repository root; both scripts download the Android NDK and Mesa source into
`turnip_workdir/` and write `.zip` packages to `/tmp/`.

- **`-N` fork build:** `bash ./turnip_builder_new.sh`
- **`-U` upstream build:** `bash ./upstream_patches.sh`
  - Pin a source ref for reproducible builds, e.g.
    `MESA_REF=<tag-or-sha> BUILD_VERSION=12 bash ./upstream_patches.sh`

> The legacy `turnip_builder.sh` / `turnip_builder_haks.sh` are kept only for historical builds and
> are **out of date** — use the two scripts above instead.

## Releases and release notes

Releases are tagged `tu_v<version>` (the build suffix, e.g. `-N` / `-U`, is part
of the version string). The GitHub release body is generated by
`.github/scripts/generate-release-notes.sh`, which the build workflows run, and
it includes:

- the **patches applied** by that builder, with a one-line explanation for each
  (plus the sync stack and, for `-N`, the equivalent branch stack),
- the **commits to `main` since the previous release**,
- the downloadable artifact names, sizes and **SHA-256 checksums**.

The **Highlights** section is editable: put your notes in
`.github/release-notes/changes.md` before dispatching a release, or pass the
`changes` input when you run the workflow (the input wins when non-empty).

## Attributions

This project stands entirely on other people's work. Thanks to:

| Person / project | Contribution |
|---|---|
| **Mesa / Freedreno contributors** | Turnip and the Freedreno driver itself. |
| **whitebelyash** | The `mesa-unified` / `turnip/gen8` tree and many of the A8XX patches this is built on. |
| **WinterMist010** *(me)* | The `new-a810` branch, the A810/812 bring-up work, mesh shader emulation improvement patches, testing, and the builders. |
| **StevenMXZ** | Sync patches for upstream. |
| **ilhan-athn7** | The original `freedreno_turnip-CI` builder scripts this repo descends from. |
| **bylaws** | [libadrenotools](https://github.com/bylaws/libadrenotools) — how these packages get loaded. |
| **The412Banner** | [Banners-Turnip](https://github.com/The412Banner/Banners-Turnip) `a8xx-winnative` patches. |
| **Max's Tech Review** | Mesh-shader emulation and the A8XX correctness patches in `patches/base`. |
| **Hugo223** | Sample-shading gate patch. |
| **Vauzi-17** | A710/720/722 sysmem patches, the A8XX Shader-Processor hysteresis patch (and the HS/DS/GS completion + per-GPU tuning built on it), the A8XX LRZ coherence patches (`FORCE_BI_DIR_LRZ_DISABLE`, `GRAS_LRZ_COLOR_COMP_MASK`), the sysmem WFI relaxation, dynamic draw-state de-duplication, the ir3 lower-live scheduler heuristic, and the A810 blob analysis. |
| **Karmjit Mahil (Igalia)** | Upstream Freedreno work that these patches build on. |


## References

- Turnip on Adreno (XDA): https://forum.xda-developers.com/t/getting-freedreno-turnip-mesa-vulkan-driver-on-a-poco-f3.4323871/
- Mesa freedreno/turnip issue tracker: https://gitlab.freedesktop.org/mesa/mesa/-/issues/6802
- libadrenotools: https://github.com/bylaws/libadrenotools

## Contact

Discord: **voided.exe_**

## AI disclaimer

Some code and documentation in this project were generated with AI, or written with the help of AI.
Anything that ships in a major release (the builds labelled *"vvexp"* or anything tagged as a pre-release (PR)) is tested before it goes
out — but treat all of it as experimental, and please report anything that breaks.