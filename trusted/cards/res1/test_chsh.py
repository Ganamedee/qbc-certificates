import csv
import sys
from itertools import product
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chsh import chsh  # noqa: E402


def test_best_classical_strategy_is_two():
    scores = [a0 * b0 + a0 * b1 + a1 * b0 - a1 * b1 for a0, a1, b0, b1 in product([1, -1], repeat=4)]
    assert max(scores) == 2 and min(scores) == -2


def test_noiseless_value_is_two_root_two():
    S, err = chsh()
    assert abs(S - 2 * np.sqrt(2)) < 3 * err


def test_noise_shrinks_s_as_predicted():
    for p in [0.1, 0.2, 0.3]:
        S, err = chsh(p)
        assert abs(S - 2 * np.sqrt(2) * (1 - p)) < 3 * err


def test_sweep_crosses_the_classical_bound():
    root = Path(__file__).resolve().parents[1]
    with open(root / "chsh_sweep.csv") as f:
        rows = sorted((float(r["p"]), float(r["S"])) for r in csv.DictReader(f))
    assert rows[0][0] == 0 and rows[-1][0] >= 0.5
    first_below = next(p for p, S in rows if S < 2)
    assert 0.25 <= first_below <= 0.35  # theory: 1 - 1/sqrt(2), about 0.29
    assert (root / "chsh_vs_noise.png").exists()
