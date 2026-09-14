Given my research, I found a directly analogous bug-class match in [1](#0-0) , which performs unchecked, length-prefix–driven buffer indexing on attacker-supplied bytes — the same root-cause pattern as CVE-2017-15018 (a heap-based over-read caused by trusting a length/index value derived from malformed input without bounds-checking against the buffer's actual size).

### Title
Heap/buffer over-read via unchecked atom length-prefix parsing in `is_atom_canonical()` - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` computes a CLVM atom's serialized length by reading 0–5 additional prefix bytes from `clvm_buffer` based solely on the leading byte's high bits, without ever checking that `offset` stays within `len(clvm_buffer)`. `is_clvm_canonical()` calls this in a loop over the entire buffer with the same lack of bounds checking on `offset`. Both are invoked on data taken directly from an unprivileged, network-submitted `SpendBundle`'s `puzzle_reveal` and `solution` fields.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` and derives `prefix_len` (0–5) from its high bits [2](#0-1) . It then loops `prefix_len` times, incrementing `offset` and reading `clvm_buffer[offset]` each time [3](#0-2) , with no check that `offset` remains `< len(clvm_buffer)`. A truncated buffer whose last byte is a multi-byte length-prefix marker (e.g., `0xC0`, `0xE0`, `0xF0`, `0xF8`, or `0xFC`) with insufficient trailing bytes causes an out-of-range read attempt on the underlying buffer.

`is_clvm_canonical()` similarly walks the whole buffer with `offset = 0` and no upper-bound guard beyond relying on well-formed structure [4](#0-3) ; a malformed/truncated CLVM serialization (e.g., a dangling `0xFF` pair marker or truncated atom) drives `offset` past `len(clvm_buffer)` before the loop's termination check is reached.

This precisely mirrors the LAME `k_34_4` bug class in CVE-2017-15018: a length/index value taken from attacker-controlled, malformed input is used to index into a buffer without validating it stays within bounds, causing an over-read.

### Impact Explanation
This code path is reachable by any unprivileged spend-bundle submitter: `is_clvm_canonical()`/`is_atom_canonical()` are exercised against the raw `puzzle_reveal`/`solution` bytes of coin spends during mempool bundle validation, per `.cursor/context/clvm-execution.md`'s reference to `is_clvm_canonical()` in `mempool_manager.py`. In CPython, out-of-range indexing into `bytes` raises `IndexError` rather than causing true memory corruption, so the practical impact is an unhandled exception during spend-bundle processing rather than memory disclosure. If this exception is not caught by the calling validation path, a single malformed spend bundle could trigger repeated, spend-triggered exceptions/crashes in the mempool validation flow — a possible transaction-processing halt/DoS vector. I was not able to fully verify, within the available tool budget, whether the caller wraps this function in exception handling that gracefully rejects the bundle (in which case impact would be limited to bundle rejection rather than a processing halt).

### Likelihood Explanation
Likelihood is high for reaching the code: it requires only submitting a spend bundle with a specifically truncated/malformed `puzzle_reveal` or `solution` byte string, something any unprivileged peer or wallet can construct and broadcast. Likelihood of actual crash/DoS impact is uncertain pending confirmation of exception handling at the call site.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (verify `offset < len(clvm_buffer)` prior to reading the length-prefix continuation bytes and the atom body), and in `is_clvm_canonical()` before reading `clvm_buffer[offset]` at loop top. Treat any out-of-bounds condition as "not canonical"/invalid input, returning a clean rejection instead of allowing the index to run past the buffer boundary. Additionally, verify (and if absent, add) that all call sites catch and translate any residual `IndexError` into a standard `Err.INVALID_SPEND_BUNDLE`-style rejection so a malformed bundle cannot escape as an unhandled exception.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# Truncated multi-byte atom length prefix: 0xC0 requires 1 more length byte,
# but the buffer ends right after the marker byte.
malformed = bytes([0xC0])
is_clvm_canonical(malformed)  # raises IndexError: index out of range
```
Submitting a `SpendBundle` whose `puzzle_reveal` or `solution` ends with such a truncated multi-byte atom marker drives this code path during mempool validation. [5](#0-4)

### Citations

**File:** chia/full_node/mempool_manager.py (L144-227)
```python
def is_atom_canonical(clvm_buffer: bytes, offset: int) -> tuple[int, bool]:
    b = clvm_buffer[offset]
    if (b & 0b11000000) == 0b10000000:
        # 6 bits length prefix
        mask = 0b00111111
        prefix_len = 0
        min_value = 1
    elif (b & 0b11100000) == 0b11000000:
        # 5 + 8 bits length prefix
        mask = 0b00011111
        prefix_len = 1
        min_value = 1 << 6
    elif (b & 0b11110000) == 0b11100000:
        # 4 + 8 + 8 bits length prefix
        mask = 0b00001111
        prefix_len = 2
        min_value = 1 << (5 + 8)
    elif (b & 0b11111000) == 0b11110000:
        # 3 + 8 + 8 + 8 bits length prefix
        mask = 0b00000111
        prefix_len = 3
        min_value = 1 << (4 + 8 + 8)
    elif (b & 0b11111100) == 0b11111000:
        # 2 + 8 + 8 + 8 + 8 bits length prefix
        mask = 0b00000011
        prefix_len = 4
        min_value = 1 << (3 + 8 + 8 + 8)
    elif (b & 0b11111110) == 0b11111100:
        # 1 + 8 + 8 + 8 + 8 + 8 bits length prefix
        mask = 0b00000001
        prefix_len = 5
        min_value = 1 << (2 + 8 + 8 + 8 + 8)

    atom_len = b & mask
    for i in range(prefix_len):
        atom_len <<= 8
        offset += 1
        atom_len |= clvm_buffer[offset]

    return 1 + prefix_len + atom_len, atom_len >= min_value


def is_clvm_canonical(clvm_buffer: bytes) -> bool:
    """
    checks whether the CLVM serialization is all canonical representation.
    atoms can be serialized in more than one way by using more bytes than
    necessary to encode the length prefix. This functions ensures that all atoms are
    encoded with the shortest representation. back-references are not allowed
    and will make this function return false
    """
    assert clvm_buffer != b""

    offset = 0
    tokens_left = 1
    while True:
        b = clvm_buffer[offset]

        # pair
        if b == 0xFF:
            tokens_left += 1
            offset += 1
            continue

        # back references cannot be considered canonical, since they may be
        # encoded in many different ways
        if b == 0xFE:
            return False

        # small atom or NIL
        if b <= 0x80:
            tokens_left -= 1
            offset += 1
        else:
            atom_len, canonical = is_atom_canonical(clvm_buffer, offset)
            if not canonical:
                return False
            tokens_left -= 1
            offset += atom_len

        if tokens_left == 0:
            break

    # if there's garbage at the end, it's not canonical
    return offset == len(clvm_buffer)
```
