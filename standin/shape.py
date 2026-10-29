"""A keyed permutation that keeps a value's shape.

A stand-in for an ID has to do three things: look like the original (same
length, letters where there were letters, digits where there were digits),
come out the same every time for the same key, and never give two different
originals the same stand-in. The last one is what keeps primary keys unique and
joins working, and no hash can promise it. So the letters and digits of a value
are read as one number in a mixed radix (10 for a digit, 26 for a letter), the
number goes through a keyed Feistel network, and the result is written back
into the same pattern. A Feistel network is a permutation whatever its round
function is, so two inputs can't land on the same output.

This is the shape of NIST's FF1 (SP 800-38G), with keyed BLAKE2b as the round
function because the standard library has no AES. It is not FF1 and nobody has
reviewed it as a cipher. Anyone who holds the key can run it backwards.

Nothing here knows about CSV files or rules, so it can be tested on its own.
"""

import string
from hashlib import blake2b
from math import isqrt

ROUNDS = 10  # the number FF1 uses
SPARE_BITS = 128  # extra round-function output, so reducing it modulo the half-domain has no visible bias


def prf(key: bytes, message: bytes) -> bytes:
    """64 bytes that look random to anyone without the key.

    Keyed BLAKE2b (RFC 7693) is a MAC on its own, without HMAC's wrapping, and
    it measured six times faster than hmac.digest with SHA-256 here, which
    matters at forty calls a row.
    """
    return blake2b(message, key=key).digest()


def permute(x: int, n: int, key: bytes, tweak: bytes) -> int:
    """Map x in range(n) to another number in range(n), one to one.

    The Feistel network works on a square domain of size*size, which is at least
    n. When a result falls outside range(n) it is fed back in until it lands
    inside ("cycle walking"). That stays a permutation of range(n) and takes
    fewer than two passes on average.
    """
    if n < 2:
        return x
    size = isqrt(n - 1) + 1
    width = (size.bit_length() + 7) // 8
    counters = [j.to_bytes(4, "big") for j in range((size.bit_length() + SPARE_BITS + 511) // 512)]
    # The tweak is length-prefixed and everything after it is fixed-width, so
    # two different inputs to the round function never share a message.
    prefix = len(tweak).to_bytes(4, "big") + tweak
    while True:
        left, right = divmod(x, size)
        for i in range(ROUNDS):
            message = prefix + bytes((i,)) + right.to_bytes(width, "big")
            stream = b"".join([prf(key, message + j) for j in counters])
            left, right = right, (left + int.from_bytes(stream, "big")) % size
        x = left * size + right
        if x < n:
            return x


def restyle(value: str, key: bytes, tweak: bytes = b"") -> str:
    """Return a stand-in for value with the same length and pattern.

    Each ASCII digit becomes a digit and each ASCII letter becomes a letter of
    the same case. Everything else (punctuation, spaces, other scripts) stays
    where it is and is part of the tweak, so "AB-12" and "AB/12" are permuted
    independently. Raises nothing; checking that a value is safe to restyle is
    the caller's job.
    """
    pattern = []
    radices = []
    x = 0
    for c in value:
        if c in string.digits:
            pattern.append("9")
            radices.append(10)
            x = x * 10 + int(c)
        elif c in string.ascii_letters:
            pattern.append("A" if c.isupper() else "a")
            radices.append(26)
            x = x * 26 + ord(c.lower()) - ord("a")
        else:
            # A literal can never be mistaken for a class marker, because the
            # markers are ASCII letters and digits, which always get a class.
            pattern.append(c)
    n = 1
    for r in radices:
        n *= r
    x = permute(x, n, key, tweak + b"\0" + "".join(pattern).encode())

    out = []
    for marker in reversed(pattern):
        if marker == "9":
            x, d = divmod(x, 10)
            out.append(string.digits[d])
        elif marker in "Aa":
            x, d = divmod(x, 26)
            out.append(string.ascii_uppercase[d] if marker == "A" else string.ascii_lowercase[d])
        else:
            out.append(marker)
    return "".join(reversed(out))
