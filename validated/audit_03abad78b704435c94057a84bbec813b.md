[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/cluster_info_vote_listener.rs (L996-1011)
```rust
    // Returns if the slot was optimistically confirmed, and whether
    // the slot was new
    fn track_optimistic_confirmation_vote(
        vote_tracker: &VoteTracker,
        slot: Slot,
        hash: Hash,
        pubkey: Pubkey,
        stake: u64,
        total_epoch_stake: u64,
    ) -> (Vec<bool>, bool) {
        let slot_tracker = vote_tracker.get_or_insert_slot_tracker(slot);
        // Insert vote and check for optimistic confirmation
        let mut w_slot_tracker = slot_tracker.write().unwrap();

        w_slot_tracker.add_optimistic_vote(hash, pubkey, stake, total_epoch_stake)
    }
```
