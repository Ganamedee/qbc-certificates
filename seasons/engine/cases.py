"""Season 3's personal cases: every GitHub account gets a case of its own, made from SEASONS_SEED and its account id.

Only the score job holds the seed. It derives one case key from it, and from that a seed for each account, which it
hands to child.py on stdin and nowhere else: never to results.json, the state, a comment, a board or a log. The post
job never sees any of them, and checks every case it publishes against its fingerprint instead.
"""

from __future__ import annotations

import hashlib
import hmac

from . import seal

CASE_DIR = "seasons/cases"     # where the post job writes each builder's case, in the claims repo


def case_key(seed: str) -> bytes:
    """The key every Season 3 case is made from, derived from the SEASONS_SEED secret."""
    return seal.hkdf(seed.strip().encode(), b"", b"qbc seasons season 3 cases v1", 32)


def case_seed(key: bytes, account_id: int) -> bytes:
    """The seed of one GitHub account's case. The account id never changes, so a case can never be re-rolled."""
    if not isinstance(account_id, int) or isinstance(account_id, bool) or account_id < 1:
        raise ValueError("a case needs a GitHub account id")
    return hmac.new(key, f"qbc season 3 case v1 account {account_id}".encode(), hashlib.sha256).digest()
