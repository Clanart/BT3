### Title
Divide-by-zero DoS in mempool block builder when a fully-deduplicated spend's cost equals the item's total cost - ([File: chia/full_node/mempool.py])

### Summary
`create_bundle_from_mempool_items()` computes `item_cost = cost - cost_saving` from a mempool item's `conds.cost` and a dedup-derived cost saving, then immediately divides `fee / item_cost` for a log statement, with no guard against `item_cost == 0`.

### Finding Description
When building a block from the mempool, `Mempool.create_bundle_from_mempool_items()` iterates admitted mempool items and, for items that are not in the "skipped" fast-path, calls `IdenticalSpendDedup.get_deduplication_info()` to compute `cost_saving` — the cumulative cost of coin spends in the item that are `ELIGIBLE_FOR_DEDUP` and have already been included (with an identical solution) via an earlier, higher-fee-rate mempool item [1](#0-0) .

The code then computes:
```
item_cost = cost - cost_saving
log.info(... fee / item_cost, item_cost)
``` [2](#0-1) 

`cost` is `item.conds.cost`, the item's total CLVM cost, and `cost_saving` is the sum of `dedup_coin_spend.cost` for every coin spend in the item whose coin ID was already deduplicated against a prior item. If a mempool item consists of a single coin spend that is `ELIGIBLE_FOR_DEDUP`, and an earlier item in the same block-building pass already included the identical `(coin_id, solution)` pair, then `cost_saving` can equal the item's entire `conds.cost`, driving `item_cost` to `0`. The subsequent `fee / item_cost` then raises `ZeroDivisionError`, an unhandled exception inside `create_bundle_from_mempool_items()`.

`ELIGIBLE_FOR_DEDUP` spends and duplicate submission of the same coin ID + identical solution across multiple mempool items is an intended, reachable feature: `check_removals()` in `mempool_manager.py` explicitly allows two different mempool items to spend the same coin ID when both are `ELIGIBLE_FOR_DEDUP` and carry the same solution, treating this as "no conflict" rather than `MEMPOOL_CONFLICT` [3](#0-2) . This is precisely the offers/aggregation pattern (multiple independent spend bundles referencing a shared settlement/announcement coin with a public, signature-free solution) that dedup was designed to support, and is reachable by any unprivileged wallet user submitting ordinary spend bundles to the mempool — no special privileges, malicious peer behavior, or node compromise required.

This is structurally analogous to the reported `vproxy` bug: attacker/user-controlled input (a spend bundle's cost/solution shape) flows unguarded into a division operation (`fee / item_cost`), and a specific, reachable input value (`item_cost == 0`) triggers an unhandled `ZeroDivisionError` that crashes the code path processing that data.

### Impact Explanation
`create_bundle_from_mempool_items()` is invoked when constructing a block from the mempool. An unhandled `ZeroDivisionError` here would abort block-building for the process, which is a spend-triggered halt of transaction processing — a full node (or its farming logic) can be repeatedly prevented from successfully creating candidate blocks from the mempool as long as such a spend bundle configuration remains present, without any signature/authorization bypass being required to construct it. This matches the "spend-triggered transaction-processing halt" impact category.

### Likelihood Explanation
Reachability requires: (1) submitting two spend bundles, each containing a coin spend eligible for dedup with byte-identical `solution`s for the same coin ID (straightforward for an unprivileged wallet/offer participant to construct, e.g. via typical offer/settlement coin flows that mark spends `ELIGIBLE_FOR_DEDUP`), and (2) arranging that the item consisting solely of the deduplicated spend has `conds.cost` exactly equal to that single spend's `BundleCoinSpend.cost` (i.e., a mempool item whose only spend is the fully-eligible/duplicated one, with no other spends contributing extra cost). Because `conds.cost` for a single-spend item is expected to be dominated by that spend's execution+condition cost, this equality is plausible though it depends on exact cost accounting between `SpendBundleConditions.cost` and the per-spend `condition_cost + execution_cost` sum tracked in `BundleCoinSpend.cost`. I could not fully verify from the available code whether `conds.cost` always exactly equals the sum of per-spend costs with zero additional overhead (e.g., base costs, byte costs excluded elsewhere) — this exact-equality condition is the main uncertainty in confirming `item_cost` reaches precisely `0` rather than some small positive remainder.

### Recommendation
Guard the division in `chia/full_node/mempool.py` before line 668: if `item_cost <= 0`, skip the log's fee-rate computation (or clamp/guard it, e.g. `fee / item_cost if item_cost > 0 else float("inf")`), and add an explicit invariant/assert that `cost_saving` can never make `item_cost` reach zero for an admitted mempool item (since a zero-cost item should not exist per admission checks in `mempool_manager.py` `if cost == 0: return Err.UNKNOWN`). Add a regression test with two dedup-eligible identical-solution spends where the second item is a single-spend item whose cost is fully subsumed by `cost_saving`.

### Proof of Concept
1. Submit spend bundle A containing coin spend `S` on coin `C` where `S` is flagged `ELIGIBLE_FOR_DEDUP` (e.g., a puzzle with no `AGG_SIG` requirement), included in the mempool.
2. Submit spend bundle B containing solely a coin spend on coin `C'` (different coin, same underlying puzzle/solution bytes as `S`, also `ELIGIBLE_FOR_DEDUP` and matching the dedup key/solution used in `IdenticalSpendDedup`), such that bundle B's `conds.cost` is driven entirely by that single spend.
3. Trigger `Mempool.create_bundle_from_mempool_items()` (block building). During iteration, when the dedup entry for the shared coin/solution has already been recorded from a higher-fee-rate item, `get_deduplication_info()` returns `cost_saving` equal to bundle B's full `conds.cost`, producing `item_cost = 0` at `chia/full_node/mempool.py:666`, and the subsequent `fee / item_cost` at line 668 raises `ZeroDivisionError`, aborting block construction for that call. [4](#0-3)

### Citations

**File:** chia/full_node/eligible_coin_spends.py (L188-214)
```python
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
```

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

**File:** chia/full_node/mempool_manager.py (L293-306)
```python
            # if the spend we're adding is DEDUP, but there's a conflicting spend
            # that isn't DEDUP, we cannot merge them, so that's a conflict
            elif same_coin and coin_bcs.eligible_for_dedup and not conflict_bcs.eligible_for_dedup:
                conflicts.add(item)

            # if the spend we're adding is DEDUP but the existing spend has a
            # different solution, we cannot merge them, so that's a conflict
            elif (
                same_coin
                and coin_bcs.eligible_for_dedup
                and bytes(coin_bcs.coin_spend.solution) != bytes(conflict_bcs.coin_spend.solution)
            ):
                conflicts.add(item)

```
