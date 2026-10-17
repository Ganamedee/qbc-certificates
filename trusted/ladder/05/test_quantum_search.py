"""Tests for Project 05: Grover's Algorithm, the Quantum Search Engine.

Works with plain Python OR pytest (run from the project folder):
    python tests/test_quantum_search.py
    python -m pytest tests/             (only if you have pytest installed)

Which code gets tested (the header line tells you):
    1. the folder named in the QBC_TARGET environment variable, if set
       (macOS/Linux: QBC_TARGET=starter python tests/test_quantum_search.py
        PowerShell:  $env:QBC_TARGET="starter"; python tests/test_quantum_search.py)
    2. otherwise solution/, if that folder exists
    3. otherwise starter/ (your code: the usual case in your own repo)
"""

import math
import os
import sys
import tempfile
from pathlib import Path

from qiskit.quantum_info import Statevector

ROOT = Path(__file__).resolve().parent.parent


def _pick_target() -> str:
    target = os.environ.get("QBC_TARGET", "").strip()
    if not target:
        target = "solution" if (ROOT / "solution").is_dir() else "starter"
    # A plain folder name inside the project only (no paths like ../elsewhere).
    if not target.replace("_", "").replace("-", "").isalnum() or not (ROOT / target).is_dir():
        sys.exit(f"QBC_TARGET={target!r} is not a folder in {ROOT}. Try QBC_TARGET=starter.")
    return target


TARGET = _pick_target()
sys.path.insert(0, str(ROOT / TARGET))

import quantum_search as qs  # noqa: E402


# ---- Classical baseline --------------------------------------------------
def test_classical_linear_search():
    assert qs.classical_linear_search(qs.DATABASE, "Ada") == (0, 1)
    assert qs.classical_linear_search(qs.DATABASE, "Lise") == (12, 13)
    assert qs.classical_linear_search(qs.DATABASE, "Nobody") == (-1, 16)


def test_average_classical_queries():
    assert math.isclose(qs.average_classical_queries(16, 1), 8.5)
    assert math.isclose(qs.average_classical_queries(16, 2), 17 / 3)


# ---- Grover theory -------------------------------------------------------
def test_optimal_iterations_small_cases():
    expected = {(4, 1): 1, (8, 1): 2, (16, 1): 3, (16, 2): 2, (16, 4): 1, (64, 1): 6}
    for (n_items, n_marked), k in expected.items():
        assert qs.optimal_iterations(n_items, n_marked) == k, (n_items, n_marked)


def test_optimal_iterations_scale_like_sqrt_n():
    # About (pi/4) * sqrt(N): quadratic, not exponential, fewer queries.
    for n in (2 ** 10, 2 ** 16, 2 ** 20):
        k = qs.optimal_iterations(n, 1)
        assert abs(k - math.pi / 4 * math.sqrt(n)) <= 1, (n, k)
        assert k < qs.average_classical_queries(n, 1)


def test_theoretical_success_probability():
    assert math.isclose(qs.theoretical_success_probability(4, 1, 1), 1.0)
    assert math.isclose(qs.theoretical_success_probability(16, 1, 0), 1 / 16)
    assert math.isclose(qs.theoretical_success_probability(16, 1, 3), 0.96131, abs_tol=1e-4)


# ---- Circuit structure ---------------------------------------------------
def test_oracle_flips_sign_of_marked_states_only():
    for marked in ([12], [3, 5]):
        uniform = Statevector.from_label("+" * 4)
        amplitudes = uniform.evolve(qs.build_oracle(4, marked)).data
        for i, amp in enumerate(amplitudes):
            expected = -0.25 if i in marked else 0.25
            assert abs(amp - expected) < 1e-9, (marked, i, amp)


def test_diffuser_reflects_about_uniform_state():
    # The circuit is I - 2|s><s| = -(2|s><s| - I): it sends the uniform state
    # |s> to -|s>, which is |s> up to a global phase (equiv ignores that).
    uniform = Statevector.from_label("+" * 3)
    assert uniform.evolve(qs.build_diffuser(3)).equiv(uniform)
    # The textbook 2|s><s| - I gives a basis state |x> amplitude 2/N - 1 on x
    # and 2/N everywhere else: for N = 8, -0.75 and 0.25. The circuit gives the
    # same times -1 (0.75 and -0.25). Compare ratios so the global phase cancels.
    out = Statevector.from_label("000").evolve(qs.build_diffuser(3)).data
    assert abs(abs(out[0]) - 0.75) < 1e-9, out
    for i in range(1, 8):
        assert abs(out[i] / out[0] - (0.25 / -0.75)) < 1e-9, (i, out)


def test_grover_circuit_uses_k_oracle_calls():
    ops = qs.build_grover_circuit(4, [12], iterations=3).count_ops()
    assert ops.get("mcx") == 6, ops      # 3 oracles + 3 diffusers
    assert ops.get("measure") == 4, ops


def test_exact_probability_matches_theory():
    for num_qubits, marked in ((3, [5]), (4, [12]), (4, [0, 9])):
        for k in range(5):
            exact = qs.exact_success_probability(num_qubits, marked, k)
            theory = qs.theoretical_success_probability(2 ** num_qubits, len(marked), k)
            assert abs(exact - theory) < 1e-9, (num_qubits, marked, k, exact, theory)


def test_bad_inputs_rejected():
    for bad in (lambda: qs.build_oracle(4, [16]), lambda: qs.build_oracle(4, []),
                lambda: qs.grover_search(qs.DATABASE[:10], ["Ada"]),
                lambda: qs.grover_search(qs.DATABASE, ["Nobody"])):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


# ---- Simulation ----------------------------------------------------------
def test_four_items_found_with_certainty():
    # N = 4, one iteration: theta = pi/6 and sin^2(3 * pi/6) = 1 exactly.
    counts = qs.run_grover(2, [2], shots=500, seed=1)
    assert counts == {2: 500}, counts


def test_finds_target_name():
    found, counts = qs.grover_search(qs.DATABASE, ["Lise"], shots=500, seed=3)
    assert found == "Lise", counts


def test_success_rate_at_optimal_k_seeded():
    # Theory 0.961. With 2,000 shots sd = 0.0043, so 0.93 is a 7-sigma margin.
    rate = qs.success_rate(qs.run_grover(4, [12], shots=2000, seed=7), [12])
    assert rate > 0.93, rate


def test_success_rate_at_optimal_k_unseeded():
    rate = qs.success_rate(qs.run_grover(4, [6], shots=2000), [6])
    assert rate > 0.93, rate


def test_two_targets():
    # N = 16, M = 2: k = 2, theory 0.945; sd with 2,000 shots = 0.005.
    counts = qs.run_grover(4, [3, 10], shots=2000)
    assert abs(qs.success_rate(counts, [3, 10]) - 0.9453) < 0.03, counts


def test_zero_iterations_is_a_random_guess():
    # No Grover iterations: uniform, P = 1/16. sd with 4,000 shots = 0.0038.
    rate = qs.success_rate(qs.run_grover(4, [12], iterations=0, shots=4000), [12])
    assert abs(rate - 1 / 16) < 0.025, rate


def test_too_many_iterations_overshoot():
    # k = 6 rotates past the target: theory P = 0.020.
    rate = qs.success_rate(qs.run_grover(4, [12], iterations=6, shots=2000), [12])
    assert rate < 0.1, rate


def test_same_seed_is_repeatable():
    assert qs.run_grover(4, [12], 2, 300, seed=5) == qs.run_grover(4, [12], 2, 300, seed=5)


def test_success_rate_helper():
    assert qs.success_rate({1: 30, 2: 10}, [1]) == 0.75
    assert qs.success_rate({1: 30, 2: 10}, [1, 2]) == 1.0


def test_plot_creates_png():
    counts = qs.run_grover(4, [12], shots=200, seed=9)
    curve = qs.success_curve(4, [12], 3, shots=200, seed=9)
    assert [k for k, _, _ in curve] == [0, 1, 2, 3]
    with tempfile.TemporaryDirectory() as tmp:
        out = qs.plot_results(counts, curve, qs.DATABASE, [12], Path(tmp) / "chart.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    print(f"Project 05 tests (target: {TARGET})")
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
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
