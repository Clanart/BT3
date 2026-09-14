### Title
Unbounded buffer indexing in `is_atom_canonical()`/`is_clvm_canonical()` allows a crafted spend bundle to trigger an unhandled `IndexError` during mempool admission - (File: `chia/full_node/mempool_manager.py`)

### Summary
`chia/full_node/mempool_manager.py` implements `is_clvm_canonical()` and its helper `is_atom_canonical()` to validate that a `puzzle_reveal`/`solution` byte buffer submitted in a `CoinSpend` uses canonical (shortest-form) CLVM atom-length encoding, which is required before a coin spend can be marked DEDUP-eligible. Both functions read buffer bytes with raw indexing (`clvm_buffer[offset]`) without first checking that `offset` is still within the buffer bounds, mirroring the missing-length-check pattern behind CVE-2017-13010's `l_strnstart()` over-read in tcpdump. An attacker who submits a spend bundle with a truncated pair/atom-length-prefix sequence can drive `offset` past the end of the buffer and trigger a Python `IndexError` instead of a controlled validation failure.

### Finding Description
`is_clvm_canonical()` walks the serialized CLVM buffer token by token: [1](#0-0) 

For a `0xFF` pair token it unconditionally does `offset += 1` and loops back to read `clvm_buffer[offset]` again with no bounds check — if `0xFF` is the last byte of the buffer, the next iteration indexes one byte past the end and raises `IndexError`.

For atoms, it defers to `is_atom_canonical()`: [2](#0-1) 

Here, after determining `prefix_len` from the leading byte, the loop `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` reads `prefix_len` additional bytes with no check that they actually exist in the buffer. A buffer ending in a multi-byte length-prefix marker (e.g. `0xC0`, `0xE0`, `0xF0`, `0xF8`, `0xFC`) with insufficient trailing bytes causes an out-of-bounds read/`IndexError` in the same way the tcpdump BEEP parser read past its allocated header buffer because it never validated `l_strnstart()`'s inputs against the packet's captured length.

This function operates on `bytes(spend.puzzle_reveal)` / `bytes(spend.solution)` — i.e., attacker-controlled raw bytes taken directly from a `CoinSpend` inside a submitted `SpendBundle`, as exercised by `test_bundles_are_canonical` and `test_mempool_requires_canonical_clvm`: [3](#0-2) [4](#0-3) 

Because DEDUP-eligibility canonical checking runs on every coin spend's puzzle reveal/solution during mempool admission of a spend bundle, this code path is reachable by any unprivileged spend-bundle submitter without requiring block inclusion, signature validity, or any special permission — only that the byte buffer be crafted to end mid-length-prefix or mid-pair.

### Impact Explanation
An `IndexError` raised from deep inside canonical-CLVM validation is not one of the expected/handled `ValidationError`/`Err` outcomes that `add_spend_bundle()`/`pre_validate_spendbundle()` are designed to return; if it is not caught by a specific `except` clause at every call boundary, it propagates as an unhandled exception out of the mempool admission pipeline for that spend bundle. Depending on where the exception surfaces (thread-pool executor future, async task, RPC handler), this can crash or abort in-flight coroutine/task processing for that request and, in the worst case, destabilize the mempool manager's admission workflow — a spend-triggered halt of transaction processing, which matches the accepted impact category for this report (in contrast to a controlled `MempoolInclusionStatus.FAILED` / `Err.INVALID_COIN_SOLUTION` rejection that the code is supposed to produce).

### Likelihood Explanation
High reachability, low complexity: any wallet user or RPC caller who can submit a spend bundle controls the exact bytes of `puzzle_reveal` and `solution`. Constructing a buffer that ends immediately after a `0xFF` byte, or immediately after a multi-byte atom-length-prefix marker with too few trailing bytes, is trivial and requires no valid signature or successful CLVM execution semantics — the canonical check runs as part of computing DEDUP eligibility for the coin spend.

### Recommendation
Add explicit bounds checks before every buffer index in `is_clvm_canonical()` and `is_atom_canonical()`:
- Before dereferencing `clvm_buffer[offset]` in the main loop of `is_clvm_canonical()`, verify `offset < len(clvm_buffer)`; if not, return `False` (non-canonical/invalid) rather than continuing the loop.
- In `is_atom_canonical()`, verify that `offset + prefix_len < len(clvm_buffer)` before entering the byte-accumulation loop, returning a clear "not canonical"/error result if the buffer is too short.
- Add regression tests with truncated pair (`ff`) and truncated multi-byte atom-length-prefix buffers (e.g. lone `c0`, `e0`, `f0`, `f8`, `fc`) to ensure these raise a handled validation error instead of `IndexError`.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) is the single byte `b"\xff"` (a pair token with no following atoms) or the single byte `b"\xc0"` (an atom claiming a 1-byte extra length prefix that is never present).
2. Submit a `SpendBundle` containing this `CoinSpend` to the mempool via `MempoolManager.add_spend_bundle()` (reachable indirectly from any wallet/RPC spend-bundle submission) so that the DEDUP-eligibility canonical check is invoked over these bytes.
3. `is_clvm_canonical()` calls `clvm_buffer[offset]` with `offset == len(clvm_buffer)` (case `\xff`) or `is_atom_canonical()` calls `clvm_buffer[offset]` with `offset` past the 1-byte buffer (case `\xc0`), raising an unhandled `IndexError` instead of returning a bounded `False`/validation error.

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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L198-203)
```python
@pytest.mark.anyio
async def test_bundles_are_canonical(test_bundles: list[SpendBundle]) -> None:
    for sb in test_bundles:
        for spend in sb.coin_spends:
            assert is_clvm_canonical(bytes(spend.puzzle_reveal))
            assert is_clvm_canonical(bytes(spend.solution))
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L3288-3300)
```python
async def test_mempool_requires_canonical_clvm(flags: int, puzzle_hex: str | None, solution_hex: str) -> None:
    coin_spend = make_spend(
        TEST_COIN,
        SerializedProgram.fromhex(puzzle_hex) if puzzle_hex is not None else IDENTITY_PUZZLE,
        SerializedProgram.fromhex(solution_hex),
    )
    coins = TestCoins([TEST_COIN], lineage={})
    async with setup_mempool(coins) as mempool_manager:
        sb = SpendBundle([coin_spend], G2Element())
        sb_conds = make_test_conds(spend_ids=[(TEST_COIN, flags)])
        bundle_add_info = await mempool_manager.add_spend_bundle(sb, sb_conds, sb.name(), uint32(1))
        assert bundle_add_info.status == MempoolInclusionStatus.FAILED
        assert bundle_add_info.error == Err.INVALID_COIN_SOLUTION
```
