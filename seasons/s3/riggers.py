"""Season 3's case maker: twelve files of bits, and between three and five of them rigged.

Every builder's case comes from a seed of their own. The club's scoring workflow makes each real case from a seed
that only it knows, so this file can be public: it says how every flaw works, but never which of your files has
one. `python detective.py --practice` uses it to make practice cases from seeds anyone can see, answers included.

Every bit comes from SHAKE-256, a cryptographic generator, so no file gives away anything about another.
Standard library only.
"""

from __future__ import annotations

import base64
import hashlib

VERSION = 1
FILES = 12
BITS = 24_000
LOW, HIGH = 3, 5                 # how many files in a case are rigged, at least and at most
FLAWS = ("loaded", "sticky", "warming", "lazy", "loop")
NAMES = {"loaded": "🪙 the loaded coin", "sticky": "🧲 the sticky coin", "warming": "🌡️ the warming coin",
         "lazy": "🧱 the lazy bit", "loop": "🔁 the short loop"}

# How strong each flaw is. Each one is tuned so the test that suits it scores about 5.5 standard deviations on
# average (owner/README.md has the measurements), while the tests that don't suit it see next to nothing.
LOADED = 0.0178                  # 1 comes up with probability 0.5 + LOADED, or 0 does
STICKY = 0.0178                  # each bit repeats the one before with probability 0.5 + STICKY
WARMING = 0.0355                 # the chance of a 1 drifts from 0.5 - WARMING to 0.5 + WARMING, or back
LAZY = 0.0502                    # one position in every byte: 1 with probability 0.5 + LAZY, or 0 does
LOOP = (9_000, 17_000)           # the short loop starts again after this many bits, at least and at most

HEADER = "QBC Season 3 case v1"


class Stream:
    """Random bytes from a seed and a label: SHAKE-256 in counter mode."""

    def __init__(self, seed: bytes, label: str) -> None:
        self._prefix = b"qbc season 3 v1|" + bytes(seed) + b"|" + label.encode() + b"|"
        self._block = 0
        self._buf = b""
        self._pos = 0

    def take(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            if self._pos >= len(self._buf):
                self._buf = hashlib.shake_256(self._prefix + self._block.to_bytes(8, "big")).digest(1 << 16)
                self._block += 1
                self._pos = 0
            chunk = self._buf[self._pos:self._pos + n - len(out)]
            self._pos += len(chunk)
            out += chunk
        return bytes(out)

    def u32s(self, n: int) -> list:
        data = self.take(4 * n)
        return [int.from_bytes(data[i:i + 4], "big") for i in range(0, 4 * n, 4)]

    def below(self, n: int) -> int:
        """A uniform whole number from 0 to n - 1, with no bias (rejection sampling)."""
        limit = (1 << 32) - (1 << 32) % n
        while True:
            (u,) = self.u32s(1)
            if u < limit:
                return u % n

    def bits(self, n: int) -> list:
        data = self.take((n + 7) // 8)
        return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)][:n]


def _threshold(p: float) -> int:
    return round(p * (1 << 32))


def layout(seed: bytes) -> dict:
    """{file number: (flaw, its settings)} for the rigged files of the case this seed makes."""
    s = Stream(seed, "layout")
    count = LOW + s.below(HIGH - LOW + 1)
    files = list(range(1, FILES + 1))
    flaws = list(FLAWS)
    for items in (files, flaws):
        for i in range(len(items) - 1, 0, -1):           # Fisher-Yates
            j = s.below(i + 1)
            items[i], items[j] = items[j], items[i]
    out = {}
    for number, flaw in zip(sorted(files[:count]), flaws[:count]):
        if flaw in ("loaded", "warming"):
            settings = {"sign": 1 if s.below(2) else -1}
        elif flaw == "lazy":
            settings = {"position": s.below(8), "sign": 1 if s.below(2) else -1}
        elif flaw == "loop":
            settings = {"period": LOOP[0] + s.below(LOOP[1] - LOOP[0] + 1)}
        else:
            settings = {}
        out[number] = (flaw, settings)
    return out


def make_file(seed: bytes, number: int, flaw: str | None = None, settings: dict | None = None) -> list:
    """File `number` of the case: BITS bits, fair if flaw is None."""
    s = Stream(seed, f"file {number}")
    settings = settings or {}
    if flaw is None:
        return s.bits(BITS)
    if flaw == "loop":
        start = s.bits(settings["period"])
        return [start[i % len(start)] for i in range(BITS)]
    draws = s.u32s(BITS)
    if flaw == "loaded":
        t = _threshold(0.5 + settings["sign"] * LOADED)
        return [1 if u < t else 0 for u in draws]
    if flaw == "sticky":
        t = _threshold(0.5 + STICKY)
        out = [draws[0] >> 31]
        for u in draws[1:]:
            out.append(out[-1] if u < t else 1 - out[-1])
        return out
    if flaw == "warming":
        sign = settings["sign"]
        return [1 if u < _threshold(0.5 + sign * WARMING * (2 * i / (BITS - 1) - 1)) else 0
                for i, u in enumerate(draws)]
    if flaw == "lazy":
        lean = _threshold(0.5 + settings["sign"] * LAZY)
        position = settings["position"]
        return [(1 if u < lean else 0) if i % 8 == position else u >> 31 for i, u in enumerate(draws)]
    raise ValueError(f"no such flaw: {flaw}")


def make_case(seed: bytes) -> tuple:
    """(files, layout): files is {number: bits} for all twelve."""
    rig = layout(seed)
    files = {n: make_file(seed, n, *rig.get(n, (None, None))) for n in range(1, FILES + 1)}
    return files, rig


def pack(bits: list) -> bytes:
    out = bytearray()
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def unpack(data: bytes, n: int = BITS) -> list:
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)][:n]


def fingerprint(files: dict) -> str:
    """The case's fingerprint: SHA-256 of all twelve files, in order."""
    digest = hashlib.sha256(HEADER.encode() + b"\n")
    for n in range(1, FILES + 1):
        digest.update(pack(files[n]))
    return digest.hexdigest()


def case_text(files: dict, login: str, account: int) -> str:
    """The case as the workflow publishes it, in seasons/cases/season-03 in the claims repo."""
    lines = [HEADER, f"github: {login}", f"account: {account}", f"fingerprint: {fingerprint(files)}"]
    lines += [f"{n:02d} {base64.b64encode(pack(files[n])).decode()}" for n in range(1, FILES + 1)]
    return "\n".join(lines) + "\n"


class CaseError(ValueError):
    """A case file that can't be read. The message is safe to show."""


def read_case_text(text: str) -> dict:
    """{"github", "account", "fingerprint", "files"} from a published case, checked against its fingerprint."""
    lines = [line.strip() for line in (text or "").strip().splitlines()]
    if len(lines) != 4 + FILES or lines[0] != HEADER:
        raise CaseError("that isn't a Season 3 case file")
    fields = {}
    for line, key in zip(lines[1:4], ("github", "account", "fingerprint")):
        if not line.startswith(key + ": "):
            raise CaseError("the case file is damaged")
        fields[key] = line[len(key) + 2:]
    files = {}
    for n, line in enumerate(lines[4:], 1):
        if not line.startswith(f"{n:02d} "):
            raise CaseError("the case file is damaged")
        try:
            data = base64.b64decode(line[3:], validate=True)
        except ValueError:
            raise CaseError("the case file is damaged") from None
        if len(data) * 8 != BITS:
            raise CaseError("the case file is damaged")
        files[n] = unpack(data)
    if not fields["account"].isdigit() or fingerprint(files) != fields["fingerprint"]:
        raise CaseError("the case file is damaged")
    return {"github": fields["github"], "account": int(fields["account"]), "fingerprint": fields["fingerprint"],
            "files": files}


def practice_seed(name: str) -> bytes:
    """The seed of practice case `name`: anyone can make it, so its answers are no secret."""
    return hashlib.sha256(b"qbc season 3 practice|" + str(name).encode()).digest()


def check_calls(calls) -> list:
    """The files a set of calls says are rigged, as sorted two-digit numbers ("01" to "12"). Raises CaseError."""
    if not isinstance(calls, (list, tuple)) or len(calls) > FILES:
        raise CaseError("the calls aren't a list of file numbers")
    out = set()
    for call in calls:
        if not (isinstance(call, str) and len(call) == 2 and call.isdigit() and 1 <= int(call) <= FILES):
            raise CaseError("the calls aren't a list of file numbers")
        out.add(call)
    if len(out) != len(calls):
        raise CaseError("a file is called twice")
    return sorted(out)


def score(calls, rig: dict) -> dict:
    """What a set of calls scores against a case's layout: +1 for each rigged file called, -1 for each fair one."""
    called = {int(c) for c in check_calls(list(calls))}
    caught = sorted(n for n in called if n in rig)
    flaws = {rig[n][0]: ("caught" if n in called else "missed") for n in sorted(rig)}
    return {"caught": len(caught), "false_alarms": len(called) - len(caught), "rigged": len(rig),
            "score": len(caught) - (len(called) - len(caught)), "flaws": flaws}
