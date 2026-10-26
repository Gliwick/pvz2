#!/usr/bin/env python3
"""Simulate available sun over time for N copies of a single sun plant.

Usage: sun_timeline.py --plant NAME --level N --count N [--shinevinelevel N]
                       [--positions X,Y ...] [--outdir DIR]

Arguments:
  --plant   Plant name, same as a file in plants/ without ".csv"
            (sunflower, twinsunflower, primalsunflower, shinevine, sunshroom,
            moonflower), or shinevine+<plant> (e.g. shinevine+sunflower) to
            plant pairs of <plant> with a shinevine on top. Moonflower and
            shinevine cannot be combined with shinevine.
  --level   Plant level, a "Level" value from that plant's csv (1-10).
  --shinevinelevel  shinevine+<plant> only: the shinevine's level
            (default: same as --level).
  --count   How many of the plant to plant in total; nothing else is planted
            until this many are out. Must be at least 1. For shinevine+<plant>
            it is the number of pairs (count <plant> plus count shinevines).
  --positions  Required for moonflower, not used by other plants. One X,Y tile
            per plant, X in 1-9 and Y in 1-5, in planting order, e.g.
            --positions 1,1 2,1 2,2. The number of tiles must equal --count
            and tiles must be distinct.
  --outdir  Where to write the output file (default: next to this script).

Plant stats (cost, recharge, sun production) are read from plants/<plant>.csv.
The result row (plant, level, sun at second 0, 1, ... 180) is appended to
<count>.csv, so runs with the same --count share one file. A row with the same
first two cells (plant and level) is replaced instead of duplicated.
For moonflower the first cell is moonflower(<positions>), e.g.
moonflower(1,1 2,1 2,2), so different layouts can be told apart.

Example:
  python3 sun_timeline.py --plant sunflower --level 3 --count 4
  python3 sun_timeline.py --plant sunshroom --level 10 --count 4
  (both rows end up in 4.csv)
  python3 sun_timeline.py --plant moonflower --level 5 --count 3 --positions 1,1 2,1 2,2
  python3 sun_timeline.py --plant shinevine+sunflower --level 5 --shinevinelevel 3 --count 2

Shinevine+<plant>: each plant type has its own recharge timer and sun is shared;
whichever type is ready and affordable is planted next. The n-th shinevine pairs
with the n-th <plant>, whichever of the two is planted first (a shinevine may
go on an empty tile). Once both are out, that <plant> produces extra sun equal
to the shinevine's Sun Production Amplifier on every production, and the
shinevine produces its own sun too.
For shinevine+<plant> the second cell is <shinevinelevel>/<plant level>.

Moonflower produces min(Base Sun Production * (1 + adjacent moonflowers),
Max Sun Production). Adjacent means within 1 tile horizontally, vertically or
diagonally, counting only moonflowers already planted when the sun is produced.
"""
import argparse
import csv
import sys
from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLANTS_DIR = HERE / "plants"

STEP_MS = 250
DURATION_MS = 180_000
START_SUN = 75
PLANT_DELAY_MS = 1000
FIRST_PRODUCTION_MS = 14_000
PRODUCTION_PERIOD_MS = 34_000
SKY_SUN = 50
SKY_FIRST_MS = 5000
SKY_PERIOD_MS = 7500


def ms(seconds):
    """Convert seconds (number or string) to integer milliseconds."""
    return round(Decimal(str(seconds)) * 1000)


class Plant(ABC):
    """Base class for sun-producing plants; subclasses define how much sun is produced."""

    def __init__(self, row):
        self.cost = int(row["Cost"])
        self.recharge_ms = ms(row["Recharge"])

    @abstractmethod
    def production(self, age_ms, neighbors=0):
        """Sun produced by a plant of the given age (ms since planting).

        neighbors is the number of adjacent plants of the same kind; only
        plants that care about it (moonflower) use it.
        """


class FlatPlant(Plant):
    """Plant that always produces the same amount of sun."""

    def __init__(self, row):
        super().__init__(row)
        self.sun = int(row["Sun Production"])

    def production(self, age_ms, neighbors=0):
        return self.sun


class Shinevine(FlatPlant):
    """Flat producer whose amplifier is added to the plant it is planted on."""

    def __init__(self, row):
        super().__init__(row)
        self.amplifier = int(row["Sun Production Amplifier"])


class Sunshroom(Plant):
    """Small -> medium after Growth Time, medium -> big after another one."""

    def __init__(self, row):
        super().__init__(row)
        self.stages = [int(row[f"Sun Production {s}"]) for s in ("Small", "Medium", "Big")]
        self.growth_ms = ms(row["Growth Time"])

    def production(self, age_ms, neighbors=0):
        return self.stages[min(age_ms // self.growth_ms, 2)]


class Moonflower(Plant):
    """Production grows with each adjacent moonflower, up to a maximum."""

    def __init__(self, row):
        super().__init__(row)
        self.sun = int(row["Base Sun Production"])
        self.max_sun = int(row["Max Sun Production"])

    def production(self, age_ms, neighbors=0):
        return min(self.sun * (1 + neighbors), self.max_sun)


PLANT_CLASSES = {
    "moonflower": Moonflower,
    "sunflower": FlatPlant,
    "twinsunflower": FlatPlant,
    "primalsunflower": FlatPlant,
    "shinevine": Shinevine,
    "sunshroom": Sunshroom,
}


def load_plant(name, level):
    """Build the Plant for the given name and level from plants/<name>.csv; exit on bad input."""
    cls = PLANT_CLASSES.get(name)
    if cls is None:
        sys.exit(f"Unsupported plant '{name}'. Supported: {', '.join(sorted(PLANT_CLASSES))}")
    with open(PLANTS_DIR / f"{name}.csv", newline="") as f:
        for row in csv.DictReader(f):
            if int(row["Level"]) == level:
                return cls(row)
    sys.exit(f"Level {level} not found for '{name}'")


def count_neighbors(pos, others):
    """Number of tiles in others within 1 tile of pos (including diagonals)."""
    return sum(1 for o in others
               if o != pos and abs(o[0] - pos[0]) <= 1 and abs(o[1] - pos[1]) <= 1)


def parse_positions(tokens, count):
    """Parse ['x,y', ...] into [(x, y), ...]; exit unless valid, distinct and `count` long."""
    positions = []
    for tok in tokens:
        try:
            x, y = (int(v) for v in tok.split(","))
        except ValueError:
            sys.exit(f"Bad position '{tok}', expected X,Y like 3,2")
        if not (1 <= x <= 9 and 1 <= y <= 5):
            sys.exit(f"Position {tok} out of range: x must be 1-9, y must be 1-5")
        positions.append((x, y))
    if len(positions) != count:
        sys.exit(f"--positions has {len(positions)} tiles but --count is {count}; they must match")
    if len(set(positions)) != len(positions):
        sys.exit("--positions must not contain the same tile twice")
    return positions


class Planted:
    """One plant on the lawn."""

    def __init__(self, kind, at, index, tile=None):
        self.kind = kind  # index into the simulated plant list
        self.at = at  # planting time, ms
        self.index = index  # how many plants of this kind were planted before it
        self.tile = tile  # (x, y), moonflower only


def simulate(plants, count, positions=None):
    """Return the available sun at seconds 0..180.

    plants is [plant] or, for a shinevine combination, [plant, shinevine];
    count of each kind is planted. positions[i] is the tile of the i-th
    planted plant (moonflower only).
    """
    sun = START_SUN
    lawn = []
    pending = []  # (kind, planting time) of plantings waiting out the delay
    recharge_ready = [0] * len(plants)
    timeline = []

    def planted_count(kind):
        return sum(1 for p in lawn if p.kind == kind)

    for t in range(0, DURATION_MS + 1, STEP_MS):
        if t >= SKY_FIRST_MS and (t - SKY_FIRST_MS) % SKY_PERIOD_MS == 0:
            sun += SKY_SUN
        tiles = [p.tile for p in lawn]
        for p in lawn:
            since = t - p.at - FIRST_PRODUCTION_MS
            if since >= 0 and since % PRODUCTION_PERIOD_MS == 0:
                neighbors = count_neighbors(p.tile, tiles) if positions else 0
                sun += plants[p.kind].production(t - p.at, neighbors)
                # The n-th shinevine pairs with the n-th plant, whichever went down first.
                if p.kind == 0 and len(plants) > 1 and planted_count(1) > p.index:
                    sun += plants[1].amplifier
        for kind, at in pending:
            if at != t:
                continue
            sun -= plants[kind].cost
            tile = positions[len(lawn)] if positions else None
            lawn.append(Planted(kind, t, planted_count(kind), tile))
            recharge_ready[kind] = t + plants[kind].recharge_ms
        pending = [(kind, at) for kind, at in pending if at != t]
        # Plant whichever kinds are ready; the earliest recharged goes first.
        reserved = sum(plants[kind].cost for kind, _ in pending)
        for kind in sorted(range(len(plants)), key=lambda k: recharge_ready[k]):
            plant = plants[kind]
            in_flight = planted_count(kind) + sum(1 for k, _ in pending if k == kind)
            if (in_flight >= count or t < recharge_ready[kind]
                    or any(k == kind for k, _ in pending)
                    or sun - reserved < plant.cost):
                continue
            pending.append((kind, t + PLANT_DELAY_MS))
            reserved += plant.cost
        if t % 1000 == 0:
            timeline.append(sun)
    return timeline


def write_row(path, row):
    """Write row into the csv at path, replacing a row with the same first two cells."""
    rows = []
    if path.exists():
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
    for i, existing in enumerate(rows):
        if existing[:2] == row[:2]:
            rows[i] = row
            break
    else:
        rows.append(row)
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def main():
    """Parse arguments, run the simulation and append the result row to <count>.csv."""
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=__doc__.split("Arguments:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plant", required=True,
                    help=f"plant name, one of: {', '.join(sorted(PLANT_CLASSES))}")
    ap.add_argument("--level", type=int, required=True, help="plant level (as in the plant's csv)")
    ap.add_argument("--count", type=int, required=True,
                    help="amount of sun producers to plant (pairs for shinevine+<plant>)")
    ap.add_argument("--shinevinelevel", type=int,
                    help="shinevine+<plant> only: shinevine level (default: same as --level)")
    ap.add_argument("--positions", nargs="+", metavar="X,Y",
                    help="moonflower only: one X,Y tile per plant (x 1-9, y 1-5), "
                         "in planting order; count must equal --count")
    ap.add_argument("--outdir", type=Path, default=HERE,
                    help="directory for the <count>.csv output file (default: script directory)")
    args = ap.parse_args()
    if args.count < 1:
        sys.exit("count must be at least 1")

    name = args.plant.lower()
    label = name
    level_cell = args.level
    if name.startswith("shinevine+"):
        base_name = name.split("+", 1)[1]
        if base_name == "moonflower":
            sys.exit("moonflower is incompatible with shinevine")
        if base_name == "shinevine":
            sys.exit("shinevine cannot be planted on shinevine")
        vine_level = args.shinevinelevel or args.level
        plants = [load_plant(base_name, args.level), load_plant("shinevine", vine_level)]
        level_cell = f"{vine_level}/{args.level}"
    else:
        if args.shinevinelevel:
            sys.exit("--shinevinelevel is only used for shinevine+<plant>")
        plants = [load_plant(name, args.level)]
    positions = None
    if name == "moonflower":
        if not args.positions:
            sys.exit("moonflower requires --positions")
        positions = parse_positions(args.positions, args.count)
    elif args.positions:
        sys.exit(f"--positions is only used for moonflower, not '{name}'")
    timeline = simulate(plants, args.count, positions)

    if positions:
        label += "(" + " ".join(f"{x},{y}" for x, y in positions) + ")"
    write_row(args.outdir / f"sun_timeline_{args.count}_plants.csv", [label, str(level_cell), *map(str, timeline)])


if __name__ == "__main__":
    main()
