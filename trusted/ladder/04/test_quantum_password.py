"""Tests for Project 04: Quantum Password Generator.

Run it from the project folder, inside your activated virtual environment:
    python tests/test_quantum_password.py
    python -m pytest tests/             (optional, if you have pytest installed)

Which code gets tested:
    1. the folder named in the QBC_TARGET environment variable, if it is set;
    2. otherwise solution/, if that folder exists here;
    3. otherwise starter/ (your code: the starter pack has no solution/).
Force your own code with QBC_TARGET=starter python tests/test_quantum_password.py
(Windows PowerShell: $env:QBC_TARGET="starter"; python tests/test_quantum_password.py).
"""

import math
import os
import string
import sys
import tempfile
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("QBC_TARGET") or ("solution" if (ROOT / "solution").is_dir() else "starter")
TARGET_DIR = (ROOT / TARGET).resolve()
if TARGET_DIR.parent != ROOT or not (TARGET_DIR / "quantum_password.py").is_file():
    sys.exit(f"QBC_TARGET={TARGET!r}: no quantum_password.py in a folder of that name next to tests/. "
             "Use starter or solution.")
sys.path.insert(0, str(TARGET_DIR))

import quantum_password as qp  # noqa: E402

ALL_94 = string.ascii_letters + string.digits + string.punctuation


# ---- Alphabet ------------------------------------------------------------
def test_full_alphabet_has_94_characters():
    classes = qp.build_classes()
    assert [len(c) for c in classes] == [26, 26, 10, 32]
    assert set("".join(classes)) == set(ALL_94)


def test_exclude_ambiguous():
    alphabet = "".join(qp.build_classes(exclude_ambiguous=True))
    assert not set("Il1O0o") & set(alphabet)
    assert len(alphabet) == 94 - 6


# ---- Randomness source ---------------------------------------------------
def test_randbelow_range_and_uniformity():
    rng = qp.QuantumRandom(seed=1)
    values = [rng.randbelow(94) for _ in range(18_800)]
    assert min(values) == 0 and max(values) == 93
    expected = len(values) / 94
    chi2 = sum((values.count(v) - expected) ** 2 / expected for v in range(94))
    assert chi2 < 140.893, chi2  # 93 degrees of freedom, 0.1% critical value


def test_randbelow_rejects_bad_input():
    try:
        qp.QuantumRandom(seed=1).randbelow(0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ---- Passwords -----------------------------------------------------------
def test_length_and_allowed_characters():
    rng = qp.QuantumRandom(seed=2)
    for length in (4, 12, 16, 40):
        pw = qp.generate_password(length, rng=rng)
        assert len(pw) == length and set(pw) <= set(ALL_94)


def test_every_class_present_when_required():
    rng = qp.QuantumRandom(seed=3)
    for _ in range(200):
        pw = qp.generate_password(4, rng=rng)  # shortest possible: very strict
        for cls in qp.build_classes():
            assert any(ch in cls for ch in pw), pw


def test_options_respected():
    rng = qp.QuantumRandom(seed=4)
    for _ in range(100):
        pw = qp.generate_password(20, symbols=False, exclude_ambiguous=True, rng=rng)
        assert not set(pw) & set(string.punctuation)
        assert not set(pw) & set("Il1O0o")


def test_too_short_is_rejected():
    for bad in (lambda: qp.generate_password(3), lambda: qp.generate_password(0)):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_seeded_passwords_are_reproducible():
    # This is a WARNING demonstrated as a test: anyone with the seed can
    # regenerate the password. Never seed a real password generator.
    assert qp.generate_password(16, seed=99) == qp.generate_password(16, seed=99)


def test_unseeded_passwords_differ():
    rng = qp.QuantumRandom()
    passwords = {qp.generate_password(16, rng=rng) for _ in range(50)}
    assert len(passwords) == 50


def test_characters_are_uniform_without_class_rule():
    rng = qp.QuantumRandom(seed=5)
    text = "".join(qp.generate_password(20, require_each_class=False, rng=rng)
                   for _ in range(940))
    expected = len(text) / 94
    chi2 = sum((text.count(ch) - expected) ** 2 / expected for ch in ALL_94)
    assert chi2 < 140.893, chi2


# ---- Strength maths ------------------------------------------------------
def test_entropy_formula():
    assert math.isclose(qp.entropy_bits(16, 94), 104.873, abs_tol=1e-3)
    assert math.isclose(qp.entropy_bits(8, 2), 8.0)


def test_count_valid_passwords_matches_brute_force():
    classes = ["ab", "C", "12"]
    sizes = [len(c) for c in classes]
    alphabet = "".join(classes)
    for length in (3, 4, 5):
        brute = sum(1 for chars in product(alphabet, repeat=length)
                    if all(any(ch in cls for ch in chars) for cls in classes))
        assert qp.count_valid_passwords(length, sizes) == brute, length


def test_class_rule_costs_a_little_entropy():
    exact = qp.entropy_with_requirements(16, [26, 26, 10, 32])
    plain = qp.entropy_bits(16, 94)
    assert plain - 1.0 < exact < plain, (exact, plain)


def test_crack_time_grows_exponentially():
    assert math.isclose(qp.average_crack_years(81) / qp.average_crack_years(80), 2.0)


def test_classical_password():
    pw = qp.classical_password(24)
    assert len(pw) == 24 and set(pw) <= set(ALL_94)


def test_plot_creates_png():
    rng = qp.QuantumRandom(seed=6)
    sample = [qp.generate_password(16, rng=rng) for _ in range(100)]
    with tempfile.TemporaryDirectory() as tmp:
        out = qp.plot_results(sample, qp.build_classes(), Path(tmp) / "pw.png")
        assert out.exists() and out.stat().st_size > 1000


# ---- Plain-Python runner -------------------------------------------------
if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    print(f"Project 04 tests (target: {TARGET})\n")
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
