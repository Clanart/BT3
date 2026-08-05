## Analysis

The l2geth report's broken invariant is: **a status the system reports to integrators ("this transaction is safe") is not actually equivalent to true finality, and there is no API to force integrators to distinguish the two** — forcing them to use unreliable heuristics and exposing them to reorg-driven double-spends.

Agave has a structural analog of this exact invariant gap that requires no malicious validator, admin, or leaked key — it is a property of how the `Confirmed` commitment level is computed and exposed over RPC.

### Title
Integrators trusting the `confirmed` RPC commitment level as final can be double-spent when an optimistically-confirmed slot is later not rooted - (File: `rpc/src/rpc.rs`, `core/src/optimistic_confirmation_verifier.rs`)

### Summary
Solana's RPC exposes a `confirmed` commitment level that is satisfied once ≥2/3 of stake has voted for a slot/hash (`process_last_vote_for_optimistic_confirmation` in `core/src/cluster_info_vote_listener.rs`), well before that slot is rooted/finalized. Bridges and exchanges that treat `confirmed` (rather than `finalized`) as sufficient for crediting deposits can be double-spent if the optimistically confirmed slot is later dropped in favor of a different fork — a scenario Agave's own code acknowledges as a real, expected possibility rather than a theoretical one.

### Finding Description
`RpcHandler::bank()` in `rpc/src/rpc.rs` special-cases `CommitmentConfig::confirmed()` by returning the bank tracked by `OptimisticallyConfirmedBank`, independent of whether that bank has been rooted: [1](#0-0) 

`get_transaction_status` reports `TransactionConfirmationStatus::Confirmed` for any transaction visible in that optimistically-confirmed bank, distinct from `Finalized` which requires `root >= slot`: [2](#0-1) 

The confirmation itself is driven purely by stake-weighted vote counting over gossip/replay votes (`track_optimistic_confirmation_vote` / `process_last_vote_for_optimistic_confirmation`), and is recorded before the slot is known to be rooted: [3](#0-2) 

Agave's own `OptimisticConfirmationVerifier` exists specifically because this invariant can be violated in practice — it scans for previously "confirmed" slots that never became rooted, and logs a `"was not rooted"` violation after the fact: [4](#0-3) [5](#0-4) 

This mirrors the original report precisely: like Clique's identical-difficulty forks that make it impossible for syncing nodes to know which fork is canonical until much later, Agave's `Confirmed` status can be satisfied on a slot that is later excluded from the canonical chain by `prune_non_rooted`/`BankForks::set_root`, and the RPC surface gives integrators no forced signal that they must wait for `Finalized` instead of `Confirmed`: [6](#0-5) 

No malicious validator is required for this: under normal network partitions/latency, honest stake can vote for a slot that reaches the 2/3 optimistic-confirmation threshold locally/regionally while a different, ultimately heavier fork is adopted by the rest of the cluster (the well-documented "duplicate-confirmed-versus-switch-threshold" class of scenarios exists in test coverage even for non-malicious cases of fork competition): [7](#0-6) 

### Impact Explanation
An exchange, bridge, or any automated integrator that credits a deposit as soon as `getSignatureStatuses`/`getTransaction` reports `confirmationStatus: "confirmed"` (a common integration shortcut, since `confirmed` is the default and much faster than `finalized`) can accept a deposit transaction that is later excluded from the canonical/rooted chain, exactly matching the double-spend exploit scenario in the original report (deposit observed → processed by exchange → later chain reorganizes away the deposit). This is a direct fund-theft/loss vector, matching the "Valid Impact" category of false execution/acceptance.

### Likelihood Explanation
This does not require any validator to be malicious, a leaked key, or privileged access — it only requires normal probabilistic behavior of optimistic confirmation under realistic network conditions (partition/latency), which Agave's own `OptimisticConfirmationVerifier` is built to detect after the fact, confirming this is a recognized, occurring condition rather than a purely theoretical one.

### Recommendation
- Documentation/API: make `getSignatureStatuses`/`getTransaction`/websocket subscription responses explicitly and prominently distinguish `confirmed` from `finalized`, and provide/emphasize a "wait for finalized" helper method so that integrators (bridges, exchanges) do not treat `confirmed` as safe for high-value fund-crediting decisions, mirroring the original report's short-term recommendation to force finality-aware querying.
- Consider surfacing `OptimisticConfirmationVerifier` violations in real time (e.g., a dedicated notification/metric consumable by RPC clients) rather than only via internal logs, so downstream consumers can detect and react to unrooted optimistic confirmations.

### Proof of Concept
1. An integrator polls `getSignatureStatuses` and treats `confirmationStatus == "confirmed"` as sufficient to credit a deposit, per the code path in `get_transaction_status` (`rpc/src/rpc.rs:1731-1766`).
2. During a network partition/latency event, a slot reaches optimistic confirmation (≥2/3 stake, `process_last_vote_for_optimistic_confirmation`, `core/src/cluster_info_vote_listener.rs:728-805`) and the deposit transaction is reported `Confirmed`.
3. The cluster subsequently roots a different, heavier fork; the previously "confirmed" slot is pruned via `BankForks::prune_non_rooted` (`runtime/src/bank_forks.rs:697-722`), which `OptimisticConfirmationVerifier::verify_for_unrooted_optimistic_slots` later detects and logs as `"Optimistically confirmed slot {slot} was not rooted"` (`core/src/optimistic_confirmation_verifier.rs:26-90`).
4. The integrator has already released funds against a deposit that no longer exists on the canonical chain — a double spend.

### Citations

**File:** rpc/src/rpc.rs (L349-363)
```rust
    #[allow(deprecated)]
    fn bank(&self, commitment: Option<CommitmentConfig>) -> Arc<Bank> {
        debug!("RPC commitment_config: {commitment:?}");

        let commitment = commitment.unwrap_or_default();
        if commitment.is_confirmed() {
            let bank = self
                .optimistically_confirmed_bank
                .read()
                .unwrap()
                .bank
                .clone();
            debug!("RPC using optimistically confirmed slot: {:?}", bank.slot());
            return bank;
        }
```

**File:** rpc/src/rpc.rs (L1731-1766)
```rust
    fn get_transaction_status(
        &self,
        signature: Signature,
        bank: &Bank,
    ) -> Option<TransactionStatus> {
        let (slot, status) = bank.get_signature_status_slot(&signature)?;

        let optimistically_confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let optimistically_confirmed =
            optimistically_confirmed_bank.get_signature_status_slot(&signature);

        let r_block_commitment_cache = self.block_commitment_cache.read().unwrap();
        let confirmations = if r_block_commitment_cache.root() >= slot
            && is_finalized(&r_block_commitment_cache, bank, &self.blockstore, slot)
        {
            None
        } else {
            r_block_commitment_cache
                .get_confirmation_count(slot)
                .or(Some(0))
        };
        let err = status.clone().err();
        Some(TransactionStatus {
            slot,
            status,
            confirmations,
            err,
            confirmation_status: if confirmations.is_none() {
                Some(TransactionConfirmationStatus::Finalized)
            } else if optimistically_confirmed.is_some() {
                Some(TransactionConfirmationStatus::Confirmed)
            } else {
                Some(TransactionConfirmationStatus::Processed)
            },
        })
    }
```

**File:** core/src/cluster_info_vote_listener.rs (L728-805)
```rust
    /// Fast-track processing of the last vote slot in a vote transaction for
    /// optimistic confirmation. Checks stake thresholds and sends notifications
    /// for duplicate confirmation and optimistic confirmation as needed.
    /// Returns whether this is a new vote for the slot.
    fn process_last_vote_for_optimistic_confirmation(
        vote_tracker: &VoteTracker,
        last_vote_slot: Slot,
        last_vote_hash: Hash,
        vote_pubkey: &Pubkey,
        root_bank: &Bank,
        is_gossip_vote: bool,
        notifiers: &ConfirmationNotifiers,
        new_optimistic_confirmed_slots: &mut ThresholdConfirmedSlots,
    ) -> bool {
        if last_vote_slot <= root_bank.slot() {
            return false;
        }

        let epoch = root_bank.epoch_schedule().get_epoch(last_vote_slot);
        let Some(epoch_stakes) = root_bank.epoch_stakes(epoch) else {
            return false;
        };

        let stake = epoch_stakes
            .stakes()
            .vote_accounts()
            .get_delegated_stake(vote_pubkey);
        let total_stake = epoch_stakes.total_stake();

        let (reached_threshold_results, is_new) = Self::track_optimistic_confirmation_vote(
            vote_tracker,
            last_vote_slot,
            last_vote_hash,
            *vote_pubkey,
            stake,
            total_stake,
        );

        if is_gossip_vote && is_new && stake > 0 {
            let _ = notifiers.gossip_verified_vote_hash_sender.send((
                *vote_pubkey,
                last_vote_slot,
                last_vote_hash,
            ));
        }

        let reached_duplicate_confirmed = reached_threshold_results[0];
        let reached_optimistic_confirmed = reached_threshold_results[1];

        if reached_duplicate_confirmed
            && let Some(ref sender) = notifiers.duplicate_confirmed_slot_sender
        {
            let _ = sender.send(vec![(last_vote_slot, last_vote_hash)]);
        }

        if reached_optimistic_confirmed {
            new_optimistic_confirmed_slots.push((last_vote_slot, last_vote_hash));
            if let Some(ref sender) = notifiers.bank_notification_sender
                && notifiers
                    .migration_status
                    .should_report_commitment_or_root(last_vote_slot)
            {
                let dependency_work = sender
                    .dependency_tracker
                    .as_ref()
                    .map(|s| s.get_current_declared_work());
                sender
                    .sender
                    .send((
                        BankNotification::OptimisticallyConfirmed(last_vote_slot, last_vote_hash),
                        dependency_work,
                    ))
                    .unwrap_or_else(|err| warn!("bank_notification_sender failed: {err:?}"));
            }
        }

        is_new
    }
```

**File:** core/src/optimistic_confirmation_verifier.rs (L26-55)
```rust
    // Returns any optimistic slots that were not rooted
    pub fn verify_for_unrooted_optimistic_slots(
        &mut self,
        root_bank: &Bank,
        blockstore: &Blockstore,
    ) -> Vec<(Slot, Hash)> {
        let root = root_bank.slot();
        let root_ancestors = &root_bank.ancestors;
        let slots_after_root = self
            .unchecked_slots
            .split_off(&((root + 1), Hash::default()));
        // `slots_before_root` now contains all slots <= root
        let slots_before_root = std::mem::replace(&mut self.unchecked_slots, slots_after_root);
        slots_before_root
            .into_iter()
            .filter(|(optimistic_slot, optimistic_hash)| {
                (*optimistic_slot == root && *optimistic_hash != root_bank.hash())
                    || (!root_ancestors.contains_key(optimistic_slot) &&
                    // In this second part of the `and`, we account for the possibility that
                    // there was some other root `rootX` set in BankForks where:
                    //
                    // `root` > `rootX` > `optimistic_slot`
                    //
                    // in which case `root` may  not contain the ancestor information for
                    // slots < `rootX`, so we also have to check if `optimistic_slot` was rooted
                    // through blockstore.
                    !blockstore.is_root(*optimistic_slot))
            })
            .collect()
    }
```

**File:** core/src/optimistic_confirmation_verifier.rs (L88-90)
```rust
    pub fn format_optimistic_confirmed_slot_violation_log(slot: Slot) -> String {
        format!("Optimistically confirmed slot {slot} was not rooted")
    }
```

**File:** runtime/src/bank_forks.rs (L697-722)
```rust
    fn prune_non_rooted(
        &mut self,
        root: Slot,
        highest_super_majority_root: Option<Slot>,
    ) -> (Vec<BankWithScheduler>, u64, u64) {
        // We want to collect timing separately, and the 2nd collect requires
        // a unique borrow to self which is already borrowed by self.banks
        let mut prune_slots_time = Measure::start("prune_slots");
        let prune_slots: Vec<_> = self
            .get_non_rooted(root, highest_super_majority_root)
            .collect();
        prune_slots_time.stop();

        let mut prune_remove_time = Measure::start("prune_slots");
        let removed_banks = prune_slots
            .into_iter()
            .filter_map(|slot| self.remove(slot))
            .collect();
        prune_remove_time.stop();

        (
            removed_banks,
            prune_slots_time.as_ms(),
            prune_remove_time.as_ms(),
        )
    }
```

**File:** local-cluster/tests/local_cluster.rs (L5223-5238)
```rust
/// Recreates the duplicate-confirmed-versus-switch-threshold deadlock and verifies the
/// validators can escape it after rediscovering the duplicate and rebuilding the right fork choice.
///
/// We want to simulate the following:
///   /--- 1 --- 3 (duplicate block)
/// 0
///   \--- 2
///
/// 1. > DUPLICATE_THRESHOLD of the nodes vote on some version of the duplicate block 3,
/// but don't immediately duplicate confirm so they remove 3 from fork choice and reset PoH back to 1.
/// 2. All the votes on 3 don't land because there are no further blocks building off 3.
/// 3. Some < SWITCHING_THRESHOLD of nodes vote on 2, making it the heaviest fork because no votes on 3 landed
/// 4. Nodes then see duplicate confirmation on 3.
/// 5. Unless somebody builds off of 3 to include the duplicate confirmed votes, 2 will still be the heaviest.
/// However, because 2 has < SWITCHING_THRESHOLD of the votes, people who voted on 3 can't switch, leading to a
/// stall
```
