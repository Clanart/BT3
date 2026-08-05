### Title
`OptimisticallyConfirmedBankTracker` never invalidates a previously "confirmed" bank when it is later proven duplicate and dumped, allowing RPC clients to observe finality for state that gets rolled back - (File: `rpc/src/optimistically_confirmed_bank_tracker.rs`)

### Summary
The Merkl bug stems from a "live root" getter (`getMerkleRoot`) that treats a pending value as final once a time-based condition is met, without checking whether that value has meanwhile been marked as disputed. Agave has a structural analog: the `optimistically_confirmed_bank_tracker` caches the bank/hash for the "confirmed" commitment level purely from vote-derived `BankNotification::OptimisticallyConfirmed` events, but the separate duplicate-slot dispute-resolution pipeline (`cluster_slot_state_verifier` / `ReplayStage::dump_then_repair_correct_slots`) that can later prove a slot's frozen version is wrong has no channel to invalidate that cache.

### Finding Description
`OptimisticallyConfirmedBankTracker::process_notification` updates the shared `optimistically_confirmed_bank` cache whenever it receives `BankNotification::OptimisticallyConfirmed(slot, hash)` and the local bank's hash matches: [1](#0-0) 

This cache is what backs the RPC "confirmed" commitment level (`getSlot`, `getBalance`, `getTransaction`, etc., see the tests exercising `getSlot` against it): [2](#0-1) 

Independently, Agave's duplicate/dispute-resolution state machine in `cluster_slot_state_verifier.rs` can later determine that the locally-frozen version of a slot is *not* the one the cluster actually confirmed (a genuine equivocation/duplicate scenario), and instructs `ReplayStage` to dump and repair the slot: [3](#0-2) [4](#0-3) 

`dump_then_repair_correct_slots` removes the bad bank from `BankForks` and clears blockstore state for it: [5](#0-4) 

However, the entire `BankNotification` enum that feeds `OptimisticallyConfirmedBankTracker` only has four variants — `OptimisticallyConfirmed`, `Frozen`, `NewRootBank`, `NewRootedChain` — with no variant to signal "this previously confirmed slot/hash has been invalidated/dumped": [6](#0-5) 

The cached `optimistically_confirmed_bank.bank` is a cloned `Arc<Bank>`, so it survives independently of `BankForks`. Once `w_optimistically_confirmed_bank.bank = bank.clone()` is set for a slot, nothing in this module or its notification stream ever tells it that slot was later found to be on a duplicate/incorrect fork and purged, exactly mirroring how `Distributor.getMerkleRoot()` kept serving the pending tree without checking `disputer`.

### Impact Explanation
The "confirmed" commitment level is documented and widely relied upon by wallets, exchanges, and bridges as a strong (supermajority-of-stake) finality signal that is safe to act upon (e.g., releasing funds, crediting deposits) without waiting for `finalized`. If a slot that was optimistically confirmed is subsequently proven duplicate/incorrect via the dispute pipeline (`check_slot_agrees_with_cluster` → `dump_then_repair_correct_slots`), any RPC node whose `optimistically_confirmed_bank` cache still points at the dumped bank will continue to answer `confirmed`-level queries (balances, transaction status, slot) using that now-invalid state. This is a false-acceptance condition: downstream consumers can be misled into believing state is settled when the canonical chain has since diverged, which can translate directly into fund loss for any off-chain system that acts on a "confirmed" response (a direct parallel to the Merkl impact — acting on state before the dispute is actually resolved).

### Likelihood Explanation
Optimistic-confirmation reversals themselves require a real network-level partition or equivocation event to trigger the duplicate/dispute pipeline (`cluster_slot_state_verifier`) — this is not attacker-controlled by an ordinary transaction sender. What makes this a genuine unprivileged-impact gap (rather than "malicious validator only") is the *response* side: no privileged or malicious action is required to make an RPC node serve stale "confirmed" data once a dispute happens — the missing invalidation path is unconditional, structural, and affects every RPC node running the tracker, not just misbehaving nodes. The severity therefore hinges on the (rare, but not excluded by the impact list) occurrence of a duplicate-confirmed reversal, at which point every node exhibits the flaw automatically.

### Recommendation
Add a `BankNotification` variant (e.g., `BankNotification::Invalidated(Slot, Hash)`/`Dumped`) emitted from `ReplayStage::dump_then_repair_correct_slots` (and any other path that removes a bank from `BankForks` after it was previously reported optimistically confirmed), and have `OptimisticallyConfirmedBankTracker::process_notification` clear/roll back `optimistically_confirmed_bank` (and any matching entries in `pending_optimistically_confirmed_banks`) when such a notification for the currently cached slot/hash is received — analogous to Merkl's fix of checking `disputer == address(0)` before treating the pending value as final.

### Proof of Concept
1. A slot `S` is frozen locally with hash `H1` and receives enough vote stake to trigger `BankNotification::OptimisticallyConfirmed(S, H1)`.
2. `process_notification` sees `bank.hash() == H1` and sets `optimistically_confirmed_bank.bank` to the bank for `S` (`rpc/src/optimistically_confirmed_bank_tracker.rs:306-327`). RPC `getSlot`/`getBalance` at `confirmed` now report based on this bank.
3. Independently, the cluster later duplicate-confirms a different hash `H2` for slot `S` (real equivocation/duplicate event). `check_duplicate_confirmed_hash_against_bank_status` detects the mismatch (`core/src/repair/cluster_slot_state_verifier.rs:395-407`) and schedules `RepairDuplicateConfirmedVersion`/`MarkSlotDuplicate`.
4. `ReplayStage::dump_then_repair_correct_slots` dumps the bank for `S` with hash `H1` out of `BankForks` (`core/src/replay_stage.rs:2022-2091`), and repairs/replays the correct version with `H2`.
5. No `BankNotification` variant exists to inform `OptimisticallyConfirmedBankTracker` of this dump; `optimistically_confirmed_bank.bank` continues to hold the dumped `H1` bank until an unrelated future `Frozen`/`NewRootBank` notification for a *higher* slot happens to overwrite it (`rpc/src/optimistically_confirmed_bank_tracker.rs:370-420`). In the interim, `confirmed`-commitment RPC queries return finalized-looking answers for the invalidated fork.

### Citations

**File:** rpc/src/optimistically_confirmed_bank_tracker.rs (L45-52)
```rust
#[derive(Clone)]
pub enum BankNotification {
    OptimisticallyConfirmed(Slot, Hash),
    Frozen(Arc<Bank>),
    NewRootBank(Arc<Bank>),
    /// The newly rooted slot chain with bank ids and the parent slot of the oldest bank in the rooted chain.
    NewRootedChain(Vec<(Slot, BankId)>, Slot),
}
```

**File:** rpc/src/optimistically_confirmed_bank_tracker.rs (L306-327)
```rust
        match notification {
            BankNotification::OptimisticallyConfirmed(slot, hash) => {
                let bank = bank_forks.read().unwrap().get(slot);
                if let Some(bank) = bank {
                    if bank.is_frozen() {
                        if bank.hash() != hash {
                            if slot > bank_forks.read().unwrap().root() {
                                pending_optimistically_confirmed_banks.insert((slot, hash));
                                debug!(
                                    "defer notifying optimistic confirmation for slot {slot}: \
                                     local bank hash {} does not match optimistic confirmation \
                                     hash {hash}",
                                    bank.hash()
                                );
                            }
                        } else {
                            let mut w_optimistically_confirmed_bank =
                                optimistically_confirmed_bank.write().unwrap();

                            if bank.slot() > w_optimistically_confirmed_bank.bank.slot() {
                                w_optimistically_confirmed_bank.bank = bank.clone();
                            }
```

**File:** rpc/src/rpc.rs (L9153-9158)
```rust
        let req =
            r#"{"jsonrpc":"2.0","id":1,"method":"getSlot","params":[{"commitment": "confirmed"}]}"#;
        let res = io.handle_request_sync(req, meta.clone());
        let json: Value = serde_json::from_str(&res.unwrap()).unwrap();
        let slot: Slot = serde_json::from_value(json["result"].clone()).unwrap();
        assert_eq!(slot, 2);
```

**File:** core/src/repair/cluster_slot_state_verifier.rs (L368-408)
```rust
fn check_duplicate_confirmed_hash_against_bank_status(
    state_changes: &mut Vec<ResultingStateChange>,
    slot: Slot,
    duplicate_confirmed_hash: Hash,
    bank_status: BankStatus,
) {
    match bank_status {
        BankStatus::Unprocessed => {}
        BankStatus::Dead => {
            // If the cluster duplicate confirmed some version of this slot, then
            // there's another version of our dead slot
            warn!(
                "Cluster duplicate confirmed slot {slot} with hash {duplicate_confirmed_hash}, \
                 but we marked slot dead"
            );
            state_changes.push(ResultingStateChange::RepairDuplicateConfirmedVersion(
                duplicate_confirmed_hash,
            ));
        }
        BankStatus::Frozen(bank_frozen_hash) if duplicate_confirmed_hash == bank_frozen_hash => {
            // If the versions match, then add the slot to the candidate
            // set to account for the case where it was removed earlier
            // by the `on_duplicate_slot()` handler
            state_changes.push(ResultingStateChange::DuplicateConfirmedSlotMatchesCluster(
                bank_frozen_hash,
            ));
        }
        BankStatus::Frozen(bank_frozen_hash) => {
            // The duplicate confirmed slot hash does not match our frozen hash.
            // Modify fork choice rule to exclude our version from being voted
            // on and also repair the correct version
            warn!(
                "Cluster duplicate confirmed slot {slot} with hash {duplicate_confirmed_hash}, \
                 but our version has hash {bank_frozen_hash}"
            );
            state_changes.push(ResultingStateChange::MarkSlotDuplicate(bank_frozen_hash));
            state_changes.push(ResultingStateChange::RepairDuplicateConfirmedVersion(
                duplicate_confirmed_hash,
            ));
        }
    }
```

**File:** core/src/replay_stage.rs (L2022-2091)
```rust
    pub fn dump_then_repair_correct_slots(
        duplicate_slots_to_repair: &mut DuplicateSlotsToRepair,
        ancestors: &mut HashMap<Slot, HashSet<Slot>>,
        descendants: &mut HashMap<Slot, HashSet<Slot>>,
        progress: &mut ProgressMap,
        bank_forks: &RwLock<BankForks>,
        blockstore: &Blockstore,
        poh_bank_slot: Option<Slot>,
        purge_repair_slot_counter: &mut PurgeRepairSlotCounter,
        dumped_slots_sender: &DumpedSlotsSender,
        my_pubkey: &Pubkey,
        leader_schedule_cache: &LeaderScheduleCache,
    ) {
        if duplicate_slots_to_repair.is_empty() {
            return;
        }

        let root_bank = bank_forks.read().unwrap().root_bank();
        let mut dumped = vec![];
        // TODO: handle if alternate version of descendant also got confirmed after ancestor was
        // confirmed, what happens then? Should probably keep track of dumped list and skip things
        // in `duplicate_slots_to_repair` that have already been dumped. Add test.
        duplicate_slots_to_repair.retain(|duplicate_slot, correct_hash| {
            // Should not dump duplicate slots if there is currently a poh bank building
            // on top of that slot, as BankingStage might still be referencing/touching that state
            // concurrently.
            // Luckily for us, because the fork choice rule removes duplicate slots from fork
            // choice, and this function is called after:
            // 1) We have picked a bank to reset to in `select_vote_and_reset_forks()`
            // 2) And also called `reset_poh_recorder()`
            // Then we should have reset to a fork that doesn't include the duplicate block,
            // which means any working bank in PohRecorder that was built on that duplicate fork
            // should have been cleared as well. However, if there is some violation of this guarantee,
            // then log here
            let is_poh_building_on_duplicate_fork = poh_bank_slot
                .map(|poh_bank_slot| {
                    ancestors
                        .get(&poh_bank_slot)
                        .expect("Poh bank should exist in BankForks and thus in ancestors map")
                        .contains(duplicate_slot)
                })
                .unwrap_or(false);

            let did_dump_repair = {
                if !is_poh_building_on_duplicate_fork {
                    let frozen_hash = bank_forks.read().unwrap().bank_hash(*duplicate_slot);
                    if let Some(frozen_hash) = frozen_hash {
                        if frozen_hash == *correct_hash {
                            warn!(
                                "Trying to dump slot {} with correct_hash {}",
                                *duplicate_slot, *correct_hash
                            );
                            return false;
                        } else if frozen_hash == Hash::default()
                            && !progress.is_dead(*duplicate_slot).expect(
                                "If slot exists in BankForks must exist in the progress map",
                            )
                        {
                            warn!(
                                "Trying to dump unfrozen slot {} that is not dead",
                                *duplicate_slot
                            );
                            return false;
                        }
                    } else {
                        warn!(
                            "Dumping slot {} which does not exist in bank forks (possibly pruned)",
                            *duplicate_slot
                        );
                    }
```

**File:** core/src/replay_stage.rs (L2603-2643)
```rust
    fn recycle_async_verification(
        async_verification_freelist: &mut Vec<AsyncVerificationProgress>,
        async_verification: Option<AsyncVerificationProgress>,
    ) {
        if let Some(async_verification) = async_verification
            && async_verification_freelist.len() < ASYNC_VERIFICATION_FREELIST_CAPACITY
        {
            async_verification_freelist.push(async_verification);
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn process_popular_pruned_forks(
        popular_pruned_forks_receiver: &PopularPrunedForksReceiver,
        blockstore: &Blockstore,
        duplicate_slots_tracker: &mut DuplicateSlotsTracker,
        epoch_slots_frozen_slots: &mut EpochSlotsFrozenSlots,
        bank_forks: &RwLock<BankForks>,
        fork_choice: &mut HeaviestSubtreeForkChoice,
        duplicate_slots_to_repair: &mut DuplicateSlotsToRepair,
        ancestor_hashes_replay_update_sender: &AncestorHashesReplayUpdateSender,
        purge_repair_slot_counter: &mut PurgeRepairSlotCounter,
    ) {
        let root = bank_forks.read().unwrap().root();
        for new_popular_pruned_slots in popular_pruned_forks_receiver.try_iter() {
            for new_popular_pruned_slot in new_popular_pruned_slots {
                if new_popular_pruned_slot <= root {
                    continue;
                }
                check_slot_agrees_with_cluster(
                    new_popular_pruned_slot,
                    root,
                    blockstore,
                    duplicate_slots_tracker,
                    epoch_slots_frozen_slots,
                    fork_choice,
                    duplicate_slots_to_repair,
                    ancestor_hashes_replay_update_sender,
                    purge_repair_slot_counter,
                    SlotStateUpdate::PopularPrunedFork,
                );
```
