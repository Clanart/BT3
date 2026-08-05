### Title
Missing zero/default-address check in vote program's `authorize()` for `VoteAuthorize::Withdrawer` can permanently lock a vote account - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The Agave vote program's `authorize()` function sets a new authorized withdrawer key based on caller-supplied instruction data without validating that the new key is a non-default, usable `Pubkey`. This mirrors the reported `Governable::transferGovernor()` bug: an authority-transfer function accepts an arbitrary new-authority value with no sanity check, so a single mistaken call permanently and irrecoverably strips control from the account.

### Finding Description
`authorize()` in [1](#0-0)  handles the `VoteAuthorize::Withdrawer` case by only verifying that the *current* authorized withdrawer signed the transaction, then unconditionally overwriting the withdrawer field:

```
VoteAuthorize::Withdrawer => {
    // current authorized withdrawer must say "yay"
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
    vote_state.set_authorized_withdrawer(*authorized);
}
```

`set_authorized_withdrawer()` itself performs a raw assignment with no validation at all: [2](#0-1) .

There is no check anywhere in this path that `authorized != Pubkey::default()` (or any other unusable value such as a PDA with no known signer). The same absence of a corresponding sanity check exists in the CLI helper that builds this instruction, `cli/src/vote.rs`'s `VoteAuthorize::Withdrawer` branch, which only checks that the new pubkey differs from the *current* authorized signer via `check_unique_pubkeys`, not that it is non-default: [3](#0-2) .

Because `authorized_withdrawer` gates every future change to itself (`verify_authorized_signer(vote_state.authorized_withdrawer(), signers)`), once it is set to `Pubkey::default()` (or any address whose private key is unknown/unobtainable), no future `Authorize`/`AuthorizeChecked` instruction can ever succeed again for that vote account — `verify_authorized_signer` will always fail because nobody can produce a valid signature for that key.

### Impact Explanation
The vote account's withdraw authority permanently loses the ability to withdraw the account's lamports (staked SOL) via the `Withdraw` instruction, and loses the ability to ever again authorize a new voter or withdrawer. This is a direct analog of "loss of ownership" from the original report — the stake associated with the vote account becomes permanently unrecoverable, i.e., fund loss, with no possible on-chain remediation.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the current authorized withdrawer to submit an `Authorize`/`AuthorizeChecked` instruction with a bad target pubkey (e.g., `Pubkey::default()` due to a client bug, copy-paste error, or supplying an unfunded/never-generated key), exactly the same operator-error scenario described in the original report for `transferGovernor()`. No signature forgery or privilege escalation is needed — only a missing safety check that should reject `Pubkey::default()` (or otherwise flag unusable values) before committing the change.

### Recommendation
Add an explicit rejection of `Pubkey::default()` (and ideally of the vote account's own address) as the `authorized` target in the `VoteAuthorize::Withdrawer` (and `Voter`) branches of `authorize()` in `programs/vote/src/vote_state/mod.rs`, returning `InstructionError::InvalidInstructionData` (or a dedicated `VoteError`) when triggered. Mirror the same validation client-side in `cli/src/vote.rs`'s `process_vote_authorize`/`VoteAuthorize::Withdrawer` branch so operators get an early, clear error before submitting the transaction.

### Proof of Concept
1. Create a vote account with `authorized_withdrawer = W`.
2. `W` signs and submits `VoteInstruction::Authorize(Pubkey::default(), VoteAuthorize::Withdrawer)` (or accidentally passes an all-zero/typo'd pubkey through the CLI's `vote-authorize-withdrawer` command).
3. `authorize()` calls `verify_authorized_signer(vote_state.authorized_withdrawer(), signers)` — succeeds because `W` is still the signer — then executes `vote_state.set_authorized_withdrawer(Pubkey::default())`.
4. Any subsequent `Authorize`, `AuthorizeChecked`, or `Withdraw` instruction now requires a signature from `Pubkey::default()`, which no one can produce; the vote account's stake and control are permanently locked. [4](#0-3) [5](#0-4)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L683-731)
```rust
/// Authorize the given pubkey to withdraw or sign votes. This may be called multiple times,
/// but will implicitly withdraw authorization from the previously authorized
/// key
pub fn authorize<S: std::hash::BuildHasher, F>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    authorized: &Pubkey,
    vote_authorize: VoteAuthorize,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
    is_vote_authorize_with_bls_enabled: bool,
    consume_pop_compute_units: F,
) -> Result<(), InstructionError>
where
    F: FnOnce() -> Result<(), InstructionError>,
{
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    match vote_authorize {
        VoteAuthorize::Voter => {
            if is_vote_authorize_with_bls_enabled && vote_state.has_bls_pubkey() {
                return Err(InstructionError::InvalidInstructionData);
            }
            let authorized_withdrawer_signer =
                verify_authorized_signer(vote_state.authorized_withdrawer(), signers).is_ok();

            vote_state.set_new_authorized_voter(
                authorized,
                clock.epoch,
                clock
                    .leader_schedule_epoch
                    .checked_add(1)
                    .ok_or(InstructionError::InvalidAccountData)?,
                None,
                |epoch_authorized_voter| {
                    // current authorized withdrawer or authorized voter must say "yay"
                    if authorized_withdrawer_signer {
                        Ok(())
                    } else {
                        verify_authorized_signer(&epoch_authorized_voter, signers)
                    }
                },
            )?;
        }
        VoteAuthorize::Withdrawer => {
            // current authorized withdrawer must say "yay"
            verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
            vote_state.set_authorized_withdrawer(*authorized);
        }
```

**File:** programs/vote/src/vote_state/handler.rs (L58-68)
```rust
    pub(crate) fn authorized_withdrawer(&self) -> &Pubkey {
        match &self.target_state {
            TargetVoteState::V4(v4) => &v4.authorized_withdrawer,
        }
    }

    pub(crate) fn set_authorized_withdrawer(&mut self, authorized_withdrawer: Pubkey) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => v4.authorized_withdrawer = authorized_withdrawer,
        }
    }
```

**File:** cli/src/vote.rs (L1252-1260)
```rust
        VoteAuthorize::Withdrawer => {
            check_unique_pubkeys(
                (&authorized.pubkey(), "authorized_account".to_string()),
                (new_authorized_pubkey, "new_authorized_pubkey".to_string()),
            )?;
            if let Some(vote_state) = vote_state {
                check_current_authority(&[vote_state.authorized_withdrawer], &authorized.pubkey())?
            }
        }
```
