"""PDF output for puzzled.py.

* A4 PDF layout (reportlab): puzzles first, N per page, then the solutions,
  packed as many per page as fit (up to 20), numbered to match.
* Grid drawing for both puzzle types.
"""
from __future__ import annotations

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from sudoku_engine import SYMBOLS

PER_PAGE = (1, 2, 4, 6)
GRID_OF = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3)}           # columns x rows of grids
SOLUTION_LAYOUTS = [(4, 5), (4, 4), (3, 4), (3, 3), (2, 3), (2, 2)]   # densest first
MIN_SOLUTION_CELL = 4.5 * mm
MARGIN = 15 * mm
GAP, SOLUTION_GAP = 8 * mm, 4 * mm            # between grids
LABEL, SOLUTION_LABEL = 7 * mm, 5 * mm        # "#7 · Hard" line above each grid
HEADING = 14 * mm                             # "Solutions" title
FOOTER = 8 * mm
MAX_CELL = 16 * mm
CLUE_GREY = 0.82
SOLVED_GREY = 0.35


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def write_pdf(path, puzzles, per_page, shape, draw, title):
    """shape(p) -> (columns, rows) of cells; draw(c, p, x, y, cell, solved) with
    (x, y) the grid's top-left corner. Returns the page count."""
    page_w, page_h = A4
    gc, gr = shape(puzzles[0])
    sol_top = page_h - MARGIN - HEADING
    sol_layout = next((lay for lay in SOLUTION_LAYOUTS
                       if _cell(lay, sol_top, SOLUTION_GAP, SOLUTION_LABEL, gc, gr) >= MIN_SOLUTION_CELL),
                      SOLUTION_LAYOUTS[-1])
    sol_pp = sol_layout[0] * sol_layout[1]
    total = -(-len(puzzles) // per_page) + -(-len(puzzles) // sol_pp)
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle(title)
    page = 0
    for solved, pp, layout in ((False, per_page, GRID_OF[per_page]), (True, sol_pp, sol_layout)):
        for start in range(0, len(puzzles), pp):
            page += 1
            top = page_h - MARGIN
            if solved and start == 0:
                c.setFont("Helvetica-Bold", 18)
                c.drawCentredString(page_w / 2, top - 6 * mm, "Solutions")
                top -= HEADING
            _grids(c, puzzles[start:start + pp], start, layout, top, shape, draw, solved)
            c.setFont("Helvetica", 9)
            c.setFillGray(0.4)
            c.drawCentredString(page_w / 2, MARGIN - 4 * mm, f"Page {page} of {total}")
            c.showPage()
    c.save()
    return total


def _slots(layout, top, gap):
    nc, nr = layout
    slot_w = (A4[0] - 2 * MARGIN - (nc - 1) * gap) / nc
    slot_h = (top - MARGIN - FOOTER - (nr - 1) * gap) / nr
    return slot_w, slot_h


def _cell(layout, top, gap, label, gc, gr):
    slot_w, slot_h = _slots(layout, top, gap)
    return min(slot_w / gc, (slot_h - label) / gr, MAX_CELL)


def _grids(c, puzzles, start, layout, top, shape, draw, solved):
    gap, label = (SOLUTION_GAP, SOLUTION_LABEL) if solved else (GAP, LABEL)
    slot_w, slot_h = _slots(layout, top, gap)
    nc = layout[0]
    for k, p in enumerate(puzzles):
        gc, gr = shape(p)
        cell = _cell(layout, top, gap, label, gc, gr)
        sx = MARGIN + (k % nc) * (slot_w + gap)
        sy = top - (k // nc) * (slot_h + gap)
        x = sx + (slot_w - gc * cell) / 2
        c.setFillGray(0)
        c.setFont("Helvetica-Bold", 7 if solved else 11)
        c.drawString(x, sy - label + 1.5 * mm, f"#{start + k + 1} · {p.difficulty.title()}")
        draw(c, p, x, sy - label, cell, solved)


def _digit(c, text, cx, cy, size, bold, grey=0.0):
    c.setFillGray(grey)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawCentredString(cx, cy - size * 0.35, text)


def draw_sudoku(c, p, x, y, cell, solved):
    n = p.n
    for i in range(n * n):
        r, col = divmod(i, n)
        v = p.grid[i] or (p.solution[i] if solved else 0)
        if v:
            _digit(c, SYMBOLS[v - 1], x + (col + 0.5) * cell, y - (r + 0.5) * cell,
                   cell * 0.6, bool(p.grid[i]), 0 if p.grid[i] else SOLVED_GREY)
    c.setStrokeGray(0)
    for k in range(n + 1):
        c.setLineWidth(2 if k % p.br == 0 else 0.5)          # horizontal: box rows
        c.line(x, y - k * cell, x + n * cell, y - k * cell)
        c.setLineWidth(2 if k % p.bc == 0 else 0.5)          # vertical: box columns
        c.line(x + k * cell, y, x + k * cell, y - n * cell)


def draw_kakuro(c, p, x, y, cell, solved):
    size = cell * 0.3
    pad = cell * 0.08
    c.setStrokeGray(0)
    c.setLineWidth(0.6)
    for r in range(p.rows):
        for col in range(p.cols):
            cx, cy = x + col * cell, y - (r + 1) * cell                 # bottom-left corner
            if p.white[r][col]:
                c.setFillGray(1)
                c.rect(cx, cy, cell, cell, stroke=1, fill=1)
                if solved:
                    _digit(c, str(p.solution[r][col]), cx + cell / 2, cy + cell / 2, cell * 0.6, False)
                continue
            c.setFillGray(CLUE_GREY)
            c.rect(cx, cy, cell, cell, stroke=1, fill=1)
            c.line(cx, cy + cell, cx + cell, cy)
            c.setFillGray(0)
            c.setFont("Helvetica-Bold", size)
            if (r, col) in p.across:                                    # upper-right triangle
                c.drawRightString(cx + cell - pad, cy + cell - pad - size * 0.8, str(p.across[(r, col)]))
            if (r, col) in p.down:                                      # lower-left triangle
                c.drawString(cx + pad, cy + pad, str(p.down[(r, col)]))
    c.setLineWidth(1.5)
    c.rect(x, y - p.rows * cell, p.cols * cell, p.rows * cell, stroke=1, fill=0)
