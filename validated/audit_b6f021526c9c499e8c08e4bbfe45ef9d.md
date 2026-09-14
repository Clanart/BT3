### Title
Unvalidated CLVM atom length prefix causes unhandled `IndexError` in mempool canonical-serialization check - ([File: chia/full_node/mempool_manager.py])

### Summary
The OSV report describes a heap overflow in OpenEXR caused by failing to validate a count field (scanline sample count) before it is used to index/read memory. The analogous bug class — a length/count value taken from untrusted attacker-controlled input and used to index a buffer without a bounds check — exists in `is_atom_canonical`/`is_clvm_canonical` in `chia/full_node/mempool_manager.py`, which is invoked on every submitted spend bundle's `puzzle_reveal` and `solution` during mempool admission.

### Finding Description
`is_atom_canonical` reads a length-prefix byte and then loops `prefix_len` times reading subsequent bytes from `clvm_buffer[offset]` to build `atom_len`, with no check that `offset` stays within `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical` drives this loop over an attacker-supplied CLVM buffer (`puzzle_reveal`/`solution` bytes) and likewise indexes `clvm_buffer[offset]` without a bounds check before calling `is_atom_canonical`: [2](#0-1) 

This function is called directly during `validate_spend_bundle`, on the raw bytes of every `coin_spend.puzzle_reveal` and `coin_spend.solution` in a submitted `SpendBundle`, before any other structural validation of the atom occurs: [3](#0-2) 

A crafted puzzle reveal or solution ending in a multi-byte atom-length prefix (e.g. a final byte such as `0xF8`–`0xFE` indicating 2–5 additional length bytes) that is truncated at the very end of the buffer will cause the `for i in range(prefix_len)` loop to read `clvm_buffer[offset]` past the end of the buffer, raising an unhandled `IndexError` in pure Python (Python's equivalent of an out-of-bounds read; unlike the C-based OpenEXR case this cannot corrupt heap memory, but it does trigger an uncaught exception in a size/count-validation routine that was clearly meant to reject malformed input rather than crash on it).

### Impact Explanation
`validate_spend_bundle` is invoked from `add_spend_bundle`, on the code path used to admit externally submitted spend bundles into the mempool: [4](#0-3) 

An unhandled `IndexError` raised out of `is_clvm_canonical` during this call is not caught anywhere in the shown validation/add flow, meaning a single malformed but otherwise unsigned/unvalidated spend bundle submitted by any unprivileged peer can raise an unexpected exception in the mempool admission path. This matches the "spend-triggered transaction-processing halt" impact category permitted by the report's validation rules — a single crafted spend bundle disrupts mempool processing for that bundle (and potentially propagates as an unhandled task exception depending on the caller context), rather than any memory corruption (Python is memory-safe, so no heap overflow / RCE analog exists here).

### Likelihood Explanation
Likelihood is high for the crash trigger itself: any unprivileged party who can submit a spend bundle to a full node's mempool (e.g. via RPC or peer gossip) controls the exact bytes of `puzzle_reveal` and `solution`, and can trivially construct a buffer whose last byte is a multi-byte atom-length-prefix marker with the trailing length bytes omitted, forcing `is_atom_canonical` to index past the end of `clvm_buffer`. No signature validity or coin ownership is required to reach this code path since it executes before/alongside other spend validation in `validate_spend_bundle`.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` and the `while True` loop in `is_clvm_canonical` before every `clvm_buffer[offset]` access (e.g., raise/return a "not canonical" result if `offset >= len(clvm_buffer)` instead of allowing Python to raise `IndexError`), mirroring the defensive length checks already used elsewhere in the codebase such as `skip_bytes`/`skip_list` in `chia/full_node/full_block_utils.py`: [5](#0-4) 
Additionally, wrap the call to `is_clvm_canonical` in `validate_spend_bundle` with exception handling that treats malformed input as `Err.INVALID_COIN_SOLUTION` rather than allowing the exception to propagate.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# CLVM atom whose length-prefix byte (0xF8) declares a 4-byte big atom length,
# but the buffer is truncated right after the prefix byte, with no length bytes present.
malformed = bytes([0xF8])  # 2+8+8+8 bits length prefix, prefix_len=4, but 0 bytes follow

is_clvm_canonical(malformed)  # raises IndexError: index out of range
```
Submitting a `CoinSpend` whose `puzzle_reveal` or `solution` ends with this pattern as the final bytes of an otherwise well-formed CLVM buffer causes `validate_spend_bundle` to raise this unhandled `IndexError` when checking `is_clvm_canonical(bytes(coin_spend.solution))` (or `puzzle_reveal`), reachable by any unprivileged spend-bundle submitter.

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

**File:** chia/full_node/mempool_manager.py (L640-647)
```python
        err, item, remove_items = await self.validate_spend_bundle(
            new_spend,
            conds,
            spend_name,
            first_added_height,
            get_coin_records,
            get_unspent_lineage_info_for_puzzle_hash,
        )
```

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```

**File:** chia/full_node/full_block_utils.py (L15-34)
```python
def skip_list(buf: memoryview, skip_item: Callable[[memoryview], memoryview]) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"list count prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"list count {n} exceeds remaining buffer {len(buf)}")
    for _ in range(n):
        buf = skip_item(buf)
    return buf


def skip_bytes(buf: memoryview) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"byte length prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"byte length {n} exceeds remaining buffer {len(buf)}")
    return buf[n:]
```
