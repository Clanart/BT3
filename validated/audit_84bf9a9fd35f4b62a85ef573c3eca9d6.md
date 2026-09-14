### Title
Out-of-bounds buffer read in CLVM canonical-encoding parser via crafted spend bundle puzzle/solution bytes - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a raw serialized-CLVM byte buffer (`clvm_buffer`) by reading a length-prefix byte, computing a multi-byte prefix length, and then repeatedly indexing `clvm_buffer[offset]` while advancing `offset`, exactly the same pattern as the vulnerable `decode_search` function in dhcpcd's `dhcp.c`: a length-prefixed field is decoded and the cursor is advanced without validating that the cursor stays within the bounds of the supplied buffer.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` and then, depending on the high bits of `b`, sets `prefix_len` to up to 5 and loops: [1](#0-0) 
Inside that loop, `offset` is incremented and `clvm_buffer[offset]` is read again with no check that `offset < len(clvm_buffer)`: [2](#0-1) 

`is_clvm_canonical()` drives this in a `while True` loop over the full buffer, again indexing `clvm_buffer[offset]` on every iteration without a preceding bounds check, and calling `is_atom_canonical()` for large-atom prefixes: [3](#0-2) 

If an attacker crafts a CLVM byte sequence whose trailing large-atom length prefix (e.g. the 5-byte-prefix case, `0b11111110` high bits) is truncated — i.e., the buffer ends before all the prefix length bytes it claims to have — the read at `clvm_buffer[offset]` in the prefix-decoding loop, or the next iteration's `b = clvm_buffer[offset]` in `is_clvm_canonical`, will index past the end of the buffer. In Python this raises an unhandled `IndexError` rather than causing memory corruption, but it is the direct analog of the out-of-bounds read in `decode_search`: a length-prefixed decode loop that trusts the encoded length to bound the number of subsequent byte reads instead of bounding reads by the actual remaining buffer size.

### Impact Explanation
If this canonical-encoding check is invoked while processing puzzle-reveal/solution bytes taken from an attacker-submitted spend bundle (the function's docstring describes checking "CLVM serialization" canonicality, which is the kind of check applied to untrusted spend-bundle CLVM before dedup/fast-forward eligibility decisions), an uncaught `IndexError` thrown from deep inside this parser would propagate up through spend-bundle processing. Depending on where the exception is or isn't caught by the caller, this can result in a spend-triggered exception that aborts processing of that mempool item or, if the exception surfaces at a point not wrapped in error handling, disrupts mempool/transaction-processing flow — a DoS analogous to the original CVE's denial-of-service classification.

### Likelihood Explanation
Reachability requires that this canonical-check path is invoked on attacker-controlled CLVM bytes during spend-bundle admission (e.g., as part of dedup/fast-forward eligibility checks referenced in the surrounding module). I was not able to fully confirm, within the available tool budget, the exact call site(s) that invoke `is_clvm_canonical()`/`is_atom_canonical()` with untrusted spend-bundle bytes versus only test-code call sites (`chia/_tests/core/mempool/test_mempool_manager.py` also references these functions). This caller-context confirmation is the main open uncertainty in this finding.

### Recommendation
Add explicit bounds checks (`offset < len(clvm_buffer)`) before every `clvm_buffer[offset]` read in `is_atom_canonical()` and in the `is_clvm_canonical()` loop, raising a controlled/handled error (rather than letting an `IndexError` escape) when the buffer is truncated relative to its own encoded length prefixes — mirroring the bounds checks already used in the sibling `skip_bytes`/`skip_list` helpers in `chia/full_node/full_block_utils.py`, which explicitly validate length against remaining buffer size before slicing.

### Proof of Concept
1. Construct a `clvm_buffer` ending in a byte whose top bits are `0b11111100` (5-byte length prefix, per `elif (b & 0b11111110) == 0b11111100` branch), but truncate the buffer so fewer than 5 additional bytes follow.
2. Call `is_atom_canonical(clvm_buffer, offset)` with `offset` pointing at that truncated marker byte, or drive it via `is_clvm_canonical(clvm_buffer)`.
3. The loop `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` reads past the end of `clvm_buffer`, raising `IndexError`, uncaught by the function itself. [4](#0-3)

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
