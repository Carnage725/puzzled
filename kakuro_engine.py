"""Kakuro engine.

Generation pipeline
    1. random black/white layout built row by row so every run has length
       2..max_run, no run is a single cell and all white cells are connected;
    2. fill the white cells with digits 1-9, no repeats inside a run, biased
       toward sums with few partitions;
    3. read the clue sums off the filled grid;
    4. repair: while propagation at the target level leaves cells unsolved, move
       one of them to an extreme digit and re-read the sums. Propagation that
       solves every cell proves the solution is unique;
    5. harden: change digits while the puzzle stays uniquely solvable at the
       target level, until the level below no longer solves it. Levels:

        level 1 (easy)   sum-combination tables + elimination of solved digits
        level 2 (medium) + required digits, naked pairs inside a run
        level 3 (hard)   + exact per-run reasoning (every cell/digit pairing)
        level 4 (expert) trial and error needed

Sizes are the white area, width x height; the grid adds the clue row / column
(an 8x8 Kakuro is drawn as a 9x9 grid).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations

DIFFICULTIES = ("easy", "medium", "hard", "expert")
LEVEL_OF = {name: i + 1 for i, name in enumerate(DIFFICULTIES)}
FULL = 0x3FE  # digits 1..9 as bits 1..9

# (run length, sum) -> list of digit-set bitmasks
COMBOS: dict[tuple[int, int], list[int]] = {}
for _l in range(2, 10):
    for _c in combinations(range(1, 10), _l):
        _m = 0
        for _d in _c:
            _m |= 1 << _d
        COMBOS.setdefault((_l, sum(_c)), []).append(_m)
_SUM = [sum(d for d in range(1, 10) if m >> d & 1) for m in range(1024)]
_EXACT_CACHE: dict = {}   # (run domains, sum) -> Model._exact result

# layout parameters per difficulty
PARAMS = {
    "easy":   dict(p_black=0.34, max_run=5, p2x2=1.00),
    "medium": dict(p_black=0.30, max_run=6, p2x2=0.85),
    "hard":   dict(p_black=0.25, max_run=8, p2x2=0.60),
    "expert": dict(p_black=0.25, max_run=8, p2x2=0.60),   # longer runs defeat the level-2 repair
}
# probability a cell prefers digits that push its runs toward unique partitions
FILL_BIAS = {"easy": 0.9, "medium": 0.6, "hard": 0.4, "expert": 0.4}


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------
def find_runs(white):
    """[(kind 'A'|'D', [(r, c), ...]), ...] for every white run (length >= 1)."""
    rows, cols = len(white), len(white[0])
    out = []
    for r in range(rows):
        c = 0
        while c < cols:
            if white[r][c]:
                s = c
                while c < cols and white[r][c]:
                    c += 1
                out.append(("A", [(r, k) for k in range(s, c)]))
            else:
                c += 1
    for c in range(cols):
        r = 0
        while r < rows:
            if white[r][c]:
                s = r
                while r < rows and white[r][c]:
                    r += 1
                out.append(("D", [(k, c) for k in range(s, r)]))
            else:
                r += 1
    return out


def _largest_component(white):
    rows, cols = len(white), len(white[0])
    seen, best = set(), set()
    for r in range(rows):
        for c in range(cols):
            if white[r][c] and (r, c) not in seen:
                comp, stack = {(r, c)}, [(r, c)]
                seen.add((r, c))
                while stack:
                    y, x = stack.pop()
                    for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                        if 0 <= ny < rows and 0 <= nx < cols and white[ny][nx] and (ny, nx) not in seen:
                            seen.add((ny, nx))
                            comp.add((ny, nx))
                            stack.append((ny, nx))
                if len(comp) > len(best):
                    best = comp
    return best


def _valid_layout(white, max_run):
    runs = find_runs(white)
    if any(len(cells) < 2 or len(cells) > max_run for _k, cells in runs):
        return False
    return len(_largest_component(white)) == sum(map(sum, white))


def _gen_row(rng, cols, vlen, prev, max_run, last, p_black, p2x2):
    """Random valid row (index 0 stays black) given the vertical run lengths so far."""
    row = [False] * cols

    def forced(c):
        v = vlen[c]
        if v == 1:
            return True                  # a vertical run of length 1 must be extended
        if v >= max_run or (last and v == 0):
            return False
        return None

    def dfs(c, hrun):
        if c == cols:
            return hrun == 0 or hrun >= 2
        f = forced(c)
        if f is None:
            opts = [False, True] if rng.random() < p_black else [True, False]
            if hrun >= 1 and prev[c] and prev[c - 1] and row[c - 1] and rng.random() < p2x2:
                opts = [False, True]     # would complete a 2x2 white block
        else:
            opts = [f]
        for white in opts:
            if white:
                if hrun + 1 > max_run:
                    continue
                row[c] = True
                if dfs(c + 1, hrun + 1):
                    return True
                row[c] = False
            else:
                if hrun == 1:
                    continue
                if dfs(c + 1, 0):
                    return True
        return False

    return row if dfs(1, 0) else None


def make_layout(rows, cols, rng, p_black, max_run, p2x2, tries=40):
    """Constructive layout: every run has length 2..max_run, all whites connected."""
    for _ in range(tries):
        white = [[False] * cols for _ in range(rows)]
        vlen = [0] * cols
        ok = True
        for r in range(1, rows):
            row = _gen_row(rng, cols, vlen, white[r - 1], max_run, r == rows - 1, p_black, p2x2)
            if row is None:
                ok = False
                break
            white[r] = row
            for c in range(1, cols):
                vlen[c] = vlen[c] + 1 if row[c] else 0
        if not ok:
            continue
        total = sum(map(sum, white))
        if total >= 0.5 * (rows - 1) * (cols - 1) and _valid_layout(white, max_run):
            return white
    return None


def fill_digits(white, rng, bias=0.0, node_limit=40000):
    """Random digits 1-9, distinct inside every run. None if the search gives up.

    Every run leans low or high at random; with probability `bias` a cell tries
    digits in its runs' leaning first, pushing sums toward unique partitions
    (3 = 1+2, 17 = 8+9 ...) that make puzzles easier and uniqueness likelier.
    """
    rows, cols = len(white), len(white[0])
    runs = find_runs(white)
    rid = {}
    for k, (kind, cells) in enumerate(runs):
        for cell in cells:
            rid[(cell, kind)] = k
    cells = [(r, c) for r in range(rows) for c in range(cols) if white[r][c]]
    low = [rng.random() < 0.5 for _ in runs]
    used = [0] * len(runs)
    grid = [[0] * cols for _ in range(rows)]
    nodes = 0

    class _Give(Exception):
        pass

    def rec(k):
        nonlocal nodes
        if k == len(cells):
            return True
        nodes += 1
        if nodes > node_limit:
            raise _Give
        cell = cells[k]
        a, d = rid[(cell, "A")], rid[(cell, "D")]
        taken = used[a] | used[d]
        opts = [x for x in range(1, 10) if not taken >> x & 1]
        rng.shuffle(opts)
        if rng.random() < bias:
            key = (lambda x: -x, lambda x: abs(x - 5), lambda x: x)[low[a] + low[d]]
            opts.sort(key=key)
        for x in opts:
            used[a] |= 1 << x
            used[d] |= 1 << x
            grid[cell[0]][cell[1]] = x
            if rec(k + 1):
                return True
            used[a] ^= 1 << x
            used[d] ^= 1 << x
        grid[cell[0]][cell[1]] = 0
        return False

    try:
        return grid if rec(0) else None
    except _Give:
        return None


# --------------------------------------------------------------------------
# solver
# --------------------------------------------------------------------------
class Model:
    """White cells + runs with clue sums, plus propagation and search."""

    def __init__(self, white, sums):
        self.white = white
        self.runs = find_runs(white)
        self.idx = {}
        for r, row in enumerate(white):
            for c, w in enumerate(row):
                if w:
                    self.idx[(r, c)] = len(self.idx)
        self.m = len(self.idx)
        self.run_cells = [[self.idx[p] for p in cells] for _k, cells in self.runs]
        self.run_sum = list(sums)
        self.cell_runs = [[] for _ in range(self.m)]
        for ri, cells in enumerate(self.run_cells):
            for c in cells:
                self.cell_runs[c].append(ri)

    @classmethod
    def from_solution(cls, white, solution):
        runs = find_runs(white)
        return cls(white, [sum(solution[r][c] for r, c in cells) for _k, cells in runs])

    # -- constraint propagation -------------------------------------------------
    def _exact(self, dom, cells, total):
        """Digits each cell can still take in some complete valid filling of the run."""
        doms = tuple(dom[c] for c in cells)
        key = (doms, total)
        if key not in _EXACT_CACHE:
            if len(_EXACT_CACHE) > 500000:
                _EXACT_CACHE.clear()
            _EXACT_CACHE[key] = self._exact_uncached(doms, total)
        return _EXACT_CACHE[key]

    @staticmethod
    def _exact_uncached(doms, total):
        L = len(doms)
        fwd = [{0}]
        for j in range(L):
            nxt = set()
            for u in fwd[j]:
                m = doms[j] & ~u
                while m:
                    b = m & -m
                    m ^= b
                    nu = u | b
                    if _SUM[nu] <= total:
                        nxt.add(nu)
            fwd.append(nxt)
        bwd = [None] * (L + 1)
        bwd[L] = {0}
        for j in range(L - 1, -1, -1):
            s = set()
            for v in bwd[j + 1]:
                m = doms[j] & ~v
                while m:
                    b = m & -m
                    m ^= b
                    nv = v | b
                    if _SUM[nv] <= total:
                        s.add(nv)
            bwd[j] = s
        res = []
        for j in range(L):
            by_sum = {}
            for v in bwd[j + 1]:
                by_sum.setdefault(_SUM[v], []).append(v)
            poss = 0
            for u in fwd[j]:
                su = _SUM[u]
                m = doms[j] & ~u
                while m:
                    b = m & -m
                    m ^= b
                    need = total - su - (b.bit_length() - 1)
                    for v in by_sum.get(need, ()):
                        if not v & (u | b):
                            poss |= b
                            break
            if not poss:
                return None
            res.append(poss)
        return res

    def _run(self, dom, ri, level):
        cells = self.run_cells[ri]
        combos = COMBOS.get((len(cells), self.run_sum[ri]))
        if not combos:
            return None
        U = F = nf = 0
        for c in cells:
            d = dom[c]
            U |= d
            if d & (d - 1) == 0:
                F |= d
                nf += 1
        if F.bit_count() != nf:                      # same digit fixed twice
            return None
        allowed, req, ok = 0, FULL, False
        for m in combos:
            if m & ~U == 0 and m & F == F:
                allowed |= m
                req &= m
                ok = True
        if not ok:
            return None
        changed = []
        for c in cells:
            d = dom[c]
            nd = d & allowed
            if nd & (nd - 1):
                nd &= ~F
            if nd != d:
                if not nd:
                    return None
                dom[c] = nd
                changed.append(c)
        if level >= 2:
            r = req
            while r:
                bit = r & -r
                r ^= bit
                holders = [c for c in cells if dom[c] & bit]
                if not holders:
                    return None
                if len(holders) == 1 and dom[holders[0]] != bit:
                    dom[holders[0]] = bit
                    changed.append(holders[0])
            pairs = {}
            for c in cells:
                if dom[c].bit_count() == 2:
                    pairs.setdefault(dom[c], []).append(c)
            for d, cs in pairs.items():
                if len(cs) > 2:
                    return None
                if len(cs) == 2:
                    for c in cells:
                        if c not in cs and dom[c] & d:
                            nd = dom[c] & ~d
                            if not nd:
                                return None
                            dom[c] = nd
                            changed.append(c)
        if level >= 3:
            res = self._exact(dom, cells, self.run_sum[ri])
            if res is None:
                return None
            for c, nd in zip(cells, res):
                if nd != dom[c]:
                    dom[c] = nd
                    changed.append(c)
        return changed

    def propagate(self, dom, level, start=None):
        queue = list(start if start is not None else range(len(self.run_cells)))
        queued = [False] * len(self.run_cells)
        for ri in queue:
            queued[ri] = True
        while queue:
            ri = queue.pop()
            queued[ri] = False
            changed = self._run(dom, ri, level)
            if changed is None:
                return False
            for c in changed:
                for rj in self.cell_runs[c]:
                    if not queued[rj]:
                        queued[rj] = True
                        queue.append(rj)
        return True

    # -- search / rating -------------------------------------------------------
    def solutions(self, limit=2, level=2, node_limit=None):
        """Up to `limit` solutions as {(r, c): digit} dictionaries.
        None if the search needs more than `node_limit` nodes."""
        dom = [FULL] * self.m
        if not self.propagate(dom, level):
            return []
        found = []
        nodes = 0

        def rec(dom):
            nonlocal nodes
            nodes += 1
            if node_limit is not None and nodes > node_limit:
                return True
            best, bc = -1, 99
            for i, d in enumerate(dom):
                if d & (d - 1):
                    c = d.bit_count()
                    if c < bc:
                        best, bc = i, c
                        if c == 2:
                            break
            if best < 0:
                found.append(dom[:])
                return len(found) >= limit
            d = dom[best]
            while d:
                b = d & -d
                d ^= b
                nd = dom[:]
                nd[best] = b
                if self.propagate(nd, level, self.cell_runs[best]) and rec(nd):
                    return True
            return False

        rec(dom)
        if node_limit is not None and nodes > node_limit:
            return None
        inv = {i: p for p, i in self.idx.items()}
        return [{inv[i]: d.bit_length() - 1 for i, d in enumerate(sol)} for sol in found]

    def rate(self):
        for level in (1, 2, 3):
            dom = [FULL] * self.m
            if self.propagate(dom, level) and all(not d & (d - 1) for d in dom):
                return level
        return 4


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
@dataclass
class KakuroPuzzle:
    rows: int
    cols: int
    white: list      # [[bool]]
    solution: list   # [[int]] (0 on black cells)
    across: dict     # clue cell (r, c) -> sum of the run starting right of it
    down: dict       # clue cell (r, c) -> sum of the run starting below it
    rating: int
    difficulty: str
    exact: bool

    @property
    def white_count(self):
        return sum(map(sum, self.white))


def _stuck(model, level):
    """Indices of cells propagation at `level` leaves unsolved ([] = solved, so unique)."""
    dom = [FULL] * model.m
    # never fails: the clues come from a real filling. Cheaper levels first narrow
    # the domains; the fixpoint is the same, but level 3's exact pass gets cheaper
    for lv in range(min(level, 2), level + 1):
        model.propagate(dom, lv)
    return [i for i, d in enumerate(dom) if d & (d - 1)]


def _unique(model, node_limit=3000):
    sols = model.solutions(2, 3, node_limit)
    return sols is not None and len(sols) == 1


class _Digits:
    """The filled solution plus the single-digit edits generation makes to it."""

    def __init__(self, white, sol):
        self.white, self.sol = white, sol
        self.cells = [(r, c) for r, row in enumerate(white) for c, w in enumerate(row) if w]
        self.members = {}
        for _k, cells in find_runs(white):
            for cell in cells:
                self.members.setdefault(cell, []).append(cells)

    def options(self, i):
        """Other digits cell i can take without a repeat in its runs."""
        r, c = self.cells[i]
        taken = {self.sol[y][x] for run in self.members[(r, c)] for y, x in run}
        return [d for d in range(1, 10) if d not in taken]

    def model(self):
        return Model.from_solution(self.white, self.sol)


def _repair(digits, level, rng, steps=1000):
    """Edit digits until propagation at `level` solves the puzzle: a stuck cell
    takes its lowest or highest free digit, which narrows its runs' partitions."""
    for _ in range(steps):
        model = digits.model()
        stuck = _stuck(model, level)
        if not stuck:
            return model
        i = rng.choice(stuck)
        opts = digits.options(i)
        if opts:
            r, c = digits.cells[i]
            digits.sol[r][c] = rng.choice((opts[0], opts[-1]))
    return None


def _harden(digits, model, want, rng, steps=300):
    """Edit digits while the puzzle stays uniquely solvable at level `want`
    (by search for expert) until level want-1 no longer solves it."""
    below_solves = not _stuck(model, want - 1)
    for _ in range(steps):
        if not below_solves:
            return model
        i = rng.randrange(len(digits.cells))
        opts = digits.options(i)
        if not opts:
            continue
        r, c = digits.cells[i]
        old = digits.sol[r][c]
        digits.sol[r][c] = rng.choice(opts)
        cand = digits.model()
        if want == 4:
            # solved by level 3 means unique already; search only when it gets stuck
            cand_below_solves = not _stuck(cand, 3)
            ok = cand_below_solves or _unique(cand)
        else:
            ok = not _stuck(cand, want)
            cand_below_solves = ok and not _stuck(cand, want - 1)
        if ok:
            model, below_solves = cand, cand_below_solves
        else:
            digits.sol[r][c] = old
    return None


def _build(white, sol, model, difficulty, rating, exact):
    rows, cols = len(white), len(white[0])
    across, down = {}, {}
    for (kind, cells), s in zip(model.runs, model.run_sum):
        r, c = cells[0]
        if kind == "A":
            across[(r, c - 1)] = s
        else:
            down[(r - 1, c)] = s
    return KakuroPuzzle(rows, cols, [row[:] for row in white], [row[:] for row in sol],
                        across, down, rating, difficulty, exact)


def generate(width: int, height: int, difficulty: str, rng: random.Random,
             max_attempts: int = 1000):
    """A Kakuro whose white area is width x height that rates exactly `difficulty`."""
    if difficulty not in LEVEL_OF:
        raise ValueError(f"unknown difficulty {difficulty!r}")
    if width < 4 or height < 4:
        raise ValueError("Kakuro white area must be at least 4x4")
    rows, cols = height + 1, width + 1
    want = LEVEL_OF[difficulty]
    P = PARAMS[difficulty]
    for _ in range(max_attempts):
        white = make_layout(rows, cols, rng, P["p_black"], P["max_run"], P["p2x2"])
        if white is None:
            continue
        sol = fill_digits(white, rng, FILL_BIAS[difficulty])
        if sol is None:
            continue
        digits = _Digits(white, sol)
        model = _repair(digits, min(want, 2), rng)   # level 3 repair is slow; harden lifts it
        if model is None:
            continue
        if want > 1:
            model = _harden(digits, model, want, rng)
            if model is None:
                continue
        return _build(white, sol, model, difficulty, want, True)
    raise RuntimeError(f"could not generate a {width}x{height} {difficulty} Kakuro")


def generate_task(args) -> KakuroPuzzle:
    """Picklable entry point for multiprocessing: (width, height, difficulty, seed)."""
    width, height, difficulty, seed = args
    return generate(width, height, difficulty, random.Random(seed))
