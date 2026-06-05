# RoutingSoftware — Substrate DB v1

A lightweight packaging-substrate design database: C++20 in-memory object store with transactions and undo, R-tree spatial index, hierarchical constraints, connectivity/ratsnest engine, spacing DRC, Python API via nanobind, KLayout live viewing, and GDS/`.lyp`/`.lyrdb` export. This is the v1 foundation; see the full design spec at [`docs/superpowers/specs/2026-06-03-substrate-db-foundation-design.md`](docs/superpowers/specs/2026-06-03-substrate-db-foundation-design.md).

---

## Install

Requires cmake and ninja. If they are not already installed system-wide:

```
python3 -m venv .venv && .venv/bin/pip install cmake ninja
```

Then install the package (builds the C++ extension in-place):

```
pip install -e .[dev]
```

---

## Run tests

**C++ suite (61 tests):**

```
cmake -S . -B build -G Ninja -DSDB_BUILD_TESTS=ON -DSDB_BUILD_PYTHON=OFF \
  && cmake --build build \
  && ctest --test-dir build --output-on-failure
```

**Python suite (30 tests):**

```
python -m pytest tests/python -v
```

---

## Run the demo

```
python examples/demo_flipchip_bga.py
```

The demo builds a flip-chip-on-BGA design (20×20 die bumps, 30×30 BGA balls, six named nets, two escape traces, two deliberate spacing violations), runs the DRC engine and connectivity/ratsnest check, exports `demo_fcbga.gds`, `demo_fcbga.lyp`, and `demo_fcbga.lyrdb` into `build/demo_view/`, and pushes the GDS to KLayout if it is running with the klive plugin.

---

## KLayout live-view setup

Install the **klive** salt package inside KLayout (Tools → Salt Package Manager → search "klive"). It listens on `localhost:8082` and hot-reloads the GDS each time `show()` is called from Python.

Without klive, `show()` falls back to launching `klayout <gds> -l <lyp>` as a detached process.

To review DRC markers: open the exported `.lyrdb` in KLayout via **Tools → Marker Browser → Load Report**.
