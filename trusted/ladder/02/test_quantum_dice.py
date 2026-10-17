"""Tests for Project 02: Quantum Dice Roller.

Run it from the project folder, inside your activated virtual environment:
    python tests/test_quantum_dice.py
    python -m pytest tests/             (optional, if you have pytest installed)

Which code gets tested:
    1. the folder named in the QBC_TARGET environment variable, if it is set;
    2. otherwise solution/, if that folder exists here;
    3. otherwise starter/ (your code: the starter pack has no solution/).
Force your own code with QBC_TARGET=starter python tests/test_quantum_dice.py
(Windows PowerShell: $env:QBC_TARGET="starter"; python tests/test_quantum_dice.py).
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("QBC_TARGET") or ("solution" if (ROOT / "solution").is_dir() else "starter")
TARGET_DIR = (ROOT / TARGET).resolve()
if TARGET_DIR.parent != ROOT or not (TARGET_DIR / "quantum_dice.py").is_file():
    sys.exit(f"QBC_TARGET={TARGET!r}: no quantum_dice.py in a folder of that name next to tests/. "
             "Use starter or solution.")
sys.path.insert(0, str(TARGET_DIR))

import quantum_dice as dice  # noqa: E402


# ---- Circuit structure ---------------------------------------------------
def test_three_qubits_for_a_d6():
    assert dice.qubits_needed(6) == 3
    assert dice.qubits_needed(2) == 1
    assert dice.qubits_needed(8) == 3
    assert dice.qubits_needed(20) == 5


def test_circuit_is_hadamards_then_measurements():
    circuit = dice.build_dice_circuit(3)
    assert circuit.num_qubits == 3 and circuit.num_clbits == 3
    ops = [instruction.operation.name for instruction in circuit.data]
    assert ops.count("h") == 3 and ops.count("measure") == 3, ops
    assert ops.index("measure") > max(i for i, op in enumerate(ops) if op == "h")


# ---- Raw circuit output --------------------------------------------------
def test_raw_outcomes_cover_0_to_7_uniformly():
    raw = dice.raw_outcomes(16_000, 3, seed=5)
    assert set(raw) == set(range(8))
    for value in range(8):
        share = raw.count(value) / len(raw)
        assert abs(share - 1 / 8) < 0.015, (value, share)  # sd ~ 0.0026


# ---- Rolls ---------------------------------------------------------------
def test_returns_requested_number_of_rolls():
    for n in (0, 1, 5, 333):
        assert len(dice.quantum_rolls(n, seed=1)) == n


def test_faces_are_1_to_6_only():
    rolls = dice.quantum_rolls(3_000, seed=2)
    assert set(rolls) == {1, 2, 3, 4, 5, 6}


def test_same_seed_is_repeatable():
    assert dice.quantum_rolls(300, seed=42) == dice.quantum_rolls(300, seed=42)


def test_each_face_about_one_sixth():
    rolls = dice.quantum_rolls(12_000, seed=3)
    counts = dice.face_counts(rolls)
    for face, count in counts.items():
        # sd of each share is sqrt((1/6)(5/6)/12000) ~ 0.0034; 0.017 is 5 sigma
        assert abs(count / len(rolls) - 1 / 6) < 0.017, (face, count)


def test_chi_square_accepts_quantum_die_seeded():
    assert dice.is_fair(dice.quantum_rolls(6_000, seed=4))


def test_chi_square_accepts_quantum_die_unseeded():
    # Fresh randomness every run, so use a stricter cut-off (z = 4.753, about
    # a one-in-a-million false alarm rate) to keep the test from ever flaking.
    chi2 = dice.chi_square_uniform(dice.quantum_rolls(6_000))
    assert chi2 < dice.chi_square_critical(5, z=4.753), chi2


def test_naive_modulo_is_detectably_biased():
    naive = dice.naive_modulo_rolls(6_000, seed=6)
    counts = dice.face_counts(naive)
    assert counts[1] / len(naive) > 0.22 and counts[2] / len(naive) > 0.22, counts
    assert not dice.is_fair(naive)


def test_other_dice_sizes():
    for sides in (2, 4, 10, 20):
        rolls = dice.quantum_rolls(400 * sides, sides=sides, seed=sides)
        assert set(rolls) == set(range(1, sides + 1))
        assert dice.is_fair(rolls, sides), sides


# ---- Helpers and baseline ------------------------------------------------
def test_face_counts_include_missing_faces():
    assert dice.face_counts([1, 1, 3]) == {1: 2, 2: 0, 3: 1, 4: 0, 5: 0, 6: 0}


def test_critical_value_close_to_table():
    assert abs(dice.chi_square_critical(5) - 20.515) < 0.5


def test_classical_baseline_is_fair():
    assert dice.is_fair(dice.classical_rolls(6_000, seed=1))


def test_invalid_inputs_rejected():
    for bad in (lambda: dice.quantum_rolls(-1), lambda: dice.qubits_needed(1)):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_plot_creates_png():
    q = dice.quantum_rolls(600, seed=9)
    with tempfile.TemporaryDirectory() as tmp:
        out = dice.plot_results(q, dice.naive_modulo_rolls(600, seed=9),
                                dice.classical_rolls(600, seed=9), Path(tmp) / "d.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    print(f"Project 02 tests (target: {TARGET})\n")
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
