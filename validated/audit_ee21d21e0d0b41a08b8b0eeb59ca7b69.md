### Title
Unhandled `IndexError` in `is_atom_canonical()` from an attacker-crafted truncated CLVM atom header lets a single spend bundle crash mempool transaction processing - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_clvm_canonical()` / `is_atom_canonical()` in `chia/full_node/mempool_manager.py` parse the raw CLVM serialization bytes of a coin spend's `puzzle_reveal` and `solution` without bounds-checking the length-prefix continuation bytes against the buffer size, analogous to the CVE-2020-27545 libdwarf bug where an invalid/truncated table entry causes an out-of-bounds pointer dereference during untrusted-input parsing.

### Finding Description
`is_atom_canonical()` decodes a variable-length atom-size prefix (1–6 bytes depending on the leading byte's high bits): [1](#0-0) 

For each of `prefix_len` continuation bytes, it does `offset += 1; atom_len |= clvm_buffer[offset]` with no check that `offset` is still within `len(clvm_buffer)`. If an attacker crafts a `puzzle_reveal` or `solution` buffer whose last byte is a length-prefix marker requiring several continuation bytes (e.g. `0xFC` requiring 5 more bytes) but the buffer ends immediately after that marker, indexing `clvm_buffer[offset]` beyond the buffer bound raises an uncaught `IndexError`.

This function is invoked unconditionally — not just for DEDUP-eligible spends as the internal docs describe — on every coin spend's puzzle and solution during mempool admission: [2](#0-1) 

`validate_spend_bundle()` is called from `add_spend_bundle()`: [3](#0-2) 

Neither `validate_spend_bundle()` nor `add_spend_bundle()` wraps this code in a `try/except` that catches `IndexError`; the only exception handling nearby is a `ValueError`/`ValidationError` conversion around `validate_clvm_and_signature` earlier in `pre_validate_spendbundle()`: [4](#0-3) 

Because `is_clvm_canonical` is called from `validate_spend_bundle`, an `IndexError` here propagates up uncaught through `add_spend_bundle`, which is reached directly from network/RPC transaction submission and mempool re-validation paths (e.g. `new_peak` re-checks, `push_tx`/`respond_transaction` handling). An unhandled exception at this layer can abort the coroutine handling that spend bundle and, depending on the caller's exception handling around `add_spend_bundle`, potentially disrupt processing of that mempool operation.

### Impact Explanation
An unprivileged party who can submit a spend bundle (mempool submitter, wallet RPC caller, or any peer relaying a transaction) can construct a `puzzle_reveal` or `solution` whose CLVM byte encoding is deliberately truncated at a multi-byte atom-length prefix. This is a "spend-triggered transaction-processing halt" class issue: a single non-canonical, truncated CLVM buffer triggers an unhandled `IndexError` inside the mempool's admission path instead of a controlled `INVALID_COIN_SOLUTION` rejection, since the canonical-check function assumes well-formed prefix continuation and never validates remaining buffer length before indexing.

### Likelihood Explanation
Likelihood is high for reachability: `is_clvm_canonical()` runs on every coin spend in every submitted `SpendBundle`, unconditionally, and the crafted trigger is a straightforward truncated byte sequence (e.g., a solution or puzzle blob ending in `0xFC` with fewer than 5 trailing bytes) requiring no special privileges, signatures, or valid puzzle logic — the malformed bytes only need to reach `validate_spend_bundle()`.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` (and by extension `is_clvm_canonical()`) before indexing `clvm_buffer[offset]` for each continuation byte, returning a "not canonical"/rejection result (or raising a well-defined `ValidationError`/`Err.INVALID_COIN_SOLUTION`) when the buffer is shorter than the declared atom-length prefix, rather than allowing a raw `IndexError` to propagate out of mempool validation.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) bytes end with a multi-byte atom length-prefix marker but are truncated before all continuation bytes are present, e.g. `bytes.fromhex("fc03")` (marker `0xFC` normally requires 5 continuation bytes but only one is supplied).
2. Wrap it in a `SpendBundle` and submit it so that it reaches `MempoolManager.add_spend_bundle()` (e.g., via `push_tx`/RPC transaction submission).
3. `validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.solution))` → `is_atom_canonical()`, which attempts to read past the end of the truncated buffer, raising an uncaught `IndexError` instead of returning `Err.INVALID_COIN_SOLUTION`.

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

**File:** chia/full_node/mempool_manager.py (L560-569)
```python
        # validate_clvm_and_signature raises a ValueError with an error code
        except ValueError as e:
            # Convert that to a ValidationError
            if len(e.args) > 1:
                error = Err(e.args[1])
                raise ValidationError(error)
            else:
                raise ValidationError(Err.UNKNOWN)  # pragma: no cover
        finally:
            self._worker_queue_size -= 1
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
