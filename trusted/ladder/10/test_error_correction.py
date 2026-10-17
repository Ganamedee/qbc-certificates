"""Tests for Project 10: Quantum Error Correction, beat the noise.

Works with plain Python OR pytest (run from the project folder):
    python tests/test_error_correction.py
    python -m pytest tests/             (only if you have pytest installed)

Which code gets tested (the header line tells you):
    1. the folder named in the QBC_TARGET environment variable, if set
       (macOS/Linux: QBC_TARGET=starter python tests/test_error_correction.py
        PowerShell:  $env:QBC_TARGET="starter"; python tests/test_error_correction.py)
    2. otherwise solution/, if that folder exists
    3. otherwise starter/ (your code: the usual case in your own repo)
"""

import math
import os
import sys
import tempfile
from pathlib import Path

from qiskit import QuantumCircuit
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

import error_correction as qec  # noqa: E402


def _read_back(code, state, inject, shots=50):
    """Run the memory circuit with no random noise; return the result counts."""
    circuit = qec.build_memory_circuit(code, state, inject)
    return qec.register_counts(qec.run_counts(circuit, shots, seed=1), circuit, "result")


def _expected(state):
    return "1" if state in ("1", "-") else "0"


# ---- Classical baseline ----------------------------------------------------
def test_repetition_theory_formula():
    for p in (0.0, 0.05, 0.1, 0.3, 0.5, 1.0):
        assert math.isclose(qec.repetition_theory(p), 3 * p**2 - 2 * p**3, abs_tol=1e-12), p
    assert math.isclose(qec.repetition_theory(0.2, 1), 0.2)
    assert math.isclose(qec.repetition_theory(0.1, 5), 10e-3 - 15e-4 + 6e-5)


def test_classical_baseline_matches_theory():
    # 100,000 trials at p = 0.1: theory 0.028, sd 0.00052, so 0.003 is a 5.7-sigma band.
    rate = qec.classical_repetition(0.1, 100_000, seed=1)
    assert abs(rate - 0.028) < 0.003, rate


def test_bad_inputs_rejected():
    for bad in (lambda: qec.make_noise_model(1.5), lambda: qec.make_noise_model(0.1, "y"),
                lambda: qec.build_memory_circuit("steane"), lambda: qec.build_memory_circuit(state="2"),
                lambda: qec.build_memory_circuit(distance=4), lambda: qec.build_memory_circuit(inject=["x3"]),
                lambda: qec.logical_error_rate("bit_flip", "x", 0.1, shots=0)):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


# ---- Steps 1 to 3: encoder, syndrome, decoder ------------------------------
def test_bit_flip_encoder_spreads_a_superposition():
    circuit = QuantumCircuit(3)
    circuit.h(0)
    qec.add_encoder(circuit, circuit.qubits, "bit_flip")
    amplitudes = Statevector(circuit).data
    for index, amp in enumerate(amplitudes):
        expected = 1 / math.sqrt(2) if index in (0, 7) else 0.0
        assert abs(amp - expected) < 1e-9, (index, amp)


def test_syndrome_table_for_every_single_x_error():
    table = {(): "00", ("x0",): "01", ("x1",): "11", ("x2",): "10"}
    for inject, syndrome in table.items():
        assert qec.measure_syndrome_of(list(inject)) == syndrome, (inject, syndrome)


def test_decoder_points_at_the_flipped_qubit():
    assert qec.DECODER.get(qec.measure_syndrome_of([]), []) == []
    for q in range(3):
        assert qec.DECODER[qec.measure_syndrome_of([f"x{q}"])] == [q], q


# ---- Step 4: the memory circuit --------------------------------------------
def test_memory_circuit_structure():
    circuit = qec.build_memory_circuit("bit_flip")
    sizes = {reg.name: reg.size for reg in circuit.qregs + circuit.cregs}
    assert sizes == {"data": 3, "ancilla": 2, "syndrome": 2, "result": 1}, sizes
    ops = circuit.count_ops()
    # 2 encoder + 4 parity checks + 2 decoder CX, one id per data qubit,
    # one if_test per syndrome that needs a fix.
    assert (ops.get("cx"), ops.get("id"), ops.get("if_else"), ops.get("measure")) == (8, 3, 3, 3), ops
    bare = qec.build_memory_circuit("none")
    assert bare.num_qubits == 1 and bare.count_ops().get("id") == 1 and "cx" not in bare.count_ops()


def test_no_errors_no_change():
    for code in ("none", "bit_flip"):
        for state in qec.STATES:
            assert _read_back(code, state, []) == {_expected(state): 50}, (code, state)


def test_every_single_x_error_is_corrected():
    for state in ("0", "1"):
        for q in range(3):
            assert _read_back("bit_flip", state, [f"x{q}"]) == {_expected(state): 50}, (state, q)


def test_two_x_errors_fool_the_decoder():
    # Two flips give the syndrome of one flip on the third qubit. The decoder
    # "fixes" that qubit, all three are now flipped: a logical error, every time.
    for a, b, other in ((0, 1, 2), (0, 2, 1), (1, 2, 0)):
        inject = [f"x{a}", f"x{b}"]
        assert qec.measure_syndrome_of(inject) == qec.measure_syndrome_of([f"x{other}"])
        assert _read_back("bit_flip", "0", inject) == {"1": 50}, inject


def test_bit_flip_code_cannot_see_phase_flips():
    # A Z error leaves every parity alone (syndrome 00) but flips + to -.
    for q in range(3):
        assert qec.measure_syndrome_of([f"z{q}"]) == "00", q
        assert _read_back("bit_flip", "+", [f"z{q}"]) == {"1": 50}, q


# ---- Step 5: noise -----------------------------------------------------------
def test_noise_survives_the_transpiler():
    # p = 1 flips the unprotected qubit every time, so the id gates must still be there.
    for state in ("0", "1"):
        assert qec.logical_error_rate("none", "x", 1.0, shots=100, seed=1, state=state) == 1.0, state
        assert qec.logical_error_rate("none", "x", 0.0, shots=100, seed=1, state=state) == 0.0, state


def test_unencoded_qubit_error_rate_is_p():
    # sd = sqrt(0.1 * 0.9 / 4000) = 0.0047, so 0.025 is a 5-sigma band.
    rate = qec.logical_error_rate("none", "x", 0.1, shots=4000, seed=3)
    assert abs(rate - 0.1) < 0.025, rate


def test_logical_below_physical_at_p_005():
    # Theory 3p^2 - 2p^3 = 0.00725. sd with 4,000 shots = 0.00134, so 0.0067 is 5 sigma.
    rate = qec.logical_error_rate("bit_flip", "x", 0.05, shots=4000, seed=11)
    assert rate < 0.05, rate
    assert abs(rate - 0.00725) < 0.0067, rate


def test_same_seed_same_result():
    first = qec.logical_error_rate("bit_flip", "x", 0.2, shots=500, seed=5)
    assert first == qec.logical_error_rate("bit_flip", "x", 0.2, shots=500, seed=5)


# ---- Step 6: the phase-flip code ---------------------------------------------
def test_phase_flip_encoder_makes_plus_and_minus_states():
    for prepare_one, sign in ((False, 1), (True, -1)):
        circuit = QuantumCircuit(3)
        if prepare_one:
            circuit.x(0)
        qec.add_encoder(circuit, circuit.qubits, "phase_flip")
        for index, amp in enumerate(Statevector(circuit).data):
            expected = (sign ** bin(index).count("1")) / math.sqrt(8)   # |+++> or |--->
            assert abs(amp - expected) < 1e-9, (prepare_one, index, amp)


def test_phase_flip_code_corrects_every_single_z_error():
    table = {(): "00", ("z0",): "01", ("z1",): "11", ("z2",): "10"}
    for inject, syndrome in table.items():
        assert qec.measure_syndrome_of(list(inject), "phase_flip") == syndrome, inject
    for state in ("0", "1"):
        for q in range(3):
            assert _read_back("phase_flip", state, [f"z{q}"]) == {_expected(state): 50}, (state, q)


def test_phase_flip_beats_z_noise():
    rate = qec.logical_error_rate("phase_flip", "z", 0.05, shots=4000, seed=11)
    assert rate < 0.05, rate
    assert abs(rate - 0.00725) < 0.0067, rate


# ---- Section 8: the sweep and the break-even point ---------------------------
def test_sweep_follows_the_seeding_rule():
    # A bare qubit, because Aer seeds dynamic circuits shot by shot (shot k gets
    # seed + k), so seeds 5 and 6 give the bit-flip code almost the same shots.
    points = qec.sweep("none", "x", (0.1, 0.3), shots=300, seed=5)
    assert points == [(0.1, qec.logical_error_rate("none", "x", 0.1, 300, 5)),
                      (0.3, qec.logical_error_rate("none", "x", 0.3, 300, 6))], points
    # The other arguments go straight through: the blind-spot curve stores +.
    plus = qec.sweep("bit_flip", "z", (0.2,), shots=200, seed=5, state="+")
    assert plus == [(0.2, qec.logical_error_rate("bit_flip", "z", 0.2, 200, 5, "+"))], plus
    assert [p for p, _ in qec.sweep("none", "x", (0.1, 0.3), shots=50)] == [0.1, 0.3]


def test_find_break_even():
    theory = [(p, 3 * p**2 - 2 * p**3) for p in qec.P_VALUES]
    assert abs(qec.find_break_even(theory) - 0.5) < 1e-9
    assert abs(qec.find_break_even([(0.1, 0.05), (0.3, 0.35)]) - 0.2) < 1e-9
    assert qec.find_break_even([(0.1, 0.01), (0.2, 0.05)]) is None      # wins everywhere
    assert qec.find_break_even([(0.1, 0.2), (0.2, 0.3)]) is None        # loses everywhere


def test_plot_creates_png():
    curves = {"bit_flip": qec.sweep("bit_flip", "x", (0.1, 0.2), shots=200, seed=9)}
    with tempfile.TemporaryDirectory() as tmp:
        out = qec.plot_results(curves, Path(tmp) / "chart.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Section 11, required: noisy gates ---------------------------------------
def test_noisy_gates_break_the_code():
    assert "cx" in qec.make_noise_model(0.05, "x", gate_noise=1.0).noise_instructions
    # Every CX as noisy as the storage: a single faulty gate is enough to fail.
    rate = qec.logical_error_rate("bit_flip", "x", 0.05, shots=4000, seed=11, gate_noise=1.0)
    assert rate > 0.05, rate


def test_better_gates_win_again():
    # Gates 10 times cleaner than the storage: the code wins again, but less
    # than with perfect gates (theory 0.00725).
    rate = qec.logical_error_rate("bit_flip", "x", 0.05, shots=4000, seed=11, gate_noise=0.1)
    assert 0.00725 < rate < 0.05, rate


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    print(f"Project 10 tests (target: {TARGET})")
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
