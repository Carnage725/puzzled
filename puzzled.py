"""Sudoku and Kakuro puzzles to an A4 PDF, solutions at the end, stored in PostgreSQL.

    .venv/bin/python puzzled.py sudoku      # arrow-key menus for every setting
    .venv/bin/python puzzled.py kakuro --size 8x8 --difficulty hard --count 12 --per-page 4
    .venv/bin/python puzzled.py bank kakuro --size 8x8 --difficulty hard --count 100
    .venv/bin/python puzzled.py db stats
"""
from __future__ import annotations

import os
import queue
import random
import signal
import sys
import threading
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Annotated, Literal, Optional

import psycopg
import questionary
import typer
from rich.console import Console
from rich.progress import (BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn,
                           TimeElapsedColumn, TimeRemainingColumn)
from rich.table import Table

import db
import kakuro_engine as K
import sudoku_engine as S
from pdf_render import PER_PAGE, draw_kakuro, draw_sudoku, write_pdf

app = typer.Typer(help="Sudoku and Kakuro puzzles to an A4 PDF, solutions at the end.",
                  no_args_is_help=True, add_completion=False)
bank_app = typer.Typer(help="Generate puzzles into the database bank (no PDF); later PDFs use them first.",
                       no_args_is_help=True)
db_app = typer.Typer(help="Browse the puzzle database.", no_args_is_help=True)
app.add_typer(bank_app, name="bank")
app.add_typer(db_app, name="db")
console = Console()
PDF_DIR = Path(__file__).resolve().parent / "pdfs"     # pdfs/<game>/<size>/

Difficulty = Literal["easy", "medium", "hard", "expert"]
Game = Literal["sudoku", "kakuro"]
SUDOKU_SIZES = ["9", "12"]
KAKURO_SIZES = ["4x4", "6x6", "8x8", "9x11", "10x10"]
SIX_FIT = {"9", "12", "4x4", "6x6"}       # sizes that stay writable at 6 per page

SudokuSizeOpt = Annotated[Optional[str], typer.Option("--size", help="9 or 12 (default 9).")]
KakuroSizeOpt = Annotated[Optional[str], typer.Option("--size", help="White area: 4x4, 6x6, 8x8, 9x11 or 10x10 (default 8x8).")]
DifficultyOpt = Annotated[Optional[Difficulty], typer.Option(help="Difficulty (default medium).")]
PdfDifficultyOpt = Annotated[Optional[Literal["easy", "medium", "hard", "expert", "mixed"]], typer.Option(
    help="Difficulty (default medium). mixed: 10% easy, 20% medium, 30% hard, 40% expert, easiest first; "
         "count must be a multiple of 10.")]
MIX = (1, 2, 3, 4)                         # tenths of a mixed PDF per difficulty, easy to expert
CountOpt = Annotated[Optional[int], typer.Option(min=1, max=1000, help="How many puzzles (default 12).")]
BankCountOpt = Annotated[Optional[int], typer.Option(min=1, max=5000, help="How many puzzles to add.")]
HALF_CORES = max(1, (os.cpu_count() or 2) // 2)
ForeverOpt = Annotated[bool, typer.Option("--forever", help="Keep going until you type q + Enter or press Ctrl-C.")]
CoresOpt = Annotated[Optional[int], typer.Option(min=1, help=f"CPU cores for --forever (default {HALF_CORES}, half).")]
PerPageOpt = Annotated[Optional[int], typer.Option(help="Puzzles per page: 1, 2, 4 or 6 (default 4; 6 not for Kakuro 8x8 and up).")]
OutOpt = Annotated[Optional[str], typer.Option(help="File name (default <game>_<size>_<difficulty>.pdf), saved in pdfs/<game>/<size>/. Never overwritten: adds _2, _3 ...")]
SeedOpt = Annotated[Optional[int], typer.Option(help="Seed for repeatable puzzles.")]
NoDbOpt = Annotated[bool, typer.Option("--no-db", help="Don't use the database: no bank, no saving, repeats checked only inside this PDF.")]


def sudoku_task(args):
    """(puzzle, ms): retry with new seeds until the rating matches the request exactly."""
    n, difficulty, seed = args
    start = time.time()
    rng = random.Random(seed)
    while True:
        p = S.generate(n, difficulty, rng)
        if p.exact:
            return p, int((time.time() - start) * 1000)


def kakuro_task(args):
    """(puzzle, ms): retry with new seeds until a puzzle of the exact difficulty comes out."""
    width, height, difficulty, seed = args
    start = time.time()
    rng = random.Random(seed)
    while True:
        try:
            return K.generate(width, height, difficulty, rng), int((time.time() - start) * 1000)
        except RuntimeError:
            pass


def _sudoku_job(size, difficulty, seed):
    return int(size), difficulty, seed


def _kakuro_job(size, difficulty, seed):
    width, height = map(int, size.split("x"))
    return width, height, difficulty, seed


GAMES = {
    "sudoku": dict(task=sudoku_task, job=_sudoku_job, label=lambda size: f"{size}x{size}",
                   key=lambda p: tuple(p.grid), shape=lambda p: (p.n, p.n), draw=draw_sudoku),
    "kakuro": dict(task=kakuro_task, job=_kakuro_job, label=lambda size: size,
                   key=lambda p: (str(p.white), str(sorted(p.across.items())), str(sorted(p.down.items()))),
                   shape=lambda p: (p.cols, p.rows), draw=draw_kakuro),
}


def _settings(sizes, default_size, size, difficulty, count, per_page):
    """Arrow-key menus when no setting was given on the command line, defaults otherwise."""
    if all(v is None for v in (size, difficulty, count, per_page)):
        size = questionary.select("Size", choices=sizes, default=default_size).unsafe_ask()
        difficulty = questionary.select("Difficulty", choices=[*S.DIFFICULTIES, "mixed"],
                                        default="medium").unsafe_ask()
        step = 10 if difficulty == "mixed" else 1
        count = int(questionary.text("How many puzzles" + (" (multiple of 10)" if step == 10 else ""),
                                     default="20" if step == 10 else "12",
                                     validate=lambda s: s.isdigit() and 1 <= int(s) <= 1000 and int(s) % step == 0
                                     or f"Enter a number from 1 to 1000{', a multiple of 10' if step == 10 else ''}"
                                     ).unsafe_ask())
        fits = [str(k) for k in PER_PAGE if k < 6 or size in SIX_FIT]
        per_page = int(questionary.select("Puzzles per page", choices=fits, default="4").unsafe_ask())
    size = size or default_size
    if size not in sizes:
        raise typer.BadParameter(f"choose one of {', '.join(sizes)}", param_hint="--size")
    per_page = per_page or 4
    if per_page not in PER_PAGE:
        raise typer.BadParameter("choose 1, 2, 4 or 6", param_hint="--per-page")
    if per_page == 6 and size not in SIX_FIT:
        raise typer.BadParameter("6 per page only fits Kakuro 4x4 and 6x6", param_hint="--per-page")
    difficulty, count = difficulty or "medium", count or (20 if difficulty == "mixed" else 12)
    if difficulty == "mixed" and count % 10:
        raise typer.BadParameter("mixed needs a multiple of 10 (10% easy, 20% medium, 30% hard, 40% expert)",
                                 param_hint="--count")
    return size, difficulty, count, per_page


def _free_name(path):
    """`path`, or path_2, path_3 ... when it already exists."""
    stem, ext = os.path.splitext(path)
    k = 2
    while os.path.exists(path):
        path = f"{stem}_{k}{ext}"
        k += 1
    return path


def _open_db(no_db, required=False):
    """A database connection, or None with a warning when it's off or unreachable."""
    if no_db:
        return None
    try:
        return db.connect()
    except psycopg.OperationalError as e:
        if required:
            console.print(f"[red]Database unreachable:[/] {str(e).strip().splitlines()[0]}")
            raise typer.Exit(1)
        console.print(f"[yellow]Database unreachable, puzzles won't be saved and the repeat check "
                      f"covers only this PDF.[/]\n[dim]{str(e).strip().splitlines()[0]}[/]")
        return None


def _generate(game, size, difficulty, n, rng, conn, seen):
    """n new puzzles as (db id or None, puzzle) on all cores. A puzzle already in
    `seen` or in the database is replaced by a fresh one."""
    g = GAMES[game]
    new_job = lambda: g["job"](size, difficulty, rng.getrandbits(64))
    jobs = [new_job() for _ in range(n)]
    out = [None] * n
    todo = list(range(n))
    columns = (SpinnerColumn(), TextColumn("[bold]{task.description}"), BarColumn(),
               MofNCompleteColumn(), TimeElapsedColumn(), TextColumn("left"), TimeRemainingColumn())
    with Progress(*columns, console=console) as progress, Pool(os.cpu_count()) as pool:
        bar = progress.add_task(f"Generating {g['label'](size)} {difficulty} {game.title()}", total=n)
        while todo:
            redo = []
            for i, (p, ms) in zip(todo, pool.imap(g["task"], [jobs[i] for i in todo])):
                key = g["key"](p)
                pid = None
                repeat = key in seen
                if not repeat and conn is not None:
                    pid = db.insert(conn, game, p, jobs[i][-1], ms)
                    repeat = pid is None                 # already in the database
                if repeat:
                    jobs[i] = new_job()
                    redo.append(i)
                    continue
                seen.add(key)
                out[i] = (pid, p)
                progress.advance(bar)
            todo = redo
    return out


def _make(game, size, difficulty, count, per_page, out, seed, no_db):
    g = GAMES[game]
    label = g["label"](size)
    start = time.time()
    plan = list(zip(S.DIFFICULTIES, (count * k // 10 for k in MIX))) if difficulty == "mixed" \
        else [(difficulty, count)]
    rng = random.Random(seed)
    conn = _open_db(no_db)
    try:
        items, seen, banked = [], set(), 0
        for diff, n in plan:                         # easiest first
            picked = db.take_bank(conn, game, label, diff, n) if conn else []
            seen.update(g["key"](p) for _pid, p in picked)
            items += picked + _generate(game, size, diff, n - len(picked), rng, conn, seen)
            banked += len(picked)
        folder = PDF_DIR / game / label
        folder.mkdir(parents=True, exist_ok=True)
        path = _free_name(str(folder / Path(out or f"{game}_{label}_{difficulty}.pdf").name))
        pages = write_pdf(path, [p for _pid, p in items], per_page, g["shape"], g["draw"], game.title())
        if conn:
            pdf_id = db.record_pdf(conn, game, path, label, difficulty, per_page, pages,
                                   [pid for pid, _p in items])
            conn.commit()
    finally:
        if conn:
            conn.close()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[bold green]Wrote", os.path.relpath(path))
    parts = [", ".join(f"{n} {d}" for d, n in plan)] if difficulty == "mixed" else []
    if conn:
        parts.append(f"{banked} from bank, {count - banked} new")
    detail = f" ({'; '.join(parts)})" if parts else ""
    table.add_row("Puzzles", f"{count} {label} {difficulty} {game.title()}{detail}")
    table.add_row("Pages", f"{pages} ({per_page} per page, solutions at the end)")
    table.add_row("Repeats", "none, checked against the whole database" if conn else "none in this PDF")
    table.add_row("Database", f"saved as PDF #{pdf_id}" if conn else "[yellow]not saved")
    table.add_row("Time", f"{time.time() - start:.1f} s")
    console.print(table)


@app.command()
def sudoku(size: SudokuSizeOpt = None, difficulty: PdfDifficultyOpt = None, count: CountOpt = None,
           per_page: PerPageOpt = None, out: OutOpt = None, seed: SeedOpt = None, no_db: NoDbOpt = False):
    """Sudoku PDF, sizes 9 and 12. No options: arrow-key menus."""
    size, difficulty, count, per_page = _settings(SUDOKU_SIZES, "9", size, difficulty, count, per_page)
    _make("sudoku", size, difficulty, count, per_page, out, seed, no_db)


@app.command()
def kakuro(size: KakuroSizeOpt = None, difficulty: PdfDifficultyOpt = None, count: CountOpt = None,
           per_page: PerPageOpt = None, out: OutOpt = None, seed: SeedOpt = None, no_db: NoDbOpt = False):
    """Kakuro PDF, white-area sizes 4x4, 6x6, 8x8, 9x11, 10x10 (clue row and column are extra). No options: arrow-key menus."""
    size, difficulty, count, per_page = _settings(KAKURO_SIZES, "8x8", size, difficulty, count, per_page)
    _make("kakuro", size, difficulty, count, per_page, out, seed, no_db)


# --------------------------------------------------------------------------
# bank
# --------------------------------------------------------------------------
def _bank(game, size, difficulty, count, seed):
    start = time.time()
    conn = _open_db(False, required=True)
    with conn:
        _generate(game, size, difficulty, count, random.Random(seed), conn, set())
    console.print(f"[bold green]Banked[/] {count} {GAMES[game]['label'](size)} {difficulty} "
                  f"{game.title()} in {time.time() - start:.1f} s")


def _wait_for_quit(stop):
    for line in sys.stdin:
        if line.strip().lower() in ("q", "quit", "exit", "stop"):
            break
    stop.set()


def _bank_forever(game, size, difficulty, cores, seed):
    """Generate into the bank until q + Enter or Ctrl-C. Each puzzle is committed
    as soon as it's made, so stopping loses only the ones still in progress."""
    g = GAMES[game]
    label = g["label"](size)
    conn = _open_db(False, required=True)
    conn.autocommit = True
    rng = random.Random(seed)
    stop = threading.Event()
    threading.Thread(target=_wait_for_quit, args=(stop,), daemon=True).start()
    done = queue.Queue()
    made = repeats = 0
    start = time.time()
    # workers ignore Ctrl-C; the main process handles it and shuts them down
    with Pool(cores, initializer=signal.signal, initargs=(signal.SIGINT, signal.SIG_IGN)) as pool:
        def submit():
            job = g["job"](size, difficulty, rng.getrandbits(64))
            pool.apply_async(g["task"], (job,), callback=lambda r: done.put((job, r)))

        for _ in range(cores * 2):            # keep every core busy, queue stays small
            submit()
        console.print(f"Filling the bank with {label} {difficulty} {game.title()} on {cores} cores. "
                      "[bold]Type q + Enter (or Ctrl-C) to stop.[/]")
        try:
            with console.status("Starting ...") as status:
                while not stop.is_set():
                    try:
                        job, (p, ms) = done.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if db.insert(conn, game, p, job[-1], ms) is None:
                        repeats += 1
                    else:
                        made += 1
                    submit()
                    in_bank = db.bank_count(conn, game, label, difficulty)
                    rate = made / max(time.time() - start, 1e-9) * 60
                    status.update(f"Banked {made} this run ({rate:.1f}/min), {in_bank} {label} {difficulty} "
                                  f"in bank, {repeats} repeats skipped. q + Enter to stop.")
        except KeyboardInterrupt:
            pass
        pool.terminate()
    conn.close()
    console.print(f"[bold green]Stopped.[/] Banked {made} {label} {difficulty} {game.title()} "
                  f"in {time.time() - start:.0f} s.")


def _bank_command(game, sizes, default_size, size, difficulty, count, forever, cores, seed):
    if all(v is None for v in (size, difficulty, count, cores)) and not forever:
        size = questionary.select("Size", choices=sizes, default=default_size).unsafe_ask()
        difficulty = questionary.select("Difficulty", choices=list(S.DIFFICULTIES),
                                        default="medium").unsafe_ask()
        forever = questionary.select("How many", choices=["A fixed number", "Keep going until I type q"],
                                     ).unsafe_ask() != "A fixed number"
        if forever:
            most = os.cpu_count() or 1
            cores = int(questionary.text(f"CPU cores to use (1-{most})", default=str(HALF_CORES),
                                         validate=lambda s: s.isdigit() and 1 <= int(s) <= most
                                         or f"Enter a number from 1 to {most}").unsafe_ask())
        else:
            count = int(questionary.text("How many puzzles", default="100",
                                         validate=lambda s: s.isdigit() and 1 <= int(s) <= 5000
                                         or "Enter a number from 1 to 5000").unsafe_ask())
    size = size or default_size
    difficulty = difficulty or "medium"
    cores = cores or HALF_CORES
    if size not in sizes:
        raise typer.BadParameter(f"choose one of {', '.join(sizes)}", param_hint="--size")
    if forever:
        _bank_forever(game, size, difficulty, cores, seed)
    elif count:
        _bank(game, size, difficulty, count, seed)
    else:
        raise typer.BadParameter("give --count N or --forever", param_hint="--count")


@bank_app.command("sudoku")
def bank_sudoku(count: BankCountOpt = None, size: SudokuSizeOpt = None, difficulty: DifficultyOpt = None,
                forever: ForeverOpt = False, cores: CoresOpt = None, seed: SeedOpt = None):
    """Add Sudoku puzzles to the bank: --count N, or --forever until you type q. No options: arrow-key menus."""
    _bank_command("sudoku", SUDOKU_SIZES, "9", size, difficulty, count, forever, cores, seed)


@bank_app.command("kakuro")
def bank_kakuro(count: BankCountOpt = None, size: KakuroSizeOpt = None, difficulty: DifficultyOpt = None,
                forever: ForeverOpt = False, cores: CoresOpt = None, seed: SeedOpt = None):
    """Add Kakuro puzzles to the bank: --count N, or --forever until you type q. No options: arrow-key menus."""
    _bank_command("kakuro", KAKURO_SIZES, "8x8", size, difficulty, count, forever, cores, seed)


# --------------------------------------------------------------------------
# db
# --------------------------------------------------------------------------
def _table(title, headers, rows):
    t = Table(title=title, title_justify="left")
    for h in headers:
        t.add_column(h, justify="right" if h in ("#", "Id", "Total", "Printed", "In bank", "Puzzles",
                                                  "Pages", "Givens", "White") else "left")
    for r in rows:
        t.add_row(*("" if v is None else str(v) for v in r))
    console.print(t)


@db_app.command()
def stats():
    """Puzzle counts per game, size and difficulty: total, printed, in bank."""
    with _open_db(False, required=True) as conn:
        _table("Puzzles", ["Game", "Size", "Difficulty", "Total", "Printed", "In bank"], db.stats(conn))


@db_app.command("list")
def list_(game: Annotated[Game, typer.Argument(help="sudoku or kakuro")],
          size: Annotated[Optional[str], typer.Option(help="e.g. 9x9, 8x8")] = None,
          difficulty: DifficultyOpt = None,
          bank: Annotated[bool, typer.Option("--bank", help="Only puzzles not printed yet.")] = False,
          limit: Annotated[int, typer.Option(help="How many rows.")] = 20):
    """Newest puzzles, with the PDF each appeared in."""
    with _open_db(False, required=True) as conn:
        rows = db.list_puzzles(conn, game, size, difficulty, bank, limit)
    rows = [(i, s, d, n, os.path.basename(path) if path else "bank", num, f"{at:%Y-%m-%d %H:%M}")
            for i, s, d, n, path, num, at in rows]
    _table(f"{game.title()} puzzles", ["Id", "Size", "Difficulty", "Givens" if game == "sudoku" else "White",
                                       "PDF", "#", "Created"], rows)


@db_app.command()
def pdfs(limit: Annotated[int, typer.Option(help="How many rows.")] = 50):
    """Every PDF made, newest first."""
    with _open_db(False, required=True) as conn:
        rows = db.list_pdfs(conn, limit)
    rows = [(i, g, s, d, n, pg, os.path.relpath(path), f"{at:%Y-%m-%d %H:%M}")
            for i, g, s, d, n, pg, path, at in rows]
    _table("PDFs", ["Id", "Game", "Size", "Difficulty", "Puzzles", "Pages", "File", "Created"], rows)


@db_app.command()
def reprint(pdf_id: Annotated[int, typer.Argument(help="PDF id from `db pdfs`.")]):
    """Rebuild a PDF from its stored puzzles (new file next to the original)."""
    with _open_db(False, required=True) as conn:
        found = db.pdf_puzzles(conn, pdf_id)
    if found is None:
        raise typer.BadParameter(f"no PDF with id {pdf_id}; see `db pdfs`", param_hint="PDF_ID")
    (_id, game, path, _size, _difficulty, per_page), puzzles = found
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    path = _free_name(path)
    pages = write_pdf(path, puzzles, per_page, GAMES[game]["shape"], GAMES[game]["draw"], game.title())
    console.print(f"[bold green]Wrote[/] {os.path.relpath(path)}: {len(puzzles)} puzzles, {pages} pages")


if __name__ == "__main__":
    app()
