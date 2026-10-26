#!/usr/bin/env python3
"""Simulate available sun over time for N copies of a single sun plant.

Usage: sun_timeline.py --plant NAME --level N --count N [--shinevinelevel N]
                       [--positions X,Y ...] [--outdir DIR]

Arguments:
  --plant   Plant name, same as a file in plants/ without ".csv"
            (sunflower, twinsunflower, primalsunflower, shinevine, sunshroom,
            moonflower), or <plant>+shinevine (e.g. sunflower+shinevine) to
            plant pairs of <plant> with a shinevine on top. Moonflower and
            shinevine cannot be combined with shinevine.
  --level   Plant level, a "Level" value from that plant's csv (1-10).
  --shinevinelevel  <plant>+shinevine only: the shinevine's level
            (default: same as --level).
  --goldleaflevel  <plant>+goldleaf only: the goldleaf's level
            (default: same as --level).
  --count   How many of the plant to plant in total; nothing else is planted
            until this many are out. Must be at least 1. For <plant>+shinevine
            it is the number of pairs (count <plant> plus count shinevines).
  --strategy  Required for <plant>+shinevine, not used otherwise: cost,
            recharge or production. Decides which plant is planted first when
            both are ready and affordable: the cheaper one (cost), the one
            with the longer recharge (recharge) or the one with the higher
            sun production (production; final stage for sunshroom, the
            amplifier is ignored). It is an error if the two plants tie.
  --positions  Required for moonflower, not used by other plants. One X,Y tile
            per plant, X in 1-9 and Y in 1-5, in planting order, e.g.
            --positions 1,1 2,1 2,2. The number of tiles must equal --count
            and tiles must be distinct.
  --outdir  Where to write the output file (default: output/ next to this
            script; created if missing).

Plant stats (cost, recharge, sun production) are read from plants/<plant>.csv.
Twinsunflower and shinevine start with their seed packet recharging, so they
are first available after one recharge time instead of at 0 s.
The result row (plant, level, sun at second 0, 1, ... 180) is appended to
sun_timeline_<count>_plants.csv, so runs with the same --count share one file. A row with the same
first two cells (plant and level) is replaced instead of duplicated.
For moonflower the first cell is moonflower(<positions>), e.g.
moonflower(1,1 2,1 2,2), so different layouts can be told apart.

Example:
  python3 sun_timeline.py --plant sunflower --level 3 --count 4
  python3 sun_timeline.py --plant sunshroom --level 10 --count 4
  (both rows end up in 4.csv)
  python3 sun_timeline.py --plant moonflower --level 5 --count 3 --positions 1,1 2,1 2,2
  python3 sun_timeline.py --plant sunflower+shinevine --level 5 --shinevinelevel 3 --count 2 --strategy cost

Shinevine+<plant>: each plant type has its own recharge timer and sun is shared;
whichever type is ready and affordable is planted next (--strategy picks the
one to go first if both are). The first cell is <plant>+shinevine(<strategy>). The n-th shinevine pairs
with the n-th <plant>, whichever of the two is planted first (a shinevine may
go on an empty tile). Once both are out, that <plant> produces extra sun equal
to the shinevine's Sun Production Amplifier on every production, and the
shinevine produces its own sun too.
For <plant>+shinevine the second cell is <plant level>/<shinevinelevel>.

Goldleaf+<plant> (<plant>+goldleaf): works like the shinevine pairing (the n-th
goldleaf goes with the n-th <plant>, either may be planted first, --strategy
and --goldleaflevel apply, the second cell is <plant level>/<goldleaflevel>), but
goldleaf makes no sun itself. Its tile gives 50 sun every 20 s once both are
out, the first time right when the later of the two is planted. For --strategy
production goldleaf counts as 50. Like shinevine, goldleaf starts with its seed
packet recharging. shinevine+goldleaf is allowed (the tile is occupied by the
shinevine; the shinevine's own sun is unchanged); goldleaf+shinevine is not.
Not usable alone, nor with moonflower.

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

    # Whether the seed packet must recharge once before the first planting.
    starts_recharging = False
    # False for plants that make no sun on their own (goldleaf).
    produces_itself = True

    def __init__(self, row):
        self.cost = int(row["Cost"])
        self.recharge_ms = ms(row["Recharge"])

    @abstractmethod
    def production(self, age_ms, neighbors=0):
        """Sun produced by a plant of the given age (ms since planting).

        neighbors is the number of adjacent plants of the same kind; only
        plants that care about it (moonflower) use it.
        """

    @abstractmethod
    def peak_production(self):
        """Most sun one production can give, ignoring any shinevine amplifier."""


class FlatPlant(Plant):
    """Plant that always produces the same amount of sun."""

    def __init__(self, row):
        super().__init__(row)
        self.sun = int(row["Sun Production"])

    def production(self, age_ms, neighbors=0):
        return self.sun

    def peak_production(self):
        return self.sun


class Twinsunflower(FlatPlant):
    """Flat producer that cannot be planted until its first recharge is done."""

    starts_recharging = True


class Shinevine(FlatPlant):
    """Flat producer whose amplifier is added to the plant it is planted on."""

    starts_recharging = True

    def __init__(self, row):
        super().__init__(row)
        self.amplifier = int(row["Sun Production Amplifier"])


class Goldleaf(Plant):
    """Makes no sun itself; its tile gives sun every period while a plant occupies it."""

    starts_recharging = True
    produces_itself = False
    sun = 50
    period_ms = 20_000

    def production(self, age_ms, neighbors=0):
        return self.sun

    def peak_production(self):
        return self.sun


class Sunshroom(Plant):
    """Small -> medium after Growth Time, medium -> big after another one."""

    def __init__(self, row):
        super().__init__(row)
        self.stages = [int(row[f"Sun Production {s}"]) for s in ("Small", "Medium", "Big")]
        self.growth_ms = ms(row["Growth Time"])

    def production(self, age_ms, neighbors=0):
        return self.stages[min(age_ms // self.growth_ms, 2)]

    def peak_production(self):
        return self.stages[-1]


class Moonflower(Plant):
    """Production grows with each adjacent moonflower, up to a maximum."""

    def __init__(self, row):
        super().__init__(row)
        self.sun = int(row["Base Sun Production"])
        self.max_sun = int(row["Max Sun Production"])

    def production(self, age_ms, neighbors=0):
        return min(self.sun * (1 + neighbors), self.max_sun)

    def peak_production(self):
        return self.max_sun


PLANT_CLASSES = {
    "moonflower": Moonflower,
    "sunflower": FlatPlant,
    "twinsunflower": Twinsunflower,
    "primalsunflower": FlatPlant,
    "shinevine": Shinevine,
    "goldleaf": Goldleaf,
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


STRATEGIES = {
    "cost": lambda plant: plant.cost,
    "recharge": lambda plant: -plant.recharge_ms,
    "production": lambda plant: -plant.peak_production(),
}


def simulate(plants, count, positions=None, strategy=None):
    """Return the available sun at seconds 0..180.

    plants is [plant] or, for a shinevine combination, [plant, shinevine];
    count of each kind is planted. positions[i] is the tile of the i-th
    planted plant (moonflower only). strategy decides which kind is planted
    first when several are ready and affordable, as ordered by STRATEGIES
    (required when there is more than one kind).
    """
    order = sorted(range(len(plants)), key=lambda k: STRATEGIES[strategy](plants[k])) \
        if strategy else [0]

    sun = START_SUN
    lawn = []
    pending = []  # (kind, planting time) of plantings waiting out the delay
    recharge_ready = [p.recharge_ms if p.starts_recharging else 0 for p in plants]
    timeline = []

    def planted_count(kind):
        return sum(1 for p in lawn if p.kind == kind)

    for t in range(0, DURATION_MS + 1, STEP_MS):
        if t >= SKY_FIRST_MS and (t - SKY_FIRST_MS) % SKY_PERIOD_MS == 0:
            sun += SKY_SUN
        tiles = [p.tile for p in lawn]
        for p in lawn:
            if not plants[p.kind].produces_itself:
                continue
            since = t - p.at - FIRST_PRODUCTION_MS
            if since >= 0 and since % PRODUCTION_PERIOD_MS == 0:
                neighbors = count_neighbors(p.tile, tiles) if positions else 0
                sun += plants[p.kind].production(t - p.at, neighbors)
                # The n-th shinevine pairs with the n-th plant, whichever went down first.
                if (p.kind == 0 and len(plants) > 1 and isinstance(plants[1], Shinevine)
                        and planted_count(1) > p.index):
                    sun += plants[1].amplifier
        for kind, at in pending:
            if at != t:
                continue
            sun -= plants[kind].cost
            tile = positions[len(lawn)] if positions else None
            lawn.append(Planted(kind, t, planted_count(kind), tile))
            recharge_ready[kind] = t + plants[kind].recharge_ms
        pending = [(kind, at) for kind, at in pending if at != t]
        # A goldleaf tile pays out from the moment both it and its plant are down
        # (so right after the planting above), then every period.
        for g in lawn:
            if plants[g.kind].produces_itself:
                continue
            host = next((p for p in lawn if p.kind == 0 and p.index == g.index), None)
            if host is None:
                continue
            since = t - max(g.at, host.at)
            if since % plants[g.kind].period_ms == 0:
                sun += plants[g.kind].sun
        # Plant whichever kinds are ready; the strategy decides who goes first.
        for kind in order:
            if pending:  # one planting at a time, so plantings are at least a delay apart
                break
            plant = plants[kind]
            if (planted_count(kind) >= count or t < recharge_ready[kind]
                    or sun < plant.cost):
                continue
            pending.append((kind, t + PLANT_DELAY_MS))
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
    """Parse arguments, run the simulation and append the result row to the output file."""
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=__doc__.split("Arguments:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plant", required=True,
                    help=f"plant name, one of: {', '.join(sorted(PLANT_CLASSES))}")
    ap.add_argument("--level", type=int, required=True, help="plant level (as in the plant's csv)")
    ap.add_argument("--count", type=int, required=True,
                    help="amount of sun producers to plant (pairs for <plant>+shinevine)")
    ap.add_argument("--shinevinelevel", type=int,
                    help="<plant>+shinevine only: shinevine level (default: same as --level)")
    ap.add_argument("--goldleaflevel", type=int,
                    help="<plant>+goldleaf only: goldleaf level (default: same as --level)")
    ap.add_argument("--strategy", choices=sorted(STRATEGIES),
                    help="<plant>+shinevine or <plant>+goldleaf only (required): which plant goes first when both "
                         "are ready and affordable: the cheaper, the longer-recharging or the "
                         "higher-producing one")
    ap.add_argument("--positions", nargs="+", metavar="X,Y",
                    help="moonflower only: one X,Y tile per plant (x 1-9, y 1-5), "
                         "in planting order; count must equal --count")
    ap.add_argument("--outdir", type=Path, default=HERE / "output",
                    help="directory for the output file (default: output/ next to the script)")
    args = ap.parse_args()
    if args.count < 1:
        sys.exit("count must be at least 1")

    name = args.plant.lower()
    label = name
    level_cell = args.level
    partner = name.rsplit("+", 1)[1] if name.endswith(("+shinevine", "+goldleaf")) else None
    if partner:
        base_name = name.rsplit("+", 1)[0]
        if base_name == "moonflower":
            sys.exit(f"moonflower cannot be combined with {partner}")
        if base_name == "goldleaf" or base_name == partner:
            sys.exit(f"{partner} cannot be planted on {base_name}")
        own_arg, other_arg = ((args.shinevinelevel, args.goldleaflevel) if partner == "shinevine"
                              else (args.goldleaflevel, args.shinevinelevel))
        if other_arg:
            sys.exit(f"the other partner's level option is not used for <plant>+{partner}")
        partner_level = own_arg or args.level
        plants = [load_plant(base_name, args.level), load_plant(partner, partner_level)]
        level_cell = f"{args.level}/{partner_level}"
        if not args.strategy:
            sys.exit(f"<plant>+{partner} requires --strategy cost|recharge|production")
        keys = {STRATEGIES[args.strategy](p) for p in plants}
        if len(keys) < len(plants):
            sys.exit(f"--strategy {args.strategy} is a tie between {base_name} and {partner}")
        label += f"({args.strategy})"
    else:
        if args.shinevinelevel or args.goldleaflevel:
            sys.exit("--shinevinelevel/--goldleaflevel are only used for <plant>+shinevine "
                     "or <plant>+goldleaf")
        if args.strategy:
            sys.exit("--strategy is only used for <plant>+shinevine and <plant>+goldleaf")
        if name == "goldleaf":
            sys.exit("goldleaf makes no sun on its own; use <plant>+goldleaf")
        plants = [load_plant(name, args.level)]
    positions = None
    if name == "moonflower":
        if not args.positions:
            sys.exit("moonflower requires --positions")
        positions = parse_positions(args.positions, args.count)
    elif args.positions:
        sys.exit(f"--positions is only used for moonflower, not '{name}'")
    timeline = simulate(plants, args.count, positions, args.strategy)

    if positions:
        label += "(" + " ".join(f"{x},{y}" for x, y in positions) + ")"
    args.outdir.mkdir(parents=True, exist_ok=True)
    write_row(args.outdir / f"sun_timeline_{args.count}_plants.csv",
              [label, str(level_cell), *map(str, timeline)])


if __name__ == "__main__":
    main()
