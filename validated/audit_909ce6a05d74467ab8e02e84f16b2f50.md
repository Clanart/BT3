### Title
Unbounded length-prefix parsing in `is_atom_canonical()` can raise an uncaught `IndexError`, crashing spend-bundle admission - (File: `chia/full_node/mempool_manager.py`)

### Summary
CVE-2024-38797 describes an EDK2 out-of-bounds read caused by trusting an externally supplied data pointer/length pair (`HashPeImageByType()`) without validating the length against the actual buffer size. The chia-blockchain analog is `is_atom_canonical()` / `is_clvm_canonical()` in `chia/full_node/mempool_manager.py`, which walks a length-prefix-encoded CLVM buffer using indices derived directly from attacker-controlled prefix bytes, with no bounds check against `len(clvm_buffer)` before indexing.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` decodes a CLVM atom's variable-length size prefix (1–6 bytes) directly from the buffer bytes and then computes `atom_len` from those bytes: [1](#0-0) 

Note that inside the `for i in range(prefix_len)` loop, `offset` is incremented and `clvm_buffer[offset]` is read with no check that `offset < len(clvm_buffer)`. The returned `atom_len` (used by the caller to advance `offset` by `1 + prefix_len + atom_len`) is likewise never validated against the remaining buffer length — unlike the equivalent bounds-checked parsers in `chia/full_node/full_block_utils.py` (`skip_bytes`, `skip_list`), which explicitly raise `ValueError` when a declared length exceeds the remaining buffer: [2](#0-1) 

`is_clvm_canonical()` loops over the buffer using these unchecked offsets: [3](#0-2) 

This function is invoked directly on attacker-supplied bytes during spend-bundle admission, once per coin spend, on both the puzzle reveal and the solution: [4](#0-3) 

`validate_spend_bundle()` is called from `add_spend_bundle()`, which is reachable from ordinary spend-bundle submission (RPC / mempool admission) with no try/except around the `is_clvm_canonical` calls: [5](#0-4) 

A crafted `puzzle_reveal` or `solution` byte string whose declared atom length prefix runs past the actual buffer length (e.g., a truncated multi-byte length prefix, or a length value causing `offset` to walk beyond `len(clvm_buffer)`) will cause `clvm_buffer[offset]` to raise `IndexError` in Python's `bytes.__getitem__`. Because Python enforces memory safety on `bytes` objects, this cannot leak adjacent memory the way the EDK2 C-language bug does, but it reproduces the exact same root-cause pattern: an externally supplied length field is used to walk a buffer offset without validating it against the buffer's actual size before dereferencing.

### Impact Explanation
If `IndexError` is raised inside `is_clvm_canonical()`/`is_atom_canonical()` and is not caught anywhere in the call chain (`validate_spend_bundle` → `add_spend_bundle`), it propagates as an unhandled exception out of mempool admission processing for that spend bundle. Depending on how the top-level RPC/network message handler for spend-bundle submission wraps this call, this can manifest as a spend-triggered exception disrupting transaction-processing for that request. This maps to the "spend-triggered transaction-processing halt" impact category permitted by the validation rules. It does not grant coin theft, forged asset identity, or coin-set divergence — the impact class here is availability/processing-disruption of a single node's mempool handling for a malformed but attacker-crafted coin spend.

### Likelihood Explanation
An unprivileged spend-bundle submitter fully controls `puzzle_reveal` and `solution` bytes of a `CoinSpend`. Constructing a byte sequence with a malformed/truncated multi-byte atom length-prefix (e.g., `0xFC` class prefix near the end of a buffer) that causes the offset-walking loop in `is_atom_canonical` to index past the end of `clvm_buffer` is straightforward and does not require any privileged access — it only requires submitting a spend bundle through the normal transaction submission path (matching the test harness pattern already present in the repo's own canonical-CLVM tests).

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before every `clvm_buffer[offset]` access (mirroring the pattern already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`), and validate that `atom_len` plus the current offset does not exceed `len(clvm_buffer)` before returning. Additionally, wrap the `is_clvm_canonical()` calls in `validate_spend_bundle()` in a guarded exception handler that converts any parsing failure (e.g., malformed/truncated atom encoding) into `Err.INVALID_COIN_SOLUTION` rather than allowing an unhandled `IndexError` to propagate out of mempool admission.

### Proof of Concept
1. Construct a `puzzle_reveal` or `solution` byte buffer ending in a multi-byte atom length-prefix byte (e.g., `0xFC`, which per `is_atom_canonical` requires 5 additional length bytes) but truncate the buffer so fewer than 5 bytes follow.
2. Wrap this buffer in a `CoinSpend`/`SpendBundle` and submit it through normal spend-bundle admission (e.g., via `MempoolManager.add_spend_bundle` after `pre_validate_spendbundle`, exercised the same way as the existing test `test_mempool_requires_canonical_clvm` in `chia/_tests/core/mempool/test_mempool_manager.py`, but with an even-more-truncated malformed prefix rather than merely non-canonical).
3. Observe that `is_clvm_canonical()` raises `IndexError` from `clvm_buffer[offset]` inside `is_atom_canonical()` instead of returning `False`/raising a handled `ValidationError`, propagating an unhandled exception out of `validate_spend_bundle()`. [6](#0-5)

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

**File:** chia/full_node/mempool_manager.py (L640-655)
```python
        err, item, remove_items = await self.validate_spend_bundle(
            new_spend,
            conds,
            spend_name,
            first_added_height,
            get_coin_records,
            get_unspent_lineage_info_for_puzzle_hash,
        )
        if err is None:
            # No error, immediately add to mempool, after removing conflicting TXs.
            assert item is not None
            conflict = self.mempool.remove_from_pool(remove_items, MempoolRemoveReason.CONFLICT)
            info = self.mempool.add_to_pool(item)
            if info.error is not None:
                return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.FAILED, [], info.error)
            return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.SUCCESS, [*info.removals, conflict], None)
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
