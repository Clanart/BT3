### Title
Unbounded atom length-prefix parsing in CLVM canonical-form check allows an out-of-bounds read/crash from a crafted spend bundle - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` parses a CLVM atom's variable-length size prefix (1–6 bytes) directly out of an attacker-controlled `puzzle_reveal`/`solution` buffer without ever checking that the buffer actually contains enough bytes to hold the declared prefix, mirroring the libarchive bug class where a crafted "entry-size" value drove a read past the allocated buffer.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` decodes the multi-byte length prefix of a CLVM atom by repeatedly indexing `clvm_buffer[offset]` for `prefix_len` (up to 5) additional bytes, based only on the high bits of the first byte — with no bounds check against `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` walks an entire serialized CLVM buffer (a spend's `puzzle_reveal` or `solution`) atom-by-atom, calling `is_atom_canonical()` for every atom, with no upfront validation that the buffer is long enough for the encoded prefix lengths it discovers along the way: [2](#0-1) 

This canonical-form check is used during spend-bundle admission to decide DEDUP/fast-forward eligibility, as confirmed by the test that builds a spend with a crafted, non-canonical length-prefix byte sequence (`"c00101"`) and checks the outcome: [3](#0-2) 

If a submitted `puzzle_reveal` or `solution` ends immediately after a byte that signals a multi-byte length prefix (e.g., the `0b11110000`/`0b11111000`/`0b11111100` patterns requiring 2–5 additional prefix bytes) but does not actually contain those following bytes, `clvm_buffer[offset]` in the prefix-reading loop indexes past the end of the buffer and raises an unhandled `IndexError` — not a `ValueError`.

The transaction admission path only special-cases `ValueError` and `ValidationError` when catching failures from spend-bundle processing: [4](#0-3) 

An unrelated/unexpected exception type is demonstrated (in tests) to propagate out of `add_transaction` uncaught rather than being converted into a normal `FAILED` result: [5](#0-4) 

### Impact Explanation
A single unprivileged spend-bundle submitter can craft a `puzzle_reveal`/`solution` byte sequence whose trailing bytes end right after a multi-byte atom length-prefix marker. When the mempool manager's canonical-form check runs against that buffer (during DEDUP/fast-forward eligibility evaluation of the coin spend), it triggers an out-of-bounds index access that raises an uncaught `IndexError`. Because the enclosing exception handling in `add_transaction`/`pre_validate_spendbundle` is scoped to `ValueError`/`ValidationError`, the exception is not gracefully converted to a rejected transaction, and instead propagates through the mempool/transaction-processing coroutine — a spend-triggered transaction-processing halt reachable from any peer or wallet that can submit a spend bundle.

### Likelihood Explanation
The crafted byte pattern needed is simple and fully attacker-controlled (a single-byte discriminant such as `0xF8`–`0xFF` placed at the very end of the puzzle reveal or solution buffer, with no trailing bytes). No signature or special privilege is required to submit a spend bundle to a full node's mempool, and the canonical check is exercised on every incoming coin spend's puzzle/solution bytes as part of DEDUP/FF eligibility evaluation.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access in the prefix-reading loop (raising a caught `ValueError`/returning non-canonical instead of indexing out of range), and ensure `is_clvm_canonical()`/its callers treat any parsing failure as a normal validation rejection rather than an unhandled exception.

### Proof of Concept
Construct a `puzzle_reveal` or `solution` whose bytes end with a length-prefix discriminant byte from the 5-byte-prefix range (e.g., `0xFC`) without the required following bytes, e.g. `bytes([0xFC])` as the terminal fragment of an otherwise well-formed CLVM list/pair structure, then submit a `SpendBundle` containing a `CoinSpend` with that puzzle/solution such that the item is evaluated for DEDUP/FF eligibility in `MempoolManager.add_spend_bundle`/`validate_spend_bundle`. Observe that `is_atom_canonical` raises `IndexError: index out of range` instead of returning `False`, which is not caught by the `except ValueError` blocks in `pre_validate_spendbundle`/`add_transaction` and propagates out of transaction processing.

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

**File:** chia/full_node/full_node.py (L3063-3079)
```python
        try:
            cost_result = await self.mempool_manager.pre_validate_spendbundle(
                transaction, spend_name, self._bls_cache, fee_per_cost=fee_per_cost
            )
        except ValueError as e:
            # ValueError is used to indicate a soft failure. We don't want to
            # ban the peer. Timeouts are logged by MempoolManager when
            # log_mempool is "timeout".
            self.log.info(f"Rejecting transaction {spend_name}: {e}")
            return MempoolInclusionStatus.FAILED, Err.INVALID_SPEND_BUNDLE
        except ValidationError as e:
            # Keep known-invalid bundles in seen-cache to prevent re-validation.
            self.mempool_manager.add_and_maybe_pop_seen(spend_name)
            self.log.info(f"Rejecting transaction {spend_name}: {e}")
            return MempoolInclusionStatus.FAILED, e.code
        finally:
            self.mempool_manager.remove_in_flight(spend_name)
```

**File:** chia/_tests/core/full_node/test_full_node.py (L1641-1667)
```python
@pytest.mark.anyio
async def test_add_transaction_remove_seen_on_unexpected_exception(
    one_node_one_block: tuple[FullNodeSimulator, ChiaServer, BlockTools],
) -> None:
    """When pre_validate_spendbundle raises an unexpected exception, the
    seen-cache entry must be removed and the exception re-raised."""
    full_node_1, _server_1, _bt = one_node_one_block
    fn = full_node_1.full_node

    spend_bundle = make_spend_bundle(1)
    spend_name = spend_bundle.name()

    original_pre_validate = fn.mempool_manager.pre_validate_spendbundle

    async def raise_runtime_error(*_args: Any, **_kwargs: Any) -> SpendBundleConditions:
        raise RuntimeError("simulated unexpected failure")

    fn.mempool_manager.pre_validate_spendbundle = raise_runtime_error  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="simulated unexpected failure"):
            await fn.add_transaction(spend_bundle, spend_name, test=True)
    finally:
        fn.mempool_manager.pre_validate_spendbundle = original_pre_validate  # type: ignore[method-assign]

    assert not fn.mempool_manager.in_flight(spend_name)
    assert not fn.mempool_manager.seen(spend_name)

```
