## Analysis

The Olympus bug is a broken invariant: a state machine accumulates observations/counters into a threshold-gated trigger, but when the underlying tree/fork the counter was collected for is superseded (analogous to "price moved back inside the wall"), the accumulated counter is never decremented/migrated, so a stale threshold crossing still fires the privileged action. The closest local Agave analog is in the pruned-fork duplicate-confirmation logic used for ancestor-hashes repair.

### Title
Stale, never-migrated stake counts in `RepairWeight::pruned_trees` can falsely mark a pruned fork as "popular" (`>= DUPLICATE_THRESHOLD`) - (File: `core/src/repair/repair_weight.rs`)

### Summary
`RepairWeight::get_popular_pruned_forks` decides whether a pruned fork should be treated as duplicate-confirmed (and therefore fed into the ancestor-hashes repair pathway) purely by comparing the accumulated `stake_voted_subtree` recorded in `pruned_trees` against `DUPLICATE_THRESHOLD * min_total_stake`. [1](#0-0) 
The stake counters inside a pruned `HeaviestSubtreeForkChoice` subtree are only ever added to; they are not migrated/removed when the validators who cast those votes subsequently switch to voting for a different (non-pruned) fork. The code comments explicitly acknowledge this: [2](#0-1) 

### Finding Description
This mirrors the Olympus RANGE/Operator bug class: an action-gating counter (`_status.high.count` / `stake_voted_subtree`) is compared against a static threshold (`config_.regenThreshold` / `DUPLICATE_THRESHOLD`) without re-validating that the condition which produced the counter (price outside the wall / validators still voting for that fork) still holds. In Operator.sol, once enough historical "price above wall" observations accrue, the regen path fires even though price has since moved back inside the wall, because the accumulated `count` doesn't get cleared when the underlying condition changes. In `RepairWeight`, once enough historical votes accrue for a slot inside a `pruned_trees` entry, `get_popular_pruned_forks` will report it as "popular" (i.e., presumptively duplicate confirmed at `>= DUPLICATE_THRESHOLD`) even after the validators that cast those votes have since switched their vote to a different, non-pruned fork — because `HeaviestSubtreeForkChoice`'s stake bookkeeping only updates the currently-tracked subtree, and never subtracts a validator's earlier vote from a fork it has now switched away from once that fork was moved into `pruned_trees`. [3](#0-2) 

The consumer of this signal, `ReplayStage::process_popular_pruned_forks`, feeds the result directly into the same `check_slot_agrees_with_cluster` state machine used for genuine duplicate-confirmed slots, driving `duplicate_slots_to_repair` and ancestor-hashes repair. [4](#0-3) 

### Impact Explanation
A falsely "popular" pruned fork triggers the ancestor-hashes repair / duplicate-resolution machinery for a slot that is not actually supported by current cluster stake. This can cause a validator to redirect repair and replay effort toward a stale/abandoned fork, corrupting its local view of which fork is duplicate confirmed (`epoch_slots_frozen_slots`, `duplicate_slots_to_repair`) — matching the "false acceptance" impact category, since the validator's fork-choice/duplicate bookkeeping is updated based on stake counts that no longer reflect the live vote state of the network.

### Likelihood Explanation
This requires no malicious/privileged actor: it is purely a function of normal validator behavior — validators voting on a fork, that fork later being pruned, and then those same validators (or an overlapping majority) voting on a different fork. The stake accounting gap is explicitly called out as a known limitation in the code comments themselves ("`repair_weight` does not migrate stake across subtrees ... could lead to an additional false positive"), indicating the maintainers are aware the invariant can be violated in normal operation, not just adversarial conditions. [5](#0-4) 

### Recommendation
When a validator's latest vote moves off a slot contained in a `pruned_trees` entry, decrement/migrate that validator's stake contribution out of the pruned subtree's `stake_voted_subtree` accounting (symmetric to how `HeaviestSubtreeForkChoice` updates stake for the currently active tree on vote changes), or gate `get_popular_pruned_forks` on freshness of the votes (e.g., ignore votes older than some recency bound) rather than a cumulative, never-decaying stake tally.

### Proof of Concept
1. Validators A, B, C (collectively > `DUPLICATE_THRESHOLD` of total stake) vote for slot `S` on fork `F1`.
2. `F1` subsequently gets pruned (e.g., a competing fork wins), and its `HeaviestSubtreeForkChoice` subtree — including the accumulated stake from A, B, C's votes — is moved into `RepairWeight::pruned_trees`. [6](#0-5) 
3. A, B, C now vote for a different, non-pruned fork `F2`. Because stake bookkeeping in the pruned subtree is not migrated, `pruned_trees[F1].stake_voted_subtree(S)` still reflects the old vote total.
4. `get_popular_pruned_forks` computes `duplicate_confirmed_threshold` from current `epoch_stakes` and compares it against this stale, un-decremented stake figure, reporting `S` as a "popular" pruned slot even though the votes backing that figure no longer represent current cluster intent. [7](#0-6) 
5. This slot is then propagated into `ReplayStage::process_popular_pruned_forks` → `check_slot_agrees_with_cluster`, altering the validator's local duplicate/repair state based on stale data. [4](#0-3)

### Citations

**File:** core/src/repair/repair_weight.rs (L60-68)
```rust
    // Map from root -> a subtree rooted at that `root`
    trees: HashMap<Slot, HeaviestSubtreeForkChoice>,
    // Map from root -> pruned subtree
    // In the case of duplicate blocks linking back to a slot which is pruned, it is important to
    // hold onto pruned trees so that we can repair / ancestor hashes repair when necessary. Since
    // the parent slot is pruned these blocks will never be replayed / marked dead, so the existing
    // dead duplicate confirmed pathway will not catch this special case.
    // We manage the size by removing slots < root
    pruned_trees: HashMap<Slot, HeaviestSubtreeForkChoice>,
```

**File:** core/src/repair/repair_weight.rs (L799-846)
```rust
    pub fn get_popular_pruned_forks(
        &self,
        epoch_stakes: &HashMap<Epoch, VersionedEpochStakes>,
        epoch_schedule: &EpochSchedule,
    ) -> Vec<Slot> {
        if !self.pruned_tree_tracking_enabled {
            return vec![];
        }

        #[cfg(test)]
        static_assertions::const_assert!(DUPLICATE_THRESHOLD > 0.5);
        let mut repairs = vec![];
        for (pruned_root, pruned_tree) in self.pruned_trees.iter() {
            let mut slot_to_start_repair = (*pruned_root, Hash::default());

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
```

**File:** core/src/repair/repair_weight.rs (L848-853)
```rust
            // TODO: `HeaviestSubtreeForkChoice` subtracts and migrates stake as validators switch
            // forks within the rooted subtree, however `repair_weight` does not migrate stake
            // across subtrees. This could lead to an additional false positive if validators
            // switch post prune as stake added to a pruned tree it is never removed.
            // A further optimization could be to store an additional `latest_votes`
            // in `repair_weight` to manage switching across subtrees.
```

**File:** core/src/repair/repair_weight.rs (L854-875)
```rust
            if pruned_tree
                .stake_voted_subtree(&slot_to_start_repair)
                .expect("Root of tree must exist")
                >= duplicate_confirmed_threshold
            {
                // Search to find the deepest node that still has >= duplicate_confirmed_threshold (could
                // just use best slot but this is a slight optimization that will save us some iterations
                // in ancestor repair)
                while let Some(child) = pruned_tree
                    .children(&slot_to_start_repair)
                    .expect("Found earlier, this slot should exist")
                    .find(|c| {
                        pruned_tree
                            .stake_voted_subtree(c)
                            .expect("Found in children must exist")
                            >= duplicate_confirmed_threshold
                    })
                {
                    slot_to_start_repair = *child;
                }
                repairs.push(slot_to_start_repair.0);
            }
```

**File:** core/src/replay_stage.rs (L1156-1171)
```rust
                    let mut process_popular_pruned_forks_time =
                        Measure::start("process_popular_pruned_forks_time");
                    // Check for "popular" (52+% stake aggregated across versions/descendants) forks
                    // that are pruned, which would not be detected by normal means.
                    // Signalled by `repair_service`.
                    Self::process_popular_pruned_forks(
                        &popular_pruned_forks_receiver,
                        &blockstore,
                        &mut tbft_structs.duplicate_slots_tracker,
                        &mut tbft_structs.epoch_slots_frozen_slots,
                        &bank_forks,
                        &mut tbft_structs.heaviest_subtree_fork_choice,
                        &mut duplicate_slots_to_repair,
                        &ancestor_hashes_replay_update_sender,
                        &mut purge_repair_slot_counter,
                    );
```
