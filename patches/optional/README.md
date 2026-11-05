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
| `a810-alt-tiling.py` | A/B toggle for the A810 `num_ccu` / `tile_align_*` values: current `2 ccu, 64x32` vs Vauzi-17's blob values `1 ccu, 32x16`. Run it again to flip back. | Changing CCU count/tile size changes tiling and the GMEM/CCU offset math. It needs on-device measurement before it could ever become the default. |