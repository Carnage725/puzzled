"""PostgreSQL store for puzzled.py: every puzzle made, the PDFs they went into,
and a bank of puzzles not yet printed (pdf_id IS NULL).

Connection from $PUZZLED_DB (default "dbname=puzzled"); the database and its
tables are created on first use.
"""
from __future__ import annotations

import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

import kakuro_engine as K
import sudoku_engine as S

DSN = os.environ.get("PUZZLED_DB", "dbname=puzzled")
DIFFICULTY_ORDER = "array_position(ARRAY['easy','medium','hard','expert'], difficulty)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pdf (
    id          serial PRIMARY KEY,
    game        text NOT NULL CHECK (game IN ('sudoku', 'kakuro')),
    path        text NOT NULL,
    size        text NOT NULL,
    difficulty  text NOT NULL,
    per_page    int  NOT NULL,
    puzzles     int  NOT NULL,
    pages       int  NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sudoku (
    id          serial PRIMARY KEY,
    size        text NOT NULL,                 -- '9x9'
    difficulty  text NOT NULL,
    grid        text NOT NULL UNIQUE,          -- givens row by row, '.' = empty
    solution    text NOT NULL,
    givens      int  NOT NULL,
    seed        numeric(20) NOT NULL,
    gen_ms      int  NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    pdf_id      int REFERENCES pdf,            -- first PDF it was printed in; NULL = in the bank
    pdf_number  int
);
CREATE TABLE IF NOT EXISTS kakuro (
    id          serial PRIMARY KEY,
    size        text NOT NULL,                 -- white area, '8x8'
    difficulty  text NOT NULL,
    layout      text NOT NULL,                 -- rows joined by '/', '#' = clue / black, '.' = white
    clues       jsonb NOT NULL,                -- {"across": [[r, c, sum], ...], "down": [...]}
    solution    text NOT NULL,                 -- rows joined by '/', '#' on black cells
    white_cells int  NOT NULL,
    runs        int  NOT NULL,
    seed        numeric(20) NOT NULL,
    gen_ms      int  NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    pdf_id      int REFERENCES pdf,
    pdf_number  int,
    UNIQUE (layout, clues)
);
-- every PDF's puzzles in order; a puzzle reused with --from-db appears in several PDFs
CREATE TABLE IF NOT EXISTS pdf_puzzle (
    pdf_id      int NOT NULL REFERENCES pdf,
    number      int NOT NULL,
    puzzle_id   int NOT NULL,                  -- id in the sudoku or kakuro table (pdf.game)
    PRIMARY KEY (pdf_id, number)
);
INSERT INTO pdf_puzzle SELECT pdf_id, pdf_number, id FROM sudoku WHERE pdf_id IS NOT NULL
    ON CONFLICT DO NOTHING;
INSERT INTO pdf_puzzle SELECT pdf_id, pdf_number, id FROM kakuro WHERE pdf_id IS NOT NULL
    ON CONFLICT DO NOTHING;
CREATE INDEX IF NOT EXISTS sudoku_bank ON sudoku (size, difficulty) WHERE pdf_id IS NULL;
CREATE INDEX IF NOT EXISTS kakuro_bank ON kakuro (size, difficulty) WHERE pdf_id IS NULL;
CREATE OR REPLACE VIEW sudoku_stats AS
    SELECT size, difficulty, count(*) AS total, count(pdf_id) AS printed,
           count(*) - count(pdf_id) AS in_bank
    FROM sudoku GROUP BY size, difficulty;
CREATE OR REPLACE VIEW kakuro_stats AS
    SELECT size, difficulty, count(*) AS total, count(pdf_id) AS printed,
           count(*) - count(pdf_id) AS in_bank
    FROM kakuro GROUP BY size, difficulty;
"""


def connect():
    """Connection with the schema in place, creating the database if missing.
    Raises psycopg.OperationalError when PostgreSQL can't be reached."""
    try:
        conn = psycopg.connect(DSN, connect_timeout=3)
    except psycopg.OperationalError as e:
        if "does not exist" not in str(e):
            raise
        name = conninfo_to_dict(DSN)["dbname"]
        with psycopg.connect(make_conninfo(DSN, dbname="postgres"), autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        conn = psycopg.connect(DSN, connect_timeout=3)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


# --------------------------------------------------------------------------
# puzzle <-> row
# --------------------------------------------------------------------------
def _sudoku_values(p):
    cell = lambda v: S.SYMBOLS[v - 1] if v else "."
    return dict(size=f"{p.n}x{p.n}", difficulty=p.difficulty,
                grid="".join(map(cell, p.grid)), solution="".join(map(cell, p.solution)),
                givens=p.clues)


def _sudoku_puzzle(row):
    size, difficulty, grid, solution = row
    n = int(size.split("x")[0])
    br, bc = S.BOX_SHAPES[n]
    value = lambda ch: 0 if ch == "." else S.SYMBOLS.index(ch) + 1
    g = [value(ch) for ch in grid]
    return S.SudokuPuzzle(n, br, bc, g, [value(ch) for ch in solution], sum(map(bool, g)),
                          S.LEVEL_OF[difficulty], difficulty, True)


def _kakuro_values(p):
    return dict(size=f"{p.cols - 1}x{p.rows - 1}", difficulty=p.difficulty,
                layout="/".join("".join("." if w else "#" for w in row) for row in p.white),
                clues=Jsonb({"across": sorted([r, c, s] for (r, c), s in p.across.items()),
                             "down": sorted([r, c, s] for (r, c), s in p.down.items())}),
                solution="/".join("".join(str(v) if v else "#" for v in row) for row in p.solution),
                white_cells=p.white_count, runs=len(p.across) + len(p.down))


def _kakuro_puzzle(row):
    size, difficulty, layout, clues, solution = row
    white = [[ch == "." for ch in line] for line in layout.split("/")]
    sol = [[0 if ch == "#" else int(ch) for ch in line] for line in solution.split("/")]
    return K.KakuroPuzzle(len(white), len(white[0]), white, sol,
                          {(r, c): s for r, c, s in clues["across"]},
                          {(r, c): s for r, c, s in clues["down"]},
                          K.LEVEL_OF[difficulty], difficulty, True)


GAMES = {
    "sudoku": (_sudoku_values, _sudoku_puzzle, "size, difficulty, grid, solution"),
    "kakuro": (_kakuro_values, _kakuro_puzzle, "size, difficulty, layout, clues, solution"),
}


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------
def take_bank(conn, game, size, difficulty, n):
    """Up to n unprinted puzzles as (id, puzzle), locked until the transaction ends."""
    _values, to_puzzle, cols = GAMES[game]
    rows = conn.execute(sql.SQL("SELECT id, {} FROM {} WHERE pdf_id IS NULL AND size = %s"
                                " AND difficulty = %s ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED")
                        .format(sql.SQL(cols), sql.Identifier(game)), (size, difficulty, n)).fetchall()
    return [(r[0], to_puzzle(r[1:])) for r in rows]


def insert(conn, game, puzzle, seed, gen_ms):
    """Store a new puzzle; its id, or None when the same puzzle is already stored."""
    values = GAMES[game][0](puzzle) | dict(seed=seed, gen_ms=gen_ms)
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING RETURNING id").format(
        sql.Identifier(game), sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Placeholder() * len(values)))
    row = conn.execute(query, list(values.values())).fetchone()
    return row[0] if row else None


def record_pdf(conn, game, path, size, difficulty, per_page, pages, ids):
    """Add the PDF and link its puzzles to it, numbered in order; puzzles printed for
    the first time leave the bank. Returns the PDF id."""
    pdf_id = conn.execute("INSERT INTO pdf (game, path, size, difficulty, per_page, puzzles, pages)"
                          " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          (game, path, size, difficulty, per_page, len(ids), pages)).fetchone()[0]
    numbered = [(pdf_id, k + 1, pid) for k, pid in enumerate(ids)]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO pdf_puzzle (pdf_id, number, puzzle_id) VALUES (%s, %s, %s)", numbered)
        cur.executemany(sql.SQL("UPDATE {} SET pdf_id = %s, pdf_number = %s WHERE id = %s AND pdf_id IS NULL")
                        .format(sql.Identifier(game)), numbered)
    return pdf_id


def pick_stored(conn, game, size, difficulty, n):
    """n random stored puzzles (printed or banked) as (id, puzzle); fewer if fewer exist."""
    _values, to_puzzle, cols = GAMES[game]
    rows = conn.execute(sql.SQL("SELECT id, {} FROM {} WHERE size = %s AND difficulty = %s"
                                " ORDER BY random() LIMIT %s")
                        .format(sql.SQL(cols), sql.Identifier(game)), (size, difficulty, n)).fetchall()
    return [(r[0], to_puzzle(r[1:])) for r in rows]


def stats(conn):
    """(game, size, difficulty, total, printed, in_bank) rows."""
    return conn.execute(
        "SELECT * FROM (SELECT 'sudoku' AS game, * FROM sudoku_stats"
        " UNION ALL SELECT 'kakuro', * FROM kakuro_stats) s"
        f" ORDER BY game DESC, length(size), size, {DIFFICULTY_ORDER}").fetchall()


def list_puzzles(conn, game, size=None, difficulty=None, bank_only=False, limit=20):
    """Newest first: (id, size, difficulty, givens or white cells, pdf path, number, created_at)."""
    detail = "givens" if game == "sudoku" else "white_cells"
    where = [sql.SQL("true")]
    params = []
    if size:
        where.append(sql.SQL("t.size = %s"))
        params.append(size)
    if difficulty:
        where.append(sql.SQL("t.difficulty = %s"))
        params.append(difficulty)
    if bank_only:
        where.append(sql.SQL("t.pdf_id IS NULL"))
    query = sql.SQL("SELECT t.id, t.size, t.difficulty, t.{}, p.path, t.pdf_number, t.created_at"
                    " FROM {} t LEFT JOIN pdf p ON p.id = t.pdf_id WHERE {}"
                    " ORDER BY t.id DESC LIMIT %s").format(
        sql.Identifier(detail), sql.Identifier(game), sql.SQL(" AND ").join(where))
    return conn.execute(query, params + [limit]).fetchall()


def list_pdfs(conn, limit=50):
    return conn.execute("SELECT id, game, size, difficulty, puzzles, pages, path, created_at"
                        " FROM pdf ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


def pdf_puzzles(conn, pdf_id):
    """(pdf row, puzzles in their printed order), or None for an unknown id."""
    pdf = conn.execute("SELECT id, game, path, size, difficulty, per_page FROM pdf WHERE id = %s",
                       (pdf_id,)).fetchone()
    if pdf is None:
        return None
    _values, to_puzzle, cols = GAMES[pdf[1]]
    rows = conn.execute(sql.SQL("SELECT {} FROM pdf_puzzle l JOIN {} t ON t.id = l.puzzle_id"
                                " WHERE l.pdf_id = %s ORDER BY l.number")
                        .format(sql.SQL(", ".join(f"t.{c.strip()}" for c in cols.split(","))),
                                sql.Identifier(pdf[1])), (pdf_id,)).fetchall()
    return pdf, [to_puzzle(r) for r in rows]


def bank_count(conn, game, size, difficulty):
    return conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE pdf_id IS NULL AND size = %s"
                                " AND difficulty = %s").format(sql.Identifier(game)),
                        (size, difficulty)).fetchone()[0]
