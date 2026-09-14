Found a directly reachable analog: `is_atom_canonical()`/`is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a length-prefixed CLVM atom header by indexing into `clvm_buffer` without validating that enough bytes remain for the declared prefix, mirroring the CVE's core flaw (a length/prefix parser trusting a boundary that isn't actually backed by remaining buffer data).

### Title
Unhandled IndexError in `is_atom_canonical()` from truncated CLVM length-prefix causes mempool-validation crash - (File: chia/full_node/mempool_manager.py)

### Summary
`validate_spend_bundle()` calls `is_clvm_canonical()` on every submitted coin spend's `puzzle_reveal` and `solution` bytes before other checks [1](#0-0) . `is_clvm_canonical()` walks the buffer and, for any atom byte `> 0x80`, calls `is_atom_canonical(clvm_buffer, offset)` to read the multi-byte length prefix [2](#0-1) .

### Finding Description
`is_atom_canonical()` determines from the leading atom byte how many additional prefix bytes (`prefix_len`, up to 5) it must read, then loops incrementing `offset` and indexing `clvm_buffer[offset]` for each prefix byte [3](#0-2) . Neither this function nor its caller `is_clvm_canonical()` verifies that `offset + prefix_len` stays within `len(clvm_buffer)` before dereferencing — it is exactly the CVE-2013-2174 pattern: a length-driven parser that assumes trailing bytes exist without checking the buffer boundary. If a spend bundle supplies a `puzzle_reveal` or `solution` whose final byte is a multi-byte atom-length prefix marker (e.g. `0xFC`, which claims 5 more prefix bytes) with fewer bytes actually present, the `for i in range(prefix_len): ... clvm_buffer[offset]` loop indexes past the end of `clvm_buffer` [4](#0-3) . In Python this raises an uncaught `IndexError` (unlike the C/heap-corruption outcome in curl, Python's bounds-checked buffers convert the flaw into an unhandled exception rather than memory corruption).

### Impact Explanation
`is_clvm_canonical()` is invoked directly inside `validate_spend_bundle()` with no surrounding try/except at that call site [1](#0-0) . An `IndexError` propagating out of this call is not one of the explicitly handled `Err` return paths in that function, so a single crafted spend bundle (fully attacker-controlled `puzzle_reveal`/`solution` bytes, reachable via a normal wallet-submitted spend bundle) can raise an unhandled exception during mempool admission of every node that receives and validates it. Depending on how the enclosing async task / RPC caller handles the exception, this can manifest as a per-node validation failure that repeats for every peer relaying the same spend bundle, a spend-triggered processing halt for the mempool admission path.

### Likelihood Explanation
High reachability: any unprivileged wallet/RPC caller can submit a spend bundle with an arbitrary `puzzle_reveal`/`solution` byte string ending in a truncated multi-byte atom length-prefix marker; `is_clvm_canonical()` is unconditionally exercised on every coin spend during `validate_spend_bundle()`, before signature or execution — no special privileges or timing are required to trigger the code path.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before indexing `clvm_buffer[offset]` in the prefix-reading loop (and similarly in `is_clvm_canonical()`'s outer loop before reading `clvm_buffer[offset]`), raising/returning a handled non-canonical/invalid result instead of allowing an `IndexError` to escape; alternatively wrap the `is_clvm_canonical()` calls in `validate_spend_bundle()` with a try/except that maps any parsing exception to `Err.INVALID_COIN_SOLUTION`.

### Proof of Concept
Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) bytes end with a byte matching the 5-byte-prefix pattern `0b11111110` (e.g. trailing byte `0xFC`) but with fewer than 5 bytes following it in the buffer, e.g. `bytes([0xFC])` alone as a solution atom. Submitting a `SpendBundle` containing a `CoinSpend` with this malformed solution through `push_tx`/`add_spend_bundle` drives execution into `validate_spend_bundle()` → `is_clvm_canonical(bytes(coin_spend.solution))` → `is_atom_canonical(clvm_buffer, 0)`, where the prefix-reading loop attempts `clvm_buffer[1]` on a 1-byte buffer, raising `IndexError` [5](#0-4) . This can be verified by extending the existing canonical-CLVM negative tests (`test_clvm_not_canonical`, `test_mempool_requires_canonical_clvm`) with a truncated multi-byte atom instead of a full one [6](#0-5) .

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

**File:** chia/full_node/mempool_manager.py (L212-221)
```python
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
