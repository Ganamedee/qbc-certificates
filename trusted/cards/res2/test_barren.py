import csv
import sys
from pathlib import Path

import numpy as np
from qiskit.quantum_info import SparsePauliOp, Statevector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from barren import gradient_samples, random_circuit  # noqa: E402


def load(deep):
    with open(ROOT / "variances.csv") as f:
        rows = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(f)]
    rows = [r for r in rows if (r["layers"] >= 100 if deep else r["layers"] <= 3)]
    return {int(r["qubits"]): r for r in rows}


def test_circuit_shape():
    rng = np.random.default_rng(0)
    qc = random_circuit(4, 5, rng.choice(["rx", "ry", "rz"], 20), rng.uniform(0, 6, 20))
    ops = qc.count_ops()
    assert ops["cz"] == 3 * 5
    assert sum(ops.get(g, 0) for g in ["rx", "ry", "rz"]) == 4 + 4 * 5  # 4 are the RY(pi/4) layer


def test_parameter_shift_matches_finite_difference():
    n, layers, h = 3, 4, 1e-5
    rng = np.random.default_rng(1)
    gates = rng.choice(["rx", "ry", "rz"], n * layers)
    angles = rng.uniform(0, 2 * np.pi, n * layers)
    cost = SparsePauliOp("I" * (n - 2) + "ZZ")

    def energy(a):
        return Statevector(random_circuit(n, layers, gates, a)).expectation_value(cost).real

    up, down = angles.copy(), angles.copy()
    up[0] += h
    down[0] -= h
    finite_difference = (energy(up) - energy(down)) / (2 * h)
    shifted = gradient_samples(n, layers, 1, np.random.default_rng(1))[0]
    assert abs(shifted - finite_difference) < 1e-6


def test_deep_run_is_complete():
    deep = load(deep=True)
    assert set(deep) >= {2, 4, 6, 8, 10}
    assert all(r["samples"] >= 200 for r in deep.values())


def test_deep_variance_at_two_qubits():
    assert 0.05 < load(deep=True)[2]["variance"] < 0.13


def test_barren_plateau_appears():
    deep = load(deep=True)
    assert deep[2]["variance"] / deep[10]["variance"] > 50
    qubits = sorted(deep)
    slope = np.polyfit(qubits, np.log([deep[n]["variance"] for n in qubits]), 1)[0]
    assert -0.9 < slope < -0.5  # McClean et al. fitted -0.694


def test_shallow_control_stays_flat():
    shallow = load(deep=False)
    assert 0.5 < shallow[2]["variance"] / shallow[10]["variance"] < 2


def test_figure_exists():
    assert (ROOT / "variance_vs_qubits.png").exists()
