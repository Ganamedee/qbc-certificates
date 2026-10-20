#!/usr/bin/env python3
"""QBC Season 2, Beat Einstein: the CHSH game referee.

Run it from this folder, inside your activated venv:

  python referee.py                score strategy.py exactly on both chips, pair by pair, and show your bars
  python referee.py --classical    every classical plan there is (all 16) and what each one wins
  python referee.py --play 1000    play 1,000 random rounds like a real lab, next to the exact answer
  python referee.py --noise 0.2    your win rate when 20% of the time the shared pair is pure noise
  python referee.py --pair         what Alice's and Bob's qubits have in common, measured every way
  python referee.py --card NAME    score strategy.py, then seal your card for the live board (NAME is your
                                   GitHub username)

Add --chip decaying to --play, --noise or --pair to use the decaying chip instead of the perfect one.

The game in one breath: the referee sends Alice a random bit x and Bob a random bit y. Alice answers a bit a,
Bob answers a bit b, and they can't talk. They win if a XOR b equals x AND y. A classical team wins at most 75%
of rounds, on average, however clever its plan.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from qiskit import QuantumCircuit, qasm2
from qiskit.circuit import Barrier
from qiskit.circuit.library import (CXGate, HGate, RXGate, RYGate, RZGate, SdgGate, SGate, TdgGate,
                                    TGate, XGate, YGate, ZGate)
from qiskit.quantum_info import DensityMatrix, Kraus, Pauli

HERE = Path(__file__).resolve().parent
SEASON = "Beat Einstein S2"
LIMIT = 0.75                                  # the most any classical team wins, on average
CEILING = float(np.cos(np.pi / 8) ** 2)       # the most any quantum strategy wins: Tsirelson's bound, 85.36%
CEILING_SLACK = 1e-6                          # how close to the ceiling counts as reaching it
DECAY = 0.4                                   # the decaying chip: a |1> on Bob's qubit falls to |0> 40% of the time
CHIPS = ("perfect", "decaying")
PARTS = ["shared", "alice 0", "alice 1", "bob 0", "bob 1"]
QUBITS = {"shared": 2, "alice 0": 1, "alice 1": 1, "bob 0": 1, "bob 1": 1}
NAMES = {"shared": "the shared pair (shared_pair())", "alice 0": "Alice's move for x = 0 (alice(0))",
         "alice 1": "Alice's move for x = 1 (alice(1))", "bob 0": "Bob's move for y = 0 (bob(0))",
         "bob 1": "Bob's move for y = 1 (bob(1))"}
GATES = ["x", "y", "z", "h", "s", "sdg", "t", "tdg", "rx", "ry", "rz", "cx"]
ALLOWED = {XGate: "x", YGate: "y", ZGate: "z", HGate: "h", SGate: "s", SdgGate: "sdg", TGate: "t",
           TdgGate: "tdg", RXGate: "rx", RYGate: "ry", RZGate: "rz", CXGate: "cx"}
MAX_QASM_CHARS = 20_000      # per circuit
MAX_GATES = 500              # per circuit
MAX_REGISTER_BITS = 64       # checked before parsing, so a giant qreg can't stall the live board
REGISTER_DECL = re.compile(r"\b([qc])reg\s+[A-Za-z_]\w*\s*\[\s*(\d+)\s*\]")
PAIRS = list(itertools.product((0, 1), repeat=2))
# Entries go to the club's claims repo: the scoring workflow there opens each sealed card, scores it with this
# same file and puts your bars on the live board. Only that workflow can open a card.
CLAIMS_REPO = "Ganamedee/qbc-certificates"
KEY_URL = f"https://raw.githubusercontent.com/{CLAIMS_REPO}/main/seasons/sealing-key.txt"
ENTRY_FORM = f"https://github.com/{CLAIMS_REPO}/issues/new?template=season-02-chsh.yml"
LIVE_BOARD = f"https://github.com/{CLAIMS_REPO}/blob/HEAD/seasons/boards/season-02.md"
CARD_FILE = "my_card.txt"
KEY_FILE = "sealing-key.txt"   # a copy of the key you saved yourself, used if referee.py can't download it
GITHUB_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
BARS = [("bell", "🔔 Bell Breaker", "past 75% on the perfect chip"),
        ("ceiling", "🏔️ Ceiling Club", "85.36% on the perfect chip, the most quantum mechanics allows"),
        ("decay", "🍂 Decay Breaker", "past 75% on the decaying chip")]


class EntryError(ValueError):
    """A strategy the referee can't score. The message says what to fix."""


@dataclass
class Score:
    chip: str
    win: float                          # exact win rate over the four input pairs, each with probability 1/4
    by_pair: dict = field(default_factory=dict)

    @property
    def chsh(self) -> float:
        """The CHSH number S = 8p - 4: 2 for the best classical play, 2√2 at the quantum ceiling."""
        return 8 * self.win - 4


def rate(win: float) -> float:
    """A win rate as the live board keeps it: rounded to 9 decimal places, so your laptop and the club agree."""
    return round(float(win), 9)


def bars(perfect: float, decaying: float) -> dict:
    """{bar key: cleared?} from the two exact win rates. A rate exactly at 75% doesn't count: it has to beat it."""
    return {"bell": rate(perfect) > LIMIT, "ceiling": rate(perfect) >= rate(CEILING - CEILING_SLACK),
            "decay": rate(decaying) > LIMIT}


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
        return "no resets: every circuit starts from all zeros anyway"
    if op.name in ("if_else", "while_loop", "for_loop", "switch_case") or op.num_clbits:
        return "no classical bits or classical control"
    if kind == "_DefinedGate" or (from_python and kind in ("Gate", "Instruction")
                                  and getattr(op, "definition", None) is not None):
        return (f"'{op.name}' is a custom gate. Custom and composite gates don't count: write out the allowed "
                "gates inside it")
    if kind in ("Gate", "Instruction"):
        return f"'{op.name}' is an opaque gate with no definition. Not allowed"
    return f"'{op.name}' isn't one of the allowed gates ({' '.join(GATES)})"


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _qubits(part: str) -> str:
    return f"exactly {_count(QUBITS[part], 'qubit')} and no classical bits"


def circuit_from_qasm(part: str, text: str) -> QuantumCircuit:
    """A checked circuit from OpenQASM 2 text. Never runs any Python from the entry."""
    if not isinstance(text, str) or len(text) > MAX_QASM_CHARS:
        raise EntryError(f"{NAMES[part]} is over {MAX_QASM_CHARS:,} characters")
    no_comments = re.sub(r"//[^\n]*", "", text)
    for inc in re.findall(r'include\s*"([^"]*)"', no_comments):
        if inc != "qelib1.inc":
            raise EntryError(f'{NAMES[part]}: only include "qelib1.inc" is allowed')
    sizes = {"q": 0, "c": 0}
    for kind, size in REGISTER_DECL.findall(no_comments):
        sizes[kind] += int(size)
    if sizes["q"] != QUBITS[part] or sizes["c"] or max(sizes.values()) > MAX_REGISTER_BITS:
        raise EntryError(f"{NAMES[part]} must use {_qubits(part)}")
    try:
        qc = qasm2.loads(text, include_path=())
    except Exception:
        raise EntryError(f"{NAMES[part]}: the QASM didn't parse") from None
    return check_circuit(part, qc)


def check_circuit(part: str, qc, from_python: bool = False) -> QuantumCircuit:
    if not isinstance(qc, QuantumCircuit):
        raise EntryError(f"{NAMES[part]} must return a QuantumCircuit, not {type(qc).__name__}")
    if any(inst.operation.name == "measure" for inst in qc.data):
        raise EntryError(f"{NAMES[part]}: leave out the measurements: the referee measures every qubit itself, at "
                         f"the end")
    if qc.num_qubits != QUBITS[part] or qc.num_clbits:
        raise EntryError(f"{NAMES[part]} must use {_qubits(part)} (yours has {_count(qc.num_qubits, 'qubit')} and "
                         f"{_count(qc.num_clbits, 'classical bit')})")
    if len(qc.data) > MAX_GATES:
        raise EntryError(f"{NAMES[part]} has over {MAX_GATES} gates")
    for inst in qc.data:
        if problem := _gate_problem(inst.operation, from_python):
            raise EntryError(f"{NAMES[part]}: {problem}")
        if not all(isinstance(p, (int, float)) and np.isfinite(float(p)) for p in inst.operation.params):
            raise EntryError(f"{NAMES[part]}: every angle must be an ordinary finite number (check your "
                             f"{inst.operation.name})")
    return qc


# ----------------------------------------------------------------------------- the exact referee

def decay_channel(gamma: float = DECAY) -> Kraus:
    """Amplitude damping: a |1> falls to |0> with probability gamma, the energy loss (T1) every real qubit has."""
    return Kraus([np.array([[1, 0], [0, np.sqrt(1 - gamma)]]), np.array([[0, np.sqrt(gamma)], [0, 0]])])


def pair_state(shared: QuantumCircuit, chip: str = "perfect", noise: float = 0.0) -> DensityMatrix:
    """The pair as Alice and Bob hold it: qubit 0 is Alice's, qubit 1 is Bob's. On the decaying chip, Bob's
    qubit decays on its way to him. noise mixes in white noise: (1 - noise) of the time it's your pair, the rest
    of the time it's two completely random bits."""
    if chip not in CHIPS:
        raise EntryError(f"there's no {chip!r} chip: use {' or '.join(CHIPS)}")
    if not 0 <= noise <= 1:
        raise EntryError("noise must be between 0 and 1")
    rho = DensityMatrix(shared)
    if chip == "decaying":
        rho = rho.evolve(decay_channel(), qargs=[1])
    if noise:
        rho = DensityMatrix((1 - noise) * rho.data + noise * np.eye(4) / 4)
    return rho


def score(circuits: dict, chip: str = "perfect", noise: float = 0.0) -> Score:
    """The exact win rate for the five circuits in PARTS, from density matrices: no sampling, no luck."""
    rho = pair_state(circuits["shared"], chip, noise)
    by_pair = {}
    for x, y in PAIRS:
        moves = QuantumCircuit(2)
        moves.compose(circuits[f"alice {x}"], qubits=[0], inplace=True)
        moves.compose(circuits[f"bob {y}"], qubits=[1], inplace=True)
        probs = rho.evolve(moves).probabilities()      # index = 2b + a (qubit 0 is the low bit)
        by_pair[(x, y)] = float(sum(p for i, p in enumerate(probs) if (i & 1) ^ (i >> 1) == x & y))
    result = Score(chip, sum(by_pair.values()) / 4, by_pair)
    if result.win > CEILING + 1e-6:     # impossible in quantum mechanics: a referee bug, not a win
        raise RuntimeError(f"win rate {result.win} is past Tsirelson's bound, which no strategy can reach: a bug "
                           f"in the referee, not a win")
    return result


def correlations(shared: QuantumCircuit, chip: str = "perfect") -> dict:
    """{(P, Q): <P on Alice's qubit, Q on Bob's>} for P, Q in X, Y, Z: +1 means their answers always agree when
    both measure that way, -1 always disagree, 0 no link at all."""
    rho = pair_state(shared, chip)
    # Pauli labels read right to left: the last letter acts on qubit 0, Alice's.
    return {(p, q): float(np.real(rho.expectation_value(Pauli(q + p)))) for p in "XYZ" for q in "XYZ"}


def classical_strategies() -> list:
    """All 16 deterministic plans (Alice's answer to x=0 and x=1, Bob's to y=0 and y=1) and their win rates.
    Shared randomness only mixes these plans, so it can't beat the best of them."""
    rows = []
    for a0, a1, b0, b1 in itertools.product((0, 1), repeat=4):
        wins = sum(((a0, a1)[x] ^ (b0, b1)[y]) == (x & y) for x, y in PAIRS)
        rows.append(((a0, a1, b0, b1), wins / 4))
    return rows


def play(circuits: dict, rounds: int, seed: int | None = None, chip: str = "perfect") -> int:
    """Wins in `rounds` random rounds, sampled the way a real lab would see them."""
    rng = np.random.default_rng(seed)
    exact = score(circuits, chip).by_pair
    pairs = rng.integers(0, 4, size=rounds)
    return int(sum(rng.random() < exact[PAIRS[k]] for k in pairs))


# ----------------------------------------------------------------------------- strategy.py

def load_strategy(path: Path = HERE / "strategy.py") -> dict:
    """{part: circuit} from your strategy.py, each one exported to OpenQASM 2 and read back, so the referee scores
    exactly what the club's scoring workflow will."""
    spec = importlib.util.spec_from_file_location("strategy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    circuits = {}
    for part in PARTS:
        who, _, bit = part.partition(" ")
        name = "shared_pair" if who == "shared" else who
        fn = getattr(module, name, None)
        if fn is None:
            raise EntryError(f"strategy.py has no {name}() function")
        try:
            qc = fn() if who == "shared" else fn(int(bit))
        except Exception as exc:
            raise EntryError(f"{NAMES[part]} stopped with {type(exc).__name__}: {exc}") from None
        qc = check_circuit(part, qc, from_python=True)
        try:
            text = qasm2.dumps(qc)
        except Exception as exc:
            raise EntryError(f"{NAMES[part]} can't be written as OpenQASM 2 ({exc})") from None
        circuits[part] = circuit_from_qasm(part, text)
    return circuits


# ----------------------------------------------------------------------------- cards

def normalise_qasm(text: str) -> str:
    return "\n".join(line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip())


def card_payload(circuits: dict, github: str) -> dict:
    """What a sealed card holds: the season, whose card it is and the OpenQASM 2 of all five circuits."""
    return {"season": 2, "github": github,
            "strategy": {part: normalise_qasm(qasm2.dumps(circuits[part])) for part in PARTS}}


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
                                 "now. Your strategy is safe in strategy.py: try again later") from None
        reason = getattr(exc, "reason", None) or exc
        raise seal.SealError(f"referee.py couldn't download the club's season key ({reason}). Open {KEY_URL} in "
                             f"your browser, save it in this folder as {KEY_FILE}, and run the same command "
                             f"again") from None


def make_card(circuits: dict, github: str, key: bytes) -> str:
    import seal
    return seal.seal(card_payload(circuits, github), key, 2)


# ----------------------------------------------------------------------------- what you see

def percent(p: float) -> str:
    """Two decimal places, or more when that's what tells a hair past 75% from exactly 75%."""
    text = f"{100 * p:.2f}%"
    if text == "75.00%" and rate(p) != LIMIT:
        text = f"{100 * p:.7f}".rstrip("0") + "%"
    return text


def scorecard(perfect: Score, decaying: Score) -> str:
    cleared = bars(perfect.win, decaying.win)
    marks = " · ".join(f"{name} {'✅' if cleared[key] else '❌'}" for key, name, _ in BARS)
    return f"🔔 {SEASON} · perfect chip {percent(perfect.win)} · decaying chip {percent(decaying.win)} · {marks}"


def report(result: Score) -> str:
    title = "the perfect chip" if result.chip == "perfect" else f"the decaying chip (Bob's qubit decays, {DECAY})"
    lines = [f"On {title}:"]
    for (x, y), p in result.by_pair.items():
        need = "disagree" if x & y else "agree"
        lines.append(f"  x={x}, y={y} (they must {need}): win {percent(p)}")
    lines.append(f"  Exact win rate: {percent(result.win)} (S = {result.chsh:.3f})")
    if rate(result.win) > LIMIT:
        lines.append("  🔔 Past the classical limit of 75%: no classical team can do that.")
    elif rate(result.win) == LIMIT:
        lines.append("  Right on the classical limit: the best a classical team can do, and no better.")
    else:
        lines.append("  Under the classical limit: the always-answer-0 plan wins 75%.")
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
    print(f"\nOnly the club's scoring workflow can open it, so your strategy stays private. Open the Season 2 "
          f"entry form, paste the whole block (BEGIN and END lines included) and submit it:\n\n"
          f"  {ENTRY_FORM}\n\nYour win rates and bars come back as a comment on your entry within minutes. "
          f"The live board:\n\n  {LIVE_BOARD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="QBC Season 2 referee: exact CHSH win rates")
    ap.add_argument("--classical", action="store_true", help="list all 16 classical plans")
    ap.add_argument("--play", type=int, metavar="N", help="play N random rounds")
    ap.add_argument("--seed", type=int, help="seed for --play, so a run repeats")
    ap.add_argument("--noise", type=float, metavar="P", help="mix the shared pair with this much white noise (0 to 1)")
    ap.add_argument("--pair", action="store_true", help="show what Alice's and Bob's qubits have in common")
    ap.add_argument("--chip", choices=CHIPS, default="perfect", help="the chip for --play, --noise and --pair")
    ap.add_argument("--card", metavar="GITHUB_USERNAME", help="score strategy.py, then seal your card")
    ap.add_argument("--key", metavar="FILE",
                    help="the club's season key, saved from your browser, if referee.py can't download it")
    ap.add_argument("--strategy", default=str(HERE / "strategy.py"), help="your strategy file")
    args = ap.parse_args(argv)
    if args.card is not None and not GITHUB_NAME.fullmatch(args.card.strip().lstrip("@")):
        print(f"{args.card!r} isn't a GitHub username. Use the name in your GitHub profile's address, "
              "github.com/NAME, for example `python referee.py --card ada-builds`.")
        return 1
    if args.classical:
        rows = classical_strategies()
        for (a0, a1, b0, b1), p in rows:
            print(f"  Alice answers {a0} to x=0 and {a1} to x=1, Bob answers {b0} to y=0 and {b1} to y=1: "
                  f"wins {percent(p)}")
        print(f"\nBest classical plan: {percent(max(p for _, p in rows))}. Shared random bits only mix these plans, "
              "so they can't beat the best one.")
        return 0
    path = Path(args.strategy)
    try:
        circuits = load_strategy(path)
    except FileNotFoundError:
        print(f"Can't find {path}. Run this from your season folder (the one with strategy.py in it).")
        return 1
    except EntryError as exc:
        print(f"⚠️ {exc}")
        return 1
    except Exception as exc:
        print(f"strategy.py has an error before the game starts: {type(exc).__name__}: {exc}")
        return 1
    try:
        if args.play is not None:
            if not 1 <= args.play <= 1_000_000:
                raise EntryError("--play takes 1 to 1,000,000 rounds")
            wins = play(circuits, args.play, args.seed, args.chip)
            exact = score(circuits, args.chip).win
            spread = (exact * (1 - exact) / args.play) ** 0.5
            print(f"{wins:,} wins in {args.play:,} rounds on the {args.chip} chip: {percent(wins / args.play)}. "
                  f"The exact rate is {percent(exact)}, and a run this long usually lands within "
                  f"{percent(2 * spread)} of it.")
            return 0
        if args.noise is not None:
            result = score(circuits, args.chip, args.noise)
            print(f"With {percent(args.noise)} noise on the {args.chip} chip: {percent(result.win)} "
                  f"(S = {result.chsh:.3f}): "
                  + ("still past 75%." if rate(result.win) > LIMIT else "not past 75%."))
            return 0
        if args.pair:
            table = correlations(circuits["shared"], args.chip)
            print(f"Your shared pair on the {args.chip} chip. Each number is the average of (+1 if they agree, "
                  "-1 if not)\nwhen Alice measures one way and Bob another, before any move:\n")
            print("              Bob X    Bob Y    Bob Z")
            for p in "XYZ":
                print(f"  Alice {p}  " + "".join(f"{table[(p, q)]:+9.3f}" for q in "XYZ"))
            return 0
        perfect, decaying = score(circuits, "perfect"), score(circuits, "decaying")
    except EntryError as exc:
        print(f"⚠️ {exc}")
        return 1
    print(f"🔔 {SEASON}: the CHSH game\n")
    print(report(perfect))
    print()
    print(report(decaying))
    print("\nYour scorecard (win rates only, so it's fine to post anywhere):\n")
    print(scorecard(perfect, decaying))
    if args.card is not None:
        return send_card(circuits, args.card.strip().lstrip("@"), args.key, path.resolve().parent)
    print("\nTo put this card on the live board, seal it with your GitHub username and send it:\n\n"
          "  python referee.py --card YOUR_GITHUB_USERNAME")
    return 0


if __name__ == "__main__":
    sys.exit(main())
