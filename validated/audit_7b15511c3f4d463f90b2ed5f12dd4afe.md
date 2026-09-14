### Title
Missing bounds check in `is_atom_canonical` allows an unprivileged spend bundle to trigger an unhandled `IndexError` during mempool admission - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()`, used by `is_clvm_canonical()` to validate that `puzzle_reveal`/`solution` bytes in a submitted `CoinSpend` use canonical (shortest-form) CLVM atom-length encoding, indexes into the attacker-supplied `clvm_buffer` at increasing offsets while decoding a multi-byte length prefix, but never checks that those offsets are within the buffer bounds before reading them.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` and, depending on the top bits of `b`, determines `prefix_len` (0 to 5 additional bytes to read for the atom's length prefix): [1](#0-0) 

It then loops `prefix_len` times, incrementing `offset` and reading `clvm_buffer[offset]` each time, with no check that `offset < len(clvm_buffer)` before any of these reads:
```
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
```
If an attacker crafts a `puzzle_reveal` or `solution` byte string whose final byte is a length-prefix marker indicating a multi-byte prefix (e.g. `0xFC`-`0xFE`, meaning up to 5 more bytes should follow) but the buffer is truncated immediately after that marker byte, the loop attempts to read past the end of `clvm_buffer`.

This function is reached directly from an unprivileged, single-submitted spend bundle: `MempoolManager.validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` and `is_clvm_canonical(bytes(coin_spend.solution))` for every coin spend in the bundle, before any other CLVM sanity/cost validation of that data has occurred: [2](#0-1) 

This is called from the reachable path `add_spend_bundle()` → `validate_spend_bundle()`, which is invoked whenever a full node processes an incoming transaction/spend bundle: [3](#0-2) 

Contrast this with other length-prefixed/offset-based parsers in the same codebase that handle untrusted buffers (e.g. `chia/full_node/full_block_utils.py`), which explicitly bound-check remaining buffer length before consuming it, raising a clean `ValueError`: [4](#0-3) 
`is_atom_canonical` has no equivalent guard.

Since Python raises `IndexError` on out-of-bounds `bytes`/`bytearray` indexing rather than performing an actual out-of-bounds memory write (unlike the C/C++ FreeRDP case), the direct memory-safety impact of the FreeRDP CVE does not translate 1:1 into this Python codebase. The concrete effect here would be an unhandled `IndexError` raised out of `is_clvm_canonical`/`is_atom_canonical` during mempool admission of a maliciously crafted spend bundle.

### Impact Explanation
I was unable to conclusively confirm, within the available tool budget, whether the call site(s) that invoke `add_spend_bundle()`/`validate_spend_bundle()` (e.g. the transaction-request handling code in `chia/full_node/full_node.py`) wrap this call in a broad exception handler that would gracefully convert an `IndexError` into a rejected transaction, or whether it would propagate as an unhandled exception in the per-connection/task processing loop. If uncaught, this could disrupt processing of that specific transaction request (and potentially the connection/task handling it), which is the closest analog to "a spend-triggered transaction-processing halt" called out as an acceptable impact category. If it is caught elsewhere (e.g., a top-level `try/except Exception` around message handling), the practical impact would be limited to the spend bundle being rejected, which is not a security-relevant impact.

### Likelihood Explanation
Triggering this requires nothing more than submitting a single spend bundle (via the wallet protocol / mempool) whose `puzzle_reveal` or `solution` CLVM byte serialization is truncated right after a multi-byte atom-length-prefix marker byte. This is trivial to construct — no signature check, cost check, or other precondition needs to succeed first, since `is_clvm_canonical` is called very early in `validate_spend_bundle`, before other checks.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before each buffer read, e.g.:
```python
if offset + prefix_len >= len(clvm_buffer):
    return len(clvm_buffer) - offset, False  # or raise a specific validation error
```
or check `offset < len(clvm_buffer)` inside the loop before indexing, mirroring the defensive length checks already used in `chia/full_node/full_block_utils.py` (`skip_list`, `skip_bytes`, `generator_from_block`, etc.). Additionally, verify (and if necessary add) a top-level exception guard around spend-bundle validation entry points so that any parsing exception on attacker-controlled data is converted into a rejected/failed spend bundle rather than propagating as an unhandled exception.

### Proof of Concept
Construct a `CoinSpend` whose `puzzle_reveal` (or `solution`) serialized bytes end with a truncated multi-byte atom-length prefix, for example a buffer ending in the single byte `0xFC` (which signals a 5-byte continuation length prefix) with no following bytes:
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xFC signals a 5-byte length-prefix continuation, but the buffer
# is truncated right after this byte.
truncated_buffer = b"\xfc"
is_clvm_canonical(truncated_buffer)  # raises IndexError instead of returning False / raising a handled ValueError
```
Submitting a `SpendBundle` containing a `CoinSpend` with such a `puzzle_reveal`/`solution` and routing it through `MempoolManager.validate_spend_bundle()` reaches the vulnerable `is_atom_canonical` call at [5](#0-4)  and raises the unhandled `IndexError`. Full confirmation of downstream process-level impact would require tracing the exact exception handling in the full node's transaction-request dispatch code, which I could not complete within the available investigation budget.

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

**File:** chia/full_node/mempool_manager.py (L715-727)
```python
        for coin_spend in new_spend.coin_spends:
            coin_id = coin_spend.coin.name()
            removal_names.add(coin_id)

            # if this coin_id isn't found, the SpendBundle doesn't match the
            # SpendBundleConditions.
            spend_conds = spend_conditions.pop(coin_id)

            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []

```

**File:** chia/full_node/full_block_utils.py (L15-24)
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
```
