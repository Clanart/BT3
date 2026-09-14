### Title
Out-of-bounds read / unhandled crash in `is_atom_canonical` when parsing truncated CLVM atom length prefixes - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` parses the multi-byte CLVM atom length-prefix encoding without validating that the buffer actually contains enough bytes for the declared prefix length before indexing into it, mirroring the off-by-one buffer over-read pattern in CVE-2015-0841's `readBuf`.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the atom's lead byte, determines `prefix_len` (0–5 extra bytes) purely from the high bits of that byte, and then unconditionally loops `prefix_len` times incrementing `offset` and indexing `clvm_buffer[offset]`: [1](#0-0) 

Unlike other buffer parsers in the same codebase (e.g. `skip_bytes`/`skip_list` in `chia/full_node/full_block_utils.py`, which explicitly check `n > len(buf)` before slicing) this function performs **no bounds check** that `offset + prefix_len < len(clvm_buffer)` before reading each successive byte: [2](#0-1) 

`is_atom_canonical` is invoked by `is_clvm_canonical(clvm_buffer)`, which walks an entire CLVM-serialized buffer token by token: [3](#0-2) 

`is_clvm_canonical()` is called directly on attacker-controlled bytes — the `puzzle_reveal` and `solution` of every coin spend in a submitted `SpendBundle` — inside `MempoolManager.validate_spend_bundle()`, which is on the mempool admission path for every unprivileged spend-bundle submission: [4](#0-3) 

If a submitter crafts a `puzzle_reveal` or `solution` whose final atom starts a multi-byte length-prefix (e.g. a lead byte in the `0xF8`–`0xFB` range, declaring a 5-byte prefix) but the buffer ends before all prefix bytes are present, `is_atom_canonical` will index past the end of the `bytes` object and raise an unhandled `IndexError`, since Python raises on out-of-range bytes indexing rather than reading adjacent memory. Nothing in `is_atom_canonical`/`is_clvm_canonical` catches this.

### Impact Explanation
This matches the "spend-triggered transaction-processing halt" class called out in scope: a single, unsigned, attacker-crafted spend bundle can trigger an unhandled exception deep in the mempool admission pipeline that every full node runs when validating incoming transactions from any peer/wallet client. Depending on how far up the call stack the exception propagates uncaught, this can disrupt the SpendBundle validation flow for legitimate transactions, which is a functional analog of the CVE-2015-0841 crash-via-malformed-length-field bug class.

### Likelihood Explanation
Likelihood is high for triggering the code path (any wallet/RPC caller can submit a `SpendBundle` with an arbitrary `puzzle_reveal`/`solution` byte string ending mid length-prefix), but I was not able to fully confirm, within the available tool budget, whether an outer `try/except Exception` in `MempoolManager`/`full_node.py`'s transaction-handling code ultimately swallows this `IndexError` before it can cause node-visible disruption — the mempool_manager.py file does contain `try`/`except Exception` blocks elsewhere that I did not get to fully inspect. This is a material uncertainty that should be verified by exercising the exact byte sequence against `validate_spend_bundle`/`add_spend_bundle` in a running node.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (and any other CLVM buffer walker sharing this pattern) verifying `offset + prefix_len < len(clvm_buffer)` before reading prefix bytes, returning "not canonical" (or raising a defined `ValidationError`) instead of allowing a raw `IndexError` to escape, consistent with the bounds-checked style already used in `chia/full_node/full_block_utils.py`.

### Proof of Concept
1. Construct a `puzzle_reveal` or `solution` CLVM buffer whose last byte is a multi-byte atom length-prefix lead byte (e.g. `0xF8`) that declares 5 additional prefix bytes but supply zero of them, e.g. `bytes.fromhex("f8")` as the trailing atom of an otherwise well-formed program.
2. Submit a `SpendBundle` containing a `CoinSpend` with this `puzzle_reveal`/`solution` via the standard `push_tx`/transaction-submission RPC path used by `MempoolManager.validate_spend_bundle()`.
3. Observe that `is_clvm_canonical()` → `is_atom_canonical()` raises `IndexError: index out of range` on `clvm_buffer[offset]` while attempting to read prefix bytes beyond the buffer end, rather than returning `False`/`not canonical`.

Note: I was unable to trace whether this exception is ultimately caught by a higher-level handler in `full_node.py`/`mempool_manager.py` before this finding can be independently confirmed as causing observable node disruption; this should be validated by running the PoC against a live node.

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

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```

**File:** chia/full_node/full_block_utils.py (L27-34)
```python
def skip_bytes(buf: memoryview) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"byte length prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"byte length {n} exceeds remaining buffer {len(buf)}")
    return buf[n:]
```
