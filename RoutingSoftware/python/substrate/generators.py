"""Symbol generators: JEDEC-named BGA ball arrays and die bump arrays.

All generators record their parameters as GEN_* properties on the created
symbol so it can be regenerated later. They must be called inside an open
``Design.transaction`` (they mutate the database via the facade).
"""

_ROW_LETTERS = list("ABCDEFGHJKLMNPRTUVWY")   # JEDEC: skip I, O, Q, S, X, Z


def jedec_name(row_index):
    """JEDEC row designator for a 0-based row index ("A", "B", ..., "AA")."""
    n = len(_ROW_LETTERS)
    if row_index < n:
        return _ROW_LETTERS[row_index]
    hi, lo = divmod(row_index - n, n)
    return _ROW_LETTERS[hi] + _ROW_LETTERS[lo]


def _grid(rows, cols, pitch):
    """Yield (row, col, (x, y)) over a grid centered on (0, 0): row 0 at the
    top (+y), column 0 at the left (-x), y decreasing row by row."""
    x0 = -(cols - 1) * pitch // 2
    y0 = (rows - 1) * pitch // 2
    for r in range(rows):
        for c in range(cols):
            yield r, c, (x0 + c * pitch, y0 - r * pitch)


def _record_params(db, sym, gen, rows, cols, pitch):
    db.set_property(sym, "GEN", gen)
    db.set_property(sym, "GEN_ROWS", rows)
    db.set_property(sym, "GEN_COLS", cols)
    db.set_property(sym, "GEN_PITCH", pitch)


def bga_symbol(db, name, rows, cols, pitch, padstack, depopulate=()):
    """Create a BGA ball-array symbol with JEDEC pin names (A1, A2, ... AA1).

    ``depopulate`` is an iterable of pin numbers (e.g. "B2") to omit.
    Returns the symbol Handle.
    """
    dep = set(depopulate)
    pins = []
    for r, c, off in _grid(rows, cols, pitch):
        num = f"{jedec_name(r)}{c + 1}"
        if num in dep:
            continue
        pins.append((num, padstack, off))
    sym = db.add_symbol(name, pins)
    _record_params(db, sym, "bga_array", rows, cols, pitch)
    return sym


def die_symbol(db, name, rows, cols, pitch, padstack):
    """Create a die bump-array symbol with pins numbered "1".."N" row-major.

    Returns the symbol Handle.
    """
    pins = [(str(r * cols + c + 1), padstack, off)
            for r, c, off in _grid(rows, cols, pitch)]
    sym = db.add_symbol(name, pins)
    _record_params(db, sym, "die_array", rows, cols, pitch)
    return sym
