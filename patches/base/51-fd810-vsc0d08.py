#!/usr/bin/env python3
# Replaces 51-fd810-blob.py (that script overwrites the whole A810 add_gpus block
# and would clobber the a8xx_810 props in a8xx.patch).
"""FD810: emit VSC_UNKNOWN_0D08 from the per-GPU prop (A810 blob traces write 1).

Needs a8xx.patch applied first (adds props.vsc_unknown_0d08 and a8xx_810 sets it to 1).
Run from the Mesa source root. Idempotent; warns and skips if anchors are missing.
"""
import re
import sys

NAME = "51-fd810-vsc0d08.py"
DEV_INFO = "src/freedreno/common/freedreno_dev_info.h"
CMD_BUF = "src/freedreno/vulkan/tu_cmd_buffer.cc"

EMIT_RE = re.compile(
    r"^(?P<indent>[ \t]*)tu_cs_emit_regs\(cs,[ \t]*VSC_UNKNOWN_0D08\(CHIP,[ \t]*0\)\);[ \t]*$",
    re.M)


def main():
    try:
        dev_info = open(DEV_INFO, encoding="utf-8").read()
        src = open(CMD_BUF, encoding="utf-8").read()
    except OSError as e:
        print(f"{NAME}: WARNING cannot read source files: {e}; skipped")
        return 0

    if "props.vsc_unknown_0d08" in src:
        print(f"{NAME}: already applied")
        return 0
    if "vsc_unknown_0d08" not in dev_info:
        print(f"{NAME}: WARNING props.vsc_unknown_0d08 missing (apply a8xx.patch first); skipped")
        return 0

    # Use whichever physical_device accessor this tree already uses.
    acc = ("info->props"
           if src.count("physical_device->info->") >= src.count("physical_device->dev_info.")
           else "dev_info.props")

    def sub(m):
        i = m.group("indent")
        return (f"{i}tu_cs_emit_regs(cs, VSC_UNKNOWN_0D08(CHIP,\n"
                f"{i}   cmd->device->physical_device->{acc}.vsc_unknown_0d08));")

    new, n = EMIT_RE.subn(sub, src)
    if n != 1:
        print(f"{NAME}: WARNING VSC_UNKNOWN_0D08 emit anchor matched {n}x (want 1); skipped")
        return 0

    with open(CMD_BUF, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print(f"{NAME}: applied (VSC_UNKNOWN_0D08 <- props.vsc_unknown_0d08 via {acc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
