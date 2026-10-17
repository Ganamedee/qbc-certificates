"""Tests for Project 03: Quantum Random Number Generator.

Run it from the project folder, inside your activated virtual environment:
    python tests/test_quantum_rng.py
    python -m pytest tests/             (optional, if you have pytest installed)

Which code gets tested:
    1. the folder named in the QBC_TARGET environment variable, if it is set;
    2. otherwise solution/, if that folder exists here;
    3. otherwise starter/ (your code: the starter pack has no solution/).
Force your own code with QBC_TARGET=starter python tests/test_quantum_rng.py
(Windows PowerShell: $env:QBC_TARGET="starter"; python tests/test_quantum_rng.py).
"""

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("QBC_TARGET") or ("solution" if (ROOT / "solution").is_dir() else "starter")
TARGET_DIR = (ROOT / TARGET).resolve()
if TARGET_DIR.parent != ROOT or not (TARGET_DIR / "quantum_rng.py").is_file():
    sys.exit(f"QBC_TARGET={TARGET!r}: no quantum_rng.py in a folder of that name next to tests/. "
             "Use starter or solution.")
sys.path.insert(0, str(TARGET_DIR))

import quantum_rng as q  # noqa: E402


# ---- The statistical tests themselves must be correct -----------------------
def test_monobit_matches_nist_example():
    # NIST SP 800-22 section 2.1.4: epsilon = 1011010101 -> P-value = 0.527089
    bits = [int(c) for c in "1011010101"]
    p = q.monobit_test(bits)
    assert math.isclose(p, 0.527089, abs_tol=1e-6), p


def test_runs_matches_nist_example():
    # NIST SP 800-22 section 2.3.4: epsilon = 1001101011 -> P-value = 0.147232
    bits = [int(c) for c in "1001101011"]
    p = q.runs_test(bits)
    assert math.isclose(p, 0.147232, abs_tol=1e-6), p


def test_tests_catch_obviously_bad_streams():
    assert q.monobit_test([0] * 1000) < 1e-10           # all zeros
    alternating = [i % 2 for i in range(1000)]          # 0101... balanced but not random
    assert q.monobit_test(alternating) == 1.0
    assert q.runs_test(alternating) < 1e-10


def test_bits_to_bytes():
    assert q.bits_to_bytes([1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]) == b"\x81\xff"


# ---- Circuit and generator ----------------------------------------------
def test_circuit_shape():
    circuit = q.build_qrng_circuit(16)
    ops = [instruction.operation.name for instruction in circuit.data]
    assert circuit.num_qubits == 16 and ops.count("h") == 16 and ops.count("measure") == 16


def test_bits_are_binary_and_right_length():
    rng = q.QuantumRNG(seed=1)
    for n in (0, 1, 15, 16, 17, 5000):
        bits = rng.bits(n)
        assert len(bits) == n and set(bits) <= {0, 1}


def test_seed_reproducible_and_pseudo_random():
    # Same seed -> identical output. This is the proof that the SIMULATOR is
    # deterministic software, not a source of true randomness.
    assert q.QuantumRNG(seed=7).bits(3000) == q.QuantumRNG(seed=7).bits(3000)
    assert q.QuantumRNG(seed=7).bits(3000) != q.QuantumRNG(seed=8).bits(3000)


def test_refills_do_not_repeat():
    rng = q.QuantumRNG(num_qubits=4, batch_shots=8, seed=3)  # 32 bits per refill
    first, second = rng.bits(32), rng.bits(32)
    assert rng.circuits_run == 2 and first != second


def test_randint_range_and_uniformity():
    rng = q.QuantumRNG(seed=4)
    values = [rng.randint(10, 14) for _ in range(5000)]
    assert set(values) == {10, 11, 12, 13, 14}
    for v in range(10, 15):  # each ~0.2, sd ~0.0057; 0.03 is > 5 sigma
        assert abs(values.count(v) / 5000 - 0.2) < 0.03, v
    assert rng.randint(5, 5) == 5


def test_random_float_range_and_mean():
    rng = q.QuantumRNG(seed=5)
    xs = [rng.random() for _ in range(2000)]
    assert all(0.0 <= x < 1.0 for x in xs)
    assert abs(sum(xs) / len(xs) - 0.5) < 0.04  # sd of mean ~0.0065


def test_randbytes():
    data = q.QuantumRNG(seed=6).randbytes(32)
    assert isinstance(data, bytes) and len(data) == 32


def test_invalid_arguments():
    rng = q.QuantumRNG(seed=1)
    for bad in (lambda: rng.randint(5, 4), lambda: rng.bits(-1),
                lambda: q.build_qrng_circuit(0)):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


# ---- Statistical quality ------------------------------------------------
def test_fair_stream_passes_suite_seeded():
    results = q.run_test_suite(q.QuantumRNG(seed=11).bits(100_000))
    assert set(results) == {"monobit", "runs", "byte_chi_square"}
    assert all(r["passed"] for r in results.values()), results


def test_fair_stream_passes_unseeded_with_strict_alpha():
    bits = q.QuantumRNG().bits(50_000)
    assert q.monobit_test(bits) > 1e-6 and q.runs_test(bits) > 1e-6


def test_flawed_source_fails_and_extractor_fixes_it():
    biased = q.QuantumRNG(seed=12, p_one=0.7).bits(60_000)
    assert abs(sum(biased) / len(biased) - 0.7) < 0.02
    assert not q.run_test_suite(biased)["monobit"]["passed"]
    clean = q.von_neumann_extract(biased)
    # expected yield: 2 * 0.7 * 0.3 = 0.42 output bits per pair
    assert abs(len(clean) / (len(biased) / 2) - 0.42) < 0.02
    assert q.monobit_test(clean) >= q.ALPHA and q.runs_test(clean) >= q.ALPHA


def test_von_neumann_rules():
    assert q.von_neumann_extract([0, 1, 1, 0, 0, 0, 1, 1, 1]) == [0, 1]


def test_classical_baselines_pass():
    assert q.run_test_suite(q.classical_bits(50_000, seed=1))["monobit"]["passed"]
    assert len(q.secure_bits(13)) == 13


def test_plot_creates_png():
    fair = q.QuantumRNG(seed=1).bits(4096 * 3)
    biased = q.QuantumRNG(seed=2, p_one=0.7).bits(4096)
    with tempfile.TemporaryDirectory() as tmp:
        out = q.plot_results(fair, biased, Path(tmp) / "qrng.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    print(f"Project 03 tests (target: {TARGET})\n")
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
