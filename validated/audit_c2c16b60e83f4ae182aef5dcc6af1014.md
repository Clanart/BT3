### Title
Divide-by-zero (`ZeroDivisionError`) in block-building fee/cost logging when dedup savings fully offset a mempool item's cost - (File: `chia/full_node/mempool.py`)

### Summary
`chia/full_node/mempool.py`'s `create_bundle_from_mempool_items()` computes a per-item cost after subtracting deduplication savings, and unconditionally evaluates `fee / item_cost` inline as an argument to a logging call, with no zero-guard [1](#0-0) . `item_cost` is derived as `cost - cost_saving`, where `cost_saving` comes from `IdenticalSpendDedup.get_deduplication_info()` summing the previously recorded per-coin CLVM cost of any coin spends in the item that are `eligible_for_dedup` and already registered from another item processed earlier in the same block-building pass [2](#0-1) .

### Finding Description
`item_cost = cost - cost_saving` is computed from `item.conds.cost` (the mempool item's total CLVM cost) minus `cost_saving` (sum of `dedup_coin_spend.cost` for each coin ID in the item that matches an already-seen dedup-eligible spend) [3](#0-2) . Immediately after, the code logs:

```python
log.info(
    "Cumulative cost: %d, fee per cost: %0.4f, item cost: %d", cost_sum, fee / item_cost, item_cost
)
``` [1](#0-0) 

Python evaluates `fee / item_cost` eagerly regardless of the configured log level, so if `item_cost` reaches exactly `0`, this raises an unhandled `ZeroDivisionError`.

This is analogous to CVE-2016-6505 (Wireshark PacketBB dissector divide-by-zero on crafted input): here, an unprivileged mempool submitter controls the shape of a spend bundle (which coins it spends, their `eligible_for_dedup` puzzle type, and how many other coin spends accompany them). If a mempool item's declared CLVM `cost` for its dedup-eligible spends is fully covered by a coin ID that another already-processed mempool item registered in `IdenticalSpendDedup.deduplication_spends` with the identical solution, `cost_saving` can equal the item's total `cost`, driving `item_cost` to `0`. Nothing in `get_deduplication_info()` or the caller enforces `item_cost > 0` before the log call [4](#0-3) .

Note: `MempoolManager.validate_spend_bundle()` guards against `cost == 0` at admission time [5](#0-4) , but that check is against the *original*, un-deduplicated `item.conds.cost` at submission time — it does not protect against `item_cost` (post-dedup-savings) becoming zero during later block assembly, which is a separate code path (`create_bundle_from_mempool_items`) executed on every full node attempting to build a block from the mempool.

### Impact Explanation
If triggered, this raises an unhandled `ZeroDivisionError` inside `create_bundle_from_mempool_items()`, which is the routine full nodes use to assemble transaction blocks from the mempool. An uncaught exception here would abort block-building for that call, representing a spend-triggered halt of transaction processing — matching the "no impact" exclusion boundary only if fully caught upstream; if not caught, it is a legitimate DoS analog to the reference CVE's crash-via-crafted-input pattern.

### Likelihood Explanation
Exploitability depends on precisely engineering two (or more) distinct mempool items such that: (1) they share a coin ID eligible for dedup with an identical solution (a supported feature for settlement/offer-style spends), and (2) the dependent item's total `conds.cost` is fully or exactly matched by the cumulative `cost_saving` from already-committed dedup entries. This requires the attacker to control multiple coordinated spend bundles and precise cost accounting, which is a non-trivial but plausible crafted-input scenario reachable purely through normal spend-bundle submission (no privileged access needed). I was unable to fully verify from the available code alone whether `item_cost == 0` is actually achievable in practice given the specific cost floor imposed on dedup-eligible puzzles (e.g., minimum settlement-payment execution cost), so this should be validated with a concrete PoC/test in a live node before treating it as fully confirmed.

### Recommendation
Guard against `item_cost <= 0` before computing `fee / item_cost` in the log statement in `create_bundle_from_mempool_items()` (e.g., use `%` lazy formatting or an explicit zero-check), and consider asserting/validating that `item_cost` remains positive after subtracting dedup savings, treating a zero or negative result as a defensive skip of the item rather than allowing an unguarded division.

### Proof of Concept
Not fully verified with a working end-to-end reproduction; based on static code review, the theoretical PoC would require: submitting mempool item A containing a dedup-eligible coin spend with cost `C`, then submitting mempool item B whose total `conds.cost` equals exactly `C` and which also spends the same dedup-eligible coin/solution as A, so that during `create_bundle_from_mempool_items()` processing of B after A, `cost_saving == C == cost`, making `item_cost == 0` and triggering `fee / item_cost` at [1](#0-0) .

### Citations

**File:** chia/full_node/mempool.py (L655-669)
```python
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
                )
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

**File:** chia/full_node/mempool_manager.py (L814-820)
```python
        fees = uint64(removal_amount - addition_amount)

        if cost == 0:
            return Err.UNKNOWN, None, []

        if cost > self.max_tx_clvm_cost:
            return Err.BLOCK_COST_EXCEEDS_MAX, None, []
```
