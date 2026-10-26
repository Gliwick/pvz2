#!/usr/bin/env python3
"""Simulate available sun over time for N copies of a single sun plant.

Usage: sun_timeline.py --plant NAME --level N --count N [--addonlevel N]
                       [--strategy cost|recharge|production]
                       [--positions X,Y ...] [--duration SECONDS]
                       [--start-sun N] [--night] [--outdir DIR]

Arguments:
  --plant   Plant name, same as a file in plants/ without ".csv"
            (sunflower, twinsunflower, primalsunflower, lemonaid, shinevine,
            sunshroom, moonflower), or <plant>+shinevine / <plant>+goldleaf (e.g.
            sunflower+shinevine) to plant pairs of <plant> with a shinevine or
            goldleaf on top. Goldleaf alone is not allowed (see Goldleaf
            below).
  --level   Plant level, a "Level" value from that plant's csv (1-10).
  --addonlevel  <plant>+shinevine / <plant>+goldleaf only: the shinevine's or
            goldleaf's level (1-10; default: same as --level).
  --count   How many of the plant to plant in total; nothing else is planted
            until this many are out. Must be 1-45 (9x5 tiles). For <plant>+shinevine
            and <plant>+goldleaf it is the number of pairs (count <plant>
            plus count shinevines or goldleaves).
  --strategy  Required for <plant>+shinevine and <plant>+goldleaf, not used otherwise: cost,
            recharge or production. Decides which plant is planted first when
            both are ready and affordable: the cheaper one (cost), the one
            with the longer recharge (recharge) or the one with the higher
            sun production (production; final stage for sunshroom, the
            amplifier is ignored). It is an error if the two plants tie.
  --positions  Required for moonflower and moonflower+shinevine/goldleaf, not used
            by other plants. One X,Y tile per moonflower, X in 1-9 and Y in 1-5,
            in planting order, e.g. --positions 1,1 2,1 2,2. The number of tiles
            must equal --count and tiles must be distinct. A shinevine or
            goldleaf goes on the tile of the moonflower it pairs with.
  --duration  Simulated time in full seconds (default: 180).
  --start-sun  Sun available at second 0 (default: 75).
  --night   No sun falls from the sky.
  --outdir  Where to write the output file (default: output/ next to this
            script; created if missing).

Plant stats (cost, recharge, sun production) are read from plants/<plant>.csv.
Twinsunflower and shinevine start with their seed packet recharging, so they
are first available after one recharge time instead of at 0 s. Lemonaid does
too, but for its own Starting Recharge. Lemonaid produces every Sun Production
Cooldown seconds (level dependent), counted from planting, instead of the
14 s first / 34 s repeating production of the other plants.
The result row (plant, level, sun at second 0, 1, ... --duration) is appended to
sun_timeline_<count>_plants.csv, so runs with the same --count share one
file. Non-default --duration, --start-sun and --night are added to the file
name as _<duration>s, _start<N> and _night (in that order), e.g.
sun_timeline_4_plants_120s_start50_night.csv, so only runs with the same
settings share a file. A row with the same first two cells (plant and level) is replaced
instead of duplicated.
For moonflower the first cell is moonflower(<positions>), e.g.
moonflower(1,1 2,1 2,2), so different layouts can be told apart (for
moonflower+shinevine: moonflower+shinevine(<strategy>)(<positions>), likewise
for moonflower+goldleaf).

Example:
  python3 sun_timeline.py --plant sunflower --level 3 --count 4
  python3 sun_timeline.py --plant sunshroom --level 10 --count 4
  (both rows end up in output/sun_timeline_4_plants.csv)
  python3 sun_timeline.py --plant moonflower --level 5 --count 3 --positions 1,1 2,1 2,2
  python3 sun_timeline.py --plant sunflower+shinevine --level 5 --addonlevel 3 --count 2 --strategy cost
  python3 sun_timeline.py --plant moonflower+shinevine --level 5 --count 2 --strategy cost --positions 1,1 2,1

Shinevine+<plant>: each plant type has its own recharge timer and sun is shared;
whichever type is ready and affordable is planted next (--strategy picks the
one to go first if both are). The first cell is <plant>+shinevine(<strategy>).
The n-th shinevine pairs
with the n-th <plant>, whichever of the two is planted first (a shinevine may
go on an empty tile). Once both are out, that <plant> produces extra sun equal
to the shinevine's Sun Production Amplifier on every production, and the
shinevine produces its own sun too. The amplifier does not work on moonflower:
a moonflower+shinevine pair only gets the shinevine's own sun.
For <plant>+shinevine the second cell is <plant level>/<addonlevel>.

Goldleaf+<plant> (<plant>+goldleaf): works like the shinevine pairing (the n-th
goldleaf goes with the n-th <plant>, either may be planted first, --strategy
and --addonlevel apply, the second cell is <plant level>/<addonlevel>), but
goldleaf makes no sun itself. Its tile gives 50 sun every 20 s once both are
out, the first time right when the later of the two is planted. For --strategy
production goldleaf counts as 50. Like shinevine, goldleaf starts with its seed
packet recharging. shinevine+goldleaf is allowed (the tile is occupied by the
shinevine; the shinevine's own sun is unchanged); goldleaf+shinevine is not.
Not usable alone. With moonflower, the goldleaf does not count as a neighbour.

Moonflower produces min(Base Sun Production * (1 + adjacent moonflowers),
Max Sun Production). Adjacent means within 1 tile horizontally, vertically or
diagonally, counting only moonflowers already planted when the sun is produced.
"""
import argparse
import csv
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLANTS_DIR = HERE / "plants"

STEP_MS = 250
DEFAULT_DURATION = 180
DEFAULT_START_SUN = 75
PLANT_DELAY_MS = 1000
FIRST_PRODUCTION_MS = 14_000
PRODUCTION_PERIOD_MS = 34_000
SKY_SUN = 50
SKY_FIRST_MS = 5000
SKY_PERIOD_MS = 7500
GOLD_TILE_SUN = 50
GRID_COLUMNS = 9
GRID_ROWS = 5
MAX_COUNT = GRID_COLUMNS * GRID_ROWS


def ms(seconds):
    """Convert seconds (number or string) to integer milliseconds."""
    return round(Decimal(str(seconds)) * 1000)


class Plant(ABC):
    """Anything with a seed packet: cost and recharge come from the plant's csv row."""

    def __init__(self, row):
        self.cost = int(row["Cost"])
        self.recharge_ms = ms(row["Recharge"])
        # How long the seed packet recharges before the first planting.
        self.initial_recharge_ms = 0

    @abstractmethod
    def peak_production(self):
        """Most sun one production can give, ignoring any shinevine amplifier."""


class SunProducer(Plant):
    """Base class for sun-producing plants; subclasses define how much sun is produced."""

    # Time from planting to the first production, and the time between productions.
    first_production_ms = FIRST_PRODUCTION_MS
    period_ms = PRODUCTION_PERIOD_MS

    def due(self, age_ms):
        """Whether a copy of this plant that is age_ms (ms) old produces sun now."""
        since = age_ms - self.first_production_ms
        return since >= 0 and since % self.period_ms == 0

    @abstractmethod
    def production(self, age_ms, planted, lawn):
        """Sun produced by `planted`, a copy of this plant that is age_ms (ms) old.

        lawn is the Lawn it stands on; only plants that care about their
        surroundings (moonflower) use it.
        """


class Addon(Plant):
    """Planted on top of a base plant; the n-th addon pairs with the n-th base plant."""

    def __init__(self, row):
        super().__init__(row)
        self.initial_recharge_ms = self.recharge_ms

    @abstractmethod
    def bonus_production_due(self, t, host, addon):
        """Whether this pairing gives sun at time t (ms); host and addon are their Planted."""

    @abstractmethod
    def bonus_value(self):
        """Sun given each time the pairing pays out."""

    def bonus(self, t, host, addon):
        """Sun this pairing gives at time t (ms)."""
        return self.bonus_value() if self.bonus_production_due(t, host, addon) else 0


class FlatSunProducer(SunProducer):
    """Plant that always produces the same amount of sun."""

    def __init__(self, row):
        super().__init__(row)
        self.sun = int(row["Sun Production"])

    def production(self, age_ms, planted, lawn):
        return self.sun

    def peak_production(self):
        return self.sun


class Twinsunflower(FlatSunProducer):
    """Flat producer that cannot be planted until its first recharge is done."""

    def __init__(self, row):
        super().__init__(row)
        self.initial_recharge_ms = self.recharge_ms


class Lemonaid(FlatSunProducer):
    """Flat producer with its own starting recharge and a level-dependent production period."""

    def __init__(self, row):
        super().__init__(row)
        self.initial_recharge_ms = ms(row["Starting Recharge"])
        self.period_ms = ms(row["Sun Production Cooldown"])

    def due(self, age_ms):
        # No separate first production: it just produces every period since planting.
        return age_ms > 0 and age_ms % self.period_ms == 0


class Shinevine(FlatSunProducer, Addon):
    """Flat producer whose amplifier is added to every production of the plant it sits on."""

    def __init__(self, row):
        super().__init__(row)
        self.amplifier = int(row["Sun Production Amplifier"])

    def bonus_production_due(self, t, host, addon):
        # The amplifier does not work on moonflower.
        return not isinstance(host.plant, Moonflower) and production_due(t, host)

    def bonus_value(self):
        return self.amplifier


class Goldleaf(Addon):
    """Makes no sun itself; its tile gives sun every period while a plant occupies it."""

    period_ms = 20_000

    def peak_production(self):
        return self.bonus_value()

    def bonus_production_due(self, t, host, addon):
        # Pays out from the moment both it and its plant are down, then every period.
        return (t - max(host.at, addon.at)) % self.period_ms == 0

    def bonus_value(self):
        return GOLD_TILE_SUN


class Sunshroom(SunProducer):
    """Small -> medium after Growth Time, medium -> big after another one."""

    def __init__(self, row):
        super().__init__(row)
        self.stages = [int(row[f"Sun Production {s}"]) for s in ("Small", "Medium", "Big")]
        self.growth_ms = ms(row["Growth Time"])

    def production(self, age_ms, planted, lawn):
        return self.stages[min(age_ms // self.growth_ms, 2)]

    def peak_production(self):
        return self.stages[-1]


class Moonflower(SunProducer):
    """Production grows with each adjacent moonflower, up to a maximum."""

    def __init__(self, row):
        super().__init__(row)
        self.sun = int(row["Base Sun Production"])
        self.max_sun = int(row["Max Sun Production"])

    @staticmethod
    def adjacent(a, b):
        """Whether tiles a and b are within 1 tile of each other (including diagonals)."""
        return abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1

    def neighbors(self, planted, lawn):
        """Number of other moonflowers on the lawn adjacent to `planted`."""
        return sum(1 for o in lawn.by_type[self] if o is not planted and self.adjacent(o.tile, planted.tile))

    def production(self, age_ms, planted, lawn):
        return min(self.sun * (1 + self.neighbors(planted, lawn)), self.max_sun)

    def peak_production(self):
        return self.max_sun


PLANT_CLASSES = {
    "moonflower": Moonflower,
    "sunflower": FlatSunProducer,
    "twinsunflower": Twinsunflower,
    "lemonaid": Lemonaid,
    "primalsunflower": FlatSunProducer,
    "shinevine": Shinevine,
    "goldleaf": Goldleaf,
    "sunshroom": Sunshroom,
}


def load_plant(name, level):
    """Build the plant or addon for the given name and level from plants/<name>.csv; exit on bad input."""
    cls = PLANT_CLASSES.get(name)
    if cls is None:
        sys.exit(f"Unsupported plant '{name}'. Supported: {', '.join(sorted(PLANT_CLASSES))}")
    if not 1 <= level <= 10:
        sys.exit(f"Level {level} out of range for '{name}': must be 1-10")
    path = PLANTS_DIR / f"{name}.csv"
    if not path.is_file():
        sys.exit(f"Data file not found: {path}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if int(row["Level"]) == level:
                return cls(row)
    sys.exit(f"Level {level} not found for '{name}'")


def production_due(t, planted):
    """Whether a plant planted as `planted` produces sun at time t (ms)."""
    return planted.plant.due(t - planted.at)


def parse_positions(tokens, count):
    """Parse ['x,y', ...] into [(x, y), ...]; exit unless valid, distinct and `count` long."""
    positions = []
    for tok in tokens:
        try:
            x, y = (int(v) for v in tok.split(","))
        except ValueError:
            sys.exit(f"Bad position '{tok}', expected X,Y like 3,2")
        if not (1 <= x <= GRID_COLUMNS and 1 <= y <= GRID_ROWS):
            sys.exit(f"Position {tok} out of range: "
                     f"x must be 1-{GRID_COLUMNS}, y must be 1-{GRID_ROWS}")
        positions.append((x, y))
    if len(positions) != count:
        sys.exit(f"--positions has {len(positions)} tiles but --count is {count}; they must match")
    if len(set(positions)) != len(positions):
        sys.exit("--positions must not contain the same tile twice")
    return positions


@dataclass(frozen=True)
class Pairing:
    """What a run plants: a base plant and, optionally, one addon that goes on top of it."""

    base: Plant
    addon: Addon = None

    @property
    def plants(self):
        """The plant types of this run: the base, then the addon if any."""
        return [self.base] if self.addon is None else [self.base, self.addon]


@dataclass(frozen=True)
class Pending:
    """A planting waiting out the planting delay."""

    plant: Plant  # the Plant being planted
    at: int  # time the plant lands, ms


@dataclass
class Planted:
    """One plant on the lawn."""

    plant: Plant  # the Plant it is a copy of
    at: int  # planting time, ms
    tile: tuple = None  # (x, y), moonflower only


class Lawn:
    """The plants placed so far."""

    def __init__(self, pairing):
        self.plants = []  # every Planted, in planting order
        self.by_type = {p: [] for p in pairing.plants}  # the Planted of each plant type, in planting order

    def plant(self, plant, t, tile=None):
        """Put a plant of the given type down at time t (ms) and return it."""
        planted = Planted(plant, t, tile)
        self.plants.append(planted)
        self.by_type[plant].append(planted)
        return planted


STRATEGIES = {
    "cost": lambda plant: plant.cost,
    "recharge": lambda plant: -plant.recharge_ms,
    "production": lambda plant: -plant.peak_production(),
}


def simulate(pairing, count, positions=None, strategy=None,
             duration=DEFAULT_DURATION, start_sun=DEFAULT_START_SUN, night=False):
    """Return the available sun at seconds 0..duration.

    pairing is the Pairing to plant; count of each of its plants is planted.
    duration is the simulated time in full seconds; start_sun is the sun at second 0.
    night disables the sun falling from the sky.
    positions[i] is the tile of the i-th planted plant (moonflower only).
    strategy decides which plant is planted first when both are ready and
    affordable, as ordered by STRATEGIES (required when there is an addon).
    """
    plants = pairing.plants
    order = sorted(plants, key=STRATEGIES[strategy]) if strategy else [pairing.base]

    sun = start_sun
    lawn = Lawn(pairing)
    pending = None  # the Pending planting, if any
    recharge_ready = {p: p.initial_recharge_ms for p in plants}
    timeline = []

    for t in range(0, duration * 1000 + 1, STEP_MS):
        if not night and t >= SKY_FIRST_MS and (t - SKY_FIRST_MS) % SKY_PERIOD_MS == 0:
            sun += SKY_SUN
        for p in lawn.plants:
            if isinstance(p.plant, SunProducer) and production_due(t, p):
                sun += p.plant.production(t - p.at, p, lawn)
        if pending and pending.at <= t:
            plant = pending.plant
            pending = None
            sun -= plant.cost
            # Only the base plant has a tile; an addon shares the tile of the base plant it pairs with (even if it is planted first).
            tile = positions[len(lawn.by_type[plant])] if positions and plant is pairing.base else None
            lawn.plant(plant, t, tile)
            # Cost and recharge start on landing, not on selection: this emulates the
            # delay between a human selecting a plant and actually planting it.
            recharge_ready[plant] = t + plant.recharge_ms
        # The n-th addon pairs with the n-th base plant, whichever went down first.
        if pairing.addon:
            for host, addon in zip(lawn.by_type[pairing.base], lawn.by_type[pairing.addon]):
                sun += pairing.addon.bonus(t, host, addon)
        # Plant whichever is ready; the strategy decides who goes first.
        # One planting at a time, so plantings are at least a delay apart.
        if pending is None:
            for plant in order:
                if (len(lawn.by_type[plant]) < count and t >= recharge_ready[plant]
                        and sun >= plant.cost):
                    pending = Pending(plant, t + PLANT_DELAY_MS)
                    break
        if t % 1000 == 0:
            timeline.append(sun)
    return timeline


def write_row(path, row):
    """Write row into the csv at path, replacing a row with the same first two cells."""
    rows = []
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    for i, existing in enumerate(rows):
        if existing[:2] == row[:2]:
            rows[i] = row
            break
    else:
        rows.append(row)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def main():
    """Parse arguments, run the simulation and append the result row to the output file."""
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=__doc__.split("Arguments:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plant", required=True,
                    help=f"plant name, one of: {', '.join(sorted(set(PLANT_CLASSES) - {'goldleaf'}))}; "
                         "or <plant>+shinevine / <plant>+goldleaf")
    ap.add_argument("--level", type=int, required=True, help="plant level (as in the plant's csv)")
    ap.add_argument("--count", type=int, required=True,
                    help="amount of sun producers to plant (pairs for <plant>+shinevine or <plant>+goldleaf)")
    ap.add_argument("--addonlevel", type=int,
                    help="<plant>+shinevine or <plant>+goldleaf only: addon level (default: same as --level)")
    ap.add_argument("--strategy", choices=sorted(STRATEGIES),
                    help="<plant>+shinevine or <plant>+goldleaf only (required): which plant goes first when both "
                         "are ready and affordable: the cheaper, the longer-recharging or the "
                         "higher-producing one")
    ap.add_argument("--positions", nargs="+", metavar="X,Y",
                    help="moonflower and moonflower+shinevine/goldleaf only: one X,Y tile per moonflower "
                         "(x 1-9, y 1-5), in planting order; count must equal --count")
    ap.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                    help=f"simulated time in full seconds (default: {DEFAULT_DURATION})")
    ap.add_argument("--start-sun", type=int, default=DEFAULT_START_SUN,
                    help=f"sun at second 0 (default: {DEFAULT_START_SUN})")
    ap.add_argument("--night", action="store_true",
                    help="no sun falls from the sky")
    ap.add_argument("--outdir", type=Path, default=HERE / "output",
                    help="directory for the output file (default: output/ next to the script)")
    args = ap.parse_args()
    if args.duration <= 0:
        sys.exit("--duration must be positive")
    if args.start_sun < 0:
        sys.exit("--start-sun must not be negative")
    if not 1 <= args.count <= MAX_COUNT:
        sys.exit(f"count must be 1-{MAX_COUNT} ({GRID_COLUMNS}x{GRID_ROWS} tiles)")

    name = args.plant.lower()
    label = name
    level_cell = args.level
    partner = name.rsplit("+", 1)[1] if name.endswith(("+shinevine", "+goldleaf")) else None
    if partner:
        base_name = name.rsplit("+", 1)[0]
        if base_name == "goldleaf" or base_name == partner:
            sys.exit(f"{partner} cannot be planted on {base_name}")
        partner_level = args.level if args.addonlevel is None else args.addonlevel
        pairing = Pairing(load_plant(base_name, args.level), load_plant(partner, partner_level))
        level_cell = f"{args.level}/{partner_level}"
        if not args.strategy:
            sys.exit(f"<plant>+{partner} requires --strategy cost|recharge|production")
        keys = {STRATEGIES[args.strategy](p) for p in pairing.plants}
        if len(keys) < 2:
            sys.exit(f"--strategy {args.strategy} is a tie between {base_name} and {partner}")
        label += f"({args.strategy})"
    else:
        if args.addonlevel is not None:
            sys.exit("--addonlevel is only used for <plant>+shinevine or <plant>+goldleaf")
        if args.strategy:
            sys.exit("--strategy is only used for <plant>+shinevine and <plant>+goldleaf")
        if name == "goldleaf":
            sys.exit("goldleaf makes no sun on its own; use <plant>+goldleaf")
        pairing = Pairing(load_plant(name, args.level))
    positions = None
    if isinstance(pairing.base, Moonflower):
        if not args.positions:
            sys.exit(f"{name} requires --positions")
        positions = parse_positions(args.positions, args.count)
    elif args.positions:
        sys.exit(f"--positions is only used when moonflower is present in --plant, not '{name}'")
    timeline = simulate(pairing, args.count, positions, args.strategy, args.duration, args.start_sun,
                        args.night)

    if positions:
        label += "(" + " ".join(f"{x},{y}" for x, y in positions) + ")"
    suffix = ""
    if args.duration != DEFAULT_DURATION:
        suffix += f"_{args.duration}s"
    if args.start_sun != DEFAULT_START_SUN:
        suffix += f"_start{args.start_sun}"
    if args.night:
        suffix += "_night"
    args.outdir.mkdir(parents=True, exist_ok=True)
    write_row(args.outdir / f"sun_timeline_{args.count}_plants{suffix}.csv",
              [label, str(level_cell), *map(str, timeline)])


if __name__ == "__main__":
    main()
