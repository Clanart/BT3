## Analog Found [1](#0-0) 

### Title
Exact-stake-equal-to-threshold vote never marked as "reached threshold" in `VoteStakeTracker::add_vote_pubkey` - ([File: core/src/consensus/vote_stake_tracker.rs])

### Summary
The external report describes `updateRank` failing to handle the case where an input value is *exactly equal* to one of the defined level boundaries. The closest Agave analog is `VoteStakeTracker::add_vote_pubkey`, which computes whether a newly-added vote crosses a stake percentage threshold. The comparison used to detect "threshold reached" excludes the case where the new cumulative stake lands *exactly on* the threshold value, so a vote that brings the tracked stake to precisely the threshold is never reported as having reached it.

### Finding Description
`add_vote_pubkey` computes, for each configured threshold percentage, an integer `threshold_stake = (total_stake as f64 * threshold) as u64`, then decides whether this particular vote newly crossed the threshold with:

```rust
let threshold_stake = (total_stake as f64 * threshold) as u64;
old_stake <= threshold_stake && threshold_stake < new_stake
``` [2](#0-1) 

If a vote brings `new_stake` to a value that is exactly equal to `threshold_stake` (i.e. `new_stake == threshold_stake`), the second clause `threshold_stake < new_stake` evaluates to `false`, so the threshold is *not* reported as reached on that vote — even though the tracked stake has, in fact, reached (met) the required percentage. The threshold is only reported on a subsequent vote that pushes the stake strictly above `threshold_stake`. If no further vote for that specific `(slot, hash)` combination is ever received (e.g., because the remaining validators who could push the count past threshold have already voted on a different hash, or simply never vote again on that hash), the "reached threshold" condition is permanently missed for that hash, despite the invariant "cumulative voted stake >= threshold" holding true.

This is directly analogous to the reported `updateRank` bug: a boundary value that exactly equals a defined level is not classified into that level due to a strict comparison that should have been inclusive (`<=` rather than `<`).

### Impact Explanation
`VoteStakeTracker` backs `SlotVoteTracker::add_optimistic_vote`, which is used by `ClusterInfoVoteListener::track_optimistic_confirmation_vote` to detect two thresholds: `DUPLICATE_THRESHOLD` and `VOTE_THRESHOLD_SIZE` (optimistic confirmation). [3](#0-2) [4](#0-3) 

When `reached_threshold_results[0]` (duplicate-confirmed) is not fired, `duplicate_confirmed_slot_sender` never sends the `(slot, hash)` pair, so downstream duplicate-confirmation handling in `ReplayStage` is never told that stake has actually reached `DUPLICATE_THRESHOLD` for that fork/hash. Likewise a missed `VOTE_THRESHOLD_SIZE` crossing means the validator's RPC subscribers and `BankNotification::OptimisticallyConfirmed` notifications never fire for that slot/hash even though the network has, in fact, accumulated sufficient stake. This can delay or suppress legitimate duplicate-confirmation and optimistic-confirmation signaling, which feeds into consensus-adjacent decisions (e.g., whether a duplicate slot is treated as confirmed and safe to build on) — placing it in scope as a "false acceptance/non-acceptance" style correctness defect rather than a purely cosmetic one.

### Likelihood Explanation
Triggering this exact condition requires the sum of voting stakes for one specific `(slot, hash)` to land precisely on the integer-truncated `threshold_stake` value, and for no further vote on that same hash to arrive afterward. Because validator stake amounts are public and integral, and `threshold_stake` is a simple truncated multiplication, this is a deterministic, non-malicious-input coincidence that can occur naturally with realistic stake distributions — it does not require an adversarial validator, malicious peer assumption, or leaked keys. However, it does require the additional condition that no further vote pushes the sum above the threshold, making practical exploitation/occurrence lower-probability than a guaranteed trigger, though still reachable through normal permissionless voting traffic.

### Recommendation
Change the threshold-crossing check to be inclusive of the boundary value, e.g.:
```rust
old_stake <= threshold_stake && threshold_stake <= new_stake.saturating_sub(1)
```
or more simply invert the strictness so a vote landing exactly at `threshold_stake` is treated as reaching it:
```rust
old_stake < threshold_stake_plus_one && new_stake >= threshold_stake
```
Concretely: replace `threshold_stake < new_stake` with `threshold_stake <= new_stake` combined with `old_stake < threshold_stake` (strict) so a state transition that lands exactly on `threshold_stake` is counted exactly once.

### Proof of Concept
1. Set `total_stake = 100`, threshold `= 0.10` ⇒ `threshold_stake = 10`.
2. Have a single voter with `stake = 10` cast the first vote for `(slot, hash)`: `old_stake = 0`, `new_stake = 10`.
3. Evaluate: `0 <= 10 && 10 < 10` → `false`. Threshold is not reported as reached, despite `new_stake == threshold_stake` exactly satisfying "10% of stake has voted."
4. If no other validator ever votes for this exact `hash` again (e.g., the rest of the stake votes for a different, competing hash for the same slot), the duplicate/optimistic-confirmation notification for this hash is permanently never emitted, even though the stake requirement was numerically met.

This mirrors the existing unit test `test_add_vote_pubkey`, which only exercises boundary crossings where `new_stake` strictly exceeds `threshold_stake` (`i == 6` for 70% of 10 total stake, i.e., `new_stake=7 > threshold_stake=6.7→6`) and does not cover the case `new_stake == threshold_stake` exactly. [5](#0-4) 

**Note of uncertainty**: I was unable to fully trace every downstream consumer of `duplicate_confirmed_slot_sender` in `ReplayStage` within the indexed portion of the codebase to precisely quantify the consensus-level consequence of a missed duplicate-confirmation notification (e.g., whether it can be independently re-derived from another path). The core boundary-comparison defect itself, however, is confirmed directly in `core/src/consensus/vote_stake_tracker.rs`.

### Citations

**File:** core/src/consensus/vote_stake_tracker.rs (L14-38)
```rust
    pub fn add_vote_pubkey(
        &mut self,
        vote_pubkey: Pubkey,
        stake: u64,
        total_stake: u64,
        thresholds_to_check: &[f64],
    ) -> (Vec<bool>, bool) {
        let is_new = !self.voted.contains(&vote_pubkey);
        if is_new {
            self.voted.insert(vote_pubkey);
            let old_stake = self.stake;
            let new_stake = self.stake + stake;
            self.stake = new_stake;
            let reached_threshold_results: Vec<bool> = thresholds_to_check
                .iter()
                .map(|threshold| {
                    let threshold_stake = (total_stake as f64 * threshold) as u64;
                    old_stake <= threshold_stake && threshold_stake < new_stake
                })
                .collect();
            (reached_threshold_results, is_new)
        } else {
            (vec![false; thresholds_to_check.len()], is_new)
        }
    }
```

**File:** core/src/consensus/vote_stake_tracker.rs (L53-98)
```rust
    #[test]
    fn test_add_vote_pubkey() {
        let total_epoch_stake = 10;
        let mut vote_stake_tracker = VoteStakeTracker::default();
        for i in 0..10 {
            let pubkey = solana_pubkey::new_rand();
            let (is_confirmed_thresholds, is_new) = vote_stake_tracker.add_vote_pubkey(
                pubkey,
                1,
                total_epoch_stake,
                &[VOTE_THRESHOLD_SIZE, 0.0],
            );
            let stake = vote_stake_tracker.stake();
            let (is_confirmed_thresholds2, is_new2) = vote_stake_tracker.add_vote_pubkey(
                pubkey,
                1,
                total_epoch_stake,
                &[VOTE_THRESHOLD_SIZE, 0.0],
            );
            let stake2 = vote_stake_tracker.stake();

            // Stake should not change from adding same pubkey twice
            assert_eq!(stake, stake2);
            assert!(!is_confirmed_thresholds2[0]);
            assert!(!is_confirmed_thresholds2[1]);
            assert!(!is_new2);
            assert_eq!(is_confirmed_thresholds.len(), 2);
            assert_eq!(is_confirmed_thresholds2.len(), 2);

            // at i == 6, the voted stake is 70%, which is the first time crossing
            // the supermajority threshold
            if i == 6 {
                assert!(is_confirmed_thresholds[0]);
            } else {
                assert!(!is_confirmed_thresholds[0]);
            }

            // at i == 6, the voted stake is 10%, which is the first time crossing
            // the 0% threshold
            if i == 0 {
                assert!(is_confirmed_thresholds[1]);
            } else {
                assert!(!is_confirmed_thresholds[1]);
            }
            assert!(is_new);
        }
```

**File:** core/src/cluster_info_vote_listener.rs (L98-121)
```rust
    fn add_optimistic_vote(
        &mut self,
        hash: Hash,
        pubkey: Pubkey,
        stake: u64,
        total_epoch_stake: u64,
    ) -> (Vec<bool>, bool) {
        let num_vote_hashes = self.num_optimistic_vote_hashes.entry(pubkey).or_default();
        if *num_vote_hashes >= MAX_VOTE_HASHES_PER_PUBKEY_PER_SLOT {
            return (vec![false; THRESHOLDS_TO_CHECK.len()], false);
        }

        let result @ (_, is_new) = self
            .optimistic_votes_tracker
            .entry(hash)
            .or_default()
            .add_vote_pubkey(pubkey, stake, total_epoch_stake, &THRESHOLDS_TO_CHECK);

        if is_new {
            *num_vote_hashes += 1;
        }

        result
    }
```

**File:** core/src/cluster_info_vote_listener.rs (L757-802)
```rust
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
```
