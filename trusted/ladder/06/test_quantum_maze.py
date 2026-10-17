"""Tests for Project 06: Quantum Maze Solver (the 19 core tests).

Run it from the project folder, inside your activated virtual environment:
    python tests/test_quantum_maze.py
    python -m pytest tests/             (optional, if you have pytest installed)

Which code gets tested:
    1. the folder named in the QBC_TARGET environment variable, if it is set;
    2. otherwise solution/, if that folder exists here;
    3. otherwise starter/ (your code: the starter pack has no solution/).
Force your own code with QBC_TARGET=starter python tests/test_quantum_maze.py
(Windows PowerShell: $env:QBC_TARGET="starter"; python tests/test_quantum_maze.py).

The Module 2b tests live in tests/test_gate_oracle.py, so this count stays 19.
"""

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("QBC_TARGET") or ("solution" if (ROOT / "solution").is_dir() else "starter")
TARGET_DIR = (ROOT / TARGET).resolve()
if TARGET_DIR.parent != ROOT or not (TARGET_DIR / "quantum_maze.py").is_file():
    sys.exit(f"QBC_TARGET={TARGET!r}: no quantum_maze.py in a folder of that name next to tests/. "
             "Use starter or solution.")
sys.path.insert(0, str(TARGET_DIR))

import quantum_maze as qm  # noqa: E402

PILLAR, CORRIDOR, BLOCKED = qm.MAZES["pillar"], qm.MAZES["corridor"], qm.MAZES["blocked"]


# ---- Maze helpers --------------------------------------------------------
def test_find_cells():
    assert qm.find_cell(PILLAR, "S") == (0, 0)
    assert qm.find_cell(PILLAR, "G") == (2, 2)
    try:
        qm.find_cell(["..", ".."], "S")
    except ValueError:
        return
    raise AssertionError("expected ValueError for a maze without S")


def test_move_encoding_round_trip():
    # D=2, D=2, R=1, R=1 -> 2 + 2*4 + 1*16 + 1*64 = 90
    assert qm.moves_to_index("DDRR") == 90
    assert qm.index_to_moves(90, 4) == "DDRR"
    for i in range(256):
        assert qm.moves_to_index(qm.index_to_moves(i, 4)) == i


def test_valid_and_invalid_paths():
    assert qm.is_valid_path(PILLAR, "RRDD")
    assert qm.is_valid_path(PILLAR, "DDRR")
    assert not qm.is_valid_path(PILLAR, "RDRD")   # walks into the pillar
    assert not qm.is_valid_path(PILLAR, "UURR")   # leaves the grid
    assert not qm.is_valid_path(PILLAR, "DDR")    # stops short of the goal
    assert not qm.is_valid_path(CORRIDOR, "RRDD")  # wall at the top right


# ---- Classical baselines -------------------------------------------------
def test_bfs_finds_a_shortest_path():
    path, explored = qm.classical_bfs(PILLAR)
    assert len(path) == 4 and qm.is_valid_path(PILLAR, path), path
    assert explored <= 8   # at most every open cell once
    assert qm.classical_bfs(CORRIDOR)[0] == "DDRR"


def test_bfs_reports_unreachable_goal():
    path, _ = qm.classical_bfs(BLOCKED)
    assert path is None


def test_brute_force_solutions():
    assert sorted(qm.brute_force_solutions(PILLAR, 4)) == ["DDRR", "RRDD"]
    assert qm.brute_force_solutions(CORRIDOR, 4) == ["DDRR"]
    assert qm.brute_force_solutions(BLOCKED, 4) == []


# ---- Grover set-up -------------------------------------------------------
def test_marked_indices():
    assert sorted(qm.marked_indices(PILLAR, 4)) == sorted(
        [qm.moves_to_index("DDRR"), qm.moves_to_index("RRDD")])


def test_iteration_counts():
    assert qm.optimal_iterations(256, 2) == 8
    assert qm.optimal_iterations(256, 1) == 12


def test_circuit_size_is_two_qubits_per_move():
    circuit = qm.build_maze_circuit(PILLAR, 4)
    assert circuit.num_qubits == 8
    # 8 iterations x (2 marked paths in the oracle + 1 in the diffuser) = 24 mcx
    assert circuit.count_ops().get("mcx") == 24, circuit.count_ops()


def test_no_solution_rejected():
    for bad in (lambda: qm.build_maze_circuit(BLOCKED, 4),
                lambda: qm.build_maze_circuit(PILLAR, 3),   # no 3-move path exists
                lambda: qm.build_maze_circuit(PILLAR, 7)):  # too many qubits
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_exact_probability_matches_theory():
    for maze, m in ((PILLAR, 2), (CORRIDOR, 1)):
        k = qm.optimal_iterations(256, m)
        exact = qm.exact_success_probability(maze, 4)
        theory = qm.theoretical_success_probability(256, m, k)
        assert abs(exact - theory) < 1e-9, (exact, theory)
        assert exact > 0.99


def test_exact_probability_with_no_iterations_is_guessing():
    assert math.isclose(qm.exact_success_probability(PILLAR, 4, iterations=0), 2 / 256)


# ---- Simulation ----------------------------------------------------------
def test_corridor_solved_seeded():
    counts = qm.run_maze_search(CORRIDOR, 4, shots=500, seed=7)
    assert qm.best_path(counts) == "DDRR", counts


def test_pillar_valid_fraction():
    # Theory 0.9956; with 1,000 shots sd = 0.0021, so 0.97 is a >10-sigma margin.
    counts = qm.run_maze_search(PILLAR, 4, shots=1000)
    assert qm.valid_fraction(PILLAR, counts) > 0.97, counts


def test_both_pillar_paths_found_equally():
    # Each valid path should get about half the shots. With 2,000 shots the
    # difference of the two fractions has sd ~0.022, so 0.12 is a >5-sigma band.
    counts = qm.run_maze_search(PILLAR, 4, shots=2000)
    a, b = counts.get("DDRR", 0) / 2000, counts.get("RRDD", 0) / 2000
    assert abs(a - b) < 0.12, counts


def test_longer_paths_still_work():
    # 6 moves = 12 qubits, 4096 sequences, 12 valid (detours included).
    counts = qm.run_maze_search(PILLAR, 6, shots=500, seed=3)
    assert qm.valid_fraction(PILLAR, counts) > 0.95


def test_same_seed_is_repeatable():
    assert qm.run_maze_search(PILLAR, 4, 300, seed=5) == qm.run_maze_search(PILLAR, 4, 300, seed=5)


def test_valid_fraction_helper():
    assert qm.valid_fraction(PILLAR, {"DDRR": 3, "RDRD": 1}) == 0.75


def test_plot_creates_png():
    counts = qm.run_maze_search(PILLAR, 4, shots=200, seed=9)
    work = {"Brute force (expected)": 85.7, "Grover oracle calls": 8, "BFS cells explored": 8}
    with tempfile.TemporaryDirectory() as tmp:
        out = qm.plot_results(PILLAR, counts, work, Path(tmp) / "chart.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    print(f"Project 06 tests (target: {TARGET})\n")
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # report every failure, keep going
            failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed (target: {TARGET})")
    sys.exit(1 if failures else 0)
