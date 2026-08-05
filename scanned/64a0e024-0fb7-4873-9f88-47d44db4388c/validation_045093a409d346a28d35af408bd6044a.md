Confirmed: line 209 shows `tree.add_votes(...)` is called per-`tree_root`, scoped to whichever single `HeaviestSubtreeForkChoice` instance (`self.trees` or `self.pruned_trees`) that root belongs to. Each tree instance has its own independent `latest_votes` map, so stake subtraction only happens *within* that instance when a validator's newest vote supersedes an old one *in the same tree*. Once a subtree is pruned off into `self.pruned_trees` (a separate `HeaviestSubtreeForkChoice`), any pubkey stake it recorded stays there permanently — nothing in `repair_weight.rs` ever calls "subtract" on a pruned tree when that same pubkey later votes for a completely different tree (the main rooted tree, or another pruned tree). The only cleanup path is `set_root`, which purges pruned trees whose *root* is smaller than the new root — it never migrates/decrements stake for validators who simply switched votes away from a slot still inside a pruned tree.

### Title
Stale, unrecoverable stake accumulates in `RepairWeight` pruned trees, causing `get_popular_pruned_forks` to falsely trigger ancestor-hashes repair - (File: `core/src/repair/repair_weight.rs`)

### Summary
`RepairWeight::add_voters` routes every validator vote to the single `HeaviestSubtreeForkChoice` tree instance that owns the target slot's `tree_root`, then calls `tree.add_votes(...)` only on that instance [1](#0-0) . Stake subtraction for a validator's superseded vote only happens inside `HeaviestSubtreeForkChoice::generate_update_operations`, which looks up the validator's *previous* vote in its own `latest_votes` map and emits a `Subtract` operation for that prior slot [2](#0-1) . Because pruned subtrees are tracked as separate `HeaviestSubtreeForkChoice` instances in `self.pruned_trees`, a validator's earlier vote recorded inside a pruned tree is invisible to the instance handling the validator's newer vote elsewhere, so no `Subtract` is ever issued against the pruned tree. The pruned tree's `stake_voted_subtree` value is therefore never decremented once a validator abandons that fork — the only cleanup is `set_root`, which merely drops whole pruned trees whose root falls below the new root [3](#0-2) ; it does not subtract stake from pruned trees that remain above the new root. This is explicitly acknowledged in a code comment as a known false-positive source [4](#0-3) .

### Finding Description
`get_popular_pruned_forks` uses each pruned tree's `stake_voted_subtree` to decide whether the pruned fork has crossed `DUPLICATE_THRESHOLD` of total stake and should be treated as "popular" (i.e., likely duplicate-confirmed), triggering ancestor-hashes repair against it [5](#0-4) . This is exactly analogous to the `twAML` bug: a stake contribution ("weight") that is supposed to be reversed when the contributor moves on is instead permanently stuck because the reversal mechanism (`Subtract` update in `generate_update_operations`) is scoped to a single tree object and cannot see across trees — the pruned-tree "leftover stake" is the Agave equivalent of the un-burnable `OTAP` position: once counted, it never gets an `exitPosition`-style decrement.

Any validator whose vote lands on a slot inside an already-pruned subtree (which happens naturally whenever nodes vote/gossip for forks that get pruned by `set_root`/`split_off`) contributes stake that can never be removed from that pruned tree's `stake_voted_subtree`, even after the same validator later votes for the canonical rooted fork. Repeated across many validators over the pruned tree's lifetime (which persists until the root advances past the pruned tree's own root, not merely past the leaves), the pruned tree's recorded `stake_voted_subtree` can exceed the currently-active total stake supporting that fork, without any of those validators still actually supporting it.

### Impact Explanation
This does not corrupt consensus roots or accounts state, but it can cause `get_popular_pruned_forks` to incorrectly conclude a dead/pruned fork is "popular" (≥ `DUPLICATE_THRESHOLD`), which drives ancestor-hashes repair machinery in `core/src/repair/` to spend repair bandwidth/cycles chasing a fork that is not actually duplicate-confirmed. Because the accumulation only grows (never shrinks) as validators naturally switch votes in and out of forks that get pruned, this is a persistent false-positive/DoS-adjacent condition on the repair subsystem rather than a fund-loss or halt bug — it degrades repair efficiency and can be amplified by attacker-controlled vote traffic through gossip (an unprivileged, remote input path), fitting the "non-RPC remote exhaustion/degradation" category.

### Likelihood Explanation
This requires no malicious node collusion or privileged access — it is a natural consequence of ordinary fork-switching behavior combined with `repair_weight`'s architecture of one independent `HeaviestSubtreeForkChoice` per pruned subtree. The comment in the code itself states this "could lead to an additional false positive if validators switch post prune," confirming the team is aware the condition is reachable under normal, unprivileged network activity (any validator's votes observed via gossip/replay), not just via a hypothetical attacker.

### Recommendation
Track validators' latest votes across all `RepairWeight` trees/pruned_trees (e.g., a `RepairWeight`-level `latest_votes: HashMap<Pubkey, SlotHashKey>` similar to `HeaviestSubtreeForkChoice::latest_votes`), so that when a validator's vote lands in a different tree than their previous latest vote, the previous tree (including pruned trees) receives an explicit `Subtract` before the new tree receives the `Add`. This mirrors the fix the report recommends for `twAML`: ensure the decrement path cannot be bypassed by an unprivileged action (vote switching) that only updates the "add" side of the ledger.

### Proof of Concept
1. Start `RepairWeight` with root 0 and two sibling forks A (slot 2) and B (slot 3), each descending from slot 1.
2. Validator `V` votes for slot 2 (fork A). `add_voters` records this in `self.trees[0]`'s `HeaviestSubtreeForkChoice`, contributing `V`'s stake to `stake_voted_subtree` at slot 2 [6](#0-5) .
3. Call `set_root(3)` (fork B becomes canonical); fork A (containing slot 2) is pruned and stored as its own `HeaviestSubtreeForkChoice` in `self.pruned_trees` [3](#0-2) . `V`'s stake remains recorded in the pruned tree's `stake_voted_subtree`.
4. Validator `V` now votes for a slot in the new canonical rooted tree (fork B). `add_voters` groups this vote under the rooted tree's `tree_root` and only calls `add_votes` on `self.trees[3]` [1](#0-0) ; the pruned tree instance for fork A is never touched, so its `stake_voted_subtree` at slot 2 still includes `V`'s stake.
5. Repeating steps 2–4 for many validators over time causes the pruned tree's recorded stake to accumulate stake from validators who have long since moved on, potentially pushing `get_popular_pruned_forks`'s `stake_voted_subtree(&slot_to_start_repair) >= duplicate_confirmed_threshold` check to true for a fork nobody currently supports [7](#0-6) , spuriously triggering ancestor-hashes repair.

### Citations

**File:** core/src/repair/repair_weight.rs (L101-216)
```rust
    pub fn add_voters(
        &mut self,
        blockstore: &Blockstore,
        voters: impl Iterator<Item = (Slot, Vec<Pubkey>)>,
        epoch_stakes: &HashMap<Epoch, VersionedEpochStakes>,
        epoch_schedule: &EpochSchedule,
    ) {
        let mut all_subtree_updates: HashMap<TreeRoot, HashMap<Pubkey, Slot>> = HashMap::new();
        for (slot, pubkey_voters) in voters {
            if slot < self.root {
                continue;
            }
            let mut tree_root = self.get_tree_root(slot);
            let mut new_ancestors = VecDeque::new();
            // If we don't know know  how this slot chains to any existing trees
            // in `self.trees` or `self.pruned_trees`, then use `blockstore` to see if this chains
            // any existing trees in `self.trees`
            if tree_root.is_none() {
                let (discovered_ancestors, existing_subtree_root) =
                    self.find_ancestor_subtree_of_slot(blockstore, slot);
                new_ancestors = discovered_ancestors;
                tree_root = existing_subtree_root;
            }

            let (tree_root, tree) = {
                match (tree_root, *new_ancestors.front().unwrap_or(&slot)) {
                    (Some(tree_root), _) if !tree_root.is_pruned() => (
                        tree_root,
                        self.trees
                            .get_mut(&tree_root.into())
                            .expect("If tree root was found, it must exist in `self.trees`"),
                    ),
                    (Some(tree_root), _) => (
                        tree_root,
                        self.pruned_trees.get_mut(&tree_root.into()).expect(
                            "If a pruned tree root was found, it must exist in `self.pruned_trees`",
                        ),
                    ),
                    (None, earliest_ancestor) => {
                        // There is no known subtree that contains `slot`. Thus, create a new
                        // subtree rooted at the earliest known ancestor of `slot`.
                        // If this earliest known ancestor is not part of the rooted path, create a new
                        // pruned tree from the ancestor that is `> self.root` instead.
                        if earliest_ancestor < self.root {
                            if !self.pruned_tree_tracking_enabled {
                                continue;
                            }
                            // If the next ancestor exists, it is guaranteed to be `> self.root` because
                            // `find_ancestor_subtree_of_slot` can return at max one ancestor `<
                            // self.root`.
                            let next_earliest_ancestor = *new_ancestors.get(1).unwrap_or(&slot);
                            assert!(next_earliest_ancestor > self.root);
                            // We also guarantee that next_earliest_ancestor does not
                            // already exist as a pruned tree (pre condition for inserting a new
                            // pruned tree) otherwise `tree_root` would not be None.
                            self.insert_new_pruned_tree(next_earliest_ancestor);
                            // Remove `earliest_ancestor` as it should not be added to the tree (we
                            // maintain the invariant that the tree only contains slots >=
                            // `self.root` by checking before `add_new_leaf_slot` and `set_root`)
                            assert_eq!(Some(earliest_ancestor), new_ancestors.pop_front());
                            (
                                TreeRoot::PrunedRoot(next_earliest_ancestor),
                                self.pruned_trees.get_mut(&next_earliest_ancestor).unwrap(),
                            )
                        } else {
                            // We guarantee that `earliest_ancestor` does not already exist in
                            // `self.trees` otherwise `tree_root` would not be None
                            self.insert_new_tree(earliest_ancestor);
                            (
                                TreeRoot::Root(earliest_ancestor),
                                self.trees.get_mut(&earliest_ancestor).unwrap(),
                            )
                        }
                    }
                }
            };

            // First element in `ancestors` must be either:
            // 1) Leaf of some existing subtree
            // 2) Root of new subtree that was just created above through `self.insert_new_tree` or
            //    `self.insert_new_pruned_tree`
            new_ancestors.push_back(slot);
            if new_ancestors.len() > 1 {
                for i in 0..new_ancestors.len() - 1 {
                    // TODO: Repair right now does not distinguish between votes for different
                    // versions of the same slot.
                    tree.add_new_leaf_slot(
                        (new_ancestors[i + 1], Hash::default()),
                        Some((new_ancestors[i], Hash::default())),
                    );
                    self.slot_to_tree.insert(new_ancestors[i + 1], tree_root);
                }
            }

            // Now we know which subtree this slot chains to,
            // add the votes to the list of updates
            let subtree_updates = all_subtree_updates.entry(tree_root).or_default();
            for pubkey in pubkey_voters {
                let cur_max = subtree_updates.entry(pubkey).or_default();
                *cur_max = std::cmp::max(*cur_max, slot);
            }
        }

        for (tree_root, updates) in all_subtree_updates {
            let tree = self
                .get_tree_mut(tree_root)
                .expect("Tree for `tree_root` must exist here");
            let updates: Vec<_> = updates.into_iter().collect();
            tree.add_votes(
                updates
                    .iter()
                    .map(|(pubkey, slot)| (*pubkey, (*slot, Hash::default()))),
                epoch_stakes,
                epoch_schedule,
            );
        }
```

**File:** core/src/repair/repair_weight.rs (L488-526)
```rust
        if self.pruned_tree_tracking_enabled {
            // Clean up the pruned set by trimming slots that are less than `new_root` and removing
            // empty trees
            self.pruned_trees = self
                .pruned_trees
                .drain()
                .flat_map(|(tree_root, mut pruned_tree)| {
                    if tree_root < new_root {
                        trace!("pruning tree {tree_root} with {new_root}");
                        let (removed, pruned) =
                            pruned_tree.purge_prune((new_root, Hash::default()));
                        for (slot, _) in removed {
                            self.slot_to_tree.remove(&slot);
                        }
                        pruned
                            .into_iter()
                            .chain(iter::once(pruned_tree)) // Add back the original pruned tree
                            .filter(|pruned_tree| !pruned_tree.is_empty()) // Clean up empty trees
                            .map(|new_pruned_subtree| {
                                let new_pruned_tree_root = new_pruned_subtree.tree_root().0;
                                // Resync `self.slot_to_tree`
                                for ((slot, _), _) in
                                    new_pruned_subtree.all_slots_stake_voted_subtree()
                                {
                                    *self.slot_to_tree.get_mut(slot).unwrap() =
                                        TreeRoot::PrunedRoot(new_pruned_tree_root);
                                }
                                (new_pruned_tree_root, new_pruned_subtree)
                            })
                            .collect()
                    } else {
                        vec![(tree_root, pruned_tree)]
                    }
                })
                .collect::<HashMap<u64, HeaviestSubtreeForkChoice>>();
        } else {
            self.clear_pruned_tree_state();
        }
        self.root = new_root;
```

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

**File:** core/src/consensus/heaviest_subtree_fork_choice.rs (L1010-1068)
```rust
            let mut pubkey_latest_vote = self.latest_votes.get_mut(pubkey);

            // Filter out any votes or slots < any slot this pubkey has
            // already voted for, we only care about the latest votes.
            //
            // If the new vote is for the same slot, but a different, smaller hash,
            // then allow processing to continue as this is a duplicate version
            // of the same slot.
            match pubkey_latest_vote.as_mut() {
                Some((pubkey_latest_vote_slot, pubkey_latest_vote_hash))
                    if (new_vote_slot < *pubkey_latest_vote_slot)
                        || (new_vote_slot == *pubkey_latest_vote_slot
                            && &new_vote_hash >= pubkey_latest_vote_hash) =>
                {
                    continue;
                }

                _ => {
                    // We either:
                    // 1) don't have a vote yet for this pubkey,
                    // 2) or the new vote slot is bigger than the old vote slot
                    // 3) or the new vote slot == old_vote slot, but for a smaller bank hash.
                    // In all above cases, we need to remove this pubkey stake from the previous fork
                    // of the previous vote

                    if let Some((old_latest_vote_slot, old_latest_vote_hash)) =
                        self.latest_votes.insert(*pubkey, *new_vote_slot_hash)
                    {
                        assert!(if new_vote_slot == old_latest_vote_slot {
                            warn!(
                                "Got a duplicate vote for validator: {pubkey}, slot_hash: \
                                 {new_vote_slot_hash:?}",
                            );
                            // If the slots are equal, then the new
                            // vote must be for a smaller hash
                            new_vote_hash < old_latest_vote_hash
                        } else {
                            new_vote_slot > old_latest_vote_slot
                        });

                        let epoch = epoch_schedule.get_epoch(old_latest_vote_slot);
                        let stake_update = epoch_stakes
                            .get(&epoch)
                            .map(|epoch_stakes| epoch_stakes.vote_account_stake(pubkey))
                            .unwrap_or(0);

                        if stake_update > 0 {
                            update_operations
                                .entry((
                                    (old_latest_vote_slot, old_latest_vote_hash),
                                    UpdateLabel::Subtract,
                                ))
                                .and_modify(|update| update.update_stake(stake_update))
                                .or_insert(UpdateOperation::Subtract(stake_update));
                            self.insert_aggregate_operations(
                                &mut update_operations,
                                (old_latest_vote_slot, old_latest_vote_hash),
                            );
                        }
```
