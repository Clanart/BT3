[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L702-705)
```rust
        VoteAuthorize::Voter => {
            if is_vote_authorize_with_bls_enabled && vote_state.has_bls_pubkey() {
                return Err(InstructionError::InvalidInstructionData);
            }
```

**File:** programs/vote/src/vote_state/mod.rs (L736-762)
```rust
            let authorized_withdrawer_signer =
                verify_authorized_signer(vote_state.authorized_withdrawer(), signers).is_ok();

            verify_bls_proof_of_possession(
                vote_account.get_key(),
                &args.bls_pubkey,
                &args.bls_proof_of_possession,
                consume_pop_compute_units,
            )?;

            vote_state.set_new_authorized_voter(
                authorized,
                clock.epoch,
                clock
                    .leader_schedule_epoch
                    .checked_add(1)
                    .ok_or(InstructionError::InvalidAccountData)?,
                Some(&args.bls_pubkey),
                |epoch_authorized_voter| {
                    // current authorized withdrawer or authorized voter must say "yay"
                    if authorized_withdrawer_signer {
                        Ok(())
                    } else {
                        verify_authorized_signer(&epoch_authorized_voter, signers)
                    }
                },
            )?;
```
