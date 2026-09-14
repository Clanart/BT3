### Title
Out-of-bounds/unhandled-exception read in `is_atom_canonical` during mempool CLVM canonical-form check on attacker-supplied spend bundle solution bytes - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a raw CLVM-serialized byte buffer taken from a spend bundle's puzzle/solution data during mempool admission, walking the buffer byte-by-byte without validating that `offset` (and `offset + prefix_len`) stay within the buffer bounds before indexing into it. This mirrors the CVE-2016-6238 bug class: a length-prefix-driven parser that trusts attacker-controlled size fields and indexes past the end of a buffer on a crafted/truncated input.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` and then, for length-prefixed atom encodings, loops `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` with no check that `offset` remains `< len(clvm_buffer)` before each read. [1](#0-0) 

`is_clvm_canonical(clvm_buffer)`, the caller, similarly advances `offset` based on attacker-controlled length prefixes (`offset += atom_len` for atoms, `offset += 1` for pairs) inside a `while True` loop, and only checks `clvm_buffer != b""` up front — it never bounds-checks `offset` against `len(clvm_buffer)` while looping, relying on the final `return offset == len(clvm_buffer)` check that is reached only if no earlier out-of-bounds index already raised. [2](#0-1) 

Because Python raises `IndexError` on out-of-bounds `bytes` indexing rather than silently reading adjacent memory, the practical manifestation in this codebase is not a C-style memory-safety violation but an unhandled exception path: a crafted/truncated CLVM buffer (e.g., a multi-byte length-prefix atom header placed at the very end of the buffer, with the trailing length bytes truncated, or an `atom_len` value making `offset` jump past the buffer end while more tokens are still expected) can make `clvm_buffer[offset]` raise `IndexError` inside `is_clvm_canonical()`/`is_atom_canonical()`.

### Impact Explanation
If this exception is not caught by all call sites that invoke `is_clvm_canonical()` on submitted-spend-bundle data during DEDUP-eligibility checks in mempool admission, an unprivileged spend-bundle submitter could trigger an unhandled `IndexError` during `pre_validate_spendbundle()`/mempool processing, causing a spend-triggered halt or crash of transaction processing for that request path — matching the "spend-triggered transaction-processing halt" impact category. This is a mempool-admission-time DoS vector reachable from a single submitted spend bundle, not a network/peer-level or memory-corruption bug.

### Likelihood Explanation
Likelihood is constrained by the fact that: (1) I could not fully confirm within the indexed context whether the call site(s) that invoke `is_clvm_canonical()` wrap it in a broad `try/except` that would convert an `IndexError` into a normal validation failure rather than a crash, and (2) the actual reachable call site(s) beyond the definition in `mempool_manager.py` were not fully enumerated in the available index (test references exist in `chia/_tests/core/mempool/test_mempool_manager.py`, but the specific production call site invoking it on submitted-bundle bytes was not located in the indexed content). This uncertainty is a limitation of the codebase index size rather than a confirmed absence of the call site — a Devin session with full repository access would be needed to trace every caller of `is_clvm_canonical` and confirm whether exception handling wraps it, which determines whether this is an exploitable DoS or merely a benign validation failure.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each indexed read of `clvm_buffer` (verify `offset < len(clvm_buffer)` prior to reading the length-prefix bytes, and verify the computed atom length does not push `offset` past `len(clvm_buffer)`), and in `is_clvm_canonical()`'s main loop (verify `offset < len(clvm_buffer)` before reading `b = clvm_buffer[offset]` each iteration). Return `False` (not canonical) on any bounds violation instead of allowing an `IndexError` to propagate, and ensure any caller treats this function as untrusted-input-safe (either it already catches exceptions defensively, or should be updated to do so).

### Proof of Concept
Conceptual PoC (bounds violation, not verified against a live call site due to indexing limits):
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xC0 encodes a "5+8 bit length prefix" atom (prefix_len=1), but the buffer
# ends right after the header byte, before the 1 required length byte.
truncated_buffer = b"\xc0"
is_clvm_canonical(truncated_buffer)  # raises IndexError instead of returning False
```
This truncated single-byte buffer causes `is_atom_canonical` to compute `prefix_len = 1` and then attempt `clvm_buffer[offset]` at `offset = 1`, which is out of bounds for a 1-byte buffer, raising `IndexError` rather than being handled as an invalid/non-canonical encoding. [3](#0-2)

### Citations

**File:** chia/full_node/mempool_manager.py (L144-183)
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
```

**File:** chia/full_node/mempool_manager.py (L186-227)
```python
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
