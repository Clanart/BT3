### Title
Attacker-Triggered Division-by-Zero Crash in Block-Building via Dedup Cost Accounting - (File: chia/full_node/mempool.py)

### Summary
`Mempool.create_bundle_from_mempool_items()` computes a per-item "item cost" by subtracting the deduplicated cost savings from the mempool item's total cost, and immediately divides the item's fee by that value for a log message. Because the subtrahend (`cost_saving`) is accumulated from the individual dedup-eligible coin spend costs while the minuend is the item's *total* `conds.cost`, a mempool item consisting of a single dedup-eligible coin spend whose puzzle produces no condition cost overhead can drive `item_cost` to exactly zero, causing an unguarded `ZeroDivisionError` while building a block from the mempool.

### Finding Description
`create_bundle_from_mempool_items` iterates mempool items ordered by priority to assemble a block, calling `IdenticalSpendDedup.get_deduplication_info()` to compute `cost_saving` for coin spends eligible for dedup (i.e., coins spent identically across multiple mempool items, such as shared announcement/settlement coins in offer trades) [1](#0-0) .

The result is then used unconditionally in an eagerly-evaluated logging call:
```
item_cost = cost - cost_saving
log.info("Cumulative cost: %d, fee per cost: %0.4f, item cost: %d", cost_sum, fee / item_cost, item_cost)
``` [2](#0-1) 

`cost_saving` is accumulated purely from the per-coin-spend `cost` values of duplicate dedup-eligible spends (`dedup_coin_spend.cost`), which represents only the CLVM execution cost of the *specific coin spend*, not any condition/AGG_SIG overhead of the whole bundle [3](#0-2) . Meanwhile `cost` (the minuend, `item.conds.cost`) is the *total* cost of the whole spend bundle, including AGG_SIG and condition costs. For a mempool item that consists of exactly one dedup-eligible coin spend whose puzzle produces no additional condition or signature cost, the coin spend's own execution cost equals the item's total `conds.cost`, making `cost_saving == cost` and thus `item_cost == 0`. Because Python evaluates all `%`-style logging arguments eagerly (regardless of the configured log level), `fee / item_cost` raises `ZeroDivisionError` unconditionally.

Since `create_bundle_from_mempool_items` is invoked whenever the full node assembles a transaction block from the mempool, an unhandled exception here halts block-generator creation for that mempool state, i.e., a spend-triggered transaction-processing halt.

### Impact Explanation
This is a mempool/block-building denial-of-service: any spend bundle submitter can shape a dedup-eligible, zero-overhead coin spend (a pattern legitimately used by offer/trade counterparties sharing an announcement/settlement coin across multiple competing spend bundles) to crash block assembly on the full node when it attempts to build a block containing that item, degrading availability of transaction processing.

### Likelihood Explanation
Reaching this path requires only submitting ordinary spend bundles to the mempool via the standard submission path; no privileged access, malicious peer behavior, or node compromise is needed. The dedup-eligibility mechanism is a normal feature exercised by offer counterparties, so crafting a case where a single coin spend's isolated cost equals the item's total cost is plausible with a minimal-condition puzzle.

### Recommendation
Guard against `item_cost <= 0` before performing the division (skip the log/format calculation or clamp `item_cost` to at least 1 for logging), and/or make the log argument computation lazy (using `%`-style deferred formatting properly, or wrapping in `if log.isEnabledFor(logging.INFO)`) so it isn't evaluated unconditionally. More fundamentally, validate that `cost_saving` can never reach `cost` for a mempool item (e.g., always reserve some baseline non-dedupable cost), and add a regression test with a single dedup-eligible, zero-condition-cost coin spend.

### Proof of Concept
1. Submit two spend bundles, each spending a common dedup-eligible coin (same coin ID, same solution) as part of an offer/trade settlement pattern where the shared coin spend's puzzle emits no conditions (or only conditions with negligible cost) beyond its own execution — satisfying the `eligible_for_dedup` requirement.
2. Ensure at least one of the two mempool items being processed reduces to exactly this single coin spend, i.e., the item's `conds.cost` equals that coin spend's own recorded `cost`.
3. Once both items are admitted to the mempool, trigger block creation (`create_bundle_from_mempool_items` / `create_block_generator`/`create_block_generator2`), which processes items in priority order and calls `dedup_coin_spends.get_deduplication_info()`; on the second item, `cost_saving` equals `cost`, producing `item_cost == 0` and raising `ZeroDivisionError` inside the `log.info(...)` call at chia/full_node/mempool.py, aborting block-generator creation.

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

**File:** chia/full_node/eligible_coin_spends.py (L207-222)
```python
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
