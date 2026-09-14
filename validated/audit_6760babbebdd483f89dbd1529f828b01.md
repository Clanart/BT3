Confirming the reachable path: `pre_validate_spendbundle()` is wrapped in `try/except ValueError`/`ValidationError` in `full_node.add_transaction()` [1](#0-0) , but `add_spend_bundle()` (which calls `validate_spend_bundle()` → `is_clvm_canonical()`) at line 3098 is called *outside* that try/except block [2](#0-1) , meaning an `IndexError` raised inside `is_atom_canonical`/`is_clvm_canonical` would propagate unhandled through the `blockchain.priority_mutex` critical section.

### Title
Out-of-bounds atom-length-prefix read in mempool CLVM canonical-encoding check can crash transaction processing - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager.validate_spend_bundle()` calls `is_clvm_canonical()`/`is_atom_canonical()` on the raw `puzzle_reveal` and `solution` bytes of every coin spend in a submitted `SpendBundle` [3](#0-2) . These functions manually decode CLVM atom length prefixes by indexing into the buffer without validating that the computed prefix length or atom length stays within the buffer bounds before each index access [4](#0-3) [5](#0-4) . This mirrors the bug class in CVE-2023-25181 (length-prefix parsing of an attacker-controlled buffer without a bounds check leading to out-of-bounds memory access), but in this Python/CPython context an out-of-range index raises `IndexError` rather than corrupting memory.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `clvm_buffer[offset]` to get the atom tag byte, then based on the top bits determines a `prefix_len` (0–5 additional length-prefix bytes) and loops `offset += 1; atom_len |= clvm_buffer[offset]` for each additional byte, never checking `offset < len(clvm_buffer)` [6](#0-5) . `is_clvm_canonical(clvm_buffer)` drives this in a loop over the whole buffer, similarly indexing `clvm_buffer[offset]` at the top of each iteration without a length check, relying on `assert clvm_buffer != b""` only for the initial call [7](#0-6) .

`validate_spend_bundle()` invokes `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` and `is_clvm_canonical(bytes(coin_spend.solution))` for every coin spend in a user-submitted `SpendBundle`, unconditionally, as part of mempool admission [8](#0-7) . This is reached from `add_spend_bundle()` [9](#0-8) , which `FullNode.add_transaction()` calls at line 3098, outside the `try/except ValueError`/`ValidationError` block that wraps only `pre_validate_spendbundle()` [10](#0-9) .

### Impact Explanation
If an attacker can submit a `puzzle_reveal`/`solution` byte sequence such that this hand-rolled Python re-implementation of the CLVM atom-length decoding disagrees with the Rust CLVM parser used earlier in `pre_validate_spendbundle()` (e.g., an atom whose declared length prefix runs past the end of the buffer while still being accepted as a well-formed program by the Rust deserializer/execution path), `is_atom_canonical` would raise an unhandled `IndexError` while iterating `clvm_buffer[offset]`. Since this call sits inside `validate_spend_bundle()`, executed while holding `self.blockchain.priority_mutex` in `add_transaction()` (line 3092), an unhandled exception here would propagate out of the mutex context and out of the RPC/message handler, terminating in-flight transaction processing for that call and potentially the associated asyncio task, which matches the "spend-triggered transaction-processing halt" impact category from a single unprivileged spend-bundle submission.

### Likelihood Explanation
Exploitability hinges on finding a concrete byte sequence where the Rust CLVM parser's understanding of a valid/atomic length-prefix run differs from this Python re-implementation's bit-mask decoding (e.g., disagreement over the exact byte ranges 0xC0–0xFD or an off-by-one in `prefix_len`/`min_value` bounds), such that the sequence is accepted upstream but this function walks off the end of the buffer. I was not able to construct or confirm such a discrepancy in this pass — the two implementations both appear to follow the standard CLVM atom-length-prefix bit layout, and existing tests (`test_atom_not_canonical`, `test_clvm_not_canonical`) only exercise buffers that are long enough to satisfy each declared prefix length [11](#0-10) . Because the puzzle_reveal/solution bytes have already been successfully deserialized/executed by the Rust CLVM engine before this function is reached, in the normal case the buffer is guaranteed to be well-formed and this code path will not run off the end. Whether an edge case (e.g., a back-reference byte `0xFE` interacting with `tokens_left` bookkeeping, or a buffer that is valid CLVM but terminates mid-way through what this function interprets as a length prefix) can be crafted is unresolved.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (verify `offset + prefix_len < len(clvm_buffer)` before indexing) and in `is_clvm_canonical`'s main loop (verify `offset < len(clvm_buffer)` before dereferencing `clvm_buffer[offset]`), raising a normal validation error (mapped to `Err.INVALID_COIN_SOLUTION`) instead of allowing an `IndexError` to propagate. Additionally, wrap the `add_spend_bundle()`/`validate_spend_bundle()` call in `FullNode.add_transaction()` in the same defensive `try/except` used for `pre_validate_spendbundle()` so that any unexpected exception in mempool-admission code fails the single transaction rather than propagating through the blockchain mutex.

### Proof of Concept
Not confirmed in this pass. Given the current investigation, I could not construct a concrete `puzzle_reveal`/`solution` byte sequence that both (a) is accepted by the Rust CLVM deserializer/execution used earlier in `pre_validate_spendbundle()`, and (b) causes `is_atom_canonical`/`is_clvm_canonical` to index past the end of the buffer. Without that concrete discrepancy, this should be treated as a hardening gap (missing defensive bounds checks in security-adjacent parsing code reachable from untrusted spend bundles) rather than a proven crash. A background agent with fuzzing/test-execution capability would be needed to attempt to construct such an input against the actual `chia_rs` CLVM deserializer to confirm exploitability.

### Citations

**File:** chia/full_node/full_node.py (L3063-3100)
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

        self.mempool_manager.add_and_maybe_pop_seen(spend_name)

        if log_mempool_mode == "true":
            try:
                mempool_dir = path_from_root(self.root_path, "mempool-log") / f"{self.blockchain.get_peak_height()}"
                mempool_dir.mkdir(parents=True, exist_ok=True)
                with open(mempool_dir / f"{spend_name}.bundle", "wb+") as f:
                    f.write(bytes(transaction))
            except Exception:
                self.log.exception(f"Failed to log mempool item: {spend_name}")

        async with self.blockchain.priority_mutex.acquire(priority=BlockchainMutexPriority.low):
            if self.mempool_manager.get_spendbundle(spend_name) is not None:
                self.mempool_manager.remove_seen(spend_name)
                return MempoolInclusionStatus.SUCCESS, None
            if self.mempool_manager.peak is None:
                return MempoolInclusionStatus.FAILED, Err.MEMPOOL_NOT_INITIALIZED
            info = await self.mempool_manager.add_spend_bundle(
                transaction, cost_result, spend_name, self.mempool_manager.peak.height
            )
```

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

**File:** chia/full_node/mempool_manager.py (L194-221)
```python
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

**File:** chia/full_node/mempool_manager.py (L715-726)
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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L136-178)
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
```
