Based on available evidence, I found a plausible divide-by-zero analog reachable from mempool DEDUP handling during block construction.

### Title
Divide-by-zero in mempool block-building cost accounting via DEDUP-eligible spend collision - (File: chia/full_node/mempool.py)

### Summary
`Mempool.create_bundle_from_mempool_items()` computes `item_cost = cost - cost_saving` for each mempool item being folded into a candidate block, then immediately divides `fee / item_cost` for a log statement, with no zero-guard.

### Finding Description
In `chia/full_node/mempool.py`, `create_bundle_from_mempool_items()` iterates mempool items ordered by fee rate and, for DEDUP/FF-eligible items, calls `IdenticalSpendDedup.get_deduplication_info()` to compute `cost_saving` — the cumulative cost of coin spends already deduplicated against previously-processed items in the same block: [1](#0-0) 

`get_deduplication_info()` accumulates `cost_saving += dedup_coin_spend.cost` for every coin in the item whose solution matches a coin already recorded as deduplicated by an earlier (higher fee-rate) item: [2](#0-1) 

`dedup_coin_spend.cost` and the per-coin `BundleCoinSpend.cost` are both defined as `condition_cost + execution_cost` for that single coin spend, set at admission time in `MempoolManager.validate_spend_bundle()`: [3](#0-2) 

`check_removals()`/the mempool admission pipeline explicitly allows two *different* mempool items to spend the *same* coin with an *identical* DEDUP-eligible solution without treating it as a conflict ("Both DEDUP + same solution → can merge, no conflict"): [4](#0-3) 

If an attacker submits two distinct spend bundles that both spend the same DEDUP-eligible coin with an identical solution, and the second bundle's *only* coin spend is that dedup-eligible coin (i.e., its total `item.conds.cost` equals exactly the per-coin `condition_cost + execution_cost` already recorded for that coin by the first, higher-fee-rate item), then for the second item `cost_saving` equals `cost` exactly, making `item_cost = cost - cost_saving == 0`. The subsequent `fee / item_cost` log-format expression then raises `ZeroDivisionError`, since `MempoolItem.cost`/`BundleCoinSpend.cost` are plain Python ints/uint64 with no divide-by-zero guard, unlike `is_fee_enough()` and `validate_spend_bundle()` which do check `cost == 0` before dividing: [5](#0-4) [6](#0-5) 

No equivalent `item_cost == 0` guard exists before the division inside `create_bundle_from_mempool_items()`.

### Impact Explanation
An unhandled `ZeroDivisionError` inside block-building code would raise out of the `try` block surrounding this loop (only `SkipDedup`/`ValueError` from fast-forward code are shown handled nearby); if uncaught further up, it aborts the block-creation call for that peak, which is a spend-triggered halt of transaction/block-building processing on any full node attempting to farm/build a block from the poisoned mempool — a mempool/consensus availability impact rather than data corruption.

### Likelihood Explanation
Moderate-to-uncertain. The exact conditions require: (a) a coin puzzle that legitimately sets the `ELIGIBLE_FOR_DEDUP` flag (a specific, narrow puzzle-solution class), (b) two mempool items spending that same coin+solution, and (c) the second item consisting of exactly that single spend so its total cost equals the recorded per-coin dedup cost with no other cost source diluting it. I could not fully confirm from available code whether `create_bundle_from_mempool_items()` (as opposed to the similar `create_block_generator2()` path, which has the same cost-saving arithmetic but no division) is actually the function invoked by the live full-node block-farming flow, or a legacy/alternate path — this affects real-world exploitability and should be verified against call sites before treating this as confirmed-critical.

### Recommendation
Add an explicit `if item_cost == 0: … (skip or treat specially)` guard before computing `fee / item_cost` in `create_bundle_from_mempool_items()` (chia/full_node/mempool.py), mirroring the `cost == 0` checks already present in `MempoolManager.is_fee_enough()` and `validate_spend_bundle()`. Confirm and audit whether `create_block_generator2()` is the sole active production path, and if `create_bundle_from_mempool_items()` is unused legacy code, consider removing it to reduce attack surface.

### Proof of Concept
1. Craft coin C with a puzzle/solution that chia_rs marks `ELIGIBLE_FOR_DEDUP` in `SpendBundleConditions`.
2. Submit spend bundle A: spends coin C (dedup-eligible, solution S) plus other coins, with a high fee, into the mempool.
3. Submit spend bundle B: spends *only* coin C with the identical solution S, with a lower fee (admission succeeds because DEDUP-with-identical-solution is not a mempool conflict).
4. Trigger block building; item A is processed first (higher fee rate) and records `DedupCoinSpend` for coin C with `cost = condition_cost+execution_cost` of that spend. Item B is processed next; since B's `conds.cost` equals exactly that same per-coin cost, `cost_saving == cost`, so `item_cost = 0`, and `fee / item_cost` raises `ZeroDivisionError` in `chia/full_node/mempool.py`.

### Citations

**File:** chia/full_node/mempool.py (L654-668)
```python
                else:
                    bundle_coin_spends, ff_state_update = singleton_ff.process_fast_forward_spends(
                        mempool_item=item, prev_tx_height=prev_tx_height, constants=constants
                    )
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

**File:** chia/full_node/mempool_manager.py (L479-486)
```python
    def is_fee_enough(self, fees: uint64, cost: uint64) -> bool:
        """
        Determines whether any of the pools can accept a transaction with a given fees
        and cost.
        """
        if cost == 0:
            return False
        fees_per_cost = fees / cost
```

**File:** chia/full_node/mempool_manager.py (L755-763)
```python
            bundle_coin_spends[coin_id] = BundleCoinSpend(
                coin_spend=coin_spend,
                eligible_for_dedup=bool(spend_conds.flags & ELIGIBLE_FOR_DEDUP),
                additions=spend_additions,
                cost=uint64(spend_conds.condition_cost + spend_conds.execution_cost),
                latest_singleton_lineage=lineage_info,
                atom_count=spend_conds.atom_count,
                pair_count=spend_conds.pair_count,
            )
```

**File:** chia/full_node/mempool_manager.py (L814-817)
```python
        fees = uint64(removal_amount - addition_amount)

        if cost == 0:
            return Err.UNKNOWN, None, []
```

**File:** .cursor/context/mempool.md (L106-118)
```markdown
## `check_removals()` — Conflict detection

**Location**: `mempool_manager.py:229`

### Logic per coin

1. **Spent + non-FF** → `DOUBLE_SPEND` (immediate reject)
2. **In mempool**: look up conflicting items by coin ID
   - Both FF → can chain, no conflict
   - Both DEDUP + same solution → can merge, no conflict
   - Otherwise → `MEMPOOL_CONFLICT`
3. Handles edge case of FF spends indexed under latest singleton coin ID

```
