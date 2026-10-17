"""Tests for Project 09: Quantum Teleportation and Superdense Coding.

Run it from the project folder, inside your activated virtual environment:
    python tests/test_quantum_teleport.py
    python -m pytest tests/             (optional, if you have pytest installed)

Which code gets tested (the header line tells you):
    1. the folder named in the QBC_TARGET environment variable, if it is set;
    2. otherwise solution/, if that folder exists here;
    3. otherwise starter/ (your code: the starter pack has no solution/).
Force your own code with QBC_TARGET=starter python tests/test_quantum_teleport.py
(Windows PowerShell: $env:QBC_TARGET="starter"; python tests/test_quantum_teleport.py).
"""

import math
import os
import random
import sys
import tempfile
from pathlib import Path

from qiskit.quantum_info import Statevector

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("QBC_TARGET") or (
    "solution" if (ROOT / "solution").is_dir() else "starter")
TARGET_DIR = (ROOT / TARGET).resolve()
if TARGET_DIR.parent != ROOT or not (TARGET_DIR / "quantum_teleport.py").is_file():
    sys.exit(f"QBC_TARGET={TARGET!r}: no quantum_teleport.py in a folder of that name next to "
             "tests/. Use starter or solution.")
sys.path.insert(0, str(TARGET_DIR))

import quantum_teleport as qt  # noqa: E402


def _states(n, seed):
    rng = random.Random(seed)
    return [qt.random_state(rng) for _ in range(n)]


def _close(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a, b))


# ---- Provided helpers and the classical baseline (Section 6) --------------
def test_random_states_cover_the_sphere():
    states = _states(4000, 1)
    assert all(0 <= t <= math.pi and 0 <= p < 2 * math.pi for t, p in states)
    assert _states(5, 2) == _states(5, 2)
    # Even spread: the average z is near 0 (sd 0.009 for 4,000 states).
    mean_z = sum(qt.bloch_vector(t, p)[2] for t, p in states) / len(states)
    assert abs(mean_z) < 0.05, mean_z


def test_fidelity_basics():
    theta, phi = 1.0, 2.0
    n = qt.bloch_vector(theta, phi)
    assert math.isclose(qt.fidelity(theta, phi, n), 1.0)
    assert abs(qt.fidelity(theta, phi, tuple(-c for c in n))) < 1e-12
    assert math.isclose(qt.fidelity(theta, phi, (0.0, 0.0, 0.0)), 0.5)
    assert qt.to_physical((2.0, 0.0, 0.0)) == (1.0, 0.0, 0.0)
    assert qt.to_physical((0.3, 0.0, 0.0)) == (0.3, 0.0, 0.0)


def test_classical_fax_hits_the_two_thirds_limit():
    # Average of (1 + cos^2 theta) / 2 over the sphere is exactly 2/3.
    # One state's fidelity has sd sqrt(1/18) = 0.236, so 3,000 states give
    # sd 0.0043, and 0.03 is about a 7-sigma band.
    fax = qt.classical_fax_average_fidelity(3000, seed=4)
    assert abs(fax - qt.CLASSICAL_LIMIT) < 0.03, fax


def test_tomography_converges_with_many_copies():
    # 40,000 copies per axis: sd at most 0.005 per component.
    theta, phi = 2.2, 0.4
    est = qt.estimate_bloch_classically(theta, phi, 40_000, seed=5)
    assert _close(est, qt.bloch_vector(theta, phi), 0.03), est


def test_copies_needed():
    assert qt.copies_needed(0.1) == 385
    assert qt.copies_needed(0.01) == 38_416
    assert qt.copies_needed(0.05, z=1.0) == 400
    # (1.96 / 0.00224) ** 2 is 765625.0000000002 in floating point: the guide's trap.
    assert qt.copies_needed(0.00224) == 765_625, "subtract 1e-9 before rounding up (Section 6)"


# ---- Quantum implementation (Section 7) ----------------------------------
def test_bell_pair_statevector():
    circuit = qt.bell_pair()
    assert circuit.num_qubits == 2
    amplitudes = Statevector(circuit).data
    assert _close(amplitudes, [1 / math.sqrt(2), 0, 0, 1 / math.sqrt(2)]), amplitudes


def test_bell_pair_outcomes_always_agree():
    circuit = qt.bell_pair()
    circuit.measure_all()
    counts = qt.run_counts(circuit, 1000, seed=3)
    assert set(counts) == {"00", "11"}, counts


def test_teleport_circuit_layout():
    circuit = qt.teleport_circuit(1.0, 2.0, "Z")
    assert circuit.num_qubits == 3
    assert [(r.name, r.size) for r in circuit.cregs] == [("alice", 2), ("bob", 1)], circuit.cregs
    ops = circuit.count_ops()
    assert ops.get("if_else") == 2, ops     # two classically controlled corrections
    assert ops.get("measure") == 3, ops     # two for Alice, one for Bob


def test_bob_expectation_reads_bobs_register():
    got = qt.bob_expectation({"0 01": 30, "1 10": 10})
    assert got == 0.5, f'{{"0 01": 30, "1 10": 10}} gave {got}, expected 0.5'
    got = qt.bob_expectation({"1 00": 5, "1 11": 5})
    assert got == -1.0, f'{{"1 00": 5, "1 11": 5}} gave {got}, expected -1.0 (Bob is first)'
    try:
        qt.bob_expectation({})
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty counts")


def test_basis_states_arrive_exactly():
    # |0> and |1> only need the X correction.
    for theta, expected in ((0.0, 1.0), (math.pi, -1.0)):
        counts = qt.run_counts(qt.teleport_circuit(theta, 0.0, "Z"), 400, seed=6)
        assert qt.bob_expectation(counts) == expected, (theta, counts)


def test_superposition_needs_the_z_correction():
    # |+>, |-> and |+i> are certain in X or Y, but only if Z is applied correctly.
    for phi, basis, expected in ((0.0, "X", 1.0), (math.pi, "X", -1.0), (math.pi / 2, "Y", 1.0)):
        counts = qt.run_counts(qt.teleport_circuit(math.pi / 2, phi, basis), 400, seed=7)
        assert qt.bob_expectation(counts) == expected, (phi, basis, counts)


def test_random_states_teleport_with_high_fidelity():
    # 2,000 shots per axis: sd of the fidelity estimate is at most 0.009,
    # so 0.95 is more than 5 sigma below a perfect 1.
    for k, (theta, phi) in enumerate(_states(5, 8)):
        bob = qt.teleport_bloch(theta, phi, 2000, seed=10 * k)
        assert qt.fidelity(theta, phi, bob) > 0.95, (theta, phi, bob)


def test_teleport_bloch_is_seeded_and_physical():
    assert qt.teleport_bloch(0.7, 1.3, 200, seed=9) == qt.teleport_bloch(0.7, 1.3, 200, seed=9)
    # The docstring's seeding rule: basis i (X=0, Y=1, Z=2) runs with seed + i.
    by_rule = qt.to_physical(tuple(
        qt.bob_expectation(qt.run_counts(qt.teleport_circuit(0.7, 1.3, basis), 200, 9 + i))
        for i, basis in enumerate(qt.BASES)))
    got = qt.teleport_bloch(0.7, 1.3, 200, seed=9)
    assert _close(got, by_rule), \
        f"{got} isn't {by_rule}: run basis i (X=0, Y=1, Z=2) with run_counts(..., seed + i)"
    try:
        qt.teleport_bloch(0.7, 1.3, 20)  # seed=None
    except TypeError as exc:
        raise AssertionError(f"with seed=None, run with no seed at all ({exc})") from None
    bob = qt.teleport_bloch(0.0, 0.0, 50, seed=4)  # raw estimate pokes outside the sphere
    assert math.sqrt(sum(c * c for c in bob)) <= 1 + 1e-12, bob


def test_deferred_version_is_exact():
    for theta, phi in _states(5, 11):
        rho = qt.exact_bob_state(qt.teleport_circuit_deferred(theta, phi, None))
        assert _close(qt.bloch_from_density(rho), qt.bloch_vector(theta, phi)), (theta, phi)


def test_deferred_version_measures_only_at_the_end():
    circuit = qt.teleport_circuit_deferred(1.0, 2.0, "X")
    names = [inst.operation.name for inst in circuit.data if inst.operation.name != "barrier"]
    assert "if_else" not in names, names
    first = names.index("measure")
    assert names[first:] == ["measure"] * 3, names
    theta, phi = 2.5, 5.0
    bob = qt.teleport_bloch(theta, phi, 2000, seed=12, deferred=True)
    assert qt.fidelity(theta, phi, bob) > 0.95, bob
    # The same seeding rule, on circuits with no mid-circuit measurement: Aer seeds
    # those once per run, so seed and seed + 1 give completely different shots.
    by_rule = qt.to_physical(tuple(
        qt.bob_expectation(qt.run_counts(qt.teleport_circuit_deferred(theta, phi, basis), 200, 12 + i))
        for i, basis in enumerate(qt.BASES)))
    got = qt.teleport_bloch(theta, phi, 200, seed=12, deferred=True)
    assert _close(got, by_rule), \
        f"{got} isn't {by_rule}: run basis i (X=0, Y=1, Z=2) with run_counts(..., seed + i)"


def test_superdense_encoding_uses_one_qubit():
    for message in qt.MESSAGES:
        assert qt.superdense_encode(message).num_qubits == 1, message


def test_superdense_sends_all_four_messages():
    for i, message in enumerate(qt.MESSAGES):
        counts = qt.superdense_send(message, 200, seed=i)
        assert counts == {message: 200}, (message, counts)


def test_bad_inputs_rejected():
    cases = (("superdense_encode('2')", lambda: qt.superdense_encode("2")),
             ("superdense_encode('101')", lambda: qt.superdense_encode("101")),
             ("teleport_circuit(..., basis='W')", lambda: qt.teleport_circuit(1.0, 1.0, "W")),
             ("teleport_bloch(..., shots=0)", lambda: qt.teleport_bloch(1.0, 1.0, 0)),
             ("copies_needed(0)", lambda: qt.copies_needed(0)))
    for label, bad in cases:
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError from {label}")


# ---- Visualising the result (Section 8) ----------------------------------
def test_decoding_matrix_is_the_identity():
    matrix = qt.decoding_matrix(300, seed=1)
    assert matrix == [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)], matrix


def test_plot_creates_png():
    batch = qt.teleport_batch(3, 200, seed=2)
    assert len(batch) == 3 and all(len(alice) == 3 and len(bob) == 3 for alice, bob in batch)
    # Alice's side: the states from random.Random(seed), in order (the docstring's rule).
    for (alice, _), (theta, phi) in zip(batch, _states(3, 2)):
        assert _close(alice, qt.bloch_vector(theta, phi)), \
            f"Alice's vector {alice} isn't state {(theta, phi)} from random.Random(seed)"
    # Bob's side: 200 shots per axis gives sd at most 0.029, so 0.85 is 5 sigma below 1.
    for alice, bob in batch:
        assert (1 + sum(a * b for a, b in zip(alice, bob))) / 2 > 0.85, (alice, bob)
    # ... and state k is teleported with teleport_bloch(..., seed=seed + 3 * k).
    for k, ((_, bob), (theta, phi)) in enumerate(zip(batch, _states(3, 2))):
        assert _close(bob, qt.teleport_bloch(theta, phi, 200, seed=2 + 3 * k)), \
            f"state {k}: teleport it with teleport_bloch(theta, phi, shots, seed + 3 * k)"
    with tempfile.TemporaryDirectory() as tmp:
        out = qt.plot_results(batch, qt.decoding_matrix(100, seed=2), Path(tmp) / "chart.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Testing and validation (Section 9) ----------------------------------
def test_no_signalling_bob_alone_is_a_blur():
    # Before Alice's two bits arrive, Bob's qubit is I/2 whatever she sent.
    for theta, phi in _states(5, 13):
        rho = qt.bob_state_before_bits(theta, phi).data
        assert _close(rho.flatten(), [0.5, 0, 0, 0.5]), (theta, phi, rho)


# ---- Extension, required (Section 11) ------------------------------------
def test_noise_lowers_fidelity_as_predicted():
    # Theory: 1 - p/2. 3 states x 1,000 shots: sd about 0.008, tolerance 0.05.
    curve = qt.fidelity_vs_noise([0.0, 0.5, 1.0], n_states=3, shots=1000, seed=14)
    assert [p for p, _, _ in curve] == [0.0, 0.5, 1.0]
    for p, simulated, theory in curve:
        assert math.isclose(theory, 1 - p / 2), (p, theory)
        assert abs(simulated - theory) < 0.05, (p, simulated)
    assert any(simulated != theory for _, simulated, theory in curve), \
        "the simulated values equal the theory exactly: average teleport_bloch's results instead"
    # The seeding rule: state k uses seed + 3 * k at every noise level.
    (_, simulated, _), = qt.fidelity_vs_noise([0.4], n_states=2, shots=200, seed=3)
    by_rule = sum(qt.fidelity(theta, phi, qt.teleport_bloch(theta, phi, 200, 3 + 3 * k, noise=0.4))
                  for k, (theta, phi) in enumerate(_states(2, 3))) / 2
    assert abs(simulated - by_rule) < 1e-9, \
        f"{simulated} isn't {by_rule}: teleport state k with teleport_bloch(..., seed + 3 * k, noise=p)"


def test_noisy_bell_pair_falls_below_the_classical_limit():
    ops = qt.teleport_circuit(1.0, 1.0, "Z", noise=0.3).count_ops()
    assert "quantum_channel" in ops, f"noise=0.3 added no noise (Section 11): {ops}"
    assert "quantum_channel" not in qt.teleport_circuit(1.0, 1.0, "Z").count_ops()
    try:
        qt.teleport_circuit(1.0, 1.0, "Z", noise=1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for noise=1.5")
    # p = 0.9 gives 0.55 in theory: worse than the classical fax.
    (_, simulated, _), = qt.fidelity_vs_noise([0.9], n_states=3, shots=1000, seed=15)
    assert simulated < qt.CLASSICAL_LIMIT, simulated


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    print(f"Project 09 tests (target: {TARGET})\n")
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
