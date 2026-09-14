### Title
Missing bounds checks in CLVM canonical-encoding length-prefix parser causes out-of-bounds indexing, crashing mempool spend-bundle validation — (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` decodes a CLVM atom's variable-length size prefix (1–6 bytes) directly out of an attacker-supplied `puzzle_reveal`/`solution` buffer without ever checking that `offset` stays within `len(clvm_buffer)`, and without validating that the decoded `atom_len` fits inside the remaining buffer before the caller (`is_clvm_canonical`) advances `offset` by that amount and re-indexes the buffer on the next loop iteration. [1](#0-0) 

### Finding Description
The dnsmasq CVE is caused by `extract_name()` failing to bound-check a length-prefixed field before further processing (`sort_rrset`/`memcpy`), letting a crafted name field drive an out-of-bounds access. The analogous pattern exists in `is_atom_canonical`/`is_clvm_canonical`:

- `is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]`, determines a `prefix_len` (0–5 extra bytes) from the top bits of `b`, then loops `prefix_len` times doing `offset += 1; atom_len |= clvm_buffer[offset]` with **no check that `offset < len(clvm_buffer)`** before each read. [1](#0-0) 
- The function returns `1 + prefix_len + atom_len` as the new offset delta **without validating that this value is ≤ the remaining buffer length**.
- `is_clvm_canonical(clvm_buffer)` uses this return value to advance `offset` and, on the next loop iteration, does `b = clvm_buffer[offset]` — if the previous atom's declared length overruns the real buffer, this indexing goes out of bounds. [2](#0-1) 

Both functions are called on **every coin spend's puzzle reveal and solution** in `MempoolManager.validate_spend_bundle()`, which runs for every incoming, unauthenticated spend bundle submitted to the mempool: [3](#0-2) 

Unlike this code, the equivalent low-level parsers in `chia/full_node/full_block_utils.py` (`skip_list`, `skip_bytes`, the generator-buffer length checks) explicitly validate every length field against the remaining buffer size and raise a descriptive `ValueError` before indexing. [4](#0-3) 
`is_atom_canonical`/`is_clvm_canonical` lack this defensive pattern entirely — a truncated or malformed atom-length prefix causes Python to raise an uncaught `IndexError` inside `validate_spend_bundle` instead of a handled `ValueError`/`Err`.

The existing test suite only exercises well-formed canonical atoms (`test_atom_canonical`) and non-canonical-but-well-formed cases (`test_mempool_requires_canonical_clvm`); no test constructs a truncated/malformed length prefix that would trigger the out-of-bounds read. [5](#0-4) 

### Impact Explanation
An unprivileged spend-bundle submitter can craft a `puzzle_reveal` or `solution` whose leading atom byte signals a multi-byte length prefix (e.g. `0xFC` = 5 extra bytes) but is truncated right after that byte. `is_atom_canonical` will attempt to read past the end of `clvm_buffer`, raising an unhandled `IndexError` inside `validate_spend_bundle`, which is invoked from the mempool's spend-bundle admission path for every incoming transaction. If this exception is not caught by an outer handler, it aborts the coroutine handling that spend bundle, and — depending on caller context — can propagate into the mempool-processing task, resulting in a denial of service for transaction processing (spend-triggered transaction-processing halt), consistent with the accepted impact categories for this scan.

### Likelihood Explanation
High reachability: any party able to submit a spend bundle to a full node's mempool (via RPC or peer gossip of a transaction) controls the exact bytes of `puzzle_reveal` and `solution`, and `is_clvm_canonical` is invoked unconditionally on both for every coin spend during mempool admission. Crafting a truncated atom length-prefix is trivial and requires no valid signature or special puzzle logic — it only needs to reach the canonical-check step before other validation short-circuits it.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before every buffer index (`offset < len(clvm_buffer)`) and validate that the computed `atom_len`/total consumed length does not exceed the remaining buffer, raising `Err.INVALID_COIN_SOLUTION` (or an equivalent handled error) instead of allowing an `IndexError` to propagate. Mirror the defensive style already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`, and add regression tests with truncated/malformed length-prefix buffers to `test_mempool_manager.py`.

### Proof of Concept
1. Construct a `solution` (or `puzzle_reveal`) bytes buffer whose last byte is `0xFC` (indicating a 5-byte extended atom-length prefix) with no following bytes, e.g. `b"\xfc"`.
2. Submit a `SpendBundle` containing a `CoinSpend` with this `solution` to `MempoolManager.add_spend_bundle()` / `validate_spend_bundle()`.
3. `is_clvm_canonical(bytes(coin_spend.solution))` calls `is_atom_canonical(clvm_buffer, offset)`, which attempts `clvm_buffer[offset]` for `offset` beyond the 1-byte buffer, raising `IndexError` instead of returning a handled validation error.

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

**File:** chia/full_node/mempool_manager.py (L196-227)
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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L181-195)
```python
@pytest.mark.parametrize(
    "clvm_hex, expect",
    [
        ("c040", 2 + 0x40),
        ("e02000", 3 + 0x2000),
        ("f0100000", 4 + 0x100000),
        ("f808000000", 5 + 0x8000000),
        ("fc0400000000", 6 + 0x400000000),
    ],
)
def test_atom_canonical(clvm_hex: str, expect: int) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    atom_len, is_canonical = is_atom_canonical(clvm_buf, 0)
    assert atom_len == expect
    assert is_canonical
```
