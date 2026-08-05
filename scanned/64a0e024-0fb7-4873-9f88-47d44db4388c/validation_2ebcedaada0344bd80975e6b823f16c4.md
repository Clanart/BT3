### Title
Unconditional `OptimisticConfirmation` slot notification emitted regardless of local hash agreement or replay state - ([File: rpc/src/optimistically_confirmed_bank_tracker.rs])

### Summary
The `BankNotification::OptimisticallyConfirmed` handler in `OptimisticallyConfirmedBankTracker::process_notification` always fires a `SlotUpdate::OptimisticConfirmation` notification to `slotsUpdatesSubscribe` subscribers, even along code paths where the local validator explicitly determined it *cannot* vouch for that slot/hash pair (unknown bank, hash mismatch, or already-rooted-and-dropped). This mirrors the 1inch `OrderCancelled` bug: a status event is emitted unconditionally from a single entry point that internally has multiple different confidence levels, so downstream consumers cannot distinguish a "real" locally-verified confirmation from a "best-effort, unverified" one.

### Finding Description
`process_notification` handles `BankNotification::OptimisticallyConfirmed(slot, hash)` with several distinct outcomes:
- If no local bank exists for `slot`, or the local bank's hash mismatches `hash`, the code defers/queues the info as `pending_optimistically_confirmed_banks` (i.e., it does not treat the confirmation as verified against local state). [1](#0-0) 
- If `slot` is already <= root, the notification is simply dropped/counted (`dropped-already-rooted-optimistic-bank-notification`). [2](#0-1) 
- Regardless of which of these branches was taken, the function unconditionally calls `subscriptions.notify_slot_update(SlotUpdate::OptimisticConfirmation { slot, timestamp: timestamp() })` right after the `if/else` chain, with a comment explicitly noting this fires "regardless of whether the bank is replayed": [3](#0-2) 

The `SlotUpdate::OptimisticConfirmation` variant carries only `slot` and `timestamp` — no hash — so a `slotsUpdatesSubscribe` client cannot verify after the fact whether the notified slot corresponds to the block hash that actually reached the optimistic-confirmation stake threshold on this validator's local fork, or to a divergent/unknown block. This is architecturally identical to the `1inch` `cancelOrder`→`OrderCancelled` case: a single emission path used for multiple underlying invalidation/confirmation mechanisms with different guarantees, collapsed into one generic, context-free event.

### Impact Explanation
Downstream consumers of `slotsUpdatesSubscribe` (indexers, exchanges, bridges) commonly treat `OptimisticConfirmation` as a strong finality/acceptance signal for a slot. Because the event is emitted even when the local validator's bank hash does not match the vote hash that triggered the notification (deferred branch) or when the slot is unknown locally, a consumer relying on this notification stream alone can be misled into treating a slot as accepted/confirmed on this node's fork when the node has not actually verified that. This falls into the "false execution/rooting/acceptance" impact category, since the event doesn't reliably reflect the node's own confirmed state.

### Likelihood Explanation
The trigger path is entirely normal cluster operation — no malicious peer/validator assumption is required. `BankNotification::OptimisticallyConfirmed` messages are generated whenever `process_last_vote_for_optimistic_confirmation` observes stake crossing the optimistic-confirmation threshold from votes seen via gossip/replay, which happens routinely, including benign fork scenarios where the locally replayed bank for that slot doesn't yet exist or has a different hash than the one that reached threshold (e.g., during forks/reorgs). Any RPC client subscribed to `slotsUpdatesSubscribe` will observe this behavior with a single low-cost subscription, without needing elevated privileges.

### Recommendation
Only emit `SlotUpdate::OptimisticConfirmation` from the branch that actually confirms local-bank/hash agreement, and either suppress the notification or add an explicit variant/flag for the "deferred / unverified-locally" and "already rooted" cases so subscribers can distinguish a locally-corroborated confirmation from a best-effort/inconclusive one — analogous to how the 1inch fix split `OrderCancelled` (verified, hash-keyed) from `BitInvalidatorUpdated` (batch/best-effort).

### Proof of Concept
1. Subscribe over RPC pubsub to `slotsUpdatesSubscribe`.
2. Observe cluster gossip votes causing `process_last_vote_for_optimistic_confirmation` to reach the optimistic-confirmation stake threshold for `(last_vote_slot, last_vote_hash)`, sent via `notifiers.bank_notification_sender` as `BankNotification::OptimisticallyConfirmed(last_vote_slot, last_vote_hash)`. [4](#0-3) 
3. In `OptimisticallyConfirmedBankTracker::process_notification`, if the local bank for that slot is missing, or `bank.hash() != hash` (e.g. this validator is momentarily on a different/unreplayed fork), the code takes the deferral branch (no local corroboration) — yet still falls through to the unconditional `notify_slot_update(SlotUpdate::OptimisticConfirmation {...})` call. [5](#0-4) 
4. The subscriber receives an `OptimisticConfirmation` update for `slot` with no way to tell that the local node never verified that hash — a "confirmed" signal for state the node cannot itself vouch for.

Note: I could not fully inspect the `SlotUpdate` enum definition and its exact serialized fields in `rpc-client-types/src/response.rs`, or `rpc/src/rpc_health.rs`'s use of `OptimisticConfirmation`, due to tool/search limitations in this final pass; a Devin session with full file access would be needed to confirm the exact wire-format guarantees given to subscribers and any additional consumers of this event (e.g., health checks) that might already compensate for this ambiguity.

### Citations

**File:** rpc/src/optimistically_confirmed_bank_tracker.rs (L307-369)
```rust
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

                            if slot > *highest_confirmed_slot {
                                Self::notify_or_defer_confirmed_banks(
                                    subscriptions,
                                    bank_forks,
                                    bank,
                                    *highest_confirmed_slot,
                                    None,
                                    last_notified_confirmed_slot,
                                    pending_optimistically_confirmed_banks,
                                    slot_notification_subscribers,
                                    prioritization_fee_cache,
                                );

                                *highest_confirmed_slot = slot;
                            }
                            drop(w_optimistically_confirmed_bank);
                        }
                    } else if slot > bank_forks.read().unwrap().root() {
                        pending_optimistically_confirmed_banks.insert((slot, hash));
                        debug!("defer notifying optimistic confirmation for slot {slot}");
                    } else {
                        inc_new_counter_info!(
                            "dropped-already-rooted-optimistic-bank-notification",
                            1
                        );
                    }
                } else if slot > bank_forks.read().unwrap().root() {
                    pending_optimistically_confirmed_banks.insert((slot, hash));
                } else {
                    inc_new_counter_info!("dropped-already-rooted-optimistic-bank-notification", 1);
                }

                // Send slot notification regardless of whether the bank is replayed
                subscriptions.notify_slot_update(SlotUpdate::OptimisticConfirmation {
                    slot,
                    timestamp: timestamp(),
                });
                // NOTE: replay of `slot` may or may not be complete. Therefore, most new
                // functionality to be triggered on optimistic confirmation should go in
                // `notify_or_defer()` under the `bank.is_frozen()` case instead of here.
            }
```

**File:** core/src/cluster_info_vote_listener.rs (L783-801)
```rust
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
```
