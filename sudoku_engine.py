"""Sudoku engine.

* Generates complete grids by randomised backtracking.
* Digs holes (optionally in rotationally symmetric pairs) while a solution
  counter proves that exactly ONE solution remains.
* Rates difficulty by the human techniques needed to solve the puzzle:

    level 1 (easy)   naked singles, hidden singles
    level 2 (medium) + locked candidates (pointing / claiming), pairs
    level 3 (hard)   + triples, quads, X-Wing, Swordfish, XY-Wing
    level 4 (expert) anything beyond that (chains, trial and error ...)

Supported sizes: 4x4 (2x2 boxes), 6x6 (2x3), 9x9 (3x3), 12x12 (3x4).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations

BOX_SHAPES = {4: (2, 2), 6: (2, 3), 9: (3, 3), 12: (3, 4)}
DIFFICULTIES = ("easy", "medium", "hard", "expert")
LEVEL_OF = {name: i + 1 for i, name in enumerate(DIFFICULTIES)}
SYMBOLS = "123456789ABC"

# fraction of the cells that stay as givens
CLUE_FRACTION = {
    "easy": (0.46, 0.52),
    "medium": (0.38, 0.43),
    "hard": (0.33, 0.37),
    "expert": (0.25, 0.31),
}


class _Contradiction(Exception):
    pass


class Geometry:
    """Index tables for one board size."""

    def __init__(self, n: int):
        if n not in BOX_SHAPES:
            raise ValueError(f"unsupported sudoku size {n}; choose from {sorted(BOX_SHAPES)}")
        self.n = n
        self.br, self.bc = BOX_SHAPES[n]
        self.full = (1 << n) - 1
        n2 = n * n
        self.row_of = [i // n for i in range(n2)]
        self.col_of = [i % n for i in range(n2)]
        per_band = n // self.bc
        self.box_of = [(i // n // self.br) * per_band + (i % n) // self.bc for i in range(n2)]
        self.rows = [[r * n + c for c in range(n)] for r in range(n)]
        self.cols = [[r * n + c for r in range(n)] for c in range(n)]
        self.boxes = [[] for _ in range(n)]
        for i in range(n2):
            self.boxes[self.box_of[i]].append(i)
        self.units = self.rows + self.cols + self.boxes
        self.units_of = [
            (self.rows[self.row_of[i]], self.cols[self.col_of[i]], self.boxes[self.box_of[i]])
            for i in range(n2)
        ]
        self.peers = []
        for i in range(n2):
            s = set(self.rows[self.row_of[i]]) | set(self.cols[self.col_of[i]]) | set(self.boxes[self.box_of[i]])
            s.discard(i)
            self.peers.append(sorted(s))
        self.peerset = [set(p) for p in self.peers]


_GEO: dict[int, Geometry] = {}


def geometry(n: int) -> Geometry:
    if n not in _GEO:
        _GEO[n] = Geometry(n)
    return _GEO[n]


# --------------------------------------------------------------------------
# backtracking search (solution counting / random completion)
# --------------------------------------------------------------------------
def search(geo: Geometry, grid, limit: int = 2, rng: random.Random | None = None):
    """Return up to `limit` solutions of `grid` (list of ints, 0 = empty).

    Constraint propagation (naked + hidden singles) with minimum-remaining-values
    branching, so even sparse 12x12 boards are counted quickly.
    """
    n, full = geo.n, geo.full
    peers, units_of = geo.peers, geo.units_of

    def eliminate(cand, i, bit):
        if not cand[i] & bit:
            return True
        cand[i] &= ~bit
        m = cand[i]
        if m == 0:
            return False
        if m & (m - 1) == 0:                      # naked single
            for p in peers[i]:
                if not eliminate(cand, p, m):
                    return False
        for unit in units_of[i]:                  # hidden single
            places = [j for j in unit if cand[j] & bit]
            if not places:
                return False
            if len(places) == 1 and cand[places[0]] != bit:
                if not assign(cand, places[0], bit):
                    return False
        return True

    def assign(cand, i, bit):
        others = cand[i] & ~bit
        while others:
            b = others & -others
            others ^= b
            if not eliminate(cand, i, b):
                return False
        return cand[i] == bit

    cand = [full] * (n * n)
    for i, v in enumerate(grid):
        if v and not assign(cand, i, 1 << (v - 1)):
            return []
    sols: list[list[int]] = []

    def rec(cand):
        best, best_cnt = -1, 99
        for i, m in enumerate(cand):
            if m & (m - 1):
                cnt = m.bit_count()
                if cnt < best_cnt:
                    best, best_cnt = i, cnt
                    if cnt == 2:
                        break
        if best < 0:
            sols.append([m.bit_length() for m in cand])
            return len(sols) >= limit
        m = cand[best]
        bits = []
        while m:
            b = m & -m
            bits.append(b)
            m ^= b
        if rng is not None:
            rng.shuffle(bits)
        for b in bits:
            nc = cand[:]
            if assign(nc, best, b) and rec(nc):
                return True
        return False

    rec(cand)
    return sols


def count_solutions(geo: Geometry, grid, limit: int = 2) -> int:
    return len(search(geo, grid, limit))


def random_solution(geo: Geometry, rng: random.Random):
    return search(geo, [0] * (geo.n * geo.n), 1, rng)[0]


# --------------------------------------------------------------------------
# difficulty rating with human-style techniques
# --------------------------------------------------------------------------
def rate(geo: Geometry, puzzle) -> tuple[int, bool]:
    """Return (level 1..4, solved_by_logic)."""
    n = geo.n
    n2 = n * n
    full = geo.full
    peers, units = geo.peers, geo.units
    R, C, B = geo.row_of, geo.col_of, geo.box_of
    pc = int.bit_count
    cand = [full] * n2
    for i, v in enumerate(puzzle):
        if v:
            cand[i] = 1 << (v - 1)
    done = [False] * n2

    def settle():
        stack = [i for i in range(n2) if pc(cand[i]) == 1 and not done[i]]
        while stack:
            i = stack.pop()
            if done[i]:
                continue
            done[i] = True
            bit = cand[i]
            for p in peers[i]:
                if cand[p] & bit:
                    cand[p] &= ~bit
                    if cand[p] == 0:
                        raise _Contradiction
                    if pc(cand[p]) == 1 and not done[p]:
                        stack.append(p)

    def hidden_singles():
        prog = False
        for u in units:
            for d in range(n):
                bit = 1 << d
                cells = [i for i in u if cand[i] & bit]
                if len(cells) == 1 and cand[cells[0]] != bit:
                    cand[cells[0]] = bit
                    prog = True
        return prog

    def locked():
        prog = False
        for d in range(n):
            bit = 1 << d
            for box in geo.boxes:  # pointing
                cells = [i for i in box if cand[i] & bit]
                if len(cells) < 2:
                    continue
                b = B[cells[0]]
                if len({R[i] for i in cells}) == 1:
                    for i in geo.rows[R[cells[0]]]:
                        if B[i] != b and cand[i] & bit:
                            cand[i] &= ~bit
                            prog = True
                if len({C[i] for i in cells}) == 1:
                    for i in geo.cols[C[cells[0]]]:
                        if B[i] != b and cand[i] & bit:
                            cand[i] &= ~bit
                            prog = True
            for line in geo.rows + geo.cols:  # claiming
                cells = [i for i in line if cand[i] & bit]
                if len(cells) < 2:
                    continue
                if len({B[i] for i in cells}) == 1:
                    lineset = set(line)
                    for i in geo.boxes[B[cells[0]]]:
                        if i not in lineset and cand[i] & bit:
                            cand[i] &= ~bit
                            prog = True
        return prog

    def naked(k):
        prog = False
        for u in units:
            cells = [i for i in u if 2 <= pc(cand[i]) <= k]
            for combo in combinations(cells, k):
                m = 0
                for i in combo:
                    m |= cand[i]
                if pc(m) == k:
                    for i in u:
                        if i not in combo and cand[i] & m:
                            cand[i] &= ~m
                            prog = True
        return prog

    def hidden(k):
        prog = False
        for u in units:
            pos = {}
            for d in range(n):
                bit = 1 << d
                pm = 0
                for idx, i in enumerate(u):
                    if cand[i] & bit:
                        pm |= 1 << idx
                if 2 <= pc(pm) <= k:
                    pos[d] = pm
            for combo in combinations(list(pos), k):
                pm = dm = 0
                for d in combo:
                    pm |= pos[d]
                    dm |= 1 << d
                if pc(pm) == k:
                    for idx, i in enumerate(u):
                        if pm >> idx & 1 and cand[i] & ~dm:
                            cand[i] &= dm
                            prog = True
        return prog

    def fish(k):
        """X-Wing (k=2) and Swordfish (k=3) on rows and columns."""
        prog = False
        for d in range(n):
            bit = 1 << d
            for lines, cross_of, cross_lines, line_of in (
                (geo.rows, C, geo.cols, R),
                (geo.cols, R, geo.rows, C),
            ):
                pos = {}
                for li, line in enumerate(lines):
                    cs = frozenset(cross_of[i] for i in line if cand[i] & bit)
                    if 2 <= len(cs) <= k:
                        pos[li] = cs
                for combo in combinations(list(pos), k):
                    union = set()
                    for li in combo:
                        union |= pos[li]
                    if len(union) == k:
                        for cx in union:
                            for i in cross_lines[cx]:
                                if line_of[i] not in combo and cand[i] & bit:
                                    cand[i] &= ~bit
                                    prog = True
        return prog

    def xy_wing():
        prog = False
        biv = [i for i in range(n2) if pc(cand[i]) == 2]
        biset = set(biv)
        for p in biv:
            xy = cand[p]
            pins = [q for q in peers[p] if q in biset and pc(cand[q] & xy) == 1]
            for ai, a in enumerate(pins):
                za = cand[a] & ~xy
                for b in pins[ai + 1:]:
                    zb = cand[b] & ~xy
                    if za != zb or pc(za) != 1 or (cand[a] & xy) == (cand[b] & xy):
                        continue
                    for i in geo.peerset[a] & geo.peerset[b]:
                        if i != p and cand[i] & za:
                            cand[i] &= ~za
                            prog = True
        return prog

    tiers = [
        (2, [locked, lambda: naked(2), lambda: hidden(2)]),
        (3, [lambda: naked(3), lambda: hidden(3), lambda: fish(2), xy_wing,
             lambda: naked(4), lambda: hidden(4), lambda: fish(3)]),
    ]
    level = 1
    try:
        settle()
        while True:
            if all(pc(x) == 1 for x in cand):
                return level, True
            if hidden_singles():
                settle()
                continue
            advanced = False
            for tier, techniques in tiers:
                for tech in techniques:
                    if tech():
                        level = max(level, tier)
                        settle()
                        advanced = True
                        break
                if advanced:
                    break
            if not advanced:
                return 4, False
    except _Contradiction:  # cannot happen for a valid puzzle
        return 4, False


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
@dataclass
class SudokuPuzzle:
    n: int
    br: int
    bc: int
    grid: list          # 0 = empty
    solution: list
    clues: int
    rating: int         # level actually needed (1..4)
    difficulty: str     # level that was requested
    exact: bool         # True when rating matches the request


def _dig(geo: Geometry, solution, rng, target: int, symmetric: bool):
    n2 = geo.n * geo.n
    g = list(solution)
    order = list(range(n2))
    rng.shuffle(order)
    clues = n2
    for i in order:
        if clues <= target:
            break
        if g[i] == 0:
            continue
        j = n2 - 1 - i if symmetric else i
        saved = [(k, g[k]) for k in {i, j} if g[k]]
        for k, _ in saved:
            g[k] = 0
        if count_solutions(geo, g, 2) == 1:
            clues -= len(saved)
        else:
            for k, v in saved:
                g[k] = v
    return g, clues


def _tune(geo: Geometry, grid, solution, want: int, rng, symmetric: bool):
    """Add clues back until the rating drops to exactly `want` (never below)."""
    n2 = geo.n * geo.n
    grid = list(grid)
    level = rate(geo, grid)[0]
    while level > want:
        empties = [i for i in range(n2) if grid[i] == 0]
        rng.shuffle(empties)
        moved = False
        for i in empties[:40]:
            cells = {i, n2 - 1 - i} if symmetric else {i}
            added = [k for k in cells if grid[k] == 0]
            for k in added:
                grid[k] = solution[k]
            new_level = rate(geo, grid)[0]
            if new_level >= want:
                level = new_level
                moved = True
                break
            for k in added:
                grid[k] = 0
        if not moved:
            return None
    return grid if level == want else None


def generate(n: int, difficulty: str, rng: random.Random, symmetric: bool = True,
             max_tries: int = 80) -> SudokuPuzzle:
    if difficulty not in LEVEL_OF:
        raise ValueError(f"unknown difficulty {difficulty!r}")
    geo = geometry(n)
    n2 = n * n
    want = LEVEL_OF[difficulty]
    lo_f, hi_f = CLUE_FRACTION[difficulty]
    lo = max(1, round(lo_f * n2))
    hi = max(lo, round(hi_f * n2))

    if n < 9:                      # tiny boards: graded by clue count only
        sol = random_solution(geo, rng)
        grid, clues = _dig(geo, sol, rng, rng.randint(lo, hi), symmetric)
        return SudokuPuzzle(n, geo.br, geo.bc, grid, sol, clues, want, difficulty, True)

    fallback = None
    for _ in range(max_tries):
        sol = random_solution(geo, rng)
        if want == 1:
            grid, clues = _dig(geo, sol, rng, rng.randint(lo, hi), symmetric)
            level = rate(geo, grid)[0]
        else:
            grid, clues = _dig(geo, sol, rng, 0, symmetric)      # dig to a minimal puzzle
            level = rate(geo, grid)[0]
            if level < want:
                if fallback is None or level > fallback.rating:
                    fallback = SudokuPuzzle(n, geo.br, geo.bc, grid, sol, clues, level, difficulty, False)
                continue
            if level > want:
                tuned = _tune(geo, grid, sol, want, rng, symmetric)
                if tuned is None:
                    continue
                grid, level = tuned, want
                clues = sum(1 for v in grid if v)
                if clues > hi + round(0.08 * n2):      # keep givens plausible for the label
                    continue
        if level == want:
            return SudokuPuzzle(n, geo.br, geo.bc, grid, sol, clues, level, difficulty, True)
        if fallback is None:
            fallback = SudokuPuzzle(n, geo.br, geo.bc, grid, sol, clues, level, difficulty, False)
    return fallback


def generate_task(args) -> SudokuPuzzle:
    """Picklable entry point for multiprocessing: (n, difficulty, seed, symmetric)."""
    n, difficulty, seed, symmetric = args
    return generate(n, difficulty, random.Random(seed), symmetric)
