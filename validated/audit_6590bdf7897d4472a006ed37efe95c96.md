## Analysis: Agave Analog Found

The oDAO bug is fundamentally about **stale votes never being subtracted from a tally when the voter's allegiance/membership changes**, letting an attacker's vote linger in the numerator while the denominator shrinks (or, in the Agave analog, while the vote itself moves elsewhere) — inflating an apparent consensus signal that downstream logic treats as authoritative.

### Title
Pruned-fork stake tallies in `RepairWeight` never subtract stake when a validator switches its vote away, causing stale inflated "popular" duplicate-fork detection - (File: `core/src/repair/repair_weight.rs`)

### Summary
`RepairWeight` maintains two kinds of trees: `trees` (live, rooted trees) and `pruned_trees` (subtrees that were pruned from the main tree, kept around only to support ancestor-hashes repair for duplicate blocks). Both are represented with `HeaviestSubtreeForkChoice`, whose `stake_voted_subtree`/`stake_voted_at` accounting is designed so that when a validator's latest vote moves to a new slot, the code subtracts the old contribution and adds the new one (`generate_update_operations`, `UpdateOperation::Subtract`/`Add`). This "migration" logic works correctly *within* a single tree, but `repair_weight.rs` itself documents that it does **not** perform this migration **across** `trees` and `pruned_trees`.

### Finding Description
`RepairWeight::add_voters` routes each validator's new vote into whichever tree currently contains that vote's slot (`get_tree_root`), and only that specific `HeaviestSubtreeForkChoice` instance sees the `add_votes` call that would subtract the validator's *old* vote and add the *new* one. If a validator's previous vote landed in a slot that has since been moved into `pruned_trees` (e.g., because that fork was later pruned from the main tree as an unconfirmed duplicate/loser fork), and the validator's new vote lands in a different tree (e.g., the live `trees[root]`), the stake contribution left behind in the `pruned_trees` entry is **never removed**. The comment in the code makes this explicit: [1](#0-0) 

```
// TODO: `HeaviestSubtreeForkChoice` subtracts and migrates stake as validators switch forks
// within the rooted subtree, however `repair_weight` does not migrate stake
// across subtrees. This could lead to an additional false positive if validators
// switch post prune as stake added to a pruned tree it is never removed.
```

`get_popular_pruned_forks` then uses this un-migrated, monotonically non-decreasing `stake_voted_subtree` value to decide whether a pruned fork has crossed `DUPLICATE_THRESHOLD` and should be treated as a "popular pruned fork": [2](#0-1) 

This is structurally the same broken invariant as the oDAO report: a vote counted toward a group (an oDAO proposal / a pruned-tree slot) is never removed when the voter effectively "leaves" that group (kicked from the DAO / switches its latest vote to a different tree), so a threshold computed against that group's tally becomes stale and can stay "passed" long after the real, current voting population would not support it.

### Impact Explanation
`get_popular_pruned_forks` output feeds ancestor-hashes repair (`AncestorHashesService`, wired through `repair_service.rs`/`tvu.rs`), which decides which duplicate/pruned forks a node treats as worth investigating/repairing as if they were near duplicate-confirmed. Because the stake tally for a pruned fork can never decrease even after every validator that voted for it has switched away, a fork can be perpetually flagged as "popular" (spuriously appearing to approach `DUPLICATE_THRESHOLD`) based on votes that are no longer live anywhere in current consensus. This can cause a node to repeatedly trigger ancestor-hashes repair cycles or misjudge duplicate-fork state based on phantom stake, which is a form of false consensus-signal acceptance driven purely by unprivileged validator vote-switching behavior (no malicious node assumption required — any validator legitimately switching votes across a pruned/rebuilt fork boundary triggers this).

### Likelihood Explanation
This requires only ordinary, permitted validator behavior: a validator votes on a slot, that slot's tree is later pruned (a common, expected event whenever a fork is deemed a duplicate/loser), and the validator later votes again with its latest vote landing in a different tree. This is a routine occurrence during any duplicate-block/fork-switching scenario, not a crafted attack requiring special privileges — the code comment itself acknowledges the resulting stake is "never removed," i.e., it is an always-present, unbounded-lifetime accounting gap rather than a rare edge case.

### Recommendation
Track, per pruned tree, which validators' latest votes contributed to its `stake_voted_subtree`, and when `add_voters` observes a new vote for a validator whose previous vote is attributed to a `pruned_trees` entry, explicitly subtract that validator's stake from the pruned tree (mirroring the `UpdateOperation::Subtract` logic already used within a single `HeaviestSubtreeForkChoice`). Alternatively, treat pruned-tree stake as monotonically decaying by re-deriving it periodically from only the validators' truly-current latest votes rather than accumulating it indefinitely.

### Proof of Concept
1. Validator `V` (bonded/legit, no compromise needed) votes on slot `S1`. `RepairWeight::add_voters` records `V`'s stake in the tree containing `S1`.
2. `S1`'s subtree is later pruned into `pruned_trees` (e.g., it is discovered to be an unconfirmed duplicate/loser fork; a normal occurrence handled by `RepairWeight`'s prune-tracking machinery).
3. `V` later votes on slot `S2`, which is now in a different (live) tree. `add_voters` finds `S2`'s tree root and calls `add_votes` there — this correctly adds `V`'s stake to `S2`'s tree, but has no knowledge of, and never touches, the `pruned_trees` entry that still holds `V`'s stake for `S1`.
4. `V`'s stake permanently remains counted in `pruned_trees[S1_root].stake_voted_subtree`, as documented at [1](#0-0) .
5. `get_popular_pruned_forks` ( [3](#0-2) ) computes `duplicate_confirmed_threshold` from current `epoch_stakes.total_stake()`, but compares it against the stale, never-decremented `stake_voted_subtree`, which can make the pruned fork appear to have crossed `DUPLICATE_THRESHOLD` using stake that has, in reality, moved elsewhere — driving spurious ancestor-hashes repair behavior.

### Citations

**File:** core/src/repair/repair_weight.rs (L799-857)
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
