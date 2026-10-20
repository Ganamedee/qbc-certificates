#!/usr/bin/env python3
"""QBC Circuit Golf, Season 1: the grader.

Run it from this folder, inside your activated venv:

  python grade.py                  score every hole in golf.py, print your scorecard
  python grade.py --hole 5         score one hole and show your circuit and why it passed or failed
  python grade.py --holes          show all nine holes: targets, qubits and pars
  python grade.py --robot 3        watch Qiskit's transpiler play a hole: the first score to beat
  python grade.py --card NAME      score golf.py, then seal your card for the live board (NAME is your
                                   GitHub username)

The rules in one breath: use only x y z h s sdg t tdg rx ry rz cx, exactly the hole's qubits,
and hit the target up to global phase. Every cx costs 10 strokes, every other gate 1.
Lower is better. An unsolved hole counts as 2 x par.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
from qiskit.quantum_info import Operator, Statevector

HERE = Path(__file__).resolve().parent
SEASON = "Circuit Golf S1"
SEASON_HOLES = 9
GOLF_GATES = ["x", "y", "z", "h", "s", "sdg", "t", "tdg", "rx", "ry", "rz", "cx"]
ALLOWED = {XGate: "x", YGate: "y", ZGate: "z", HGate: "h", SGate: "s", SdgGate: "sdg", TGate: "t",
           TdgGate: "tdg", RXGate: "rx", RYGate: "ry", RZGate: "rz", CXGate: "cx"}
TWO_QUBIT_COST, ONE_QUBIT_COST = 10, 1
TOL = 1e-6                 # correctness tolerance (absolute, per amplitude or matrix entry)
MAX_QASM_CHARS = 50_000    # per hole
MAX_GATES = 1_000          # per hole
MAX_REGISTER_BITS = 64     # checked before parsing, so a giant qreg can't stall the leaderboard
REGISTER_DECL = re.compile(r"\b([qc])reg\s+[A-Za-z_]\w*\s*\[\s*(\d+)\s*\]")
TARGET_OPS = {"x", "y", "z", "h", "s", "sdg", "t", "tdg", "cx", "cz", "ch", "swap", "iswap", "ccx",
              "cswap"}
# Entries go to the club's claims repo: the scoring workflow there opens each sealed card, grades it with
# this same file and puts the score on the live board. Only that workflow can open a card.
CLAIMS_REPO = "Ganamedee/qbc-certificates"
KEY_URL = f"https://raw.githubusercontent.com/{CLAIMS_REPO}/main/seasons/sealing-key.txt"
ENTRY_FORM = f"https://github.com/{CLAIMS_REPO}/issues/new?template=season-01-circuit-golf.yml"
LIVE_BOARD = f"https://github.com/{CLAIMS_REPO}/blob/HEAD/seasons/boards/season-01.md"
CARD_FILE = "my_card.txt"
KEY_FILE = "sealing-key.txt"   # a copy of the key you saved yourself, used if grade.py can't download it
GITHUB_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")


@dataclass
class Hole:
    number: int
    name: str
    qubits: int
    par: int
    kind: str  # "state" or "unitary"
    ket: str
    brief: str
    spec: dict = field(repr=False, default_factory=dict)

    @property
    def title(self) -> str:
        return f"Hole {self.number}, {self.name}"

    def target(self):
        n = self.qubits
        if self.kind == "state":
            vec = np.zeros(2 ** n, dtype=complex)
            for bits, amp in self.spec["amplitudes"].items():
                vec[int(bits, 2)] = complex(*amp) if isinstance(amp, list) else amp
            return Statevector(vec)
        qc = QuantumCircuit(n)
        for name, *qubits in self.spec["target"]:
            getattr(qc, name)(*qubits)
        return Operator(qc)


@dataclass
class Result:
    hole: Hole
    status: str  # solved, wrong, invalid, unplayed, crashed
    strokes: int | None = None
    message: str = ""
    qasm: str | None = None
    gates: dict = field(default_factory=dict)
    fidelity: float | None = None

    @property
    def counted(self) -> int:
        """Strokes that go on the card: your score if solved, otherwise 2 x par."""
        return self.strokes if self.status == "solved" else 2 * self.hole.par


# ----------------------------------------------------------------------------- holes

def hole_from_dict(d: dict) -> Hole:
    for key in ("hole", "name", "qubits", "par", "kind", "ket", "brief"):
        if key not in d:
            raise ValueError(f"hole file is missing '{key}'")
    if d["kind"] == "state":
        amps = d.get("amplitudes", {})
        if not amps or any(len(bits) != d["qubits"] or set(bits) - {"0", "1"} for bits in amps):
            raise ValueError(f"hole {d['hole']}: amplitudes need {d['qubits']}-bit keys")
    elif d["kind"] == "unitary":
        for op in d.get("target", []):
            if op[0] not in TARGET_OPS or not all(isinstance(q, int) and 0 <= q < d["qubits"] for q in op[1:]):
                raise ValueError(f"hole {d['hole']}: bad target op {op}")
    else:
        raise ValueError(f"hole {d['hole']}: kind must be 'state' or 'unitary'")
    spec = {k: d[k] for k in ("amplitudes", "target") if k in d}
    return Hole(int(d["hole"]), d["name"], int(d["qubits"]), int(d["par"]), d["kind"], d["ket"],
                d["brief"], spec)


def load_holes(root: Path = HERE) -> dict[int, Hole]:
    """Every hole in holes/: all nine are in the pack from day one."""
    holes = {}
    for path in sorted((root / "holes").glob("hole*.json")):
        hole = hole_from_dict(json.loads(path.read_text(encoding="utf-8")))
        holes[hole.number] = hole
    return dict(sorted(holes.items()))


# ----------------------------------------------------------------------------- grading

def _reject(hole: Hole, message: str, qasm: str | None = None) -> Result:
    return Result(hole, "invalid", message=message, qasm=qasm)


def _gate_problem(op, from_python: bool = False) -> str | None:
    """Why a gate isn't allowed, or None. A circuit built in Python is checked before export too, so
    the message names the gate you actually used (QASM export rewrites some gates as custom ones)."""
    base = getattr(op, "base_class", type(op))
    if base is Barrier or ALLOWED.get(base) == op.name:
        return None
    kind = base.__name__
    if from_python and op.name in GOLF_GATES:  # export would quietly turn a gate named "h" into a real h
        return f"'{op.name}' here is a custom gate wearing a real gate's name. Use qc.{op.name}(...) itself"
    if op.name == "measure":
        return "remove the measurements: holes are scored on the state before anyone measures"
    if op.name == "reset":
        return "no resets: every hole starts from all zeros anyway"
    if op.name in ("if_else", "while_loop", "for_loop", "switch_case") or op.num_clbits:
        return "no classical bits or classical control"
    if kind == "_DefinedGate" or (from_python and kind in ("Gate", "Instruction")
                                  and getattr(op, "definition", None) is not None):
        return (f"'{op.name}' is a custom gate. Custom and composite gates don't count: "
                "write out the allowed gates inside it")
    if kind in ("Gate", "Instruction"):
        return f"'{op.name}' is an opaque gate with no definition. Not allowed"
    return f"'{op.name}' isn't in the bag. Allowed: {' '.join(GOLF_GATES)}"


def strokes_for(counts: dict) -> int:
    return sum((TWO_QUBIT_COST if g == "cx" else ONE_QUBIT_COST) * n for g, n in counts.items()
               if g != "barrier")


def grade_qasm(hole: Hole, text: str) -> Result:
    """Score one hole from OpenQASM 2 text. Never runs any Python from the submission."""
    if len(text) > MAX_QASM_CHARS:
        return _reject(hole, f"that circuit is over {MAX_QASM_CHARS:,} characters")
    no_comments = re.sub(r"//[^\n]*", "", text)
    for inc in re.findall(r'include\s*"([^"]*)"', no_comments):
        if inc != "qelib1.inc":
            return _reject(hole, f'only include "qelib1.inc" is allowed, not "{inc}"', text)
    sizes = {"q": 0, "c": 0}
    for kind, size in REGISTER_DECL.findall(no_comments):
        sizes[kind] += int(size)
    if max(sizes.values()) > MAX_REGISTER_BITS:
        return _reject(hole, f"this hole uses exactly {hole.qubits} qubits and no classical bits; your "
                             f"circuit declares {sizes['q']} qubits and {sizes['c']} classical bits", text)
    try:
        qc = qasm2.loads(text, include_path=())
    except Exception as exc:  # the parser's own message is the most useful thing to show
        return _reject(hole, f"the QASM didn't parse: {exc}", text)
    if qc.num_qubits != hole.qubits:
        return _reject(hole, f"this hole uses exactly {hole.qubits} qubits; your circuit has "
                             f"{qc.num_qubits}", text)
    if len(qc.data) > MAX_GATES:
        return _reject(hole, f"over {MAX_GATES} gates", text)
    for inst in qc.data:
        if problem := _gate_problem(inst.operation):
            return _reject(hole, problem, text)
        if not all(isinstance(p, (int, float)) and np.isfinite(p) for p in inst.operation.params):
            return _reject(hole, f"every angle must be an ordinary finite number (check your "
                                 f"{inst.operation.name})", text)
    counts = {k: v for k, v in qc.count_ops().items() if k != "barrier"}
    strokes = strokes_for(counts)
    target = hole.target()
    try:
        got = Statevector(qc) if hole.kind == "state" else Operator(qc)
        ok = got.equiv(target, rtol=0, atol=TOL)
        fid = (abs(np.vdot(target.data, got.data)) ** 2 if hole.kind == "state"
               else abs(np.trace(target.data.conj().T @ got.data)) / 2 ** hole.qubits)
    except Exception as exc:  # never let one strange circuit stop the whole card
        return _reject(hole, f"the grader couldn't simulate this circuit ({type(exc).__name__}: {exc})",
                       text)
    status = "solved" if ok else "wrong"
    msg = "" if ok else ("not the target state yet" if hole.kind == "state"
                         else "not the target gate yet (it must work for every input)")
    return Result(hole, status, strokes, msg, text, counts, float(fid))


def grade_circuit(hole: Hole, qc) -> Result:
    """Score a QuantumCircuit by exporting it to QASM first, so you get exactly the live board's score."""
    if not isinstance(qc, QuantumCircuit):
        return _reject(hole, f"hole{hole.number}() must return a QuantumCircuit, not {type(qc).__name__}")
    for inst in qc.data:
        if problem := _gate_problem(inst.operation, from_python=True):
            return _reject(hole, problem)
    try:
        text = qasm2.dumps(qc)
    except Exception as exc:
        return _reject(hole, f"this circuit can't be exported to OpenQASM 2 ({exc})")
    return grade_qasm(hole, text)


def load_golf(path: Path):
    spec = importlib.util.spec_from_file_location("golf", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grade_module(module, holes: dict[int, Hole]) -> list[Result]:
    results = []
    for n, hole in holes.items():
        fn = getattr(module, f"hole{n}", None)
        if fn is None:
            results.append(Result(hole, "unplayed", message=f"no hole{n}() in golf.py yet"))
            continue
        try:
            qc = fn()
        except NotImplementedError:
            results.append(Result(hole, "unplayed", message="not played yet"))
            continue
        except Exception as exc:
            results.append(Result(hole, "crashed", message=f"{type(exc).__name__}: {exc}"))
            continue
        try:
            results.append(grade_circuit(hole, qc))
        except Exception as exc:  # a circuit so odd the grader itself trips: report it, keep going
            results.append(Result(hole, "crashed", message=f"{type(exc).__name__}: {exc}"))
    return results


def robot(hole: Hole) -> QuantumCircuit:
    """Qiskit's transpiler playing the hole from the bare target: the score to beat first."""
    from qiskit import transpile
    from qiskit.circuit.library import StatePreparation
    qc = QuantumCircuit(hole.qubits)
    if hole.kind == "state":
        qc.append(StatePreparation(hole.target().data), range(hole.qubits))
    else:
        for name, *qubits in hole.spec["target"]:
            getattr(qc, name)(*qubits)
    return transpile(qc, basis_gates=GOLF_GATES, optimization_level=1, seed_transpiler=7)


# ----------------------------------------------------------------------------- cards and files

def normalise_qasm(text: str) -> str:
    return "\n".join(line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip())


def card_code(blocks: dict[int, str]) -> str:
    joined = "\n".join(f"@hole {n}\n{normalise_qasm(blocks[n])}" for n in sorted(blocks))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:6].upper()


def card_payload(results: list[Result], github: str) -> dict:
    """What a sealed card holds: the season, whose card it is and the QASM of every solved hole."""
    return {"season": 1, "github": github,
            "holes": {str(r.hole.number): normalise_qasm(r.qasm) for r in results if r.status == "solved"}}


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
                                 "now. Your circuits are safe in golf.py: try again later") from None
        reason = getattr(exc, "reason", None) or exc
        raise seal.SealError(f"grade.py couldn't download the club's season key ({reason}). Open {KEY_URL} in "
                             f"your browser, save it in this folder as {KEY_FILE}, and run the same command "
                             f"again") from None


def make_card(results: list[Result], github: str, key: bytes) -> str:
    import seal
    return seal.seal(card_payload(results, github), key, 1)


def verdict(diff: int) -> str:
    names = {-2: "🦅 EAGLE", -1: "🐦 BIRDIE", 0: "⛳ par", 1: "bogey", 2: "double bogey"}
    return "🌟 ALBATROSS" if diff <= -3 else names.get(diff, f"+{diff}")


def scorecard(results: list[Result], blocks: dict[int, str]) -> str:
    total = sum(r.counted for r in results)
    par = sum(r.hole.par for r in results)
    solved = sum(r.status == "solved" for r in results)
    per_hole = " ".join(f"H{r.hole.number}:{r.strokes if r.status == 'solved' else 'x'}" for r in results)
    card = card_code(blocks) if blocks else "none"
    return (f"⛳ {SEASON} · {total} strokes · par {par} ({total - par:+d}) · {solved}/{len(results)} "
            f"holes · {per_hole} · card {card}")


def describe(r: Result) -> str:
    head = f"{r.hole.title} ({r.hole.qubits} qubits, par {r.hole.par})"
    if r.status == "solved":
        return f"✅ {head}: {r.strokes} strokes, {verdict(r.strokes - r.hole.par)}"
    if r.status == "wrong":
        return f"❌ {head}: {r.message} (fidelity {r.fidelity:.4f}). Counts as {r.counted}."
    icon = "⏳" if r.status == "unplayed" else "⚠️"
    return f"{icon} {head}: {r.message}. Counts as {r.counted}."


# ----------------------------------------------------------------------------- command line

def print_holes(holes: dict[int, Hole]) -> None:
    for h in holes.values():
        kind = "build this state" if h.kind == "state" else "build this gate"
        print(f"\n⛳ {h.title} · {h.qubits} qubits · par {h.par}\n   {kind}: {h.ket}\n   {h.brief}")


def send_card(results: list[Result], github: str, key_path: str | None, folder: Path = HERE) -> int:
    import seal
    try:
        text = make_card(results, github, season_key(key_path, folder))
    except (OSError, seal.SealError) as exc:
        print(f"\nNo card yet: {exc}.")
        return 1
    (folder / CARD_FILE).write_text(text + "\n", encoding="utf-8")
    print(f"\nYour sealed card for GitHub account {github} (also saved in {CARD_FILE}):\n")
    print(text)
    print(f"\nOnly the club's scoring workflow can open it, so your circuits stay private. Open the Season 1 "
          f"entry form, paste the whole block (BEGIN and END lines included) and submit it:\n\n"
          f"  {ENTRY_FORM}\n\nYour score and rank come back as a comment on your entry within minutes. "
          f"The live board:\n\n  {LIVE_BOARD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="QBC Circuit Golf, Season 1 grader")
    ap.add_argument("--hole", type=int, help="score one hole and show the details")
    ap.add_argument("--holes", action="store_true", help="show all nine holes")
    ap.add_argument("--robot", type=int, metavar="N", help="watch Qiskit's transpiler play hole N")
    ap.add_argument("--card", metavar="GITHUB_USERNAME",
                    help="score golf.py, then seal your card for the live board")
    ap.add_argument("--key", metavar="FILE",
                    help="the club's season key, saved from your browser, if grade.py can't download it")
    ap.add_argument("--golf", default=str(HERE / "golf.py"), help="your golf file (default: golf.py)")
    args = ap.parse_args(argv)
    if args.card is not None and not GITHUB_NAME.fullmatch(args.card.strip().lstrip("@")):
        print(f"{args.card!r} isn't a GitHub username. Use the name in your GitHub profile's address, "
              "github.com/NAME, for example `python grade.py --card ada-builds`.")
        return 1

    holes = load_holes()
    if args.holes:
        print_holes(holes)
        return 0
    wanted = args.robot or args.hole
    if wanted and wanted not in holes:
        print(f"There's no hole {wanted}. Season 1 has holes 1 to {SEASON_HOLES}: "
              "`python grade.py --holes` shows them all.")
        return 1
    if args.robot:
        h = holes[args.robot]
        r = grade_circuit(h, robot(h))
        print(f"🤖 The robot (Qiskit's transpiler, optimization_level=1) plays {h.title}:\n")
        print(qasm2.loads(r.qasm, include_path=()).draw(output="text"))
        print(describe(r))
        return 0
    golf = Path(args.golf)
    try:
        module = load_golf(golf)
    except FileNotFoundError:
        print(f"Can't find {golf}. Run this from your season folder (the one with golf.py in it).")
        return 1
    except Exception as exc:
        print(f"golf.py has an error before any hole runs: {type(exc).__name__}: {exc}")
        return 1
    if args.hole:
        r = grade_module(module, {args.hole: holes[args.hole]})[0]
        h = r.hole
        print(f"⛳ {h.title}: {h.ket}\n{h.brief}\n")
        if r.qasm:
            drawn = qasm2.loads(r.qasm, include_path=())
            print(drawn.draw(output="text"))
            ops = {g: n for g, n in drawn.count_ops().items() if g != "barrier"}
            print("Gates:", ", ".join(f"{g} x{n}" for g, n in sorted(ops.items())) or "none")
        print(describe(r))
        return 0

    results = grade_module(module, holes)
    for r in results:
        print(describe(r))
    blocks = {r.hole.number: r.qasm for r in results if r.status == "solved"}
    print("\nYour scorecard (scores only, so it's fine to post anywhere):\n")
    print(scorecard(results, blocks))
    if not blocks:
        print("\nNo holes solved yet. Open golf.py and start with hole1().")
        return 1 if args.card is not None else 0
    if args.card is not None:
        return send_card(results, args.card.strip().lstrip("@"), args.key, golf.resolve().parent)
    print("\nTo put this card on the live board, seal it with your GitHub username and send it:\n\n"
          "  python grade.py --card YOUR_GITHUB_USERNAME")
    return 0


if __name__ == "__main__":
    sys.exit(main())
