"""Tests for Project 08: Break RSA in miniature (Shor's algorithm).

Works with plain Python OR pytest (run from the project folder):
    python tests/test_quantum_shor.py
    python -m pytest tests/             (only if you have pytest installed)

Which code gets tested (the header line tells you):
    1. the folder named in the QBC_TARGET environment variable, if set
       (macOS/Linux: QBC_TARGET=starter python tests/test_quantum_shor.py
        PowerShell:  $env:QBC_TARGET="starter"; python tests/test_quantum_shor.py)
    2. otherwise solution/, if that folder exists
    3. otherwise starter/ (your code: the usual case in your own repo)

The tests are grouped by the guide's sections, so you can see which ones your
current step should turn green.
"""

import math
import os
import random
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
from qiskit.circuit.library import QFTGate
from qiskit.quantum_info import Operator

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

import quantum_shor as qs  # noqa: E402
import toy_rsa as rsa  # noqa: E402

BASES_15 = [2, 4, 7, 8, 11, 13, 14]


def _raises(error, fn, *args):
    try:
        fn(*args)
    except error:
        return
    except Exception as other:  # the wrong kind of error is a failure too
        raise AssertionError(f"expected {error.__name__}, got {type(other).__name__}: {other}")
    raise AssertionError(f"expected {error.__name__} for {fn.__name__}{args}")


# ---- Section 6: toy RSA and the classical attacks ---------------------------
def test_keypair_textbook_examples():
    assert rsa.make_keypair(61, 53, 17) == (3233, 17, 2753)
    assert rsa.make_keypair(3, 5, 3) == (15, 3, 3)


def test_keypair_rejects_bad_input():
    _raises(ValueError, rsa.make_keypair, 61, 61, 17)   # p == q
    _raises(ValueError, rsa.make_keypair, 60, 53, 17)   # 60 is not prime
    _raises(ValueError, rsa.make_keypair, 61, 53, 30)   # gcd(30, 3120) > 1
    _raises(ValueError, rsa.make_keypair, 3, 5, 9)      # e must be below phi = 8


def test_rsa_round_trip():
    for p, q, e in ((61, 53, 17), (3, 5, 3), (3, 7, 5)):
        n, e, d = rsa.make_keypair(p, q, e)
        for text in ("Meet me at the lab at 7", "Qubits ⚛ and émojis", ""):
            blocks = rsa.text_to_blocks(text, n)
            secret = rsa.encrypt(blocks, n, e)
            assert rsa.decrypt(secret, n, d) == blocks, (n, text)
            assert rsa.blocks_to_text(rsa.decrypt(secret, n, d), n) == text
    blocks = rsa.text_to_blocks("Meet me at the lab at 7", 3233)
    assert rsa.encrypt(blocks, 3233, 17) != blocks  # it really scrambles
    _raises(ValueError, rsa.encrypt, [3233], 3233, 17)  # blocks must be below n


def test_trial_division():
    assert rsa.trial_division(15) == (3, 2)
    assert rsa.trial_division(3233) == (53, 52)
    assert rsa.trial_division(97) == (97, 8)  # prime: no divisor up to sqrt(97)
    _raises(ValueError, rsa.trial_division, 1)


def test_classical_order():
    assert rsa.classical_order(7, 15) == 4
    assert rsa.classical_order(14, 15) == 2
    assert rsa.classical_order(2, 21) == 6
    assert rsa.classical_order(4, 21) == 3
    assert rsa.classical_order(3, 3233) == 260
    _raises(ValueError, rsa.classical_order, 3, 15)  # shares the factor 3


def test_factor_from_order_success():
    assert rsa.factor_from_order(7, 4, 15) == (3, 5)
    assert rsa.factor_from_order(11, 2, 15) == (3, 5)
    assert rsa.factor_from_order(2, 6, 21) == (3, 7)
    assert rsa.factor_from_order(3, 260, 3233) == (53, 61)


def test_factor_from_order_odd_r_fails():
    _raises(rsa.OrderFailure, rsa.factor_from_order, 4, 3, 21)  # r = 3 is odd


def test_factor_from_order_minus_one_trap():
    # a^(r/2) = -1 (mod n): the gcds only give 1 and n
    _raises(rsa.OrderFailure, rsa.factor_from_order, 14, 2, 15)
    _raises(rsa.OrderFailure, rsa.factor_from_order, 2, 780, 3233)


def test_factor_from_order_rejects_wrong_r():
    _raises(ValueError, rsa.factor_from_order, 7, 3, 15)        # 7^3 mod 15 != 1
    _raises(rsa.OrderFailure, rsa.factor_from_order, 4, 4, 15)  # a period, not the smallest
    assert not issubclass(rsa.OrderFailure, ValueError)


# ---- Section 7: the quantum part ------------------------------------------
def test_mult_mod15_every_base():
    for a in BASES_15:
        circuit = qs.mult_mod15(a)
        assert circuit.num_qubits == 4, a
        matrix = Operator(circuit).data  # column y = where |y> goes
        for y in range(1, 15):
            assert abs(matrix[a * y % 15, y]) > 0.999, (a, y)
    _raises(ValueError, qs.mult_mod15, 5)


def test_inverse_qft_matches_qiskit():
    for n in range(1, 6):
        assert Operator(qs.inverse_qft(n)).equiv(Operator(QFTGate(n).inverse())), n


def test_order_finding_circuit_shape():
    circuit = qs.order_finding_circuit(7)
    assert circuit.num_qubits == 12 and circuit.num_clbits == 8
    assert circuit.count_ops().get("measure") == 8
    _raises(ValueError, qs.order_finding_circuit, 5)   # shares a factor with 15
    _raises(ValueError, qs.order_finding_circuit, 1)   # 1 has order 1: useless


def test_phase_peaks_land_on_quarters():
    # a = 7 has r = 4: the phase must be 0, 1/4, 2/4 or 3/4, a quarter each.
    # 2,000 shots: sd of each fraction is about 0.0097, so 0.05 is a 5-sigma band.
    counts = qs.run_order_finding(7, 15, shots=2000, seed=1)
    assert set(counts) <= {0, 64, 128, 192}, sorted(counts)
    for m in (0, 64, 128, 192):
        assert abs(counts.get(m, 0) / 2000 - 0.25) < 0.05, (m, counts)
    # The same seed must give the same counts (pass seed_simulator=seed to Aer).
    assert qs.run_order_finding(7, 15, shots=2000, seed=1) == counts, "same seed, different counts"


def test_phase_peaks_land_on_halves():
    counts = qs.run_order_finding(4, 15, shots=1000, seed=2)  # r = 2
    assert set(counts) <= {0, 128}, sorted(counts)


def test_continued_fraction():
    assert qs.continued_fraction(11, 16) == [0, 1, 2, 5]
    assert qs.continued_fraction(64, 256) == [0, 4]
    assert qs.continued_fraction(0, 256) == [0]
    assert qs.continued_fraction(355, 113) == [3, 7, 16]
    _raises(ValueError, qs.continued_fraction, 1, 0)


def test_convergents():
    assert qs.convergents([0, 1, 2, 5]) == [(0, 1), (1, 1), (2, 3), (11, 16)]
    assert qs.convergents([3, 7, 16]) == [(3, 1), (22, 7), (355, 113)]
    rng = random.Random(8)
    for _ in range(200):  # the last convergent is always the fraction itself
        den = rng.randrange(1, 5000)
        num = rng.randrange(0, 3 * den)
        p, q = qs.convergents(qs.continued_fraction(num, den))[-1]
        assert Fraction(p, q) == Fraction(num, den), (num, den)


def test_candidate_order():
    assert qs.candidate_order(64, 8, 7, 15) == 4
    assert qs.candidate_order(192, 8, 7, 15) == 4
    assert qs.candidate_order(128, 8, 7, 15) is None  # 2/4 = 1/2 hides r = 4
    assert qs.candidate_order(0, 8, 7, 15) is None
    assert qs.candidate_order(128, 8, 4, 15) == 2
    assert qs.candidate_order(171, 10, 2, 21) == 6    # 171/1024 is close to 1/6
    assert qs.candidate_order(1, 8, 7, 15) is None    # 1/256: stop once q reaches N


def test_order_found_for_every_base():
    # Theory: half the shots give r for every base of 15 (s = 1 or 3 of 4, or 1 of 2).
    # 400 shots: sd 0.025, so 0.4 to 0.6 is a 4-sigma band.
    for a in BASES_15:
        counts = qs.run_order_finding(a, 15, shots=400, seed=100 + a)
        true_r = rsa.classical_order(a, 15)
        hits = sum(c for m, c in counts.items() if qs.candidate_order(m, 8, a, 15) == true_r)
        assert 0.4 <= hits / 400 <= 0.6, (a, hits)


def test_shor_factor_15():
    for seed in range(1, 6):  # seed 5 also meets the -1 trap (a = 14) on the way
        factors, log = qs.shor_factor(15, seed=seed)
        assert factors == (3, 5), (seed, factors)
        assert log and all(isinstance(line, str) for line in log)
        assert log[-1].endswith("so 15 = 3 x 5"), log[-1]
    # Same seed, same attempts: every random choice must come from the seeded rng.
    assert qs.shor_factor(15, seed=5) == (factors, log), "same seed, different attempts"


def test_shor_factor_rejects_bad_n():
    for bad in (16, 13, 25, 27):  # even, prime, prime powers: Shor needs odd p * q
        _raises(ValueError, qs.shor_factor, bad)


def test_crack_rsa_payoff():
    n, e, d = rsa.make_keypair(3, 5, 3)
    secret = rsa.encrypt(rsa.text_to_blocks("Quantum wins!", n), n, e)
    found_d, text, log = qs.crack_rsa(n, e, secret, seed=3)
    assert (found_d, text) == (d, "Quantum wins!")
    # Alice's message: no spoilers here, just proof that you decrypted it right.
    _, text, _ = qs.crack_rsa(*qs.ALICE_PUBLIC_KEY, qs.INTERCEPTED, seed=4)
    assert rsa.encrypt(rsa.text_to_blocks(text, 15), 15, 3) == qs.INTERCEPTED
    # For n = 15, d always equals e, so check the key rebuild on 3233 (d = 2753) too.
    # 3233 needs far too many qubits, so a stand-in shor_factor hands over the factors.
    real_shor = qs.shor_factor
    qs.shor_factor = lambda N, seed=None, *args, **kwargs: ((53, 61), ["stand-in"])
    try:
        n, e, d = rsa.make_keypair(61, 53, 17)
        secret = rsa.encrypt(rsa.text_to_blocks("d is not e here", n), n, e)
        found_d, text, _ = qs.crack_rsa(n, e, secret)
    finally:
        qs.shor_factor = real_shor
    assert (found_d, text) == (2753, "d is not e here"), (found_d, text)


# ---- Section 8: success rate per base ---------------------------------------
def test_success_by_base():
    rates = qs.success_by_base(15, shots=400, seed=5)
    assert list(rates) == BASES_15
    for a, (right_r, factors) in rates.items():
        assert 0.4 <= right_r <= 0.6, (a, right_r)
        if a == 14:  # 14 = -1 (mod 15): r = 2 but 14^1 = -1, the trap
            assert factors == 0, factors
        else:
            assert math.isclose(factors, right_r), (a, factors, right_r)
    # Two made-up shots for N = 21 (a stand-in replaces the circuit): m = 171 gives
    # q = 6 and m = 341 gives q = 3. Only the TRUE order counts as "right r", while
    # "factors" counts any r that factor_from_order accepts.
    real_run = qs.run_order_finding
    qs.run_order_finding = lambda a, N=15, shots=1024, seed=None, n_count=None: {171: 1, 341: 1}
    try:
        rates = qs.success_by_base(21, shots=2, seed=5)
    finally:
        qs.run_order_finding = real_run
    assert (rates[2], rates[4], rates[8]) == ((0.5, 0.5), (0.5, 0.0), (0.0, 0.5)), rates


def test_plot_creates_png():
    counts = qs.run_order_finding(7, 15, shots=200, seed=9)
    rates = qs.success_by_base(15, shots=100, seed=9)
    with tempfile.TemporaryDirectory() as tmp:
        for name, r in (("full.png", rates), ("histogram_only.png", None)):
            out = qs.plot_results(counts, 7, 15, 8, r, Path(tmp) / name)
            assert out.exists() and out.stat().st_size > 1000


# ---- Section 11 (required): factor 21 with the general multiplier ----------
def test_general_multiplier_21():
    matrix = Operator(qs.controlled_mult_mod_n(2, 21)).data
    assert matrix.shape == (64, 64)
    assert np.abs(matrix @ matrix.conj().T - np.eye(64)).max() < 1e-9  # unitary
    for y in range(32):
        out = 2 * y % 21 if y < 21 else y
        assert abs(matrix[2 * out + 1, 2 * y + 1]) > 0.999, ("control on", y)
        assert abs(matrix[2 * y, 2 * y]) > 0.999, ("control off", y)
    _raises(ValueError, qs.controlled_mult_mod_n, 7, 21)


def test_order_finding_21():
    # r = 6 is not a power of 2, so the peaks blur. Simulated: about 31% of shots give r.
    # 600 shots: sd about 0.019, so 0.2 to 0.45 is a wide, safe band.
    counts = qs.run_order_finding(2, 21, shots=600, seed=3)
    hits = sum(c for m, c in counts.items() if qs.candidate_order(m, 10, 2, 21) == 6)
    assert 0.2 <= hits / 600 <= 0.45, hits


def test_shor_factor_21():
    for seed in (3, 4):
        factors, _ = qs.shor_factor(21, seed=seed, max_attempts=60)
        assert factors == (3, 7), (seed, factors)


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    print(f"Project 08 tests (target: {TARGET})")
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
