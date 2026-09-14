### Title
Unhandled `IndexError` in `is_clvm_canonical()`/`is_atom_canonical()` can crash mempool spend-bundle admission - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager.validate_spend_bundle()` runs `is_clvm_canonical()` on every submitted coin spend's `puzzle_reveal` and `solution` bytes, unconditionally, at admission time [1](#0-0) . Like `mgetty`'s `putwhitespan()`, which walks a length-prefixed field without validating that enough bytes remain in the buffer, `is_clvm_canonical()`/`is_atom_canonical()` walk a variable-length-prefixed CLVM atom encoding by indexing directly into the `bytes` buffer with an offset advanced according to a length value read from the buffer itself, with no bounds check before each index access [2](#0-1) [3](#0-2) .

### Finding Description
`is_atom_canonical()` reads the first byte at `offset` to determine the atom's length-prefix width (`prefix_len`, up to 5 additional bytes), then loops `prefix_len` times incrementing `offset` and reading `clvm_buffer[offset]` each time, with no check that `offset` stays within `len(clvm_buffer)` [4](#0-3) . `is_clvm_canonical()` similarly advances `offset` in a loop based on values derived from the buffer, reading `clvm_buffer[offset]` on each iteration without pre-checking that `offset < len(clvm_buffer)` [5](#0-4) .

Because these are plain `bytes` objects, an out-of-range index raises `IndexError` (Python's memory-safety prevents true OOB memory disclosure/corruption unlike mgetty's C buffer), but the exception is not caught anywhere in the call path: `validate_spend_bundle()` calls `is_clvm_canonical()` directly with no `try/except` around it [1](#0-0) , and `add_spend_bundle()` (the RPC/network-reachable entry point for adding a spend bundle to the mempool) has no exception handling wrapping the call to `validate_spend_bundle()` either [6](#0-5) .

The comment/doc context asserts this canonical check is only meant to gate DEDUP eligibility [7](#0-6) , but the actual code in `validate_spend_bundle()` runs it unconditionally on every spend in every submitted bundle, regardless of DEDUP/FF flags, making this reachable from any unprivileged wallet/RPC caller submitting a transaction.

I was not able to fully confirm (within the available tooling) whether it is actually possible to construct a `CoinSpend` whose `puzzle_reveal`/`solution` `bytes()` re-serialization can produce a buffer that trips `is_atom_canonical`'s off-by-N read past the buffer end, since these buffers normally originate from a `chia_rs`-validated `SerializedProgram`/`Program` that already bounds-checked the same length-prefix encoding during deserialization (see `is_atom_canonical` mirrors the same 6/5/4/3/2/1-bit prefix scheme documented and tested for canonical/non-canonical cases [8](#0-7) ). Confirming an actual crafted-bytes path that survives Rust-side parsing but overruns the Python bounds check would require deeper testing/fuzzing than is possible via static code search alone.

### Impact Explanation
If a valid input triggering the missing bounds check can be constructed, the unhandled `IndexError` inside `validate_spend_bundle()` would propagate out of `add_spend_bundle()`. Depending on the calling context (e.g. inside the mempool manager's per-bundle processing loop or RPC handler), this could abort processing of that call path, effectively a spend-triggered transaction-processing halt/DoS for the specific code path handling that spend bundle, matching the "DoS, program may crash" impact class of CVE-2019-1010190. This is a Medium-severity local-input-parsing crash, not a memory-corruption or fund-theft vulnerability, since Python bytes indexing is memory-safe.

### Likelihood Explanation
Likelihood is uncertain/low-to-medium: the function is reachable by any unprivileged spend-bundle submitter (wallet, RPC caller) since it runs on every coin spend unconditionally in `validate_spend_bundle()` [1](#0-0) , but triggering the actual out-of-bounds index requires a buffer shape that passes `chia_rs`'s Rust CLVM deserialization (which already enforces correct atom lengths) yet still causes the Python re-implementation's prefix-walk to run past the buffer end — a scenario not confirmed to be constructible from the available code and tests reviewed.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` and `is_clvm_canonical()` before each buffer index access (raise a clear `ValueError`/return non-canonical instead of relying on Python's `IndexError`), and/or wrap the `is_clvm_canonical()` calls in `validate_spend_bundle()` in a `try/except` that treats any parsing failure as `Err.INVALID_COIN_SOLUTION` rather than allowing an unhandled exception to propagate.

### Proof of Concept
Not able to construct a concrete crafted `CoinSpend` bytes sequence that both (a) passes `chia_rs` Program/SerializedProgram deserialization to become a valid `CoinSpend.puzzle_reveal`/`solution`, and (b) causes `is_atom_canonical`/`is_clvm_canonical` to index past the buffer end, using only static code search. This would need to be validated with actual execution/fuzzing against the `chia_rs` deserializer and the Python canonical-check functions, e.g. via a Devin session with test execution access.

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

**File:** .cursor/context/clvm-execution.md (L108-111)
```markdown
### When enforced

Required for DEDUP-eligible spends. Without canonical form, identical
solutions could have different serializations, breaking dedup.
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L136-196)
```python
@pytest.mark.parametrize(
    "clvm_hex",
    [
        "fffe80",
        "c000",
        "c03f",
        "e00000",
        "e01fff",
        "f0000000",
        "f00fffff",
        "f800000000",
        "f807ffffff",
        "fc0000000000",
        "fc03ffffffff",
        "fe",
        "ff808080",
    ],
)
def test_clvm_not_canonical(clvm_hex: str) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    assert not is_clvm_canonical(clvm_buf)


@pytest.mark.parametrize(
    "clvm_hex, expect",
    [
        ("c000", 2 + 0),
        ("c03f", 2 + 0x3F),
        ("e00000", 3 + 0),
        ("e01fff", 3 + 0x1FFF),
        ("f0000000", 4 + 0),
        ("f00fffff", 4 + 0xFFFFF),
        ("f800000000", 5 + 0),
        ("f807ffffff", 5 + 0x7FFFFFF),
        ("fc0000000000", 6 + 0),
        ("fc03ffffffff", 6 + 0x3FFFFFFFF),
    ],
)
def test_atom_not_canonical(clvm_hex: str, expect: int) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    atom_len, is_canonical = is_atom_canonical(clvm_buf, 0)
    assert atom_len == expect
    assert not is_canonical


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
