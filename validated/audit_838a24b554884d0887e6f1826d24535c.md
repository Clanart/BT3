### Title
Out-of-bounds byte dereference before bounds check in mempool CLVM canonical-serialization checker (`is_atom_canonical`) - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` decode a CLVM atom's variable-length size prefix (1–6 bytes) by walking `offset` forward and dereferencing `clvm_buffer[offset]` for each prefix byte, but never checks `offset < len(clvm_buffer)` before doing so. This is the same bug class as CVE-2017-9054 (`_dwarf_decode_s_leb128_chk()`): a length-prefixed/variable-length integer decoder that dereferences the next byte before verifying it is still inside the buffer, so a truncated/malformed multi-byte length prefix at the tail of the buffer causes an out-of-bounds read.

### Finding Description
`is_atom_canonical()` reads the tag byte at `offset` to determine `prefix_len` (0–5 extra bytes for the atom's length field), then loops `prefix_len` times, incrementing `offset` and immediately dereferencing `clvm_buffer[offset]` on each iteration without any check that `offset` remains within `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` walks a full CLVM-serialized buffer token by token and calls `is_atom_canonical(clvm_buffer, offset)` for every atom it encounters, using the returned `atom_len` to advance its own `offset`, again with no length check before the byte accesses inside `is_atom_canonical`: [2](#0-1) 

If a caller passes a buffer where the last atom's tag byte declares a multi-byte length prefix (e.g. `0xC0`, `0xE0`, `0xF0`, …) but the buffer ends before all the declared prefix bytes are present, `clvm_buffer[offset]` will index past the end of the `bytes` object.

### Impact Explanation
In CPython, indexing a `bytes` object out of range does not cause native memory corruption (unlike the C-based libdwarf original); it raises an uncaught `IndexError`. Depending on where this canonicality check is invoked in the mempool admission / DEDUP-eligibility pipeline for a submitted spend bundle's solution, an attacker-controlled truncated-length-prefix buffer could propagate an unhandled `IndexError` out of spend-bundle processing, potentially halting or crashing the transaction-processing path for that request (a spend-triggered processing halt) rather than causing memory disclosure. This matches the "spend-triggered transaction-processing halt" impact category.

### Likelihood Explanation
I was not able to fully confirm, within the available tool budget, the exact call site(s) that invoke `is_clvm_canonical()`/`is_atom_canonical()` against attacker-supplied solution bytes (e.g., in the DEDUP-eligibility path in `chia/full_node/eligible_coin_spends.py` or `chia/full_node/mempool.py`), nor whether that call site wraps the check in a try/except that would downgrade this to a benign rejection rather than an unhandled exception. The grep results show additional references to these functions inside `mempool_manager.py` beyond their definitions, and existing tests reference them in `chia/_tests/core/mempool/test_mempool_manager.py`, but I could not verify the surrounding exception handling in the remaining iteration budget.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (both for the initial tag byte and inside the `for i in range(prefix_len)` loop), returning a non-canonical/invalid result instead of indexing out of range. Ensure `is_clvm_canonical()` and all its callers treat any bounds violation as "not canonical" (or explicitly catch `IndexError`) rather than allowing an exception to propagate out of spend-bundle validation.

### Proof of Concept
Construct a CLVM buffer whose last atom byte is a multi-byte length-prefix tag with insufficient trailing bytes, e.g. `bytes([0xF0])` (declares a 3-byte length prefix but supplies zero of the three follow-up bytes) as the final/only atom in the buffer, then call:
```python
from chia.full_node.mempool_manager import is_clvm_canonical
is_clvm_canonical(bytes([0xF0]))
```
This raises an unhandled `IndexError` from `is_atom_canonical()` when it attempts `clvm_buffer[offset]` beyond the 1-byte buffer, analogous to the pre-bounds-check dereference in CVE-2017-9054.

**Note:** I could not confirm the concrete production call path that feeds attacker-controlled solution bytes into `is_clvm_canonical()` (needed to fully establish DoS reachability from a single submitted spend bundle) within the available investigation budget; a Devin session with full repo/tool access is recommended to trace `eligible_coin_spends.py`/`mempool.py` call sites and confirm whether an unhandled exception can actually escape spend-bundle processing.

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
