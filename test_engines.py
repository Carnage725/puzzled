"""Run with:  .venv/bin/python -m pytest -q test_engines.py"""
import math
import random
import re

import pytest

import kakuro_engine as K
import sudoku_engine as S
from pdf_render import draw_kakuro, draw_sudoku, write_pdf


@pytest.mark.parametrize("size", [(4, 4), (6, 6), (8, 8)])
@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_kakuro_unique_and_rated(size, difficulty):
    for seed in range(3):
        p = K.generate(*size, difficulty, random.Random(seed))
        assert (p.cols - 1, p.rows - 1) == size
        m = K.Model.from_solution(p.white, p.solution)
        assert len(m.solutions(2)) == 1
        assert m.rate() == K.LEVEL_OF[difficulty]
        want = K.LEVEL_OF[difficulty]
        if want in K.MIN_STEPS:      # consistently that hard, not one lucky step
            assert K.level_steps(m, want) >= max(2, math.ceil(K.MIN_STEPS[want] * m.m))


def test_kakuro_expert_small():
    p = K.generate(6, 6, "expert", random.Random(0))
    m = K.Model.from_solution(p.white, p.solution)
    assert len(m.solutions(2)) == 1
    assert m.rate() == 4


@pytest.mark.parametrize("n", [4, 6, 9])
def test_sudoku_unique_and_solution_matches(n):
    p = S.generate(n, "medium", random.Random(1))
    geo = S.geometry(n)
    assert S.count_solutions(geo, p.grid) == 1
    assert all(g in (0, s) for g, s in zip(p.grid, p.solution))


def test_pdf_page_count(tmp_path):
    sudokus = [S.generate(4, "easy", random.Random(i)) for i in range(5)]
    write_pdf(str(tmp_path / "s.pdf"), sudokus, 4, lambda p: (p.n, p.n), draw_sudoku, "Sudoku")
    kakuros = [K.generate(4, 4, "easy", random.Random(i)) for i in range(3)]
    write_pdf(str(tmp_path / "k.pdf"), kakuros, 2, lambda p: (p.cols, p.rows), draw_kakuro, "Kakuro")
    # 5 puzzles at 4 per page (2 pages) + solutions at 8 per page (1 page)
    assert len(re.findall(rb"/Type /Page\b(?!s)", (tmp_path / "s.pdf").read_bytes())) == 3
    # 3 puzzles at 2 per page (2 pages) + solutions at 4 per page (1 page)
    assert len(re.findall(rb"/Type /Page\b(?!s)", (tmp_path / "k.pdf").read_bytes())) == 3


@pytest.fixture
def conn(monkeypatch):
    import psycopg
    import db
    monkeypatch.setattr(db, "DSN", "dbname=puzzled_test")
    try:
        c = db.connect()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL not running")
    yield c
    c.close()
    with psycopg.connect("dbname=postgres", autocommit=True) as admin:
        admin.execute("DROP DATABASE puzzled_test")


def test_db_round_trip_bank_and_repeats(conn):
    import db
    s = S.generate(9, "easy", random.Random(1))
    k = K.generate(6, 6, "hard", random.Random(1))
    sid, kid = db.insert(conn, "sudoku", s, 1, 5), db.insert(conn, "kakuro", k, 2**64 - 1, 5)
    assert sid and kid
    assert db.insert(conn, "kakuro", k, 3, 5) is None            # same puzzle again
    [(bid, bk)] = db.take_bank(conn, "kakuro", "6x6", "hard", 5)
    assert bid == kid
    assert (bk.white, bk.solution, bk.across, bk.down) == (k.white, k.solution, k.across, k.down)
    pdf_id = db.record_pdf(conn, "kakuro", "x.pdf", "6x6", "hard", 4, 2, [kid])
    assert db.take_bank(conn, "kakuro", "6x6", "hard", 5) == []   # printed, no longer in bank
    _pdf, [again] = db.pdf_puzzles(conn, pdf_id)
    assert again.solution == k.solution
    [(_sid, bs)] = db.take_bank(conn, "sudoku", "9x9", "easy", 5)
    assert (bs.grid, bs.solution) == (s.grid, s.solution)


def test_db_reuse_stored_puzzles(conn):
    import db
    k = K.generate(6, 6, "easy", random.Random(2))
    kid = db.insert(conn, "kakuro", k, 1, 5)
    first = db.record_pdf(conn, "kakuro", "a.pdf", "6x6", "easy", 4, 2, [kid])
    [(pid, again)] = db.pick_stored(conn, "kakuro", "6x6", "easy", 5)   # printed puzzles are reusable
    assert pid == kid and again.solution == k.solution
    second = db.record_pdf(conn, "kakuro", "b.pdf", "6x6", "easy", 4, 2, [pid])
    assert [p.solution for p in db.pdf_puzzles(conn, second)[1]] == [k.solution]
    assert conn.execute("SELECT pdf_id FROM kakuro WHERE id = %s", (kid,)).fetchone()[0] == first
