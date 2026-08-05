## Title
Duplicate/duplicate-confirmed proofs are silently discarded once a slot passes root, letting an invalid block become permanently rooted if proof delivery is delayed past the rooting deadline - (`core/src/repair/cluster_slot_state_verifier.rs`)

## Summary
The Sherlock report's root cause is structural, not implementation-specific: a safety check (`getLatestPrice`/depeg detection) that depends on external message delivery is gated by a deadline (epoch expiry), and once that deadline passes, the protocol falls back to an alternative, unconditional action (`triggerEndEpoch`) that assumes the safety check would have failed to trigger — permanently locking in an outcome that may be wrong, with no way to reconsider once the deadline window is missed. The Agave analog is `check_slot_agrees_with_cluster`, which is the single choke point for duplicate/duplicate-confirmed/epoch-slots-frozen signals arriving from gossip, replay, or repair. It unconditionally discards any of these signals for a slot once that slot is at or below the current root: `if slot <= root { return; }`. [1](#0-0) 

## Finding Description
Duplicate-slot handling in Agave depends on gossip/replay/repair delivering one of several signals (`Duplicate`, `DuplicateConfirmed`, `EpochSlotsFrozen`, `PopularPrunedFork`) to `check_slot_agrees_with_cluster`, which then decides whether to mark a fork invalid, purge it, or trigger repair via `apply_state_changes`. [2](#0-1) 

The very first check in that function is a hard deadline: once `slot <= root`, the function returns immediately and none of the downstream logic (`on_duplicate`, `on_duplicate_confirmed`, `on_epoch_slots_frozen`) ever runs for that slot, no matter what the incoming signal says. [1](#0-0) 

The same deadline is enforced independently at every call site that feeds this function — `process_duplicate_slots`, `process_duplicate_confirmed_slots`, `process_ancestor_hashes_duplicate_slots`, `process_popular_pruned_forks`, and `mark_slots_duplicate_confirmed` all fetch `root` from `bank_forks` and skip/continue for any `slot <= root` before ever reaching the verifier. [3](#0-2) [4](#0-3) 

This is a deliberate design choice, documented in `ReplayStage::initialize_progress_and_fork_choice_with_locked_bank_forks`: "we should ignore any duplicate proofs for the root slot" because the root is assumed to already be correct. [5](#0-4) 

The invariant this relies on is that rooting (via `TowerBFT` lockouts / `check_and_handle_new_root` / `set_bank_forks_root`) only happens after enough confirmations have accumulated that a conflicting duplicate proof "shouldn't" still be outstanding. [6](#0-5)  But this is a probabilistic assumption about network delivery timing, not an enforced ordering constraint between "duplicate-detection completes" and "root advances." If delivery of a `Duplicate`/`DuplicateConfirmed`/`EpochSlotsFrozen` signal for a still-outstanding fork is delayed past the moment the root passes that slot — exactly the "sequencer down past epoch expiry" pattern in the Sherlock report, where the safety check (depeg detection) doesn't complete before the deadline (epoch end) — the signal is discarded forever with no challenge/replay window, and the (possibly incorrect) locally-rooted version of the slot becomes permanent. Unlike Sherlock's `ControllerPeggedAssetV2`, there is no additional "challenge period" after the deadline in which a late-arriving duplicate proof could still be honored.

## Impact Explanation
If a duplicate-slot signal that would have caused `MarkSlotDuplicate`/`RepairDuplicateConfirmedSlot`/purge-and-repair state changes is delayed past the local root boundary, the validator keeps (and later confirms/supermajority-roots) a version of a block that the rest of the cluster does not agree with, without ever re-evaluating it. This is a false-rooting/false-acceptance outcome — the exact impact category called out as valid (false execution/rooting/acceptance) — because the verifier that exists specifically to reconcile local state with the cluster's canonical version is bypassed purely due to message-timing loss, with the "deadline vs. safety-check completion" race resolved unconditionally in favor of the deadline.

## Likelihood Explanation
Triggering this requires only delaying delivery of duplicate/duplicate-confirmed gossip/repair traffic to a node past the point its local root advances past the affected slot — an unprivileged, network-timing-dependent condition rather than a compromised validator. In practice the root only advances after many confirmations, which reduces the likelihood window, but the code contains no explicit synchronization or grace period guaranteeing duplicate-detection completes before rooting; it is purely a best-effort timing assumption enforced nowhere else in the pipeline.

## Recommendation
Introduce a bounded "challenge"/grace window analogous to Sherlock's recommendation: retain a limited history of already-rooted slots (or an out-of-band merkle/duplicate-proof index) for which late-arriving `Duplicate`/`DuplicateConfirmed`/`EpochSlotsFrozen` signals are still processed (e.g., logged, alerted, or fed into `duplicate_slots_to_repair` for possible reconciliation) rather than being unconditionally dropped once `slot <= root`, so that a race between duplicate-proof delivery and root advancement cannot silently and permanently entrench an incorrect version of a block.

## Proof of Concept
1. A validator observes a fork at slot `S` and (through lockouts/lockout supermajority) advances `root` past `S` via `check_and_handle_new_root`/`set_bank_forks_root`. [6](#0-5) 
2. Concurrently, gossip/repair carries a `DuplicateConfirmed` (or `Duplicate`) signal for slot `S` indicating the cluster actually settled on a different hash for `S`, but this signal's delivery is delayed (e.g., by network congestion/partition) until after step 1 completes.
3. When the delayed signal is finally processed via `process_duplicate_confirmed_slots` / `process_duplicate_slots`, the check `if confirmed_slot <= root { continue; }` (or the equivalent guard in `check_slot_agrees_with_cluster`, `if slot <= root { return; }`) causes it to be discarded with no further action. [7](#0-6) [1](#0-0) 
4. The validator's locally rooted (and possibly incorrect) version of slot `S` is never reconciled with the cluster's version — the analog of the epoch ending "incorrectly ended without a depeg" because the deadline (rooting) beat the safety-check delivery (duplicate confirmation).

### Citations

**File:** core/src/repair/cluster_slot_state_verifier.rs (L862-864)
```rust
    if slot <= root {
        return;
    }
```

**File:** core/src/repair/cluster_slot_state_verifier.rs (L935-944)
```rust
    let state_changes = slot_state_update.into_state_changes(slot);
    apply_state_changes(
        slot,
        fork_choice,
        duplicate_slots_to_repair,
        blockstore,
        ancestor_hashes_replay_update_sender,
        purge_repair_slot_counter,
        state_changes,
    );
```

**File:** core/src/replay_stage.rs (L1950-1956)
```rust
            let duplicate_slots = blockstore
                // It is important that the root bank is not marked as duplicate on initialization.
                // Although this bank could contain a duplicate proof, the fact that it was rooted
                // either during a previous run or artificially means that we should ignore any
                // duplicate proofs for the root slot, thus we start consuming duplicate proofs
                // from the root slot + 1
                .duplicate_slots_iterator(root_bank.slot().saturating_add(1))
```

**File:** core/src/replay_stage.rs (L2666-2670)
```rust
        let root = bank_forks.read().unwrap().root();
        for new_duplicate_confirmed_slots in duplicate_confirmed_slots_receiver.try_iter() {
            for (confirmed_slot, duplicate_confirmed_hash) in new_duplicate_confirmed_slots {
                if confirmed_slot <= root {
                    continue;
```

**File:** core/src/replay_stage.rs (L4924-4930)
```rust
        let root_slot = bank_forks.read().unwrap().root();
        for (slot, frozen_hash) in confirmed_slots.iter() {
            assert!(*frozen_hash != Hash::default());

            if *slot <= root_slot {
                continue;
            }
```

**File:** core/src/replay_stage.rs (L5010-5054)
```rust
    #[allow(clippy::too_many_arguments)]
    /// A wrapper around `root_utils::check_and_handle_new_root` which:
    /// - calls into `root_utils::set_bank_forks_root`
    /// - Executes `set_progress_and_tower_bft_root` to cleanup tower bft structs and the progress map
    fn check_and_handle_new_root(
        my_pubkey: &Pubkey,
        parent_slot: Slot,
        new_root: Slot,
        bank_forks: &RwLock<BankForks>,
        progress: &mut ProgressMap,
        blockstore: &Blockstore,
        leader_schedule_cache: &Arc<LeaderScheduleCache>,
        snapshot_controller: Option<&SnapshotController>,
        rpc_subscriptions: Option<&RpcSubscriptions>,
        highest_super_majority_root: Option<Slot>,
        bank_notification_sender: &Option<BankNotificationSenderConfig>,
        has_new_vote_been_rooted: &mut bool,
        tracked_vote_transactions: &mut Vec<TrackedVoteTransaction>,
        drop_bank_sender: &Sender<Vec<BankWithScheduler>>,
        tbft_structs: &mut TowerBFTStructures,
    ) {
        root_utils::check_and_handle_new_root(
            parent_slot,
            new_root,
            snapshot_controller,
            highest_super_majority_root,
            bank_notification_sender,
            drop_bank_sender,
            blockstore,
            leader_schedule_cache,
            bank_forks,
            rpc_subscriptions,
            my_pubkey,
            move |bank_forks| {
                Self::set_progress_and_tower_bft_root(
                    new_root,
                    bank_forks,
                    progress,
                    has_new_vote_been_rooted,
                    tracked_vote_transactions,
                    tbft_structs,
                )
            },
        )
    }
```
