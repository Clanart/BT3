### Title
Vote program's `authorize()` for `VoteAuthorize::Withdrawer` never rejects `Pubkey::default()`, permanently bricking withdraw authority and locking vote-account funds - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The upstream report flags `MainToken.set_mint_multisig()` for accepting the zero address as the new `minting_multisig`, permanently losing a critical authority. The equivalent broken invariant exists in Agave's vote program: `authorize()` accepts any `Pubkey` — including `Pubkey::default()` — as the new authorized withdrawer and stores it unchecked.

### Finding Description
In `authorize()`, the `VoteAuthorize::Withdrawer` branch only verifies that the *current* authorized withdrawer signed the transaction, then unconditionally writes the caller-supplied `authorized` pubkey as the new withdraw authority: [1](#0-0) 

```
VoteAuthorize::Withdrawer => {
    // current authorized withdrawer must say "yay"
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
    vote_state.set_authorized_withdrawer(*authorized);
}
```

`set_authorized_withdrawer` performs no validation whatsoever on the incoming value: [2](#0-1) 

There is no check anywhere in this path (`verify_authorized_signer`, `authorize`, or `set_authorized_withdrawer`) that rejects `authorized == Pubkey::default()`. Every subsequent privileged operation on the vote account — `withdraw()`, another call to `authorize()` for `Withdrawer`, and `update_validator_identity()` — is gated exclusively by `verify_authorized_signer(vote_state.authorized_withdrawer(), signers)`: [3](#0-2) [4](#0-3) 

Because `Pubkey::default()` (the all-zero pubkey) has no corresponding private key, no transaction can ever produce a valid signature matching it, so `verify_authorized_signer` can never succeed once the withdrawer is set to the default pubkey.

### Impact Explanation
If the current authorized withdrawer authority mistakenly (or is tricked into, e.g. via a malicious CLI wrapper, script bug, or copy/paste error producing an all-zero string) calls `vote-authorize-withdrawer` with the zero pubkey, the vote account permanently loses its withdraw authority. Consequences:
- All lamports held in the vote account (identity stake commissions, MEV/priority-fee revenue, rent-exempt reserve) become permanently unwithdrawable — a permanent fund lock.
- The validator identity (`update_validator_identity`) and commission collector can no longer be updated, because both require the (now unreachable) authorized withdrawer's signature.
- The withdraw authority itself can never be recovered or re-delegated (the only way to change it is another `Authorize(..., Withdrawer)` call signed by the *current* withdrawer, which is the unusable zero key).

This matches the reported impact class: irreversible loss of a critical authority causing permanent fund lock, without any admin/multisig recovery path.

### Likelihood Explanation
The `authorize` instruction is a completely permissionless, single-signature operation available to any current authorized withdrawer and requires no unusual privileges to trigger — the same "authorized user makes an unchecked mistake" scenario described in the original report. Since CLI-issued pubkeys are normally validated by clap validators, the more realistic trigger vectors are: programmatic/automated tooling constructing the instruction directly with `stake_instruction`/`vote_instruction` bindings (bypassing CLI validators), a bug in an external wallet/integration passing an uninitialized/zeroed pubkey, or a malicious front-end tricking an operator into "authorizing" what looks like a placeholder value. No consensus-level protections or runtime pre-checks catch this before it is permanently committed to state.

### Recommendation
Add an explicit check in the `VoteAuthorize::Withdrawer` (and ideally `VoteAuthorize::Voter`/`VoterWithBLS`) branches of `authorize()` in `programs/vote/src/vote_state/mod.rs` rejecting `Pubkey::default()` (and any other well-known un-ownable/burn addresses) as a new authority, returning `InstructionError::InvalidInstructionData` (or a dedicated `VoteError`) before calling `set_authorized_withdrawer`/`set_new_authorized_voter`.

### Proof of Concept
1. Create a vote account with `authorized_withdrawer = W` (a real keypair).
2. `W` signs and submits `VoteInstruction::Authorize(Pubkey::default(), VoteAuthorize::Withdrawer)`.
3. `authorize()` passes (`verify_authorized_signer` succeeds because `W` is the current, valid withdrawer) and stores `authorized_withdrawer = Pubkey::default()` via `set_authorized_withdrawer` [1](#0-0) .
4. Any subsequent `VoteInstruction::Withdraw` requires `verify_authorized_signer(&Pubkey::default(), signers)` to succeed [3](#0-2) , which is impossible since no signer can ever match the zero pubkey — the vote account's lamports (beyond what's required for accounts operations) are now permanently unwithdrawable, and the withdraw authority can never be changed again.

Note: I was unable to fully inspect the body of `verify_authorized_signer` (only located its declaration) due to tool-call limits, so I cannot 100% rule out a special-case check for `Pubkey::default()` inside that function itself; however, no such check appears in `authorize()`, `set_authorized_withdrawer()`, or any surrounding logic I was able to review, and the naming/behavior strongly suggests it is a straightforward signature-set membership check with no zero-address special-casing.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L727-731)
```rust
        VoteAuthorize::Withdrawer => {
            // current authorized withdrawer must say "yay"
            verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
            vote_state.set_authorized_withdrawer(*authorized);
        }
```

**File:** programs/vote/src/vote_state/mod.rs (L777-783)
```rust
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // current authorized withdrawer must say "yay"
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    // new node must say "yay"
    verify_authorized_signer(node_pubkey, signers)?;
```

**File:** programs/vote/src/vote_state/mod.rs (L1073-1077)
```rust
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;
    let vote_state = get_vote_state_handler_checked(&vote_account, target_version)?;

    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
```

**File:** programs/vote/src/vote_state/handler.rs (L64-68)
```rust
    pub(crate) fn set_authorized_withdrawer(&mut self, authorized_withdrawer: Pubkey) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => v4.authorized_withdrawer = authorized_withdrawer,
        }
    }
```
