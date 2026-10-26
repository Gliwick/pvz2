#!/usr/bin/env python3
"""Run sun_timeline.py for every single plant (no shinevine/goldleaf pairings).

Usage: run_all_plants.py --level N --count N [--positions X,Y ...]
                         [--duration SECONDS] [--start-sun N] [--night]
                         [--outdir DIR]

Arguments:
  --level, --count  Same for every plant, see sun_timeline.py.
  --positions  One X,Y tile per moonflower, see sun_timeline.py. Without it
            moonflower is skipped.
  --duration, --start-sun, --night, --outdir  Passed on to sun_timeline.py
            when given.

Goldleaf is not run: it makes no sun on its own.
"""
import argparse
import subprocess
import sys

from sun_timeline import HERE, PLANT_CLASSES

SINGLE_PLANTS = sorted(set(PLANT_CLASSES) - {"goldleaf"})


def main():
    """Parse arguments and run sun_timeline.py once per plant; exit 1 if any run fails."""
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=__doc__.split("Arguments:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--level", type=int, required=True, help="plant level, same for all plants")
    ap.add_argument("--count", type=int, required=True, help="amount of each plant to plant")
    ap.add_argument("--positions", nargs="+", metavar="X,Y",
                    help="moonflower only: one X,Y tile per moonflower (moonflower is skipped without it)")
    ap.add_argument("--duration", type=int, help="simulated time in full seconds")
    ap.add_argument("--start-sun", type=int, help="sun at second 0")
    ap.add_argument("--night", action="store_true", help="no sun falls from the sky")
    ap.add_argument("--outdir", help="directory for the output files")
    args = ap.parse_args()

    common = ["--level", str(args.level), "--count", str(args.count)]
    if args.duration is not None:
        common += ["--duration", str(args.duration)]
    if args.start_sun is not None:
        common += ["--start-sun", str(args.start_sun)]
    if args.night:
        common.append("--night")
    if args.outdir is not None:
        common += ["--outdir", args.outdir]

    failed = []
    for plant in SINGLE_PLANTS:
        extra = []
        if plant == "moonflower":
            if not args.positions:
                print("skipping moonflower: no --positions given", file=sys.stderr)
                continue
            extra = ["--positions", *args.positions]
        print(f"== {plant}", flush=True)
        result = subprocess.run(
            [sys.executable, str(HERE / "sun_timeline.py"), "--plant", plant, *common, *extra])
        if result.returncode != 0:
            failed.append(plant)
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
