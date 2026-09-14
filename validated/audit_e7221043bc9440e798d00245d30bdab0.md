### Title
Unchecked divide-by-zero when logging fee-per-cost during block building - ([File: chia/full_node/mempool.py])

### Summary
`Mempool.create_bundle_from_mempool_items()` computes `item_cost = cost - cost_saving` for every mempool item it considers when assembling a block, then immediately performs `fee / item_cost` inside a `log.info(...)` call without any zero-check on `item_cost`. `cost` is the item's total `SpendBundleConditions.cost`, and `cost_saving` is attacker-influenced dedup savings computed by `IdenticalSpendDedup.get_deduplication_info()`. If `cost_saving` can be made to equal `cost` for a given item, `item_cost` becomes `0` and the subsequent `fee / item_cost` raises `ZeroDivisionError`, an uncaught exception inside a hot full-node code path (mempool block-building), analogous to the CVE-2017-6833 divide-by-zero-on-crafted-input bug class.

### Finding Description
`create_bundle_from_mempool_items()` iterates the mempool, and for each item (that isn't in the "priority skip" branch) calls `dedup_coin_spends.get_deduplication_info(...)` to compute `cost_saving`, then: [1](#0-0) 

immediately followed by: [2](#0-1) 

`item_cost` is used as the denominator of `fee / item_cost` with no guard for `item_cost == 0`, unlike `MempoolManager.validate_spend_bundle`, which does check `if cost == 0` before dividing `fees / cost` at admission time: [3](#0-2) 

That admission-time check only guarantees the item's *own, undeduplicated* `cost` (`item.conds.cost`) is non-zero — it says nothing about `item_cost` computed later during block assembly, after dedup savings are subtracted.

`cost_saving` is produced by `IdenticalSpendDedup.get_deduplication_info()`, which sums `dedup_coin_spend.cost` for every coin in the item that is `eligible_for_dedup` and whose `coin_id` was already recorded by an earlier-processed mempool item spending the *same coin* with an *identical solution*: [4](#0-3) 

This dedup mechanism exists precisely because multiple independently-submitted spend bundles (e.g., competing offer/settlement acceptances that reference the same `settlement_payments`/announcement coin) can legitimately contain an identical coin spend. An attacker (any unprivileged mempool submitter) can construct a spend bundle whose entire set of `bundle_coin_spends` consists solely of such dedup-eligible spends against coins that another pending item has already contributed to the block being built. If the sum of the recorded per-coin costs for those spends (`cost_saving`) equals the submitted item's total `conds.cost`, `item_cost` collapses to `0`, and the very next line's `fee / item_cost` throws unhandled.

### Impact Explanation
An uncaught `ZeroDivisionError` inside `create_bundle_from_mempool_items()` aborts block-generator construction for the affected full node at that call site. Since this function is on the block-creation path (farming/mempool→block flow), this is a spend-triggered transaction-processing halt reachable by any party who can get a spend bundle admitted into the mempool alongside another pending bundle that shares a dedup-eligible coin/solution (a normal, expected scenario in offer/settlement flows, not a privileged or malicious-peer capability). This matches the "spend-triggered transaction-processing halt" acceptance criterion.

### Likelihood Explanation
Reaching `item_cost == 0` exactly requires the item's full CLVM+condition cost to be composed entirely of costs already accounted for as `cost_saving` from a previously-included item's identical coin spends — i.e., no additional AGG_SIG/CREATE_COIN/other condition costs of its own. I was not able to fully confirm from the available code whether `BundleCoinSpend.cost` (the per-coin "isolated cost" recorded in `DedupCoinSpend`) is computed to exactly match the corresponding share of `SpendBundleConditions.cost`, since that isolated-cost computation lives outside the files I could inspect in this pass. This is a real code-path gap (missing zero-guard before a division, unlike the equivalent check at admission time) but the exact reachability of `item_cost == 0` needs to be confirmed against the isolated-cost computation for `BundleCoinSpend.cost` before treating this as fully proven.

### Recommendation
Guard the division in `create_bundle_from_mempool_items()`: skip or special-case items where `item_cost <= 0` (which can also arise from cost-accounting mismatches) before computing `fee / item_cost`, mirroring the `if cost == 0` check already present in `chia/full_node/mempool_manager.py`.

### Proof of Concept
Not fully constructible from the indexed files alone: exploitation requires confirming the exact relationship between `BundleCoinSpend.cost` (per-coin isolated cost used in `DedupCoinSpend`) and the parent item's total `SpendBundleConditions.cost`, which is computed by the Rust CLVM condition-parsing layer not visible in this pass. A concrete PoC would submit two spend bundles: bundle A (any valid spend) and bundle B, both spending an identical coin/solution pair that is `eligible_for_dedup` (e.g., a shared settlement/announcement coin from an offer), such that bundle B's `bundle_coin_spends` consists entirely of such shared coins and its `conds.cost` exactly equals the accumulated `cost_saving`, then trigger `create_bundle_from_mempool_items()` (block building) with both items pending.

### Citations

**File:** chia/full_node/mempool.py (L658-669)
```python
                    (
                        unique_coin_spends,
                        cost_saving,
                        atoms_saving,
                        pairs_saving,
                        unique_additions,
                        dedup_state_update,
                    ) = dedup_coin_spends.get_deduplication_info(bundle_coin_spends=bundle_coin_spends)
                item_cost = cost - cost_saving
                log.info(
                    "Cumulative cost: %d, fee per cost: %0.4f, item cost: %d", cost_sum, fee / item_cost, item_cost
                )
```

**File:** chia/full_node/mempool_manager.py (L814-829)
```python
        fees = uint64(removal_amount - addition_amount)

        if cost == 0:
            return Err.UNKNOWN, None, []

        if cost > self.max_tx_clvm_cost:
            return Err.BLOCK_COST_EXCEEDS_MAX, None, []

        # this is not very likely to happen, but it's here to ensure SQLite
        # never runs out of precision in its computation of fees.
        # sqlite's integers are signed int64, so the max value they can
        # represent is 2^63-1
        if fees > MEMPOOL_ITEM_FEE_LIMIT or SQLITE_INT_MAX - self.mempool.total_mempool_fees() <= fees:
            return Err.INVALID_BLOCK_FEE_AMOUNT, None, []

        fees_per_cost: float = fees / cost
```

**File:** chia/full_node/eligible_coin_spends.py (L180-222)
```python
        cost_saving = 0
        atoms_saving = 0
        pairs_saving = 0
        unique_coin_spends: list[CoinSpend] = []
        unique_additions: list[Coin] = []
        # Map of coin ID to deduplication information
        new_dedup_spends: dict[bytes32, DedupCoinSpend] = {}
        # See if this item has coin spends that are eligible for deduplication
        for coin_id, spend_data in bundle_coin_spends.items():
            if not spend_data.eligible_for_dedup:
                unique_coin_spends.append(spend_data.coin_spend)
                unique_additions.extend(spend_data.additions)
                continue
            # See if we processed an item with this coin before
            dedup_coin_spend = self.deduplication_spends.get(coin_id)
            if dedup_coin_spend is None:
                # We didn't process an item with this coin before. If we end up including
                # this item, add this pair to deduplication_spends
                new_dedup_spends[coin_id] = DedupCoinSpend(
                    spend_data.coin_spend.solution,
                    spend_data.cost,
                    spend_data.atom_count,
                    spend_data.pair_count,
                )
                unique_coin_spends.append(spend_data.coin_spend)
                unique_additions.extend(spend_data.additions)
                continue
            # See if the solution was identical
            if dedup_coin_spend.solution != spend_data.coin_spend.solution:
                # This should not happen. DEDUP spends of the same coin with
                # different solutions are rejected in check_removals().
                raise SkipDedup("Solution is different from what we're deduplicating on")
            cost_saving += dedup_coin_spend.cost
            atoms_saving += spend_data.atom_count
            pairs_saving += spend_data.pair_count
        return (
            unique_coin_spends,
            uint64(cost_saving),
            atoms_saving,
            pairs_saving,
            unique_additions,
            new_dedup_spends,
        )
```
