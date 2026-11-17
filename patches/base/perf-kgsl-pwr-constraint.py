#!/usr/bin/env python3
# From: Vauzi-17 (perf-variant idea), adapted
"""OPT-IN: pin the KGSL context to the GPU's maximum power level (PWR_MAX).

The default KGSL devfreq governor ramps GPU frequency based on load, which on
short, bursty emulator frames can leave performance on the table. This script
adds the `KGSL_CONTEXT_PWR_CONSTRAINT` context flag and issues a
`KGSL_PROP_PWR_CONSTRAINT` with `KGSL_CONSTRAINT_PWR_MAX` after every draw
context is created, asking the kernel for the highest GPU power level while the
context is alive.

Costs battery life and raises thermals (it is a clock/power override, not a
correctness fix), which is why this ships under patches/optional/ and is NOT
applied by either builder. To enable it:

    cp patches/optional/perf-kgsl-pwr-constraint.py patches/base/

Every edit is anchored and the script warns + skips on drift, so it can never
break a build it does not belong to. Runs from the Mesa source root.
"""
import sys

NAME = "perf-kgsl-pwr-constraint.py"
MARKER = "wnturnip_set_pwr_max_constraint"
TARGET = "src/freedreno/vulkan/tu_knl_kgsl.cc"

FLAGS_OLD = (
    "   struct kgsl_drawctxt_create req = {\n"
    "      .flags = KGSL_CONTEXT_SAVE_GMEM |\n"
    "              KGSL_CONTEXT_NO_GMEM_ALLOC |\n"
    "              KGSL_CONTEXT_PREAMBLE,\n"
    "   };\n"
)
FLAGS_NEW = (
    "   struct kgsl_drawctxt_create req = {\n"
    "      .flags = KGSL_CONTEXT_SAVE_GMEM |\n"
    "              KGSL_CONTEXT_NO_GMEM_ALLOC |\n"
    "              KGSL_CONTEXT_PREAMBLE |\n"
    "              KGSL_CONTEXT_PWR_CONSTRAINT,\n"
    "   };\n"
)

NEW_ANCHOR = (
    "static int\n"
    "kgsl_submitqueue_new(struct tu_device *dev, struct tu_queue *queue)\n{\n"
)
HELPER = (
    "static int\n"
    "wnturnip_set_pwr_max_constraint(int fd, uint32_t context_id)\n"
    "{\n"
    "   struct kgsl_device_constraint_pwrlevel pwrlevel = {\n"
    "      .level = KGSL_CONSTRAINT_PWR_MAX,\n"
    "   };\n"
    "   struct kgsl_device_constraint constraint = {\n"
    "      .type = KGSL_CONSTRAINT_PWRLEVEL,\n"
    "      .context_id = context_id,\n"
    "      .data = (void *)&pwrlevel,\n"
    "      .size = sizeof(pwrlevel),\n"
    "   };\n"
    "   struct kgsl_device_getproperty prop = {\n"
    "      .type = KGSL_PROP_PWR_CONSTRAINT,\n"
    "      .value = (void *)&constraint,\n"
    "      .sizebytes = sizeof(constraint),\n"
    "   };\n"
    "\n"
    "   return safe_ioctl(fd, IOCTL_KGSL_SETPROPERTY, &prop);\n"
    "}\n"
    "\n"
)

CALL_OLD = (
    "   queue->msm_queue_id = req.drawctxt_id;\n"
    "\n"
    "   return 0;\n"
    "}\n"
)
CALL_NEW = (
    "   queue->msm_queue_id = req.drawctxt_id;\n"
    "\n"
    "   /* WN-Turnip: pin this draw context to the maximum GPU power level. */\n"
    "   if (wnturnip_set_pwr_max_constraint(dev->physical_device->local_fd,\n"
    "                                       req.drawctxt_id))\n"
    "      mesa_logw(\"WN-Turnip: KGSL_PROP_PWR_CONSTRAINT failed\");\n"
    "\n"
    "   return 0;\n"
    "}\n"
)


def main():
    try:
        content = open(TARGET, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read {TARGET}: {e}; skipped")
        return 0

    if MARKER in content:
        print(f"{NAME}: already applied ({MARKER} present)")
        return 0

    for old, what in ((FLAGS_OLD, "drawctxt flags"), (NEW_ANCHOR, "submitqueue_new"),
                      (CALL_OLD, "submitqueue_new return")):
        found = content.count(old)
        if found != 1:
            print(f"{NAME}: WARNING {what} anchor matched {found}x (want 1); skipped")
            return 0

    content = content.replace(FLAGS_OLD, FLAGS_NEW, 1)
    content = content.replace(NEW_ANCHOR, HELPER + NEW_ANCHOR, 1)
    content = content.replace(CALL_OLD, CALL_NEW, 1)

    try:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        print(f"{NAME}: ERROR cannot write {TARGET}: {e}", file=sys.stderr)
        return 1

    print(f"{NAME}: applied (KGSL PWR_MAX constraint on every draw context)")
    return 0


if __name__ == "__main__":
    sys.exit(main())