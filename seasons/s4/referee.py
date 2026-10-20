#!/usr/bin/env python3
"""QBC Season 4, Catch Eve: the BB84 referee. This season, you're the eavesdropper.

Run it from this folder, inside your activated venv:

  python referee.py                score spy.py exactly: the errors you cause and the share of the key you steal,
                                   state by state, and your bars
  python referee.py --play 2000    run 2,000 rounds of BB84 like a real lab, with your spy listening in
  python referee.py --card NAME    score spy.py, then seal your card for the live board (NAME is your GitHub
                                   username)

The game in one breath: Alice sends Bob one qubit per round, a random bit in a random basis, Z or X, and your spy
touches every one of them on the way. Bob measures in a random basis, and they keep the rounds where their bases
match. Then they announce the bases, you listen to your spy qubits, and you guess Alice's bit. If more than 11% of
their kept bits disagree, they throw the whole key away: you're caught.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from math import cos, isfinite, pi, sqrt
from pathlib import Path

import numpy as np
from qiskit import QuantumCircuit, qasm2
from qiskit.circuit import Barrier
from qiskit.circuit.library import (CXGate, HGate, RXGate, RYGate, RZGate, SdgGate, SGate, TdgGate,
                                    TGate, XGate, YGate, ZGate)
from qiskit.quantum_info import Statevector

HERE = Path(__file__).resolve().parent
SEASON = "Catch Eve S4"
LINE = 0.11                       # more errors than this in the kept bits, and Alice and Bob throw the key away
FRACTION = 0.44                   # Project 07's eavesdropper on 44% of the qubits sits right on the line...
PROJECT_07 = 0.5 + FRACTION / 4   # ...and steals 61% of the key
BREIDBART = (1 - FRACTION) / 2 + FRACTION * cos(pi / 8) ** 2   # a sneakier measurement angle on 44%: 65.56%
CEILING = 0.5 + sqrt(LINE * (1 - LINE))   # the most any attack on one qubit at a time steals at 11% errors: 81.29%
CEILING_SLACK = 1e-6                      # how close to the ceiling counts as reaching it
MAX_SPIES = 2
STATES = [("Z", 0), ("Z", 1), ("X", 0), ("X", 1)]
KETS = {("Z", 0): "|0>", ("Z", 1): "|1>", ("X", 0): "|+>", ("X", 1): "|->"}
PARTS = ["attack", "listen Z", "listen X"]
NAMES = {"attack": "your attack (attack())", "listen Z": "your listening in the Z basis (listen('Z'))",
         "listen X": "your listening in the X basis (listen('X'))"}
GATES = ["x", "y", "z", "h", "s", "sdg", "t", "tdg", "rx", "ry", "rz", "cx"]
ALLOWED = {XGate: "x", YGate: "y", ZGate: "z", HGate: "h", SGate: "s", SdgGate: "sdg", TGate: "t",
           TdgGate: "tdg", RXGate: "rx", RYGate: "ry", RZGate: "rz", CXGate: "cx"}
TWO_QUBIT_COST, ONE_QUBIT_COST = 10, 1     # strokes, as in Circuit Golf
MAX_QASM_CHARS = 20_000      # per circuit
MAX_GATES = 500              # per circuit
MAX_REGISTER_BITS = 64       # checked before parsing, so a giant qreg can't stall the live board
REGISTER_DECL = re.compile(r"\b([qc])reg\s+[A-Za-z_]\w*\s*\[\s*(\d+)\s*\]")
# Entries go to the club's claims repo: the scoring workflow there opens each sealed card, scores it with this
# same file and puts your bars on the live board. Only that workflow can open a card.
CLAIMS_REPO = "Ganamedee/qbc-certificates"
KEY_URL = f"https://raw.githubusercontent.com/{CLAIMS_REPO}/main/seasons/sealing-key.txt"
ENTRY_PAGE = "https://quantumbuilders.club/season"      # files each card in the claims repo as the member
LIVE_BOARD = "https://quantumbuilders.club/season#season-4"
CARD_FILE = "my_card.txt"
KEY_FILE = "sealing-key.txt"   # a copy of the key you saved yourself, used if referee.py can't download it
GITHUB_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
BARS = [("eavesdropper", "👂 Eavesdropper",
         "past 61% without getting caught, more than Project 07's eavesdropper steals"),
        ("ghost", "👻 Ghost", "81.29% without getting caught, the most any attack on one qubit at a time can steal"),
        ("wolf", "🐺 Lone Wolf", "81.29% without getting caught, with a single spy qubit")]


class EntryError(ValueError):
    """A spy the referee can't score. The message says what to fix."""


@dataclass
class Score:
    errors: float           # the exact share of kept bits where Bob's bit isn't Alice's
    steal: float            # the exact share of kept bits where your guess is Alice's bit
    spies: int              # how many spy qubits the attack uses
    strokes: int            # attack and both listens, counted as in Circuit Golf
    by_state: dict = field(default_factory=dict)    # {(basis, bit): (Bob wrong, you right)}

    @property
    def caught(self) -> bool:
        return rate(self.errors) > LINE


def rate(p: float) -> float:
    """A rate as the live board keeps it: rounded to 9 decimal places, so your laptop and the club agree."""
    return round(float(p), 9)


def bars(errors: float, steal: float, spies: int) -> dict:
    """{bar key: cleared?}. Caught clears nothing. Exactly 61% doesn't count: it has to beat Project 07's attack."""
    safe = rate(errors) <= LINE
    ceiling = safe and rate(steal) >= rate(CEILING - CEILING_SLACK)
    return {"eavesdropper": safe and rate(steal) > rate(PROJECT_07), "ghost": ceiling,
            "wolf": ceiling and spies == 1}


# ----------------------------------------------------------------------------- checking circuits

def _gate_problem(op, from_python: bool = False) -> str | None:
    """Why a gate isn't allowed, or None. A circuit built in Python is checked before export too, so the
    message names the gate you actually used (QASM export rewrites some gates as custom ones)."""
    base = getattr(op, "base_class", type(op))
    if base is Barrier or ALLOWED.get(base) == op.name:
        return None
    kind = base.__name__
    if from_python and op.name in GATES:  # export would quietly turn a gate named "h" into a real h
        return f"'{op.name}' here is a custom gate wearing a real gate's name. Use qc.{op.name}(...) itself"
    if op.name == "measure":
        return "leave out the measurements: the referee measures every qubit itself, at the end"
    if op.name == "reset":
        return "no resets: your spies start at 0 anyway"
    if op.name in ("if_else", "while_loop", "for_loop", "switch_case") or op.num_clbits:
        return "no classical bits or classical control"
    if kind == "_DefinedGate" or (from_python and kind in ("Gate", "Instruction")
                                  and getattr(op, "definition", None) is not None):
        return (f"'{op.name}' is a custom gate. Custom and composite gates don't count: write out the allowed "
                "gates inside it")
    if kind in ("Gate", "Instruction"):
        return f"'{op.name}' is an opaque gate with no definition. Not allowed"
    return f"'{op.name}' isn't one of the allowed gates ({' '.join(GATES)})"


def _count(n: int, word: str, plural: str = "") -> str:
    return f"{n} {word if n == 1 else plural or word + 's'}"


def _shape(part: str, qubits: int | None) -> str:
    if qubits is None:
        return "2 or 3 qubits (Alice's qubit, then 1 or 2 spies) and no classical bits"
    return f"exactly {_count(qubits, 'qubit')}, the same as your attack, and no classical bits"


def _fits(part: str, n: int, qubits: int | None) -> bool:
    return 2 <= n <= 1 + MAX_SPIES if qubits is None else n == qubits


def circuit_from_qasm(part: str, text: str, qubits: int | None = None) -> QuantumCircuit:
    """A checked circuit from OpenQASM 2 text. Never runs any Python from the entry. qubits: the attack's width,
    which each listen must match (None for the attack itself)."""
    if not isinstance(text, str) or len(text) > MAX_QASM_CHARS:
        raise EntryError(f"{NAMES[part]} is over {MAX_QASM_CHARS:,} characters")
    no_comments = re.sub(r"//[^\n]*", "", text)
    for inc in re.findall(r'include\s*"([^"]*)"', no_comments):
        if inc != "qelib1.inc":
            raise EntryError(f'{NAMES[part]}: only include "qelib1.inc" is allowed')
    sizes = {"q": 0, "c": 0}
    for kind, size in REGISTER_DECL.findall(no_comments):
        sizes[kind] += int(size)
    if not _fits(part, sizes["q"], qubits) or sizes["c"] or max(sizes.values()) > MAX_REGISTER_BITS:
        raise EntryError(f"{NAMES[part]} must use {_shape(part, qubits)}")
    try:
        qc = qasm2.loads(text, include_path=())
    except Exception:
        raise EntryError(f"{NAMES[part]}: the QASM didn't parse") from None
    return check_circuit(part, qc, qubits)


def check_circuit(part: str, qc, qubits: int | None = None, from_python: bool = False) -> QuantumCircuit:
    if not isinstance(qc, QuantumCircuit):
        raise EntryError(f"{NAMES[part]} must return a QuantumCircuit, not {type(qc).__name__}")
    if any(inst.operation.name == "measure" for inst in qc.data):
        raise EntryError(f"{NAMES[part]}: leave out the measurements: the referee measures every qubit itself, at "
                         f"the end")
    if not _fits(part, qc.num_qubits, qubits) or qc.num_clbits:
        raise EntryError(f"{NAMES[part]} must use {_shape(part, qubits)} (yours has "
                         f"{_count(qc.num_qubits, 'qubit')} and {_count(qc.num_clbits, 'classical bit')})")
    if len(qc.data) > MAX_GATES:
        raise EntryError(f"{NAMES[part]} has over {MAX_GATES} gates")
    for inst in qc.data:
        if problem := _gate_problem(inst.operation, from_python):
            raise EntryError(f"{NAMES[part]}: {problem}")
        if not all(isinstance(p, (int, float)) and isfinite(float(p)) for p in inst.operation.params):
            raise EntryError(f"{NAMES[part]}: every angle must be an ordinary finite number (check your "
                             f"{inst.operation.name})")
        if part != "attack" and inst.operation.name != "barrier" and any(qc.find_bit(q).index == 0
                                                                         for q in inst.qubits):
            raise EntryError(f"{NAMES[part]}: qubit 0 has gone on to Bob, so listening can only use your spies "
                             f"(qubit 1 and up)")
    return qc


def strokes(circuits: dict) -> int:
    """Every cx costs 10 strokes and every other gate 1, as in Circuit Golf, over the attack and both listens."""
    total = 0
    for part in PARTS:
        for inst in circuits[part].data:
            if inst.operation.name != "barrier":
                total += TWO_QUBIT_COST if inst.operation.name == "cx" else ONE_QUBIT_COST
    return total


# ----------------------------------------------------------------------------- the exact referee

def joint(circuits: dict, basis: str, bit: int) -> np.ndarray:
    """P[Bob's bit, your guess] when Alice sends this bit in this basis: Bob measures in her basis (only those
    rounds are kept), and your guess is spy 1's result after you listen."""
    attack = circuits["attack"]
    qc = QuantumCircuit(attack.num_qubits)
    if bit:
        qc.x(0)
    if basis == "X":
        qc.h(0)
    qc.compose(attack, inplace=True)
    if basis == "X":
        qc.h(0)                      # Bob measures in X: turn it to Z first
    qc.compose(circuits[f"listen {basis}"], inplace=True)
    probs = Statevector(qc).probabilities()      # index bit q is qubit q: bit 0 is Bob's, bit 1 is spy 1
    out = np.zeros((2, 2))
    for i, p in enumerate(probs):
        out[i & 1, (i >> 1) & 1] += p
    return out


def score(circuits: dict) -> Score:
    """The exact error rate and steal for your spy, from state vectors: no sampling, no luck."""
    by_state = {}
    for basis, bit in STATES:
        p = joint(circuits, basis, bit)
        by_state[(basis, bit)] = (float(p[1 - bit].sum()), float(p[:, bit].sum()))
    errors = sum(e for e, _ in by_state.values()) / 4
    steal = sum(r for _, r in by_state.values()) / 4
    if steal > 0.5 + sqrt(max(0.0, errors * (1 - errors))) + 1e-6:   # impossible in quantum mechanics
        raise RuntimeError(f"a steal of {steal} at {errors} errors is past the ceiling, which no attack on one qubit "
                           f"at a time can reach: a bug in the referee, not a win")
    return Score(errors, steal, circuits["attack"].num_qubits - 1, strokes(circuits), by_state)


def play(circuits: dict, rounds: int, seed: int | None = None, sample: float = 0.25) -> dict:
    """`rounds` rounds of BB84 sampled the way a real lab sees them: random bits and bases, sifting, then a
    public check of a random quarter of the kept bits, as in Project 07."""
    rng = np.random.default_rng(seed)
    tables = {state: joint(circuits, *state) for state in STATES}
    kept, wrong, right = 0, [], []
    for _ in range(rounds):
        basis, bit = "ZX"[rng.integers(2)], int(rng.integers(2))
        if rng.integers(2):           # Bob picked the other basis: the round is thrown away when they compare
            continue
        flat = tables[(basis, bit)].ravel()
        k = rng.choice(4, p=flat / flat.sum())
        bob, guess = divmod(k, 2)
        kept += 1
        wrong.append(bob != bit)
        right.append(guess == bit)
    order = rng.permutation(kept)
    checked = order[:max(1, round(sample * kept))] if kept else order
    rest = order[len(checked):]
    seen = sum(wrong[i] for i in checked)
    return {"rounds": rounds, "kept": kept, "checked": len(checked), "seen": int(seen),
            "estimate": seen / len(checked) if len(checked) else 0.0,
            "rest": len(rest), "right": int(sum(right[i] for i in rest))}


# ----------------------------------------------------------------------------- spy.py

def load_spy(path: Path = HERE / "spy.py") -> dict:
    """{part: circuit} from your spy.py, each one exported to OpenQASM 2 and read back, so the referee scores
    exactly what the club's scoring workflow will."""
    spec = importlib.util.spec_from_file_location("spy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    circuits, qubits = {}, None
    for part in PARTS:
        name = part.split()[0]
        fn = getattr(module, name, None)
        if fn is None:
            raise EntryError(f"spy.py has no {name}() function")
        try:
            qc = fn() if part == "attack" else fn(part.split()[1])
        except Exception as exc:
            raise EntryError(f"{NAMES[part]} stopped with {type(exc).__name__}: {exc}") from None
        qc = check_circuit(part, qc, qubits, from_python=True)
        try:
            text = qasm2.dumps(qc)
        except Exception as exc:
            raise EntryError(f"{NAMES[part]} can't be written as OpenQASM 2 ({exc})") from None
        circuits[part] = circuit_from_qasm(part, text, qubits)
        qubits = circuits["attack"].num_qubits
    return circuits


# ----------------------------------------------------------------------------- cards

def normalise_qasm(text: str) -> str:
    return "\n".join(line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip())


def card_payload(circuits: dict, github: str) -> dict:
    """What a sealed card holds: the season, whose card it is and the OpenQASM 2 of your three circuits."""
    return {"season": 4, "github": github,
            "spy": {part: normalise_qasm(qasm2.dumps(circuits[part])) for part in PARTS}}


def circuits_from_card(spy: dict) -> dict:
    """The three circuits in an opened card, checked exactly as the scoring workflow checks them."""
    if not isinstance(spy, dict) or set(spy) != set(PARTS) or not all(isinstance(v, str) for v in spy.values()):
        raise EntryError("this card doesn't hold Season 4's three circuits")
    circuits = {"attack": circuit_from_qasm("attack", spy["attack"])}
    for part in PARTS[1:]:
        circuits[part] = circuit_from_qasm(part, spy[part], circuits["attack"].num_qubits)
    return circuits


def season_key(path: str | None = None, folder: Path = HERE) -> bytes:
    """The club's season key: from a file you name, else downloaded, else a copy saved in your season folder."""
    import seal  # the sealing code, next to this file
    if path:
        return seal.parse_key_line(Path(path).read_text(encoding="utf-8"))
    try:
        context = None
        try:
            import ssl
            import certifi  # present in most venvs; some Python installs have no certificates without it
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        with urllib.request.urlopen(KEY_URL, timeout=20, context=context) as response:
            return seal.parse_key_line(response.read(4096).decode("utf-8", "replace"))
    except (OSError, ValueError) as exc:  # no internet, a proxy, missing certificates or a 404
        saved = folder / KEY_FILE
        if saved.is_file():
            return seal.parse_key_line(saved.read_text(encoding="utf-8"))
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
            raise seal.SealError("the club's season key isn't published yet, so cards can't be sealed right "
                                 "now. Your spy is safe in spy.py: try again later") from None
        reason = getattr(exc, "reason", None) or exc
        raise seal.SealError(f"referee.py couldn't download the club's season key ({reason}). Open {KEY_URL} in "
                             f"your browser, save it in this folder as {KEY_FILE}, and run the same command "
                             f"again") from None


def make_card(circuits: dict, github: str, key: bytes) -> str:
    import seal
    return seal.seal(card_payload(circuits, github), key, 4)


# ----------------------------------------------------------------------------- what you see

def percent(p: float) -> str:
    """Two decimal places, or more when that's what tells a hair past a line from right on it."""
    text = f"{100 * p:.2f}%"
    for line in (LINE, PROJECT_07):
        if text == f"{100 * line:.2f}%" and rate(p) != rate(line):
            text = f"{100 * p:.7f}".rstrip("0") + "%"
    return text


def scorecard(result: Score) -> str:
    cleared = bars(result.errors, result.steal, result.spies)
    marks = " · ".join(f"{name} {'✅' if cleared[key] else '❌'}" for key, name, _ in BARS)
    head = (f"caught at {percent(result.errors)} errors" if result.caught
            else f"steal {percent(result.steal)} · errors {percent(result.errors)}")
    return f"🥷 {SEASON} · {head} · {_count(result.spies, 'spy', 'spies')}, {result.strokes} strokes · {marks}"


def report(result: Score) -> str:
    lines = [f"  {'Alice sends':<11}   {'Bob gets it wrong':>17}   {'You guess right':>15}"]
    for state in STATES:
        wrong, right = result.by_state[state]
        lines.append(f"  {KETS[state] + ' in ' + state[0]:<11}   {percent(wrong):>17}   {percent(right):>15}")
    lines.append("")
    if result.caught:
        lines.append(f"Errors in the kept bits: {percent(result.errors)}. Over the 11% line, so Alice and Bob throw "
                     f"the key away. 🚨 Caught.")
        lines.append(f"You'd have guessed {percent(result.steal)} of the key, but a key that's thrown away is worth "
                     f"nothing.")
    else:
        lines.append(f"Errors in the kept bits: {percent(result.errors)}, at or under the 11% line, so Alice and Bob "
                     f"keep the key.")
        lines.append(f"You steal {percent(result.steal)} of it. Guessing blind gets 50%.")
        cleared = bars(result.errors, result.steal, result.spies)
        if cleared["ghost"]:
            lines.append("  👻 Right on the ceiling: no attack on one qubit at a time steals more at 11% errors.")
        elif cleared["eavesdropper"]:
            lines.append("  👂 Past 61%: more than Project 07's eavesdropper steals for the same errors.")
        elif rate(result.steal) == rate(PROJECT_07):
            lines.append("  Level with Project 07's eavesdropper on 44% of the qubits: 61%, and no better.")
    lines.append(f"Your spy: {_count(result.spies, 'spy qubit')}, {result.strokes} strokes (every cx 10, every other "
                 f"gate 1).")
    return "\n".join(lines)


def send_card(circuits: dict, github: str, key_path: str | None, folder: Path = HERE) -> int:
    import seal
    try:
        text = make_card(circuits, github, season_key(key_path, folder))
    except (OSError, seal.SealError) as exc:
        print(f"\nNo card yet: {exc}.")
        return 1
    (folder / CARD_FILE).write_text(text + "\n", encoding="utf-8")
    print(f"\nYour sealed card for GitHub account {github} (also saved in {CARD_FILE}):\n")
    print(text)
    print(f"\nOnly the club's scoring workflow can open it, so your spy stays private. Open the club's season "
          f"page, paste the whole block (BEGIN and END lines included) and press Send my card:\n\n  {ENTRY_PAGE}\n\n"
          f"The first time, GitHub asks you to approve the club's app, Quantum Builders Club: it only files your "
          f"entry as you. Within minutes your entry's page shows your steal, your errors, the bars this card clears "
          f"and your place on each bar's list. The live board:\n\n  {LIVE_BOARD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="QBC Season 4 referee: exact BB84 errors and steal")
    ap.add_argument("--play", type=int, metavar="N", help="play N rounds of BB84 like a real lab")
    ap.add_argument("--seed", type=int, help="seed for --play, so a run repeats")
    ap.add_argument("--card", metavar="GITHUB_USERNAME", help="score spy.py, then seal your card")
    ap.add_argument("--key", metavar="FILE",
                    help="the club's season key, saved from your browser, if referee.py can't download it")
    ap.add_argument("--spy", default=str(HERE / "spy.py"), help="your spy file")
    args = ap.parse_args(argv)
    if args.card is not None and not GITHUB_NAME.fullmatch(args.card.strip().lstrip("@")):
        print(f"{args.card!r} isn't a GitHub username. Use the name in your GitHub profile's address, "
              "github.com/NAME, for example `python referee.py --card ada-builds`.")
        return 1
    path = Path(args.spy)
    try:
        circuits = load_spy(path)
    except FileNotFoundError:
        print(f"Can't find {path}. Run this from your season folder (the one with spy.py in it).")
        return 1
    except EntryError as exc:
        print(f"⚠️ {exc}")
        return 1
    except Exception as exc:
        print(f"spy.py has an error before the game starts: {type(exc).__name__}: {exc}")
        return 1
    if args.play is not None:
        if not 1 <= args.play <= 1_000_000:
            print("⚠️ --play takes 1 to 1,000,000 rounds")
            return 1
        run, exact = play(circuits, args.play, args.seed), score(circuits)
        caught = run["estimate"] > LINE
        spread = 2 * sqrt(exact.errors * (1 - exact.errors) / run["checked"]) if run["checked"] else 0.0
        print(f"{run['rounds']:,} rounds: {run['kept']:,} kept (Bob picked Alice's basis). Alice and Bob compare "
              f"{run['checked']:,} of them in public: {run['seen']:,} disagree, {percent(run['estimate'])}.")
        if caught:
            print("Over 11%: they throw the key away. 🚨 Caught.")
        else:
            share = run["right"] / run["rest"] if run["rest"] else 0.0
            print(f"At or under 11%: they keep the other {run['rest']:,} bits, and you guessed {run['right']:,} of "
                  f"them right, {percent(share)}.")
        print(f"\nThe exact rates are {percent(exact.errors)} errors and {percent(exact.steal)} steal. A check this "
              f"size usually lands within {percent(spread)} of the exact error rate, so a spy right on the line is "
              f"caught about half the time in a real run. The live board uses the exact rates.")
        return 0
    result = score(circuits)
    print(f"🥷 {SEASON}: you're the eavesdropper\n")
    print(report(result))
    print("\nYour scorecard (steal and errors only, so it's fine to post anywhere):\n")
    print(scorecard(result))
    if args.card is not None:
        return send_card(circuits, args.card.strip().lstrip("@"), args.key, path.resolve().parent)
    print("\nTo put this card on the live board, seal it with your GitHub username and send it:\n\n"
          "  python referee.py --card YOUR_GITHUB_USERNAME")
    return 0


if __name__ == "__main__":
    sys.exit(main())
