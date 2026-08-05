### Title
Non-decreasing vote timestamp check aborts entire Vote instruction on reordering, causing self-inflicted vote/credit loss - (File: `programs/vote/src/vote_state/handler.rs`)

### Summary
The legacy `Vote`/`VoteSwitch` instruction path in the vote program embeds a strictly non-decreasing timestamp check, modeled on the exact same pattern as the RedStone oracle bug: instead of merely ignoring a stale timestamp, `VoteStateHandler::process_timestamp` propagates an error that aborts the *entire* vote-processing instruction whenever the attached timestamp/slot ordering condition fails, discarding an otherwise valid vote (lockout push + credit increment) rather than simply skipping the timestamp field.

### Finding Description
`VoteStateHandler::process_timestamp` enforces that a newly submitted vote's `(slot, timestamp)` pair must not regress relative to the vote account's stored `last_timestamp`: [1](#0-0) 

This is invoked from `process_vote_with_account`, and crucially the `?` propagation means a timestamp regression does not just skip updating `last_timestamp` — it fails the whole vote instruction after `process_vote` has already been called on the in-memory `vote_state` handler, so the successful lockout/credit bookkeping for that vote is discarded when `set_vote_account_state` is never reached: [2](#0-1) 

This is structurally identical to the RedStone bug: a monotonic timestamp gate is used to reject an otherwise valid state transition instead of merely discarding the stale price/timestamp field. Because validators derive the `timestamp` field from local wall-clock time when constructing vote transactions, and because transaction landing order inside a block is determined by the leader (not the submitter), two vote transactions from the same validator for increasing slots can land with a locally non-monotonic timestamp ordering purely due to normal network/scheduling jitter — no malicious third party is required, matching the "naturally occurring reordering" pattern described in the source report.

### Impact Explanation
When triggered, the entire `Vote` instruction fails with `VoteError::TimestampTooOld`, so the validator loses the lockout/vote-credit update for that slot even though the vote itself (slot, hash) was valid. Repeated occurrence degrades a validator's voting record and Clock-timestamp contribution (the vote timestamp feeds `Bank::update_clock`), which is a runtime/consensus-adjacent effect, though it is confined to the legacy `Vote` instruction path rather than the current `TowerSync` path used by modern validators.

### Likelihood Explanation
Likelihood is low-to-moderate and mainly relevant to validators still using the legacy `Vote`/`VoteSwitch` instructions (as opposed to `TowerSync`, which does not appear to route through this same `process_timestamp` call based on available code). I could not fully verify within the available index whether `TowerSync`/`CompactUpdateVoteState` also calls `process_timestamp` or bypasses it — `vote_processor.rs` shows dispatch for these variants but its full body was not retrievable in this session, so this should be confirmed against the full file before treating it as an exhaustive analog.

### Recommendation
Change `process_timestamp` to silently ignore (rather than error on) a regressed timestamp while still allowing the slot/lockout portion of the vote to be recorded, i.e., decouple the timestamp update from the overall instruction success, analogous to the report's recommendation to drop the non-decreasing assertion and only keep the "no unbounded future timestamp" bound.

### Proof of Concept
1. Validator V is the authorized voter for vote account `A`, using the legacy `Vote` instruction (via `process_vote_with_account`).
2. V constructs vote tx1 for slot N with `timestamp = T` (local clock at submission time) and vote tx2 for slot N+1 with `timestamp = T-δ` (e.g., δ from local clock skew/NTP correction, or because tx2 was actually constructed slightly earlier but got sequenced later by the leader).
3. Leader includes tx2 before tx1 in the same or an earlier block.
4. tx2 executes: `last_timestamp` becomes `{slot: N+1, timestamp: T-δ}`.
5. tx1 executes: `process_timestamp(N, T)` sees `slot (N) < last_timestamp.slot (N+1)` → returns `VoteError::TimestampTooOld` [3](#0-2) 
   causing the entire tx1 instruction (including its otherwise-valid lockout/credit update for slot N) to fail via the `?` in `process_vote_with_account` [4](#0-3) .

Note: I was unable to confirm within this session whether the current `TowerSync` instruction path (the primary vote mechanism on modern Agave clusters) shares this exact `process_timestamp` call or has since been decoupled from timestamp validation; the codebase index did not return the full body of `programs/vote/src/vote_processor.rs`. Confirming that is necessary to determine whether this affects mainstream validators today or only legacy-instruction users.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L457-472)
```rust
    pub(crate) fn process_timestamp(
        &mut self,
        slot: Slot,
        timestamp: UnixTimestamp,
    ) -> Result<(), VoteError> {
        let last_timestamp = self.last_timestamp();
        if (slot < last_timestamp.slot || timestamp < last_timestamp.timestamp)
            || (slot == last_timestamp.slot
                && &BlockTimestamp { slot, timestamp } != last_timestamp
                && last_timestamp.slot != 0)
        {
            return Err(VoteError::TimestampTooOld);
        }
        self.set_last_timestamp(BlockTimestamp { slot, timestamp });
        Ok(())
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L1211-1233)
```rust
pub fn process_vote_with_account<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    slot_hashes: &[SlotHash],
    clock: &Clock,
    vote: &Vote,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    let authorized_voter = vote_state.get_and_update_authorized_voter(clock.epoch)?;
    verify_authorized_signer(&authorized_voter, signers)?;

    process_vote(&mut vote_state, vote, slot_hashes, clock.epoch, clock.slot)?;
    if let Some(timestamp) = vote.timestamp {
        vote.slots
            .iter()
            .max()
            .ok_or(VoteError::EmptySlots)
            .and_then(|slot| vote_state.process_timestamp(*slot, timestamp))?;
    }
    vote_state.set_vote_account_state(vote_account)
}
```
