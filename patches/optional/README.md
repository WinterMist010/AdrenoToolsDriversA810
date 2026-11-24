# patches/optional

Opt-in scripts that are **not** applied by `turnip_builder_new.sh`,
`upstream_patches.sh` or `build_local.sh`. They are here so they can be tested
without changing the default release builds.

To enable one, copy it into `patches/base/` (or `patches/sync2/` for the `-U`
sync artifact) before building:

```bash
cp patches/optional/perf-kgsl-pwr-constraint.py patches/base/
```

Both scripts are anchored and warn + skip on drift, so a copy that no longer
matches the tree is a no-op rather than a build failure.

| Script | What it does | Why it is opt-in |
|---|---|---|
| `perf-kgsl-pwr-constraint.py` | Adds `KGSL_CONTEXT_PWR_CONSTRAINT` and issues `KGSL_PROP_PWR_CONSTRAINT`/`PWR_MAX` per draw context, asking KGSL for the top GPU power level (from Vauzi-17's perf variant). | It is a clock/power override: real FPS in bursty emulator frames, but it costs battery life and raises thermals. Not appropriate as a default for a driver people run on phones. |
| `a810-alt-tiling.py` | A/B toggle for the A810 `tile_align_*` values: current `64x32` vs Vauzi-17's blob values `32x16`. `num_ccu` is left alone on purpose (see the script). Run it again to flip back. | Changing the tile size moves the tile-space partitioning on a 576 KiB-GMEM part, so it needs on-device measurement. Changing `num_ccu` on its own would overflow the cache layout, which the build now rejects. |