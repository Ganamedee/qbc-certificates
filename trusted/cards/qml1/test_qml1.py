import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data import load_data  # noqa: E402
from quantum import QC, Scaler, model, model_shots  # noqa: E402


def results():
    with open(ROOT / "results.csv") as f:
        return list(csv.DictReader(f))


def test_split_is_fixed_and_disjoint():
    _, _, train1, test1 = load_data(3)
    _, _, train2, test2 = load_data(3)
    assert np.array_equal(train1, train2) and np.array_equal(test1, test2)
    assert not set(train1) & set(test1)


def test_scaler_learns_from_training_data_only():
    X, _, train, _ = load_data(0)
    angles = Scaler(X[train])(X[train])
    assert np.isclose(angles.min(), 0) and np.isclose(angles.max(), np.pi)


def test_outputs_are_valid_expectation_values():
    X, _, train, _ = load_data(0)
    w = np.random.default_rng(1).uniform(0, 2 * np.pi, QC.num_parameters - 2)
    assert np.all(np.abs(model(w, Scaler(X[train])(X[train]))) <= 1 + 1e-9)


def test_hand_checked_values():
    w = np.zeros(QC.num_parameters - 2)
    # every angle 0: every gate is the identity, so <Z> = 1
    assert np.isclose(model(w, np.zeros((1, 2)))[0], 1.0)
    # x0 = pi: qubit 0 is always exactly 0 or 1, so the CX never entangles it,
    # and three RY(pi) make RY(3pi): <Z> = cos(3pi) = -1
    assert np.isclose(model(w, np.array([[np.pi, 0.0]]))[0], -1.0, atol=1e-9)


def test_shots_agree_with_exact_values():
    X, _, train, _ = load_data(0)
    w = np.random.default_rng(2).uniform(0, 2 * np.pi, QC.num_parameters - 2)
    angles = Scaler(X[train])(X[train[:20]])
    assert np.max(np.abs(model(w, angles) - model_shots(w, angles, shots=4096))) < 0.1


def test_every_model_has_five_seeds_on_shared_splits():
    rows = results()
    names = {r["model"] for r in rows}
    assert {"logistic regression", "RBF SVM", "neural net"} <= names
    for name in names:
        assert len({r["seed"] for r in rows if r["model"] == name}) >= 5
    for seed in {r["seed"] for r in rows}:
        assert len({r["split_id"] for r in rows if r["seed"] == seed}) == 1


def test_quantum_model_learns():
    scores = [float(r["test_acc"]) for r in results() if r["model"] == "quantum (exact)"]
    assert np.mean(scores) > 0.80


def test_charts_exist():
    assert (ROOT / "training_curves.png").exists() and (ROOT / "accuracy_by_model.png").exists()
