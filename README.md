# puzzled

Generate printable Sudoku and Kakuro puzzle books as A4 PDFs, with the solutions at the end.
Every puzzle has exactly one solution and is graded by the solving techniques it needs.
Puzzles are stored in PostgreSQL, so no puzzle is ever printed twice, and a bank of
ready-made puzzles can be filled in advance.

## Features

- **Sudoku** 9×9 and 12×12, **Kakuro** 4x4, 6x6, 8x8, 9x11 and 10x10
- Four difficulties: easy, medium, hard, expert, rated by the techniques a solver needs
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
| `--difficulty` | `easy`, `medium`, `hard`, `expert`                         | `medium`                         |
| `--count`      | 1 to 500                                                   | 12                               |
| `--per-page`   | `1`, `2`, `4`, `6` (6 not for Kakuro 8x8 and larger)       | 4                                |
| `--out`        | file name                                                  | `<game>_<size>_<difficulty>.pdf` |
| `--seed`       | any integer, for repeatable runs                           | random                           |
| `--no-db`      | skip the database                                          | off                              |

PDFs are saved to `pdfs/<game>/<size>/`, for example `pdfs/kakuro/8x8/kakuro_8x8_hard.pdf`.

Kakuro sizes are the white area, width × height. The clue row and column are extra,
so a 4x4 Kakuro is drawn as a 5×5 grid.

### Fill the bank

Banked puzzles are generated ahead of time; later PDFs of the same size and difficulty
use them first and are ready instantly.

```sh
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
   level, until the level below can no longer solve it. The rating is then exact.

| Difficulty | Reasoning needed |
|------------|------------------|
| easy       | sum combinations, eliminating solved digits |
| medium     | + digits a run must contain, pairs inside a run |
| hard       | + exact per-run reasoning over every cell and digit |
| expert     | trial and error (uniqueness proven by search) |

Average time per puzzle on one core (Apple Silicon, measured during development):

| Size  | easy   | medium | hard   | expert |
|-------|--------|--------|--------|--------|
| 4x4   | 0.01 s | 0.01 s | 0.01 s | 0.15 s |
| 6x6   | 0.02 s | 0.05 s | 0.12 s | 0.34 s |
| 8x8   | 0.05 s | 0.25 s | 1.0 s  | 1.1 s  |
| 9x11  | 0.45 s | 1.9 s  | 8.4 s  | 8.1 s  |
| 10x10 | 0.57 s | 1.5 s  | 7.5 s  | 7.6 s  |

### Database (`db.py`)

| Table    | Holds |
|----------|-------|
| `sudoku` | size, difficulty, givens, solution, seed, generation time, PDF and number |
| `kakuro` | size, difficulty, layout, clues (JSON), solution, white cells, runs, seed, generation time, PDF and number |
| `pdf`    | every PDF made: file, size, difficulty, puzzles, pages, date |

A puzzle with no PDF is in the bank. Unique constraints on the puzzle content stop any
puzzle from being stored, and so printed, twice. Seeds reproduce a puzzle only with the
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
