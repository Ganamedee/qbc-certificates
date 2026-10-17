"""Tests for Project 01: Quantum Coin Flip.

Run it from the project folder, inside your activated virtual environment:
    python tests/test_quantum_coin.py
    python -m pytest tests/             (optional, if you have pytest installed)

Which code gets tested:
    1. the folder named in the QBC_TARGET environment variable, if it is set;
    2. otherwise solution/, if that folder exists here;
    3. otherwise starter/ (your code: the starter pack has no solution/).
Force your own code with QBC_TARGET=starter python tests/test_quantum_coin.py
(Windows PowerShell: $env:QBC_TARGET="starter"; python tests/test_quantum_coin.py).
"""

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("QBC_TARGET") or ("solution" if (ROOT / "solution").is_dir() else "starter")
TARGET_DIR = (ROOT / TARGET).resolve()
if TARGET_DIR.parent != ROOT or not (TARGET_DIR / "quantum_coin.py").is_file():
    sys.exit(f"QBC_TARGET={TARGET!r}: no quantum_coin.py in a folder of that name next to tests/. "
             "Use starter or solution.")
sys.path.insert(0, str(TARGET_DIR))

import quantum_coin as coin  # noqa: E402


# ---- Circuit structure ---------------------------------------------------
def test_circuit_has_one_qubit_and_one_classical_bit():
    circuit = coin.build_coin_circuit()
    assert circuit.num_qubits == 1
    assert circuit.num_clbits == 1


def test_fair_circuit_uses_hadamard_then_measure():
    ops = [instruction.operation.name for instruction in coin.build_coin_circuit().data]
    assert ops == ["h", "measure"], ops


def test_biased_circuit_uses_ry():
    ops = [instruction.operation.name for instruction in coin.build_coin_circuit(0.8).data]
    assert ops == ["ry", "measure"], ops


def test_theta_formula():
    assert math.isclose(coin.theta_for_probability(0.0), 0.0)
    assert math.isclose(coin.theta_for_probability(0.5), math.pi / 2)
    assert math.isclose(coin.theta_for_probability(1.0), math.pi)
    theta = coin.theta_for_probability(0.8)
    assert math.isclose(math.sin(theta / 2) ** 2, 0.8)


# ---- Outputs -------------------------------------------------------------
def test_returns_requested_number_of_flips():
    for n in (0, 1, 7, 100):
        assert len(coin.quantum_flips(n, seed=1)) == n


def test_only_heads_or_tails():
    flips = coin.quantum_flips(500, seed=2)
    assert set(flips) <= {coin.HEADS, coin.TAILS}


def test_same_seed_is_repeatable():
    assert coin.quantum_flips(200, seed=42) == coin.quantum_flips(200, seed=42)


def test_different_seeds_differ():
    assert coin.quantum_flips(200, seed=1) != coin.quantum_flips(200, seed=2)


def test_negative_flips_rejected():
    try:
        coin.quantum_flips(-1)
    except ValueError:
        return
    raise AssertionError("expected ValueError for n=-1")


# ---- Statistics: is the coin fair? ----------------------------------------
def test_fair_coin_is_about_50_50_seeded():
    # 10,000 flips: standard deviation of the heads fraction is 0.005,
    # so a 0.02 tolerance is a 4-sigma band.
    stats = coin.summarise(coin.quantum_flips(10_000, seed=7))
    assert abs(stats["heads_fraction"] - 0.5) < 0.02, stats


def test_fair_coin_is_about_50_50_unseeded():
    # Fresh randomness every run. 4,000 flips, tolerance 0.05 is > 6 sigma,
    # so a false failure is astronomically unlikely.
    stats = coin.summarise(coin.quantum_flips(4_000))
    assert abs(stats["heads_fraction"] - 0.5) < 0.05, stats


def test_chi_square_does_not_reject_fair_coin():
    chi2 = coin.chi_square_fair(coin.quantum_flips(10_000, seed=11))
    assert chi2 < coin.CHI2_CRITICAL_DF1_P001, chi2


def test_chi_square_catches_a_rigged_coin():
    rigged = [coin.HEADS] * 700 + [coin.TAILS] * 300
    assert coin.chi_square_fair(rigged) > coin.CHI2_CRITICAL_DF1_P001


def test_biased_coin_matches_requested_probability():
    stats = coin.summarise(coin.quantum_flips(10_000, seed=3, p_heads=0.8))
    # sd = sqrt(0.8 * 0.2 / 10000) = 0.004, tolerance is 5 sigma
    assert abs(stats["heads_fraction"] - 0.8) < 0.02, stats


def test_certain_coins():
    assert set(coin.quantum_flips(50, seed=5, p_heads=1.0)) == {coin.HEADS}
    assert set(coin.quantum_flips(50, seed=5, p_heads=0.0)) == {coin.TAILS}


# ---- Classical baseline and helpers ---------------------------------------
def test_classical_baseline_is_fair():
    stats = coin.summarise(coin.classical_flips(10_000, seed=1))
    assert abs(stats["heads_fraction"] - 0.5) < 0.02, stats


def test_running_fraction():
    fractions = coin.running_heads_fraction(["H", "T", "H", "H"])
    assert fractions == [1.0, 0.5, 2 / 3, 0.75]


def test_plot_creates_png():
    flips = coin.quantum_flips(200, seed=9)
    with tempfile.TemporaryDirectory() as tmp:
        out = coin.plot_results(flips, coin.classical_flips(200, seed=9),
                                Path(tmp) / "chart.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    print(f"Project 01 tests (target: {TARGET})\n")
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
