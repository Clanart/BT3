### Title
Missing bounds check in `is_atom_canonical()` causes unhandled out-of-bounds read on attacker-controlled spend bundle CLVM buffers - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse the CLVM atom length-prefix format directly out of an attacker-supplied byte buffer without ever checking that `offset` stays within `len(clvm_buffer)`. This is the same bug class as CVE-2020-21724 (a length-prefixed stream parser advancing an offset/pointer past the end of the buffer without a bounds check before dereferencing it): here it manifests as an unchecked Python index access instead of a raw C buffer overflow, but the root cause — trusting an attacker-supplied length/prefix field to walk a buffer without validating remaining length — is identical.

### Finding Description
`is_atom_canonical()` reads the CLVM atom encoding directly from the raw bytes of `coin_spend.puzzle_reveal` and `coin_spend.solution`, both of which are fully attacker-controlled fields of a submitted `SpendBundle`: [1](#0-0) 

Note that `b = clvm_buffer[offset]` is executed at entry, and then inside the loop `offset += 1; atom_len |= clvm_buffer[offset]` is repeated up to 5 times (`prefix_len` up to 5) — with **no check anywhere that `offset < len(clvm_buffer)`** before each index. Likewise `is_clvm_canonical()`'s own loop indexes `clvm_buffer[offset]` on every iteration without checking `offset` against the buffer length before dereferencing it: [2](#0-1) 

This is called for every coin spend in every submitted spend bundle during mempool admission, directly on attacker-controlled bytes: [3](#0-2) 

A crafted `puzzle_reveal` or `solution` blob that ends with a truncated multi-byte length-prefix atom header (e.g. a `0xFC` prefix byte, which claims 5 additional length bytes, placed as the very last byte of the buffer) makes `is_atom_canonical()` index past the end of `clvm_buffer`, raising an uncaught `IndexError` in Python instead of returning a graceful "non-canonical/invalid" result.

By contrast, other buffer parsers in the same codebase that operate on untrusted network/consensus data (e.g., `chia/full_node/full_block_utils.py`'s `skip_bytes()` / `skip_list()`) explicitly validate the remaining buffer length before slicing: [4](#0-3) 

`is_atom_canonical`/`is_clvm_canonical` lack this same defensive pattern despite processing equally untrusted, attacker-supplied input.

### Impact Explanation
Since Python bounds-checks list/bytes indexing, this cannot corrupt memory the way the original C++ `oggvideotools` bug could; it instead produces an unhandled `IndexError` exception during spend-bundle validation (`validate_spend_bundle()`, invoked from mempool admission for every incoming spend bundle). If this exception is not caught by an intermediate handler in the mempool/RPC admission chain, a single malformed spend bundle submitted by any unprivileged user can throw an unhandled exception in the mempool admission path, which is a spend-triggered transaction-processing disruption for that admission call. I was not able to fully verify, within the available index, whether every caller in the chain (e.g. `add_spend_bundle()`, RPC `push_tx`, and the P2P `new_transaction` handler) wraps this call in a broad `except Exception` — the codebase index did not return that specific wrapping code, so the exact blast radius (single-request failure vs. broader mempool-manager task failure) is not fully confirmed from what was found.

### Likelihood Explanation
High: constructing the trigger requires only crafting a `puzzle_reveal` or `solution` byte string with a truncated multi-byte CLVM atom length prefix as the final bytes of the buffer — no privileged access, no cryptographic material, and no interaction with other participants is needed. This is directly reachable from `push_tx` RPC or the P2P `new_transaction`/`RequestTransaction` path, i.e., exactly the "unprivileged spend-bundle submitter" threat actor this scan is scoped to.

### Recommendation
Add explicit remaining-length checks in `is_atom_canonical()` before every buffer index (both the initial `clvm_buffer[offset]` and each byte consumed in the `for i in range(prefix_len)` loop), and likewise validate `offset < len(clvm_buffer)` at the top of the `is_clvm_canonical()` loop, returning `False` (non-canonical) or raising a well-defined validation error (e.g., `Err.INVALID_COIN_SOLUTION`) instead of letting an `IndexError` propagate. Mirror the existing bounds-checked pattern already used in `chia/full_node/full_block_utils.py` (`skip_bytes`/`skip_list`), and add regression tests analogous to `test_chialisp_deserialization.py`'s overflow-atom tests but targeting `is_atom_canonical`/`is_clvm_canonical` with truncated buffers.

### Proof of Concept
Construct a `puzzle_reveal` (or `solution`) blob whose last byte is a multi-byte atom length-prefix marker with no following length bytes, e.g.:
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xFC signals a 5-byte length prefix must follow, but the buffer ends here
truncated = bytes([0xFC])
is_clvm_canonical(truncated)  # raises IndexError instead of returning False
```
Submitting a `CoinSpend` whose `puzzle_reveal` or `solution` ends with such a truncated prefix (e.g., hex `...fc`) as part of a `SpendBundle` via `push_tx` triggers this code path inside `validate_spend_bundle()` at [3](#0-2) .

### Citations

**File:** chia/full_node/mempool_manager.py (L144-184)
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
