### Title
Unvalidated negative fee computation in mempool spend-bundle validation causes unhandled cast exception - ([File: chia/full_node/mempool_manager.py])

### Summary
`MempoolManager.validate_spend_bundle` computes the transaction fee by subtracting the total `CREATE_COIN` addition amount from the total removal (spent-coin) amount and immediately narrows the result into a `uint64`, without first checking that the subtraction result is non-negative: [1](#0-0) 

This mirrors the CVE-2026-70378 bug class: a value derived from unvalidated/attacker-controlled input (here, the sum of `CREATE_COIN` amounts declared in a submitted spend bundle) is used in an arithmetic operation that can go negative, and the negative result is fed directly into an unsigned/narrowing cast (`uint64(...)`) without a prior bounds check — exactly like the `Carve::apply` ratio being cast to an unsigned width without a positivity check.

### Finding Description
`addition_amount` is accumulated purely from the `CREATE_COIN` conditions produced by executing the spend bundle's puzzles: [2](#0-1) 

`removal_amount` is the sum of amounts of the coins actually being spent: [3](#0-2) 

Then:
```python
fees = uint64(removal_amount - addition_amount)
```
If `addition_amount > removal_amount` — i.e., an attacker submits a spend bundle whose puzzle(s) declare `CREATE_COIN` conditions that create more total value than is being spent (an "inflation" attempt, or simply a malformed/incorrect solution) — the subtraction yields a negative Python `int`. Passing a negative `int` into `uint64(...)` (backed by `chia_rs.sized_ints`) raises an exception (comparable to Rust's `assert!`/panic pattern in the CVE), because `uint64` has no defined behavior for negative inputs and does not saturate or clamp.

This is in contrast to how the codebase treats the analogous problem elsewhere — e.g. `validate_block_body` explicitly checks `coin.amount < 0` before proceeding (`Err.COIN_AMOUNT_NEGATIVE`): [4](#0-3) 

and the test-only helper `compute_fee_test` clamps the value with `max(ret, 0)` before casting: [5](#0-4) 

but the production mempool path at `mempool_manager.py:814` has no equivalent clamp/guard before the narrowing cast.

### Impact Explanation
This function is reached for every spend bundle admitted to the mempool — i.e., directly reachable by any unprivileged wallet/RPC caller or peer submitting a transaction. An unhandled exception thrown mid-way through `validate_spend_bundle` can abort processing of that mempool-admission call path, matching the "spend-triggered transaction-processing halt" impact category explicitly listed as in-scope. Because this occurs on the mempool ingestion hot path (used for every new transaction and repeatedly during block-template construction/mempool maintenance), a submitter can reliably trigger the crash-path with a single crafted spend bundle.

### Likelihood Explanation
Likelihood is high for triggering the exception: a spend bundle is trivial to construct where an inner puzzle emits `CREATE_COIN` conditions summing to more than the coins being spent (this does not need signature validation to reach the condition-cost/fee computation stage, since `conds` is computed from CLVM execution before higher-level supply-inflation checks are applied). What remains unverified with the available context is whether the exception is caught by an outer try/except in the peer-message/RPC dispatch layer (which is common in chia's async API handling) before it can destabilize the process — I could not confirm the full call stack from network/RPC entry point down to `add_spend_bundle`/`validate_spend_bundle` within the available index. This uncertainty should be resolved by an engineer with full repository access before treating this as a confirmed process-crash primitive rather than a per-request failure.

### Recommendation
Before computing `fees`, explicitly validate `removal_amount >= addition_amount` and return a proper `Err` (e.g. a new `Err.INVALID_BLOCK_FEE_AMOUNT`-style rejection) instead of relying on the `uint64(...)` cast to enforce the invariant, consistent with the explicit `coin.amount < 0` checks used in `chia/consensus/block_body_validation.py`. Add a regression test analogous to `test_compute_block_fee` (in `chia/_tests/core/consensus/test_block_creation.py`) but targeting `MempoolManager.validate_spend_bundle`/`validate_spend_bundle`'s fee computation specifically, to ensure a crafted spend bundle with `addition_amount > removal_amount` is rejected with a clean `Err` rather than raising an uncaught exception.

### Proof of Concept
1. Construct a `SpendBundle` containing a single coin spend whose puzzle, when run, emits one or more `CREATE_COIN` conditions whose summed `amount` exceeds the amount of the coin being spent (e.g., spend a 1-mojo coin but emit `CREATE_COIN` for 1,000,000 mojos — no valid CAT/TAIL is needed since this is plain XCH and the puzzle can be any that unconditionally returns such a condition, e.g., a custom, unsigned unlock puzzle for coins the attacker controls).
2. Submit the spend bundle via the standard transaction submission path so it reaches `MempoolManager.add_spend_bundle` → `validate_spend_bundle`.
3. Execution reaches `fees = uint64(removal_amount - addition_amount)` at `chia/full_node/mempool_manager.py:814` with a negative operand, raising an exception instead of a graceful `Err` result. [6](#0-5)

### Citations

**File:** chia/full_node/mempool_manager.py (L748-814)
```python
            spend_additions = []
            for puzzle_hash, amount, _ in spend_conds.create_coin:
                child_coin = Coin(coin_id, puzzle_hash, uint64(amount))
                spend_additions.append(child_coin)
                additions_dict[child_coin.name()] = child_coin
                addition_amount += amount

            bundle_coin_spends[coin_id] = BundleCoinSpend(
                coin_spend=coin_spend,
                eligible_for_dedup=bool(spend_conds.flags & ELIGIBLE_FOR_DEDUP),
                additions=spend_additions,
                cost=uint64(spend_conds.condition_cost + spend_conds.execution_cost),
                latest_singleton_lineage=lineage_info,
                atom_count=spend_conds.atom_count,
                pair_count=spend_conds.pair_count,
            )

        non_ff_spend_ids = set()
        effective_spend_ids = set()
        for coin_id, spend_data in bundle_coin_spends.items():
            if spend_data.latest_singleton_lineage is None:
                non_ff_spend_ids.add(coin_id)
                effective_spend_id = coin_id
            else:
                effective_spend_id = spend_data.latest_singleton_lineage.coin_id
            # Fast forward spends are only allowed to be spent once in a spend bundle
            if effective_spend_id in effective_spend_ids:
                return Err.INVALID_SPEND_BUNDLE, None, []
            effective_spend_ids.add(effective_spend_id)
        # Fast forward spends are only allowed when bundled with other, non-FF
        # spends in order to evict an FF spend, it must be associated with a
        # normal spend that can be included in a block or invalidated some
        # other way.
        if len(non_ff_spend_ids) == 0:
            return Err.INVALID_SPEND_BUNDLE, None, []

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

**File:** chia/consensus/block_body_validation.py (L410-416)
```python
    for coin, coin_name in additions + coinbase_additions:
        additions_dic[coin_name] = coin
        if coin.amount < 0:
            return Err.COIN_AMOUNT_NEGATIVE

        if coin.amount > constants.MAX_COIN_AMOUNT:
            return Err.COIN_AMOUNT_EXCEEDS_MAXIMUM
```

**File:** chia/simulator/block_tools.py (L2436-2440)
```python
    ret = removal_amount - addition_amount
    # in order to allow creating blocks that mint coins, clamp the fee
    # to 0, if it ends up being negative
    ret = max(ret, 0)
    return uint64(ret)
```
