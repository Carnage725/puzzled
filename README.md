# puzzled

Generate printable Sudoku and Kakuro puzzle books as A4 PDFs, with the solutions at the end.
Every puzzle has exactly one solution and is graded by the solving techniques it needs.
Puzzles are stored in PostgreSQL, so no puzzle is ever printed twice, and a bank of
ready-made puzzles can be filled in advance.

## Features

- **Sudoku** 9×9 and 12×12, **Kakuro** 4x4, 6x6, 8x8, 9x11 and 10x10
- Four difficulties: easy, medium, hard, expert, rated by the techniques a solver needs,
  plus `mixed` (10/20/30/40%) and `balanced` (25% each) books, easiest first
- A4 PDFs with 1, 2, 4 or 6 puzzles per page, numbered labels and page numbers
- Solutions at the end, packed as densely as stays readable (up to 20 per page)
- Arrow-key menus, or plain command-line options for scripting
- Generation on all CPU cores with a live progress bar
- PostgreSQL archive: no repeats across PDFs, a puzzle bank, reprints, browsing
- Output is never overwritten: a second run writes `name_2.pdf`

## Setup

Requires Python 3.10+ and, for the database features, a running PostgreSQL server.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The database `puzzled` and its tables are created automatically on first use.
To point at another server, set `PUZZLED_DB`, a libpq connection string:

```sh
export PUZZLED_DB="host=localhost port=5432 dbname=puzzled user=me"
```

Without PostgreSQL everything still works: PDFs are written, a warning says the puzzles
were not saved, and the repeat check covers only the current PDF.

## All commands

Run these from the project folder with the virtual environment active
(`source .venv/bin/activate`). Every command prints its options with `--help`.

| Command | What it does |
|---------|--------------|
| `python puzzled.py sudoku` | Make a Sudoku PDF. No options: arrow-key menus, starting with new, reused or nuclear |
| `python puzzled.py kakuro` | Make a Kakuro PDF. No options: arrow-key menus, starting with new, reused or nuclear |
| `python puzzled.py bank sudoku` | Add Sudoku puzzles to the bank, no PDF. No options: menus |
| `python puzzled.py bank kakuro` | Add Kakuro puzzles to the bank, no PDF. No options: menus |
| `python puzzled.py db stats` | Count puzzles per game, size and difficulty: total, printed, in bank |
| `python puzzled.py db list GAME` | Newest puzzles of `sudoku` or `kakuro`, with the PDF each is in |
| `python puzzled.py db pdfs` | Every PDF made, newest first, with its id |
| `python puzzled.py db reprint ID` | Rebuild PDF number `ID` from its stored puzzles |
| `python puzzled.py --help` | List the commands; `<command> --help` lists its options |
| `python -m pytest -q test_engines.py` | Run the tests |

**Options**

| Command | Options |
|---------|---------|
| `sudoku`, `kakuro` | `--size` · `--difficulty` (`easy` `medium` `hard` `expert` `mixed` `balanced`) · `--count` · `--per-page` · `--out` · `--seed` · `--from-db` · `--nuclear` · `--no-db` |
| `bank sudoku`, `bank kakuro` | `--size` · `--difficulty` · `--count N` or `--forever` · `--cores` · `--seed` |
| `db list` | `GAME` · `--size` · `--difficulty` · `--bank` · `--limit` |
| `db pdfs` | `--limit` |
| `db reprint` | `ID` |

**Examples**

```sh
python puzzled.py sudoku --size 12 --difficulty hard --count 24 --per-page 4
python puzzled.py kakuro --size 10x10 --difficulty mixed --count 20 --per-page 2
python puzzled.py kakuro --size 8x8 --difficulty balanced --count 12 --from-db
python puzzled.py sudoku --nuclear --size 9 --per-page 6
python puzzled.py bank kakuro --size 9x11 --difficulty expert --forever --cores 6
python puzzled.py db list kakuro --size 8x8 --difficulty hard --bank --limit 50
python puzzled.py db reprint 7
```

The sections below explain each command in detail.

## Usage

All commands assume the virtual environment is active.

### Make a PDF

```sh
python puzzled.py sudoku                 # arrow-key menus for every setting
python puzzled.py kakuro

python puzzled.py sudoku --size 9 --difficulty hard --count 12 --per-page 6
python puzzled.py kakuro --size 8x8 --difficulty expert --count 8 --per-page 2 --out mybook.pdf
```

| Option         | Values                                                     | Default                          |
|----------------|------------------------------------------------------------|----------------------------------|
| `--size`       | Sudoku `9`, `12` · Kakuro `4x4`, `6x6`, `8x8`, `9x11`, `10x10` | `9` / `8x8`                   |
| `--difficulty` | `easy`, `medium`, `hard`, `expert`, `mixed`, `balanced`    | `medium`                         |
| `--count`      | 1 to 1000                                                  | 12                               |
| `--per-page`   | `1`, `2`, `4`, `6` (6 not for Kakuro 8x8 and larger)       | 4                                |
| `--out`        | file name                                                  | `<game>_<size>_<difficulty>.pdf` |
| `--seed`       | any integer, for repeatable runs                           | random                           |
| `--from-db`    | build only from stored puzzles, generate nothing           | off                              |
| `--nuclear`    | every stored puzzle of that size, all difficulties         | off                              |
| `--no-db`      | skip the database                                          | off                              |

`--from-db` reuses puzzles from earlier runs: it picks stored puzzles of that size and
difficulty at random, printed before or still in the bank, and generates nothing. It stops
with a message if not enough are stored.

`--nuclear` prints every stored puzzle of one size, printed before or banked, all
difficulties, easiest first, as `<game>_<size>_all.pdf`. It generates nothing and ignores
`--difficulty` and `--count`. Banked puzzles it prints count as printed afterwards.

`mixed` makes a puzzle book that gets harder as you go: 10% easy, 20% medium, 30% hard
and 40% expert, in that order. The count must be a multiple of 10, so `--count 20` gives
2 easy, 4 medium, 6 hard and 8 expert.

`balanced` gives 25% of each difficulty, easiest first. The count must be a multiple
of 4, so `--count 20` gives 5 of each.

PDFs are saved to `pdfs/<game>/<size>/`, for example `pdfs/kakuro/8x8/kakuro_8x8_hard.pdf`.

Kakuro sizes are the white area, width × height. The clue row and column are extra,
so a 4x4 Kakuro is drawn as a 5×5 grid.

### Fill the bank

Banked puzzles are generated ahead of time; later PDFs of the same size and difficulty
use them first and are ready instantly.

```sh
python puzzled.py bank kakuro                # arrow-key menus: size, difficulty, how many
python puzzled.py bank kakuro --size 9x11 --difficulty hard --count 100
python puzzled.py bank sudoku --size 12 --difficulty expert --forever
```

`--forever` keeps generating until you type `q` and press Enter, or press Ctrl-C.
Each puzzle is saved as soon as it is made. It uses half the CPU cores by default;
change that with `--cores`.

### Browse the database

```sh
python puzzled.py db stats                                  # totals, printed, in bank
python puzzled.py db list kakuro --size 8x8 --difficulty hard --bank --limit 50
python puzzled.py db pdfs                                   # every PDF made, with its id
python puzzled.py db reprint 3                              # rebuild PDF #3 from stored puzzles
```

Any PostgreSQL client (TablePlus, pgAdmin, DBeaver) can also open the database
`puzzled` on `localhost:5432`. The views `sudoku_stats` and `kakuro_stats` give the same
overview as `db stats`.

## How difficulty is decided, in plain English

Difficulty is not about how many squares are blank. It is about the **hardest trick you
are forced to use** to finish the puzzle.

The program contains a robot solver that thinks like a person. It has a toolbox of
solving tricks sorted from simple to clever, and it grades a puzzle like this:

1. Give the robot only the **easy** tricks. If it can finish, the puzzle is **easy**.
2. Stuck? Also give it the **medium** tricks. If it can finish now, the puzzle is **medium**.
3. Still stuck? Add the **hard** tricks.
4. Still stuck? Add the **expert** tricks. In Sudoku that is anything beyond hard; in
   Kakuro it is the one-cell "what if" check described below.

So "hard" means: you cannot finish without hard tricks, but you never need to guess.
Kakuro puzzles never need guessing at any level, expert included. Every puzzle is
checked this way before it is printed.

For Kakuro, one hard trick is not enough to count as hard. The robot also counts how many
times it gets stuck and needs a trick of the puzzle's level. Medium, hard and expert
puzzles must need it a minimum number of times: about 5%, 9% and 3% of the white cells,
and at least 2 for medium and hard (1 for expert). A 10x10 hard Kakuro therefore needs at
least 7 hard steps, so every hard puzzle feels properly hard instead of being easy with
one tricky moment.

### Sudoku tricks

- **Easy.** *Only one number fits this square:* its row has 1, 2, 3, 4, its column has
  5, 6, 7 and its box has 9, so it must be 8. *Only one square fits this number:* in a
  row, the 5 cannot go in eight squares, so it goes in the ninth.
- **Medium.** *Locked in a line:* inside one box the 7 can only sit in the top row, so no
  other square in that row, outside the box, can be 7. *Pairs:* two squares in a row can
  each only be 3 or 8; together they use up the 3 and the 8, so no other square in that
  row can be 3 or 8.
- **Hard.** The same thinking across bigger patterns, such as three squares sharing three
  numbers, or a number trapped in a rectangle across two rows (an "X-Wing").
- **Expert.** None of the tricks work; you must try "what if this is a 4?" and see
  whether it breaks.

**Walkthrough: making a medium Sudoku.**
1. Fill a complete, correct 9×9 grid.
2. Erase numbers one at a time. After each erase, check the puzzle still has exactly one
   answer; if not, put that number back.
3. Grade it. With easy tricks the robot gets stuck, so it is not easy. With medium tricks
   it still gets stuck, so it is harder than medium.
4. Too hard, so put one erased number back as a free clue and grade again. Repeat until
   the robot can finish with medium tricks but not with easy ones alone.
5. That puzzle is exactly medium.

### Kakuro tricks

Clues are sums: these cells add up to this number, using 1 to 9 with no repeats.

- **Easy.** *Sums that can only be made one way:* 2 cells adding to 3 must be 1 and 2;
  2 cells adding to 17 must be 8 and 9. *Crossing clues:* if a cell's across clue says
  "1 or 2" and its down clue says "1 or 3", the only number in both is 1.
- **Medium.** *Must contain:* 3 cells adding to 23 can only be 6 + 8 + 9, so the run must
  hold a 9; if only one of its cells can take a 9, that cell is the 9. *Pairs:* two cells
  that can only be 1 or 3 use up the 1 and the 3 for the rest of the run. *Band sums:*
  take a band of whole rows. Its across clues add up to everything inside the band; the
  down clues crossing it add up to the same cells plus the cells poking out above and
  below. The difference is the total of the cells poking out, so if only one of them is
  still unknown, it is fixed.
- **Hard.** For one clue, picture every way to fill it that fits what you already know
  about each cell. A digit that never appears in any working way for a cell is crossed
  out. Slow and careful, but still pure logic.
- **Expert.** *What if:* pick a cell that can only be, say, 3 or 7. Pencil in 3 and apply
  the hard tricks only. If that breaks a clue (a run that can no longer reach its sum),
  3 was impossible, so the cell is 7. Nothing is kept from the trial; it only rules a
  number out. It is still logic, never a guess.

**Walkthrough: making a hard Kakuro.**
1. Draw a pattern of white and grey cells.
2. Fill the white cells with digits, leaning toward sums that can only be made one way,
   and write the totals as clues.
3. *Fix it.* The robot tries to solve it from the clues alone. Where it gets stuck, change
   one stuck cell to its smallest or largest allowed digit (which makes the sums more
   telling) and rewrite the clues. Repeat until the robot solves it completely, which
   proves there is only one answer.
4. *Make it harder.* Tweak digits one at a time, keeping a tweak only if the robot can
   still solve the puzzle with hard tricks. Stop once medium tricks alone are no longer
   enough and the robot needs the minimum number of hard steps for that grid size.
5. That puzzle needs plenty of hard tricks, never a guess, and has exactly one answer.

## How it works

### Sudoku (`sudoku_engine.py`)

1. Build a random complete grid by backtracking.
2. Remove clues, in rotationally symmetric pairs, while a solution counter proves that
   exactly one solution remains.
3. Rate the puzzle with a human-style solver and add clues back until the rating matches
   the request exactly.

| Difficulty | Techniques needed |
|------------|-------------------|
| easy       | naked and hidden singles |
| medium     | + locked candidates, pairs |
| hard       | + triples, quads, X-Wing, Swordfish, XY-Wing |
| expert     | more than that |

### Kakuro (`kakuro_engine.py`)

1. Build a random layout: every run 2 or more cells long, all white cells connected.
2. Fill it with digits, biased toward sums with few possible combinations
   (3 = 1+2, 17 = 8+9 ...).
3. **Repair:** while constraint propagation leaves cells unsolved, move one of them to its
   lowest or highest free digit and read the clue sums again. When propagation solves every
   cell, the solution is proven unique.
4. **Harden:** change digits while the puzzle stays uniquely solvable at the requested
   level, until the level below can no longer solve it and the solver needs a minimum
   number of deductions at that level (`MIN_STEPS`, scaled by the number of white cells).
   The rating is then exact and the effort consistent.

| Difficulty | Reasoning needed |
|------------|------------------|
| easy       | sum combinations, eliminating solved digits |
| medium     | + digits a run must contain, pairs inside a run, band sums |
| hard       | + exact per-run reasoning over every cell and digit |
| expert     | + one-cell "what if": a candidate is ruled out when it breaks the puzzle under the hard rules |

Every level is pure logic, and solving every cell by logic proves the solution is unique.
A puzzle that would need real guessing is never generated.

Average time per puzzle on one core (Apple Silicon, measured during development):

| Size  | easy   | medium | hard   | expert |
|-------|--------|--------|--------|--------|
| 4x4   | 0.05 s | 0.09 s | 0.03 s | 1.6 s  |
| 6x6   | 0.06 s | 0.10 s | 0.13 s | 1.2 s  |
| 8x8   | 0.10 s | 0.37 s | 0.67 s | 1.9 s  |
| 9x11  | 0.46 s | 0.81 s | 1.3 s  | 3.6 s  |
| 10x10 | 0.24 s | 1.1 s  | 2.9 s  | 4.7 s  |

### Database (`db.py`)

| Table    | Holds |
|----------|-------|
| `sudoku` | size, difficulty, givens, solution, seed, generation time, PDF and number |
| `kakuro` | size, difficulty, layout, clues (JSON), solution, white cells, runs, seed, generation time, PDF and number |
| `pdf`    | every PDF made: file, size, difficulty, puzzles, pages, date |
| `pdf_puzzle` | which puzzles are in each PDF, in order (a reused puzzle appears in several) |

A puzzle never printed is in the bank. Unique constraints on the puzzle content stop any
puzzle from being stored twice, so normal runs never print a repeat; only `--from-db`
reuses puzzles on purpose. Seeds reproduce a puzzle only with the
same engine code; stored puzzles never depend on them.

## Project layout

```
puzzled.py         command-line app (Typer, Rich, Questionary)
sudoku_engine.py   Sudoku generator, solver and rater
kakuro_engine.py   Kakuro generator, solver and rater
pdf_render.py      A4 PDF layout and grid drawing (reportlab)
db.py              PostgreSQL schema and queries (psycopg)
test_engines.py    tests
pdfs/              generated PDFs, by game and size
```

## Tests

```sh
python -m pytest -q test_engines.py
```

The tests check uniqueness and exact ratings for both games, PDF page counts, and a
database round trip on a temporary `puzzled_test` database. The database test is skipped
when PostgreSQL is not running.
