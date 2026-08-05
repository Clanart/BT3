Based on the code, this report does not hold up.

**Insertion into `duplicate_confirmed_slots` is always gated on real, validated stake — not an unvalidated "signal".**

Every write path into `DuplicateConfirmedSlots` requires an actual stake-weighted threshold to have been reached from real vote transactions before insertion:

- `cluster_info_vote_listener.rs`'s `process_last_vote_for_optimistic_confirmation` computes `stake`/`total_stake` from `root_bank.epoch_stakes(epoch)` (the real, rooted stake distribution) and only sends a `duplicate_confirmed_slot_sender` notification when `reached_duplicate_confirmed` becomes true via `track_optimistic_confirmation_vote`, which tallies real signed vote transactions against actual stake. [1](#0-0) 
- `replay_stage.rs`'s `process_duplicate_confirmed_slots` and `mark_slots_duplicate_confirmed` are the only places that call `duplicate_confirmed_slots.insert(...)`, and both assert that if a slot already has an entry, any subsequent insert must carry the *same* hash, panicking otherwise (`"Additional duplicate confirmed notification for slot {} with a different hash"`). [2](#0-1) [3](#0-2) 

There is no code path where an unvalidated/forged hash can be insert

### Citations

**File:** core/src/cluster_info_vote_listener.rs (L746-781)
```rust
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
```

**File:** core/src/replay_stage.rs (L2671-2683)
```rust
                } else if let Some(prev_hash) =
                    duplicate_confirmed_slots.insert(confirmed_slot, duplicate_confirmed_hash)
                {
                    // This assertion is intentional - it is not possible to split the cluster to get 52% on two versions
                    // without a massive turbine failure
                    assert_eq!(
                        prev_hash, duplicate_confirmed_hash,
                        "Additional duplicate confirmed notification for slot {confirmed_slot} \
                         with a different hash"
                    );
                    // Already processed this signal
                    continue;
                }
```

**File:** core/src/replay_stage.rs (L4933-4941)
```rust
            if let Some(prev_hash) = duplicate_confirmed_slots.insert(*slot, *frozen_hash) {
                assert_eq!(
                    prev_hash, *frozen_hash,
                    "Additional duplicate confirmed notification for slot {slot} with a different \
                     hash"
                );
                // Already processed this signal
                continue;
            }
```
