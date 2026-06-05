# Unit helpers: the database unit is 1 nm (integer Coord).
def nm(v): return int(round(v))
def um(v): return int(round(v * 1_000))
def mm(v): return int(round(v * 1_000_000))
