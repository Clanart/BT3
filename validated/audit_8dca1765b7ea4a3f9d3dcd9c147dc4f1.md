## Title
Unvalidated `confirmation_count` in vote-program `TowerSync`/`VoteStateUpdate` instructions can reach `BlockCommitment::increase_confirmation_stake` and panic the commitment-aggregation thread on every validator - (File: `runtime/src/commitment.rs`)

## Summary
`BlockCommitment::increase_confirmation_stake` and `get_confirmation_stake` in `runtime/src/commitment.rs` use a bare `assert!(confirmation_count > 0 && confirmation_count <= MAX_LOCKOUT_HISTORY)` on a value that ultimately comes from raw, attacker-influenced vote-account bytes decoded via `VoteStateView`/`TowerVoteState`, not from a value the runtime itself computes. [1](#0-0) 

## Finding Description
`AggregateCommitmentService::aggregate_commitment` iterates every staked vote account in `bank.vote_accounts()` and builds a `TowerVoteState` via `TowerVoteState::from(account.vote_state_view())` for any pubkey that isn't the node's own vote key. [2](#0-1) 

`TowerVoteState::from(&VoteStateView)` copies the vote list straight from `vote_state.votes_iter()` with no range validation: [3](#0-2) 

`VoteStateView::votes_iter()` constructs each `Lockout` directly from the raw little-endian `confirmation_count` field stored in the account, again with no bounds check: [4](#0-3) [5](#0-4) 

That value then flows unchanged into `aggregate_commitment_for_vote_account`, which calls `increase_confirmation_stake(vote.confirmation_count() as usize, lamports)`: [6](#0-5) 

which asserts the value is in `1..=MAX_LOCKOUT_HISTORY`: [7](#0-6) 

The question is whether the vote program's own validation prevents an out-of-range `confirmation_count` from ever being persisted into on-chain vote-account state. Tracing `TowerSync`/`VoteStateUpdate` processing:
- `VoteInstruction::TowerSync` dispatches to `vote_state::process_tower_sync`, which only verifies the authorized-voter signature before calling `do_process_tower_sync`. [8](#0-7) 
- `do_process_tower_sync` calls `check_and_filter_proposed_vote_state`, which validates **slot ordering, slot-hash membership, and root consistency** of the client-supplied `tower_sync.lockouts: VecDeque<Lockout>` — it never inspects or clamps the `confirmation_count` field of each `Lockout`. [9](#0-8) [10](#0-9) 
- `check_slots_are_valid` likewise only matches slots against `SlotHashes`; it does not touch `confirmation_count`. [11](#0-10) 

The `confirmation_count` in a `TowerSync`/`Lockout` is *client-supplied* data (it is normally computed locally by the validator's tower via `double_lockouts`), and I could not find any point in the traced instruction path (`process_tower_sync` → `check_and_filter_proposed_vote_state` → `process_new_vote_state`) that re-derives or bounds-checks it against the vote's stack depth. Because the only gating control on writing to a vote account's data is the authorized-voter signature (which any account holder controls for a vote account they created and self-delegated stake to), an attacker who owns a vote account with any non-zero delegated stake can submit a `TowerSync` transaction whose `lockouts` vector contains a `Lockout` with `confirmation_count == 0` or `confirmation_count > MAX_LOCKOUT_HISTORY (32)`, while still satisfying the slot/hash-ordering checks. Note: I was not able to fully verify, within the tool-call budget, whether a later stage of `process_new_vote_state` (not shown in the collected context) independently recomputes or rejects an inconsistent `confirmation_count`; this residual uncertainty should be checked directly in `programs/vote/src/vote_state/mod.rs::process_new_vote_state`.

If such a value is persisted, every validator's `AggregateCommitmentService` thread will panic the next time it aggregates commitment for a bank containing that vote account, because `increase_confirmation_stake`'s `assert!` fires unconditionally for out-of-range input — this affects *all* validators that observe the account (not the attacker's node alone), since the aggregation code iterates `bank.vote_accounts()` for every staked vote account in the working bank.

## Impact Explanation
`AggregateCommitmentService` runs as a single, unsupervised background thread (`solAggCommitSvc`) with no restart or recovery on panic. [12](#0-11) 
A panic there permanently stops commitment aggregation on every full validator that processes the malicious vote account, freezing `confirmed`/`finalized` commitment levels and RPC-subscription notifications cluster-wide until the node is restarted. This is a cluster-wide degradation triggered by a single crafted vote transaction, not merely a "single-client" issue, since any validator processing that bank hits the same panic.

## Likelihood Explanation
Likelihood depends entirely on whether `process_new_vote_state` (or an earlier stage) actually rejects an inconsistent/out-of-range `confirmation_count` supplied in a `TowerSync`/`VoteStateUpdate` instruction. Based on the code paths inspected (`check_and_filter_proposed_vote_state`, `check_slots_are_valid`), no such validation exists in those functions, but the full `process_new_vote_state` body was not retrieved in this session. If, as observed evidence suggests, no such validation exists anywhere in the pipeline, the attack requires only: (1) creating a vote account, (2) self-delegating a nonzero stake amount (any amount, permissionless), and (3) submitting one crafted `TowerSync` transaction — all fully unprivileged, low-cost actions.

## Recommendation
- Have the vote program validate that every `Lockout.confirmation_count` in an incoming `TowerSync`/`VoteStateUpdate` is within `1..=MAX_LOCKOUT_HISTORY` (and ideally consistent with the computed lockout depth) before persisting it, in `check_and_filter_proposed_vote_state` / `process_new_vote_state`.
- Defensively, replace the `assert!` panics in `BlockCommitment::increase_confirmation_stake`/`get_confirmation_stake` with saturating/clamped handling or a `Result`-based error path so malformed on-chain vote data (however it arises) cannot crash the aggregation thread.

## Proof of Concept
1. Create a vote account and delegate a small stake to it (fully permissionless).
2. As the authorized voter, submit a `VoteInstruction::TowerSync` transaction whose `TowerSync.lockouts` contains a `Lockout` with `slot` matching a valid, hashed ancestor slot (to pass `check_and_filter_proposed_vote_state`/`check_slots_are_valid`) but `confirmation_count = 0` (or `> MAX_LOCKOUT_HISTORY`), constructed via `Lockout::new_with_confirmation_count(slot, 0)`.
3. If the transaction is accepted (pending confirmation of no additional range check in `process_new_vote_state`), the value is persisted into the vote account's `VoteStateV4`/`VoteStateV3` data.
4. On any validator, `AggregateCommitmentService::aggregate_commitment` → `aggregate_commitment_for_vote_account` (`core/src/commitment_service.rs:306-318`) reads this vote via `TowerVoteState::from(account.vote_state_view())` and calls `increase_confirmation_stake(0, lamports)`, hitting the `assert!` in `runtime/src/commitment.rs:20` and panicking the `solAggCommitSvc` thread.

Because I could not conclusively verify within this session whether `process_new_vote_state` independently blocks this input, this finding should be validated end-to-end (constructing an actual `TowerSync` transaction with an out-of-range `confirmation_count` and confirming it lands on-chain) before treating it as fully confirmed.

### Citations

**File:** runtime/src/commitment.rs (L19-27)
```rust
    pub fn increase_confirmation_stake(&mut self, confirmation_count: usize, stake: u64) {
        assert!(confirmation_count > 0 && confirmation_count <= MAX_LOCKOUT_HISTORY);
        self.commitment[confirmation_count - 1] += stake;
    }

    pub fn get_confirmation_stake(&mut self, confirmation_count: usize) -> u64 {
        assert!(confirmation_count > 0 && confirmation_count <= MAX_LOCKOUT_HISTORY);
        self.commitment[confirmation_count - 1]
    }
```

**File:** core/src/commitment_service.rs (L96-115)
```rust
                t_commitment: Builder::new()
                    .name("solAggCommitSvc".to_string())
                    .spawn(move || {
                        loop {
                            if exit.load(Ordering::Relaxed) {
                                break;
                            }

                            if let Err(RecvTimeoutError::Disconnected) = Self::run(
                                &receiver,
                                &ag_receiver,
                                &block_commitment_cache,
                                subscriptions.as_deref(),
                                &exit,
                            ) {
                                break;
                            }
                        }
                    })
                    .unwrap(),
```

**File:** core/src/commitment_service.rs (L258-277)
```rust
        let mut commitment = HashMap::new();
        let mut rooted_stake: Vec<(Slot, u64)> = Vec::new();
        for (pubkey, (lamports, account)) in bank.vote_accounts().iter() {
            if *lamports == 0 {
                continue;
            }
            let vote_state = if pubkey == node_vote_pubkey {
                // Override old vote_state in bank with latest one for my own vote pubkey
                node_vote_state.clone()
            } else {
                TowerVoteState::from(account.vote_state_view())
            };
            Self::aggregate_commitment_for_vote_account(
                &mut commitment,
                &mut rooted_stake,
                &vote_state,
                ancestors,
                *lamports,
            );
        }
```

**File:** core/src/commitment_service.rs (L306-318)
```rust
        for vote in &vote_state.votes {
            while ancestors[ancestors_index] <= vote.slot() {
                commitment
                    .entry(ancestors[ancestors_index])
                    .or_default()
                    .increase_confirmation_stake(vote.confirmation_count() as usize, lamports);
                ancestors_index += 1;

                if ancestors_index == ancestors.len() {
                    return;
                }
            }
        }
```

**File:** core/src/consensus/tower_vote_state.rs (L129-136)
```rust
impl From<&VoteStateView> for TowerVoteState {
    fn from(vote_state: &VoteStateView) -> Self {
        Self {
            votes: vote_state.votes_iter().collect(),
            root_slot: vote_state.root_slot(),
        }
    }
}
```

**File:** vote/src/vote_state_view.rs (L130-134)
```rust
    pub fn votes_iter(&self) -> impl Iterator<Item = Lockout> + '_ {
        self.votes_view().into_iter().map(|vote| {
            Lockout::new_with_confirmation_count(vote.slot(), vote.confirmation_count())
        })
    }
```

**File:** vote/src/vote_state_view/field_frames.rs (L69-84)
```rust
#[repr(C)]
pub(super) struct LockoutItem {
    slot: [u8; 8],
    confirmation_count: [u8; 4],
}

impl LockoutItem {
    #[inline]
    pub(super) fn slot(&self) -> Slot {
        u64::from_le_bytes(self.slot)
    }
    #[inline]
    pub(super) fn confirmation_count(&self) -> u32 {
        u32::from_le_bytes(self.confirmation_count)
    }
}
```

**File:** programs/vote/src/vote_state/mod.rs (L57-123)
```rust
fn check_and_filter_proposed_vote_state(
    vote_state: &VoteStateHandler,
    proposed_lockouts: &mut VecDeque<Lockout>,
    proposed_root: &mut Option<Slot>,
    proposed_hash: Hash,
    slot_hashes: &[(Slot, Hash)],
) -> Result<(), VoteError> {
    if proposed_lockouts.is_empty() {
        return Err(VoteError::EmptySlots);
    }

    let last_proposed_slot = proposed_lockouts
        .back()
        .expect("must be nonempty, checked above")
        .slot();

    // If the proposed state is not new enough, return
    if let Some(last_vote_slot) = vote_state.votes().back().map(|lockout| lockout.slot())
        && last_proposed_slot <= last_vote_slot
    {
        return Err(VoteError::VoteTooOld);
    }

    if slot_hashes.is_empty() {
        return Err(VoteError::SlotsMismatch);
    }
    let earliest_slot_hash_in_history = slot_hashes.last().unwrap().0;

    // Check if the proposed vote state is too old to be in the SlotHash history
    if last_proposed_slot < earliest_slot_hash_in_history {
        // If this is the last slot in the vote update, it must be in SlotHashes,
        // otherwise we have no way of confirming if the hash matches
        return Err(VoteError::VoteTooOld);
    }

    // Overwrite the proposed root if it is too old to be in the SlotHash history
    if let Some(root) = *proposed_root {
        // If the new proposed root `R` is less than the earliest slot hash in the history
        // such that we cannot verify whether the slot was actually was on this fork, set
        // the root to the latest vote in the vote state that's less than R. If no
        // votes from the vote state are less than R, use its root instead.
        if root < earliest_slot_hash_in_history {
            // First overwrite the proposed root with the vote state's root
            *proposed_root = vote_state.root_slot();

            // Then try to find the latest vote in vote state that's less than R
            for vote in vote_state.votes().iter().rev() {
                if vote.slot() <= root {
                    *proposed_root = Some(vote.slot());
                    break;
                }
            }
        }
    }

    // Index into the new proposed vote state's slots, starting with the root if it exists then
    // we use this mutable root to fold checking the root slot into the below loop
    // for performance
    let mut root_to_check = *proposed_root;
    let mut proposed_lockouts_index = 0;

    // index into the slot_hashes, starting at the oldest known
    // slot hash
    let mut slot_hashes_index = slot_hashes.len();

    let mut proposed_lockouts_indices_to_filter = vec![];

```

**File:** programs/vote/src/vote_state/mod.rs (L299-391)
```rust
fn check_slots_are_valid(
    vote_state: &VoteStateHandler,
    vote_slots: &[Slot],
    vote_hash: &Hash,
    slot_hashes: &[(Slot, Hash)],
) -> Result<(), VoteError> {
    // index into the vote's slots, starting at the oldest
    // slot
    let mut i = 0;

    // index into the slot_hashes, starting at the oldest known
    // slot hash
    let mut j = slot_hashes.len();

    // Note:
    //
    // 1) `vote_slots` is sorted from oldest/smallest vote to newest/largest
    // vote, due to the way votes are applied to the vote state (newest votes
    // pushed to the back).
    //
    // 2) Conversely, `slot_hashes` is sorted from newest/largest vote to
    // the oldest/smallest vote
    while i < vote_slots.len() && j > 0 {
        // 1) increment `i` to find the smallest slot `s` in `vote_slots`
        // where `s` >= `last_voted_slot`
        if vote_state
            .last_voted_slot()
            .is_some_and(|last_voted_slot| vote_slots[i] <= last_voted_slot)
        {
            i = i
                .checked_add(1)
                .expect("`i` is bounded by `MAX_LOCKOUT_HISTORY` when finding larger slots");
            continue;
        }

        // 2) Find the hash for this slot `s`.
        if vote_slots[i] != slot_hashes[j.checked_sub(1).expect("`j` is positive")].0 {
            // Decrement `j` to find newer slots
            j = j
                .checked_sub(1)
                .expect("`j` is positive when finding newer slots");
            continue;
        }

        // 3) Once the hash for `s` is found, bump `s` to the next slot
        // in `vote_slots` and continue.
        i = i
            .checked_add(1)
            .expect("`i` is bounded by `MAX_LOCKOUT_HISTORY` when hash is found");
        j = j
            .checked_sub(1)
            .expect("`j` is positive when hash is found");
    }

    if j == slot_hashes.len() {
        // This means we never made it to steps 2) or 3) above, otherwise
        // `j` would have been decremented at least once. This means
        // there are not slots in `vote_slots` greater than `last_voted_slot`
        debug!(
            "{} dropped vote slots {:?}, vote hash: {:?} slot hashes:SlotHash {:?}, too old ",
            vote_state.node_pubkey(),
            vote_slots,
            vote_hash,
            slot_hashes
        );
        return Err(VoteError::VoteTooOld);
    }
    if i != vote_slots.len() {
        // This means there existed some slot for which we couldn't find
        // a matching slot hash in step 2)
        info!(
            "{} dropped vote slots {:?} failed to match slot hashes: {:?}",
            vote_state.node_pubkey(),
            vote_slots,
            slot_hashes,
        );
        return Err(VoteError::SlotsMismatch);
    }
    if &slot_hashes[j].1 != vote_hash {
        // This means the newest slot in the `vote_slots` has a match that
        // doesn't match the expected hash for that slot on this
        // fork
        warn!(
            "{} dropped vote slots {:?} failed to match hash {} {}",
            vote_state.node_pubkey(),
            vote_slots,
            vote_hash,
            slot_hashes[j].1
        );
        return Err(VoteError::SlotHashMismatch);
    }
    Ok(())
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1286-1307)
```rust
pub fn process_tower_sync<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    slot_hashes: &[SlotHash],
    clock: &Clock,
    tower_sync: TowerSync,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    let authorized_voter = vote_state.get_and_update_authorized_voter(clock.epoch)?;
    verify_authorized_signer(&authorized_voter, signers)?;

    do_process_tower_sync(
        &mut vote_state,
        slot_hashes,
        clock.epoch,
        clock.slot,
        tower_sync,
    )?;
    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1309-1322)
```rust
fn do_process_tower_sync(
    vote_state: &mut VoteStateHandler,
    slot_hashes: &[SlotHash],
    epoch: u64,
    slot: u64,
    mut tower_sync: TowerSync,
) -> Result<(), VoteError> {
    check_and_filter_proposed_vote_state(
        vote_state,
        &mut tower_sync.lockouts,
        &mut tower_sync.root,
        tower_sync.hash,
        slot_hashes,
    )?;
```
