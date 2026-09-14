### Title
Missing pre-check before `fees = uint64(removal_amount - addition_amount)` allows an unhandled underflow/exception during mempool spend-bundle validation - (File: `chia/full_node/mempool_manager.py`)

### Summary
`MempoolManager.validate_spend_bundle` (formerly `add_spend_bundle` path) computes `fees = uint64(removal_amount - addition_amount)` at [1](#0-0)  without first verifying that `removal_amount >= addition_amount`. This mirrors the reported bug class: an arithmetic subtraction into an unsigned type performed before validating that the subtrahend cannot exceed the minuend.

### Finding Description
In consensus-level block validation, the equivalent computation is guarded: `chia/consensus/block_body_validation.py` explicitly checks `if removed < added: return Err.MINTING_COIN` at [2](#0-1)  **before** doing `fees = removed - added` at [3](#0-2) .

The mempool-manager code path that validates an individual, user-submitted spend bundle before block inclusion does not perform this same ordering. It sums `removal_amount` from `removal_record_dict`/coin records at [4](#0-3)  and then immediately casts the difference to `uint64` at [5](#0-4)  with no preceding "additions <= removals" check analogous to `MINTING_COIN`. If a submitted spend bundle's puzzle/solution pair generates `create_coin` conditions whose total amount (`addition_amount`, accumulated at [6](#0-5) ) exceeds the sum of the amounts of the coins actually being spent (`removal_amount`), the subtraction is negative, and casting a negative Python int to `uint64` (a `chia_rs` sized-int wrapper) raises rather than silently wraps — unlike the Solidity case where the underflow reverts the whole transaction, here it is an uncaught arithmetic/type exception thrown deep inside per-spend-bundle mempool validation, not a controlled `Err.*` return value used everywhere else in this function.

### Impact Explanation
This is the mempool-layer counterpart to the reported `VaultFundManager.addFundsAndFulfillRedeem()` bug: a value/amount arithmetic operation subtracts an untrusted, attacker-influenced quantity (`addition_amount`, fully controlled by the spend bundle's `create_coin` conditions) from another quantity without first checking that the result is non-negative. Because block-level validation (`block_body_validation.py`) already special-cases and rejects this scenario as `Err.MINTING_COIN`, but the mempool intake path performs the unchecked subtraction first, a spend bundle attempting to mint value could cause the mempool manager to throw an unhandled exception instead of cleanly rejecting the spend bundle with a well-defined error code. Depending on how/whether callers of `validate_spend_bundle` catch generic exceptions, this can manifest as a spend-bundle-triggered processing halt/DoS for the mempool ingestion path, and it inconsistently duplicates trust-boundary logic that is properly enforced elsewhere in the codebase (raising the question of whether other reachable amount-diff computations similarly lack the guard).

### Likelihood Explanation
Reachable directly by any unprivileged user who can submit a spend bundle to a full node's mempool (via RPC `push_tx` or the wallet's spend-bundle submission path) with `create_coin` conditions whose sum exceeds spent-coin amounts. No special privileges, peer trust, or node compromise required — this is exactly the same attacker model as the external report (an ordinary user submitting a redemption/transaction).

### Recommendation
Add the same ordering/guard used in `block_body_validation.py`: compute `removal_amount` and `addition_amount` as plain Python ints, verify `removal_amount >= addition_amount` and return `Err.MINTING_COIN` (or an equivalent mempool-specific error) if not, and only then perform `fees = uint64(removal_amount - addition_amount)`.

### Proof of Concept
1. Construct a spend bundle that spends a set of coins summing to `removal_amount = N`.
2. Craft the puzzle reveal/solution for one of the spent coins so its `CREATE_COIN` conditions (parsed at [6](#0-5) ) sum to `addition_amount = N + k` for some `k > 0` (attempting to mint `k` extra mojos).
3. Submit the spend bundle to a full node's mempool (e.g., via the `push_tx` RPC path).
4. `validate_spend_bundle` accumulates `removal_amount` and `addition_amount`, then executes `fees = uint64(removal_amount - addition_amount)` at line 814 with no preceding sufficiency check — verify whether this raises an unhandled exception instead of returning a clean `Err` result, in contrast to the equivalent, properly-guarded computation in `block_body_validation.py` lines 541–545.

*(Note: due to index/content-size limits, the exact exception-handling behavior of `uint64()` on a negative value in the vendored `chia_rs` bindings, and whether callers of `validate_spend_bundle`/`add_spend_bundle` wrap this call in a try/except that would mask the crash, could not be fully confirmed from the indexed code alone. A full Devin session with direct repository/runtime access would be needed to execute the PoC and confirm the exact failure mode end-to-end.)*

### Citations

**File:** chia/full_node/mempool_manager.py (L748-753)
```python
            spend_additions = []
            for puzzle_hash, amount, _ in spend_conds.create_coin:
                child_coin = Coin(coin_id, puzzle_hash, uint64(amount))
                spend_additions.append(child_coin)
                additions_dict[child_coin.name()] = child_coin
                addition_amount += amount
```

**File:** chia/full_node/mempool_manager.py (L784-814)
```python
        removal_record_dict: dict[bytes32, CoinRecord] = {}
        removal_amount: int = 0
        removal_records = await get_coin_records(removal_names)
        for record in removal_records:
            removal_record_dict[record.coin.name()] = record

        for name in removal_names:
            if name not in removal_record_dict and name not in additions_dict:
                return Err.UNKNOWN_UNSPENT, None, []
            if name in additions_dict:
                removal_coin = additions_dict[name]
                # The timestamp and block-height of this coin being spent needs
                # to be consistent with what we use to check time-lock
                # conditions (below). All spends (including ephemeral coins) are
                # spent simultaneously. Ephemeral coins with an
                # ASSERT_SECONDS_RELATIVE 0 condition are still OK to spend in
                # the same block.
                assert self.peak.timestamp is not None
                removal_record = CoinRecord(
                    removal_coin,
                    uint32(self.peak.height + 1),
                    uint32(0),
                    False,
                    self.peak.timestamp,
                )
                removal_record_dict[name] = removal_record
            else:
                removal_record = removal_record_dict[name]
            removal_amount += removal_record.coin.amount

        fees = uint64(removal_amount - addition_amount)
```

**File:** chia/consensus/block_body_validation.py (L541-543)
```python
    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN
```

**File:** chia/consensus/block_body_validation.py (L545-545)
```python
    fees = removed - added
```
