### Title
Out-of-bounds read in CLVM atom length-prefix parsing during mempool canonical-encoding check - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse the CLVM atom length prefix directly out of attacker-supplied bytes (a spend bundle's coin-spend solution) without ever checking the computed offsets against the length of `clvm_buffer`. This mirrors the OpenSSL X.509 bug class (CWE-125): a length/prefix value taken from untrusted input is used to index into a buffer with no bounds check before the read.

### Finding Description
`is_atom_canonical` reads the first byte at `offset` to determine how many additional length-prefix bytes follow (`prefix_len`, up to 5), then loops reading `clvm_buffer[offset]` for each of those bytes: [1](#0-0) 

There is no check anywhere in this function that `offset + prefix_len < len(clvm_buffer)` before indexing. The caller, `is_clvm_canonical`, walks the whole buffer token-by-token, repeatedly calling `is_atom_canonical(clvm_buffer, offset)` and advancing `offset` by the returned `atom_len`, again with no bounds validation of `atom_len` against the remaining buffer size: [2](#0-1) 

Because `clvm_buffer` originates from a spend bundle's coin-spend solution content submitted by an unprivileged party, an attacker can craft a solution whose encoded length prefix (e.g. the 5+8+8+8+8-bit form, `0b11111100` pattern) claims a byte count that extends past the end of the actual buffer, or where the final `atom_len` pushes `offset` beyond `len(clvm_buffer)`. In C/Rust-style buffers this class of bug is a classic heap read overrun (as in the OpenSSL advisory); in this Python implementation the equivalent failure mode is an unhandled `IndexError` raised from the byte-indexing operations, since there is no explicit length validation guarding the reads.

### Impact Explanation
If this canonical-encoding check is invoked on attacker-controlled solution bytes before being wrapped in adequate exception handling, a single malicious spend bundle can trigger an unhandled `IndexError` deep in mempool processing logic, which is a spend-triggered transaction-processing halt (denial of service) for the path that evaluates whether a submitted spend's CLVM encoding is canonical (used for CLVM fast-forward / dedup eligibility decisions in the mempool). This matches the "no bounds check before indexing an attacker-supplied length field" root cause of the reported OpenSSL CVE-2022-4203, translated to Python's IndexError-as-crash equivalent of a buffer overread.

### Likelihood Explanation
Likelihood depends on whether all call sites of `is_clvm_canonical`/`is_atom_canonical` wrap this logic in exception handling that catches `IndexError` and converts it into a normal validation rejection (`Err.INVALID_SPEND_BUNDLE` or similar) rather than letting it propagate and crash the mempool worker. I was not able to confirm the exact call site and its surrounding exception handling before running out of search budget — the grep results indicated `is_clvm_canonical` is referenced/tested in `chia/_tests/core/mempool/test_mempool_manager.py` and used twice within `chia/full_node/mempool_manager.py`, but I could not view the precise invocation context (e.g., whether it sits inside a `try/except` in `pre_validate_spendbundle` or a related helper) to determine if the unhandled-exception path is actually reachable in production versus already mitigated.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (verify `offset + prefix_len < len(clvm_buffer)` before the prefix-byte loop, and verify the final `1 + prefix_len + atom_len` does not exceed the remaining buffer) and in `is_clvm_canonical` (verify `offset` stays within `len(clvm_buffer)` after each `atom_len` advance), converting any detected overrun into a `False`/rejection result instead of relying on Python's default `IndexError` for out-of-range indexing.

### Proof of Concept
Not independently verified end-to-end (could not confirm the exact call site/exception handling in the remaining budget). Conceptually: craft a CLVM-encoded solution byte string whose leading atom uses the 5-byte length-prefix form (top byte `0b111111xx`) but is truncated so that the declared `prefix_len` bytes or the computed `atom_len` extend past the actual buffer length, then submit a spend bundle containing this solution; if the canonical-check path lacks exception handling around the indexing, this raises an unhandled `IndexError` during mempool processing of that single spend bundle.

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
