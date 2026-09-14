This is called for every coin spend in the mempool admission path via `MempoolManager.validate_spend_bundle()` at [1](#0-0) , so it is directly reachable by an unprivileged spend-bundle submitter.

### Title
Unhandled `IndexError` in `is_atom_canonical`/`is_clvm_canonical` allows a crafted spend bundle to crash mempool admission - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a raw `bytes` buffer (the CLVM-serialized puzzle reveal or solution of an incoming coin spend) by reading length-prefix bytes and advancing an `offset` index, exactly analogous to the ICU `utf8TextAccess`/`utext_setNativeIndex*` pattern from CVE-2017-7867 where an index is advanced based on an attacker-influenced length field without validating it stays within buffer bounds.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `clvm_buffer[offset]` for a variable number of `prefix_len` continuation bytes (1–5) to reconstruct an atom length, incrementing `offset` in a loop with no bounds check against `len(clvm_buffer)`: [2](#0-1) . Its caller, `is_clvm_canonical()`, also indexes `clvm_buffer[offset]` in an unbounded `while True` loop, advancing `offset` by `atom_len` (an attacker-controlled value derived straight from the buffer) without verifying `offset + atom_len <= len(clvm_buffer)` before the next iteration reads `clvm_buffer[offset]` again: [3](#0-2) . Both functions are invoked on attacker-supplied bytes — the coin spend's `puzzle_reveal` and `solution` — inside `MempoolManager.validate_spend_bundle()`, which runs for every coin spend of every spend bundle submitted to the mempool: [1](#0-0) .

A crafted puzzle reveal or solution whose final atom's declared length prefix places `offset` (or `offset + atom_len`) past the end of `clvm_buffer` will trigger a Python `IndexError` on the next `clvm_buffer[offset]` read, since Python raises on out-of-range indexing rather than performing memory-unsafe access (unlike the C/C++ ICU case). This is caught neither in `is_clvm_canonical`/`is_atom_canonical` nor immediately in `validate_spend_bundle`'s per-coin-spend loop (lines 715-726), which has no try/except around the canonicality check.

### Impact Explanation
If unhandled, the `IndexError` propagates out of `validate_spend_bundle()`. This is called from `add_spend_bundle()` and ultimately from the full-node's spend-bundle intake path (`respond_transaction`/`send_transaction` RPC and peer transaction handling), which do not appear (from what was inspected) to wrap this call in a broad exception handler the way `pre_validate_spendbundle()`'s Rust `ValidationError` conversion does at . An uncaught exception here would abort processing of that spend bundle and, depending on the caller's exception handling, could disrupt the mempool-manager's per-item processing for the connection/task handling that transaction — a spend-triggered transaction-processing disruption reachable by any unprivileged node that can submit a spend bundle (wallet RPC, peer `transaction` protocol message, or offer counterparty flow).

### Likelihood Explanation
Likelihood is high in terms of reachability (any coin spend's puzzle reveal/solution bytes are attacker-controlled and pass through this check on every mempool admission attempt), but I could not confirm from the indexed code whether an outer exception handler (e.g., in `full_node.py`'s transaction-handling coroutine or asyncio task wrapper) already catches generic `Exception`/`IndexError` and simply logs it, which would reduce this to a per-transaction failure rather than a broader processing halt. This uncertainty is a limitation of what the code search surfaced.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` read (raising a well-defined `ValueError`/returning non-canonical instead of indexing out of range) and in `is_clvm_canonical()`'s main loop before dereferencing `clvm_buffer[offset]`, and audit the call chain from `validate_spend_bundle()` up through the transaction-intake RPC/protocol handlers to guarantee any parsing exception here is caught and converted into a normal `MempoolInclusionStatus.FAILED` result rather than propagating as an unhandled exception.

### Proof of Concept
Based on the test file's existing helper for constructing non-canonical/oversized atom headers, a coin spend whose `solution` (or `puzzle_reveal`) ends with a multi-byte length prefix (e.g. `0xFC` prefix class requiring 5 continuation bytes per [4](#0-3) ) but only 1–2 actual bytes remaining in the buffer, submitted via `send_transaction`, would reach `is_clvm_canonical(bytes(coin_spend.solution))` in `validate_spend_bundle()` at line 723-725 and raise `IndexError` while reading past the end of the byte string. I was not able to fully execute or trace this against a live node from static analysis alone to confirm downstream exception handling, so this should be verified with an actual reproduction (e.g. via `chia/_tests/core/mempool/test_mempool_manager.py`'s `test_mempool_requires_canonical_clvm` harness at [5](#0-4) , modified to use a truncated multi-byte length prefix instead of a merely non-canonical one).

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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L3277-3300)
```python
@pytest.mark.anyio
@pytest.mark.parametrize("flags", [0, ELIGIBLE_FOR_DEDUP, ELIGIBLE_FOR_FF, ELIGIBLE_FOR_FF | ELIGIBLE_FOR_DEDUP])
@pytest.mark.parametrize(
    "puzzle_hex,solution_hex",
    [
        # ((1)) with a non-canonical atom length prefix in the solution
        (None, "ffffc001018080"),
        # atom 1 with a non-canonical length prefix in the puzzle
        ("c00101", "80"),
    ],
)
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
