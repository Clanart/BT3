### Title
Unbounded read in `is_atom_canonical` when checking CLVM atom length prefixes near buffer end - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical` reads the length-prefix bytes of a CLVM atom directly from `clvm_buffer` using an `offset` that is advanced by `prefix_len` (up to 5) without ever checking that `offset` stays within `len(clvm_buffer)`. Its only caller, `is_clvm_canonical`, likewise advances `offset` and dereferences `clvm_buffer[offset]` without first confirming the "count of available bytes" remaining is sufficient for the token being decoded — this is exactly the bug class described in CVE-2023-25752 ("the count of available bytes needed to be checked in the calling function to be within bounds"). [1](#0-0) 

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` inspects the byte at `offset` to determine the length-prefix encoding (0–5 extra bytes depending on the high bits of the first byte), then loops `prefix_len` times incrementing `offset` and indexing `clvm_buffer[offset]` again: [2](#0-1) 

There is no check anywhere in this function (or in its single caller) that `offset + prefix_len < len(clvm_buffer)` before these reads occur. The caller, `is_clvm_canonical`, walks the buffer token-by-token and calls `is_atom_canonical` whenever it sees a byte `> 0x80` (indicating a multi-byte atom), again with no bounds validation before making the call: [3](#0-2) 

A crafted CLVM buffer ending with a truncated multi-byte atom prefix (e.g., a byte such as `0xFE`-adjacent value like `0xFC` indicating a 5-byte length prefix but with fewer than 5 bytes remaining in the buffer) causes `clvm_buffer[offset]` to be evaluated with `offset >= len(clvm_buffer)`, raising an uncaught `IndexError` in Python (an out-of-bounds memory read in bytes-object terms, distinct from an `IndexError` raised deliberately with a bounds check).

### Impact Explanation
`is_clvm_canonical` is used to determine DEDUP-eligibility of solutions during mempool spend-bundle processing, meaning it is invoked on attacker-supplied CLVM solution bytes from a submitted spend bundle before full validation. An out-of-bounds/`IndexError` here is uncaught by the function itself, and if not caught by a broad exception handler higher in the mempool-add pipeline, this can cause the spend-bundle processing task to raise an unexpected exception for a specific unprivileged, low-cost input — a spend-triggered processing halt/DoS on the node handling mempool admission, consistent with the CVE's "future code to be incorrect" consequence stemming from unchecked available-byte counts.

### Likelihood Explanation
Likelihood is limited by the fact that this canonical-serialization check path is reachable only for spends that take the DEDUP-eligible code path and only when the CLVM buffer parser encounters an atom-length-prefix byte near the very end of the buffer, a case that is not covered by the existing `full_block_utils.py` bounds-checked parsers (`skip_bytes`, `skip_list`, etc., which explicitly guard length vs. remaining buffer). Since `is_clvm_canonical`/`is_atom_canonical` lack any equivalent explicit bounds check, an attacker crafting a minimal spend bundle with a truncated multi-byte atom prefix at the tail of a solution's CLVM serialization can reliably trigger it without needing privileged access — it only requires submitting a spend bundle to a node's mempool.

### Recommendation
Add explicit remaining-length checks in `is_atom_canonical` (and in the calling loop in `is_clvm_canonical`) before indexing `clvm_buffer[offset]`, mirroring the pattern already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list` (i.e., raise/return not-canonical when `offset + prefix_len >= len(clvm_buffer)` rather than reading out of bounds), and ensure any exception from this path is caught and translated into a normal mempool-rejection error rather than propagating unhandled.

### Proof of Concept
Conceptually: construct a CLVM buffer whose last byte is `0xFC` (5-byte length prefix indicator per the mask table in `is_atom_canonical`) with zero or fewer than 5 trailing bytes, then call `is_clvm_canonical(buffer)`. The loop in `is_atom_canonical` at lines 177–183 will attempt `clvm_buffer[offset]` past the end of the buffer, raising an uncaught `IndexError` instead of returning `False`/handling the truncation gracefully — analogous to the linked test patterns in `chia/_tests/util/test_full_block_utils.py` (`test_skip_bytes_rejects_short_length_prefix`, etc.) that exist for the block-parsing bounds checks but have no equivalent for `is_atom_canonical`/`is_clvm_canonical`. [4](#0-3)

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

**File:** chia/full_node/mempool_manager.py (L196-221)
```python
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
```

**File:** chia/_tests/util/test_full_block_utils.py (L159-161)
```python
def test_skip_bytes_rejects_short_length_prefix() -> None:
    with pytest.raises(ValueError, match="byte length prefix requires 4 bytes, remaining buffer 1"):
        skip_bytes(memoryview(b"\x00"))
```
