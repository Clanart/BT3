## Title
`get_popular_pruned_forks()` uses a live, epoch-mutable stake denominator to decide duplicate-fork repair, unlike the fixed-supply invariant the rest of consensus relies on - ([File: core/src/repair/repair_weight.rs])

## Summary
The external report flags `Goldigovernor._getProposalState()` for comparing accumulated votes against a live, ever-changing `totalSupply()`, which can silently flip a proposal's state as the denominator drifts. The Agave analog is `RepairWeight::get_popular_pruned_forks()`, which computes a `DUPLICATE_THRESHOLD` cutoff from `epoch_stakes` `total_stake()` values that can differ across the epoch boundaries a pruned tree spans. The code already documents that stake can be added/removed across an epoch boundary and only partially compensates for it (`min_total_stake`), leaving an acknowledged gap where the comparison denominator is not fixed relative to the accumulated numerator.

## Finding Description
`get_popular_pruned_forks()` determines whether a pruned fork is "popular" (i.e., suspected duplicate-confirmed by ≥52% of stake) by comparing `stake_voted_subtree` (a value accumulated incrementally over time via `add_voters()`) against a threshold computed from `total_stake()`: [1](#0-0) 

The comment block in the same function explicitly acknowledges the underlying instability of the denominator: [2](#0-1) 

The mitigation taken is to use `min_total_stake` (the minimum `total_stake()` across all epochs the pruned tree spans) rather than a value fixed at the time voting began. This is structurally the same class of bug as the report: the code accumulates a numerator (`stake_voted_subtree`, populated by `HeaviestSubtreeForkChoice`-style incremental stake tracking via `add_voters`) over an unbounded time window, then divides by a denominator (`total_stake()`) that is *not* guaranteed to be the same value that applied when each unit of numerator stake was recorded. The accompanying `TODO` comment in the same function confirms the fix is incomplete: [3](#0-2) 

Specifically: `HeaviestSubtreeForkChoice` migrates/subtracts stake as validators switch votes within a rooted subtree, but `repair_weight`'s pruned-tree stake bookkeeping does not perform the equivalent migration across pruned subtrees. Combined with the cross-epoch stake changes described above, `stake_voted_subtree` for a pruned tree can retain votes recorded under an old, larger total stake, while `min_total_stake` is computed from a newer, smaller total stake for a different epoch on the same pruned tree — producing a threshold crossing that does not correspond to any single, coherent snapshot of cluster stake, exactly analogous to the totalSupply()-drift bug in the report.

## Impact Explanation
When `get_popular_pruned_forks()` returns a false positive, the slot is queued as `AncestorHashesReplayUpdate::PopularPrunedFork(slot)` and processed by `AncestorHashesService`, which prioritizes it into `popular_pruned_slot_pool` and later `repairable_dead_slot_pool`: [4](#0-3) 

This feeds into `cluster_slot_state_verifier::on_popular_pruned_fork()`, which flags the slot as "suspected to be duplicate" and drives ancestor-hashes sampling / dump-and-repair logic: [5](#0-4) 

This is a repair/blockstore-path decision that determines whether the node treats a fork as popular-and-suspect versus ignoring it. An incorrectly triggered "popular pruned fork" classification (caused by a numerator/denominator mismatch spanning epoch boundaries) can cause the validator to dump and attempt to repair onto ancestry that is not actually duplicate-confirmed by the cluster, i.e., false acceptance of fork-state information built on a stake ratio that never existed at a single point in time.

## Likelihood Explanation
This requires a pruned tree whose votes span an epoch boundary where the active/total stake changes materially (stake activation/deactivation), which is a routine, permissionless, and expected occurrence on Solana (delegators can activate/deactivate stake every epoch) — not a "malicious validator" assumption. The comment block itself states this "*could* lead to a false positive," confirming the developers are aware the current `min_total_stake` heuristic does not fully close the gap. Likelihood is bounded by needing (a) a pruned fork, and (b) an epoch boundary with sufficient stake churn within the window the pruned tree's votes were accumulated — plausible but not trivially triggerable at will by a single unprivileged actor, so likelihood is moderate.

## Recommendation
Replace the `min_total_stake`-across-epochs heuristic with a stake accounting scheme that ties each unit of accumulated `stake_voted_subtree` to the specific epoch's `total_stake()` that was in effect at the time the vote was recorded, similar to how `HeaviestSubtreeForkChoice` migrates stake, so that the ratio compared against `DUPLICATE_THRESHOLD` always reflects a single, internally consistent stake snapshot rather than mixing numerator stake recorded under one epoch's total against a threshold computed from a different epoch's total.

## Proof of Concept
1. A fork is pruned at slot `pruned_root` and accumulates votes over slots spanning an epoch boundary, e.g. votes at slot 6 (epoch N, total_stake = `S_N`) and slot 22 (epoch N+1, total_stake = `S_N+1`, where `S_N+1 != S_N` due to normal stake activation/deactivation).
2. `get_popular_pruned_forks()` computes `min_total_stake = min(S_N, S_N+1)` across all slots in the pruned tree and derives `duplicate_confirmed_threshold = min_total_stake * DUPLICATE_THRESHOLD` per [6](#0-5) .
3. Because `stake_voted_subtree` is an accumulated total across the whole pruned tree lifetime (not epoch-partitioned) and pruned-subtree stake is never migrated/subtracted as described in the TODO comment, the same aggregate stake value gets compared against a denominator (`min_total_stake`) that was never the true total stake at any single instant relevant to that aggregate — mirroring exactly how `Goldiswap.totalSupply()` drifting under an accumulated `forVotes` comparison flips `ProposalState` in the external report.
4. This is demonstrated in the existing test `test_get_popular_pruned_forks_stake_change_across_epoch_boundary`, which simulates stake deactivation/reactivation across epoch boundaries and shows the threshold crossing behavior differs depending on which epoch's stake is used: [7](#0-6) .

### Citations

**File:** core/src/repair/repair_weight.rs (L814-834)
```rust
            // This pruned tree *could* span an epoch boundary. To be safe we use the
            // minimum DUPLICATE_THRESHOLD across slots in case of stake modification. This
            // *could* lead to a false positive.
            //
            // Additionally, we could have a case where a slot that reached `DUPLICATE_THRESHOLD`
            // no longer reaches threshold post epoch boundary due to stake modifications.
            //
            // Post boundary, we have 2 cases:
            //      1) The previously popular slot stays as the majority fork. In this
            //         case it will eventually reach the new duplicate threshold and
            //         validators missing the correct version will be able to trigger this pruned
            //         repair pathway.
            //      2) With the stake modifications, this previously popular slot no
            //         longer holds the majority stake. The remaining stake is now expected to
            //         reach consensus on a new fork post epoch boundary. Once this consensus is
            //         reached, validators on the popular pruned fork will be able to switch
            //         to the new majority fork.
            //
            // In either case, `HeaviestSubtreeForkChoice` updates the stake only when observing new
            // votes leading to a potential mixed bag of stakes being observed. It is safest to use
            // the minimum threshold from either side of the boundary.
```

**File:** core/src/repair/repair_weight.rs (L835-857)
```rust
            let min_total_stake = pruned_tree
                .slots_iter()
                .map(|slot| {
                    epoch_stakes
                        .get(&epoch_schedule.get_epoch(slot))
                        .expect("Pruned tree cannot contain slots more than an epoch behind")
                        .total_stake()
                })
                .min()
                .expect("Pruned tree cannot be empty");
            let duplicate_confirmed_threshold =
                ((min_total_stake as f64) * DUPLICATE_THRESHOLD) as u64;

            // TODO: `HeaviestSubtreeForkChoice` subtracts and migrates stake as validators switch
            // forks within the rooted subtree, however `repair_weight` does not migrate stake
            // across subtrees. This could lead to an additional false positive if validators
            // switch post prune as stake added to a pruned tree it is never removed.
            // A further optimization could be to store an additional `latest_votes`
            // in `repair_weight` to manage switching across subtrees.
            if pruned_tree
                .stake_voted_subtree(&slot_to_start_repair)
                .expect("Root of tree must exist")
                >= duplicate_confirmed_threshold
```

**File:** core/src/repair/repair_weight.rs (L2732-2825)
```rust
    #[test]
    fn test_get_popular_pruned_forks_stake_change_across_epoch_boundary() {
        let blockstore = setup_big_forks();
        let stake = 100;
        let (bank, vote_pubkeys) = bank_utils::setup_bank_and_vote_pubkeys_for_tests(10, stake);
        let mut epoch_stakes = bank.epoch_stakes_map().clone();
        let mut epoch_schedule = bank.epoch_schedule().clone();

        // Simulate epoch boundary at slot 10, where half of the stake deactivates
        // Additional epoch boundary at slot 20, where 30% of the stake reactivates
        let initial_stakes = epoch_stakes
            .get(&epoch_schedule.get_epoch(0))
            .unwrap()
            .clone();
        let mut dec_stakes = epoch_stakes
            .get(&epoch_schedule.get_epoch(0))
            .unwrap()
            .clone();
        let mut inc_stakes = epoch_stakes
            .get(&epoch_schedule.get_epoch(0))
            .unwrap()
            .clone();
        epoch_schedule.first_normal_slot = 0;
        epoch_schedule.slots_per_epoch = 10;
        assert_eq!(
            epoch_schedule.get_epoch(10),
            epoch_schedule.get_epoch(9) + 1
        );
        assert_eq!(
            epoch_schedule.get_epoch(20),
            epoch_schedule.get_epoch(19) + 1
        );
        dec_stakes.set_total_stake(dec_stakes.total_stake() - 5 * stake);
        inc_stakes.set_total_stake(dec_stakes.total_stake() + 3 * stake);
        epoch_stakes.insert(epoch_schedule.get_epoch(0), initial_stakes);
        epoch_stakes.insert(epoch_schedule.get_epoch(10), dec_stakes);
        epoch_stakes.insert(epoch_schedule.get_epoch(20), inc_stakes);

        // Add a little stake for each fork
        let votes = vec![
            (4, vec![vote_pubkeys[0]]),
            (11, vec![vote_pubkeys[1]]),
            (6, vec![vote_pubkeys[2]]),
            (23, vec![vote_pubkeys[3]]),
        ];
        let mut repair_weight = RepairWeight::new(0);
        repair_weight.add_voters(
            &blockstore,
            votes.into_iter(),
            bank.epoch_stakes_map(),
            bank.epoch_schedule(),
        );

        // Set root to 4, there should now be 3 pruned trees with `stake`
        repair_weight.set_root(4);
        assert_eq!(repair_weight.trees.len(), 1);
        assert_eq!(repair_weight.pruned_trees.len(), 3);
        assert!(
            repair_weight
                .pruned_trees
                .iter()
                .all(|(root, pruned_tree)| pruned_tree
                    .stake_voted_subtree(&(*root, Hash::default()))
                    == Some(stake))
        );

        // No fork hash `DUPLICATE_THRESHOLD`, should not be any popular forks
        assert!(
            repair_weight
                .get_popular_pruned_forks(&epoch_stakes, &epoch_schedule)
                .is_empty()
        );

        // 400 stake, For the 6 tree it will be less than `DUPLICATE_THRESHOLD`, however 11
        // has epoch modifications where at some point 400 stake is enough. For 22, although it
        // does cross the second epoch where the stake requirement was less, because it doesn't
        // have any blocks in that epoch the minimum total stake is still 800 which fails.
        let four_votes = vote_pubkeys.iter().copied().take(4).collect_vec();
        let votes = vec![
            (11, four_votes.clone()),
            (6, four_votes.clone()),
            (22, four_votes),
        ];
        repair_weight.add_voters(
            &blockstore,
            votes.into_iter(),
            bank.epoch_stakes_map(),
            bank.epoch_schedule(),
        );
        assert_eq!(
            vec![11],
            repair_weight.get_popular_pruned_forks(&epoch_stakes, &epoch_schedule)
        );
    }
```

**File:** core/src/repair/ancestor_hashes_service.rs (L548-580)
```rust
                AncestorHashesReplayUpdate::PopularPrunedFork(pruned_slot) => {
                    // The `dead_slot_pool` or `repairable_dead_slot_pool` can already contain this slot already
                    // if the below order of events happens:
                    //
                    // 1. Slot is marked dead/duplicate confirmed
                    // 2. Slot is pruned
                    if dead_slot_pool.contains(&pruned_slot) {
                        // Similar to the above case where `pruned_slot` was first pruned and then marked
                        // dead, since `pruned_slot` is part of a popular pruned fork it has become
                        // `EpochSlotsFrozen` as 52% must have frozen a version of this slot in
                        // order to vote.
                        // This fits the alternate criteria we use in `find_epoch_slots_frozen_dead_slots`
                        // so we can upgrade it to `repairable_dead_slot_pool`.
                        info!(
                            "{pruned_slot} is part of a popular pruned fork however we previously \
                             marked it as dead. Upgrading as dead duplicate confirmed"
                        );
                        dead_slot_pool.remove(&pruned_slot);
                        repairable_dead_slot_pool.insert(pruned_slot);
                    } else if repairable_dead_slot_pool.contains(&pruned_slot) {
                        // If we already observed `pruned_slot` as dead duplicate confirmed, we
                        // ignore the additional information that `pruned_slot` is popular pruned.
                        // This is similar to the above case where `pruned_slot` was first pruned
                        // and then marked dead duplicate confirmed.
                        info!(
                            "Received pruned duplicate confirmed status for {pruned_slot} that \
                             was previously marked dead duplicate confirmed. Ignoring and \
                             processing it as dead duplicate confirmed."
                        );
                    } else {
                        popular_pruned_slot_pool.insert(pruned_slot);
                    }
                }
```

**File:** core/src/repair/cluster_slot_state_verifier.rs (L675-684)
```rust
fn on_popular_pruned_fork(slot: Slot) -> Vec<ResultingStateChange> {
    warn!(
        "{slot} is part of a pruned fork which has reached the DUPLICATE_THRESHOLD aggregating \
         across descendants and slot versions. It is suspected to be duplicate or have an \
         ancestor that is duplicate. Notifying ancestor_hashes_service"
    );
    vec![ResultingStateChange::SendAncestorHashesReplayUpdate(
        AncestorHashesReplayUpdate::PopularPrunedFork(slot),
    )]
}
```
