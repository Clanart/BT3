### Title
Out-of-Bounds Read / Unhandled `IndexError` in CLVM Canonical-Encoding Check on Attacker-Controlled Spend Bundle Data - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a length-prefix field taken directly from the CLVM-serialized `puzzle_reveal` and `solution` bytes of a submitted `SpendBundle`, without validating that the prefix bytes or the derived atom length stay within the buffer bounds. This mirrors the reported Turso bug class: an attacker-controlled "count"/length field consumed by a byte-buffer reader with no bounds check, leading to an out-of-bounds index access and a crash.

### Finding Description
`is_atom_canonical()` reads the discriminant byte at `clvm_buffer[offset]`, determines `prefix_len` (0–5 extra bytes) from the high bits, and then loops `prefix_len` times reading `clvm_buffer[offset]` to build `atom_len`: [1](#0-0) 

There is no check that `offset` stays within `len(clvm_buffer)` while consuming the multi-byte length prefix. `is_clvm_canonical()`, the caller, only asserts the buffer is non-empty before the loop and does not otherwise track remaining length before dereferencing `clvm_buffer[offset]`: [2](#0-1) 

This function is invoked directly on attacker-supplied bytes during spend-bundle admission to the mempool, once per coin spend, for every submitted `SpendBundle`: [3](#0-2) 

If the last byte of `puzzle_reveal` or `solution` is an atom-length discriminant that signals a multi-byte length prefix (e.g. `0xF8`–`0xFF` range) but the buffer ends before all prefix bytes are present, `is_atom_canonical()` will index past the end of `clvm_buffer`, raising a Python `IndexError` from inside `validate_spend_bundle()`. This is architecturally analogous to the Turso bug, where an attacker-controlled cell-count field is trusted without bounds validation, causing an out-of-bounds index/panic when the untrusted data is processed. Notably, other parsers in this codebase that read similar attacker length fields (e.g. `skip_list`, `skip_bytes`, `generator_from_block`, `block_info_from_block` in `chia/full_node/full_block_utils.py`) were hardened with explicit bounds checks and descriptive `ValueError`s, but `is_atom_canonical`/`is_clvm_canonical` were not given the same treatment: [4](#0-3) 

### Impact Explanation
An unprivileged spend-bundle submitter can craft a `puzzle_reveal` or `solution` whose trailing bytes are a truncated multi-byte atom-length prefix. When the full node processes this bundle for mempool admission, `is_clvm_canonical()`/`is_atom_canonical()` raise an unhandled `IndexError` mid-validation. Depending on how far up the call stack the exception propagates before being caught, this can abort in-flight mempool validation for that spend bundle (localized DoS) or, if uncaught in the surrounding task/executor, disrupt the mempool-manager task processing that bundle — a spend-triggered transaction-processing halt matching the required impact class. Because this path runs for every submitted coin spend (not gated behind a rarely-used feature), it is broadly reachable from ordinary transaction submission.

### Likelihood Explanation
Likelihood is high for a determined attacker: constructing a `puzzle_reveal`/`solution` byte string with a trailing truncated atom-length prefix requires no special privileges, keys, or network position — only the ability to submit a spend bundle (e.g. via RPC or peer transaction gossip) that reaches `MempoolManager.validate_spend_bundle()`. The crafted bytes do not need to be a valid/executable CLVM program in the sense of running successfully; they only need to pass whatever earlier structural checks precede this canonical-encoding check.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()`/`is_clvm_canonical()` before every buffer index, mirroring the pattern already used in `chia/full_node/full_block_utils.py` (`skip_list`, `skip_bytes`, etc.): verify `offset < len(clvm_buffer)` before reading the discriminant byte, verify enough bytes remain for the full `prefix_len`, and verify the computed `atom_len` plus consumed offset does not exceed `len(clvm_buffer)`; return `False` (non-canonical) or raise a well-defined `ValidationError`/`Err.INVALID_COIN_SOLUTION` instead of letting an `IndexError` escape. Additionally, wrap the call site in `validate_spend_bundle()` to explicitly catch `IndexError` and convert it to a controlled validation failure, ensuring malformed submissions are rejected gracefully rather than raising an unexpected exception during mempool processing.

### Proof of Concept
1. Construct a `CoinSpend` whose `puzzle_reveal` (or `solution`) bytes end with a discriminant byte indicating a multi-byte atom length prefix — for example, a final byte in the `0xF8`–`0xFB` range (5-bit length + 3 length bytes expected) — but omit all of the following length bytes, so the buffer terminates immediately after the discriminant.
2. Build a `SpendBundle` containing this `CoinSpend` (a normal, well-formed aggregated signature is not required to reach this code path since `is_clvm_canonical()` runs before/alongside other spend-bundle checks in `validate_spend_bundle()`).
3. Submit the bundle for mempool admission (e.g. via the full node's transaction submission RPC or peer protocol) so it reaches `MempoolManager.validate_spend_bundle()`.
4. Observe that `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` calls `is_atom_canonical()`, which increments `offset` past the end of `clvm_buffer` while reading the truncated length prefix, raising an unhandled `IndexError` instead of a controlled validation error.

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
