"""Tests for Project 07: Quantum Key Distribution (BB84) (the 19 core tests).

Run it from the project folder, inside your activated virtual environment:
    python tests/test_quantum_bb84.py
    python -m pytest tests/             (optional, if you have pytest installed)

Which code gets tested:
    1. the folder named in the QBC_TARGET environment variable, if it is set;
    2. otherwise solution/, if that folder exists here;
    3. otherwise starter/ (your code: the starter pack has no solution/).
Force your own code with QBC_TARGET=starter python tests/test_quantum_bb84.py
(Windows PowerShell: $env:QBC_TARGET="starter"; python tests/test_quantum_bb84.py).

Extra exact-physics tests live in tests/test_bonus.py, so this count stays 19.
"""

import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("QBC_TARGET") or ("solution" if (ROOT / "solution").is_dir() else "starter")
TARGET_DIR = (ROOT / TARGET).resolve()
if TARGET_DIR.parent != ROOT or not (TARGET_DIR / "quantum_bb84.py").is_file():
    sys.exit(f"QBC_TARGET={TARGET!r}: no quantum_bb84.py in a folder of that name next to tests/. "
             "Use starter or solution.")
sys.path.insert(0, str(TARGET_DIR))

import quantum_bb84 as bb  # noqa: E402


def sifted_keys(run):
    idx = bb.sift_indices(run["alice_bases"], run["bob_bases"])
    return [run["alice_bits"][i] for i in idx], [run["bob_results"][i] for i in idx]


# ---- Circuit structure ---------------------------------------------------
def test_alice_encodes_bit_then_basis():
    ops = [d.operation.name for d in bb.build_bb84_circuit([1], [bb.X_BASIS], [bb.Z_BASIS]).data]
    assert ops == ["x", "h", "measure"], ops     # |-> sent, Bob measures in Z


def test_bob_measures_in_his_basis():
    ops = [d.operation.name for d in bb.build_bb84_circuit([0], [bb.Z_BASIS], [bb.X_BASIS]).data]
    assert ops == ["h", "measure"], ops


def test_eve_measures_and_resends():
    circuit = bb.build_bb84_circuit([0], [bb.Z_BASIS], [bb.Z_BASIS], eve_bases=[bb.X_BASIS])
    ops = [d.operation.name for d in circuit.data]
    assert ops == ["h", "measure", "h", "measure"], ops
    assert [reg.name for reg in circuit.cregs] == ["eve", "bob"]


def test_mismatched_lengths_rejected():
    try:
        bb.build_bb84_circuit([0, 1], [0], [0, 1])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ---- Physics without Eve --------------------------------------------------
def test_matching_bases_give_identical_bits():
    # No Eve, no noise: whenever bases match, Bob's bit equals Alice's. Always.
    for seed in (1, 2, 3):
        alice_key, bob_key = sifted_keys(bb.run_bb84(500, seed=seed))
        assert alice_key == bob_key


def test_mismatched_bases_give_coin_flips():
    # ~1,000 mismatched positions: sd of the agreement rate is ~0.016.
    run = bb.run_bb84(2000)
    idx = [i for i in range(2000) if run["alice_bases"][i] != run["bob_bases"][i]]
    agree = sum(run["alice_bits"][i] == run["bob_results"][i] for i in idx) / len(idx)
    assert abs(agree - 0.5) < 0.08, agree


def test_about_half_the_bits_survive_sifting():
    # 4,000 qubits: sd of the sifted fraction is 0.0079, tolerance 0.04 (5 sigma).
    run = bb.run_bb84(4000)
    fraction = len(bb.sift_indices(run["alice_bases"], run["bob_bases"])) / 4000
    assert abs(fraction - 0.5) < 0.04, fraction


def test_protocol_without_eve_keeps_a_shared_key():
    result = bb.bb84_protocol(2000, seed=5)
    assert not result["aborted"]
    assert result["qber_sample"] == 0.0
    assert result["alice_final"] == result["bob_final"]
    assert len(result["alice_final"]) == result["sifted_bits"] - result["sample_bits"]


# ---- Eve -----------------------------------------------------------------
def test_eve_causes_25_percent_qber_seeded():
    # ~2,000 sifted bits: sd = sqrt(0.25 * 0.75 / 2000) = 0.0097; tolerance 0.05.
    alice_key, bob_key = sifted_keys(bb.run_bb84(4000, seed=11, eve_fraction=1.0))
    assert abs(bb.qber(alice_key, bob_key) - 0.25) < 0.05


def test_eve_causes_25_percent_qber_unseeded():
    alice_key, bob_key = sifted_keys(bb.run_bb84(4000, eve_fraction=1.0))
    assert abs(bb.qber(alice_key, bob_key) - 0.25) < 0.05


def test_eve_is_detected_and_protocol_aborts():
    # Sample of ~500 bits: sd 0.019, so 0.25 vs the 0.11 threshold is >7 sigma.
    result = bb.bb84_protocol(4000, eve_fraction=1.0)
    assert result["aborted"], result["qber_sample"]
    assert result["alice_final"] == [] and result["bob_final"] == []


def test_eve_learns_about_three_quarters():
    # Right basis (1/2): she knows the bit. Wrong basis (1/2): a coin flip.
    result = bb.bb84_protocol(4000, eve_fraction=1.0)
    assert abs(result["eve_agreement"] - 0.75) < 0.05, result["eve_agreement"]


def test_partial_interception_scales_qber():
    # Eve intercepts 40%: QBER = 0.4 / 4 = 0.10. ~3,000 sifted bits: sd 0.0055.
    alice_key, bob_key = sifted_keys(bb.run_bb84(6000, eve_fraction=0.4))
    assert abs(bb.qber(alice_key, bob_key) - 0.10) < 0.03


# ---- Noise and classical comparison ---------------------------------------
def test_channel_noise_gives_two_thirds_p():
    # A random Pauli with probability p flips the bit in 2 of 3 cases: QBER = 2p/3.
    alice_key, bob_key = sifted_keys(bb.run_bb84(4000, noise=0.06))
    assert abs(bb.qber(alice_key, bob_key) - 0.04) < 0.02


def test_classical_eavesdropper_is_invisible():
    result = bb.classical_exchange(1000, seed=1, eavesdrop=True)
    assert bb.qber(result["alice_key"], result["bob_key"]) == 0.0
    assert result["eve_copy"] == result["alice_key"]


# ---- Helpers -------------------------------------------------------------
def test_sift_and_qber_helpers():
    assert bb.sift_indices([0, 1, 1, 0], [0, 0, 1, 1]) == [0, 2]
    assert bb.qber([0, 1, 1, 0], [0, 1, 0, 0]) == 0.25


def test_estimate_qber_discards_the_sample():
    alice = [random.Random(1).randint(0, 1) for _ in range(100)]
    bob = list(alice)
    bob[0] ^= 1
    estimate, a_rest, b_rest = bb.estimate_qber(alice, bob, 25, random.Random(2))
    assert len(a_rest) == len(b_rest) == 75
    assert 0.0 <= estimate <= 1 / 25 + 1e-12


def test_same_seed_is_repeatable():
    assert bb.run_bb84(300, seed=4, eve_fraction=1.0) == bb.run_bb84(300, seed=4, eve_fraction=1.0)


def test_plot_creates_png():
    scenarios = {"Quantum, no Eve": 0.0, "Quantum, Eve": 0.25, "Classical, Eve": 0.0}
    with tempfile.TemporaryDirectory() as tmp:
        out = bb.plot_results(scenarios, [(0.0, 0.0), (1.0, 0.25)], Path(tmp) / "chart.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    print(f"Project 07 tests (target: {TARGET})\n")
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
