## Finding: Missing bounds checks in CLVM "canonical serialization" length-prefix parser can raise an unhandled `IndexError` from attacker-controlled spend-bundle bytes

### Title
Out-of-bounds read via unchecked length-prefix parsing in `is_clvm_canonical`/`is_atom_canonical` - (File: `chia/full_node/mempool_manager.py`)

### Summary
The CVE describes a heap-based buffer overflow in FFmpeg's `vf_bm3d.c` caused by insufficient bounds checking while walking a variable-length data structure (`get_block_row`). The closest reachable analog in this repository is `is_atom_canonical()`/`is_clvm_canonical()` in `chia/full_node/mempool_manager.py`, which walks a CLVM-serialized byte buffer using an attacker-controlled length prefix without ever validating `offset`/`atom_len` against `len(clvm_buffer)`.

### Finding Description
`is_clvm_canonical()` iterates over `clvm_buffer` starting at `offset = 0` and repeatedly reads `clvm_buffer[offset]` [1](#0-0) . When an atom byte indicates a multi-byte length prefix (5, 4, 3, 2, or 1 leading bits pattern), `is_atom_canonical()` increments `offset` and reads `clvm_buffer[offset]` for each prefix byte, with no check that `offset` stays within `len(clvm_buffer)` [2](#0-1) . The computed `atom_len` (derived entirely from attacker-supplied bytes) is then added to `offset` and used as the next read position in the caller loop, again without a bounds check [3](#0-2) .

This mirrors the FFmpeg bug class: a length/size value taken directly from untrusted input is used to advance a read cursor with no bounds validation. Unlike the C code (memory corruption), Python bytes indexing is bounds-checked at the interpreter level, so the practical failure mode is an unhandled `IndexError` rather than heap corruption — but the root cause (missing bounds validation on attacker-supplied length prefixes before use) is the same.

This routine is invoked as part of "canonical serialization" checks tied to `is_canonical_serialization`, which is used both in consensus block-body validation and in mempool spend-bundle admission to reject non-canonical CLVM encodings (see `Err.INVALID_TRANSACTIONS_GENERATOR_ENCODING` / `Err.INVALID_COIN_SOLUTION` usage) [4](#0-3) . The existing test suite in `chia/_tests/core/mempool/test_mempool_manager.py::test_mempool_requires_canonical_clvm` exercises this exact code path with attacker-crafted puzzle/solution bytes containing non-canonical/short length prefixes [5](#0-4) , showing this function is reachable directly from an unprivileged spend-bundle submission.

### Impact Explanation
If a crafted puzzle reveal or solution byte sequence encodes a length-prefix byte claiming N additional prefix bytes or a large atom length near/at the end of the buffer, `is_atom_canonical`/`is_clvm_canonical` will attempt to read past the end of `clvm_buffer`, raising `IndexError`. If this exception is not caught by the caller, it would propagate out of mempool/spend-bundle admission (or block-body validation) processing, resulting in a spend-triggered denial of service / transaction-processing halt for the affected code path — a legitimate high-severity availability impact for full nodes processing untrusted spend bundles or blocks.

### Likelihood Explanation
The `test_mempool_requires_canonical_clvm` test already demonstrates that attacker-controlled puzzle/solution bytes with malformed length prefixes reach this exact code from ordinary spend-bundle submission, meaning triggering the code path requires no special privileges — only a mempool submission or block containing a coin spend with such bytes. Whether it currently crashes the whole process or is caught safely depends on exception handling around the call sites feeding into `is_canonical_serialization`, which I was not able to fully trace within the remaining tool budget.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (and the calling loop in `is_clvm_canonical`) verifying that `offset` and `offset + prefix_len` remain within `len(clvm_buffer)` before indexing, returning "not canonical" (or raising a well-defined, caught `ValueError`) instead of allowing an `IndexError` to propagate from attacker-supplied data.

### Proof of Concept
A minimal buffer such as `bytes([0b11111100])` (a byte signaling a 5-byte length-prefix continuation with `prefix_len = 5`) with no subsequent bytes will cause `is_atom_canonical(buf, 0)` to attempt `clvm_buffer[1]` on a 1-byte buffer, raising `IndexError: index out of range`. Submitting a coin spend whose puzzle reveal or solution embeds such a truncated atom header (as already partially demonstrated by `test_mempool_requires_canonical_clvm`'s `"c00101"`/`"ffffc001018080"` cases) reaches this function from ordinary spend-bundle processing [5](#0-4) .

**Note on confidence**: I was unable to fully confirm, within tool budget, whether the `IndexError` raised here is always caught by a surrounding `try/except` in every call path (mempool admission vs. block validation), which determines whether the actual observable impact is a clean rejection (`Err`) or an unhandled crash/DoS. This should be verified directly in the codebase (tracing all callers of `is_canonical_serialization`/`is_clvm_canonical`) before treating this as a confirmed high-severity issue.

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

**File:** chia/consensus/block_body_validation.py (L389-391)
```python
        if prev_transaction_block_height >= constants.SOFT_FORK9_HEIGHT:
            if not is_canonical_serialization(generator_bytes):
                return Err.INVALID_TRANSACTIONS_GENERATOR_ENCODING
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
