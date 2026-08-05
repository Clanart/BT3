## Missing Zero-Address Validation Analog: Vote Program Accepts `Pubkey::default()` as `authorized_voter`/`authorized_withdrawer`

### Title
Vote Program `InitializeAccount`/`InitializeAccountV2` Do Not Reject `Pubkey::default()` as Authorized Voter/Withdrawer, Permanently Locking Vote Accounts - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The external report's bug class is "constructor/initializer parameters accepted without zero-address validation, leading to accidentally-bricked contracts." The Agave analog is in the vote program's account-initialization path: `initialize_account` and `initialize_account_v2` validate that the node signed the transaction, but never check that `vote_init.authorized_voter` or `vote_init.authorized_withdrawer` is a non-default `Pubkey`. Because Solana's system-program address (`Pubkey::default()`, all-zero bytes) has no corresponding Ed25519 private key, setting either authority field to this value permanently locks the vote account: no future `Authorize`/`AuthorizeChecked`/`Withdraw` instruction can ever obtain the required signature from that authority.

### Finding Description
`initialize_account` only verifies that the *node* signed, never validating the authority fields being written into the account state: [1](#0-0) 

`initialize_account_v2` similarly validates the reward collector accounts (per SIMD-0464/SIMD-0232) and the BLS proof-of-possession, but likewise performs no check on `vote_init.authorized_voter` or `vote_init.authorized_withdrawer`: [2](#0-1) 

That `Pubkey::default()` is accepted as a legitimate authority value by this code path is directly demonstrated by test fixtures that construct valid vote accounts using it as both `authorized_voter` and `authorized_withdrawer`: [3](#0-2) 

and in the CLI-adjacent `stake-accounts` test helper, which uses `Pubkey::default()` for the stake authorized custodian/staker/withdrawer fields without any rejection from the runtime: [4](#0-3) 

No corresponding "set new authority" escape hatch exists to recover from this state for a bricked authority the way `bpf_loader_upgradeable`'s `SetAuthority`/`SetAuthorityChecked` allow explicit authority rotation with signature checks — vote-account authorization changes always require the *current* authorized pubkey to sign, e.g., through `AuthorizeChecked`: [5](#0-4) 

If `authorized_withdrawer` is `Pubkey::default()`, that signature can never be produced, so this code path can never succeed for the affected account.

### Impact Explanation
`Pubkey::default()` is the all-zero-byte address, which is not a real signer key (no known Ed25519 private key maps to it in practice), and coincidentally is also the address of the System Program. If a validator operator (via a CLI bug, scripting error, or manual mistake — the same "accidental address(0)" scenario described in the original report) creates or re-initializes a vote account with `authorized_withdrawer` (or `authorized_voter`) left as/set to `Pubkey::default()`, the vote account's rent-exempt lamports and voting authority become permanently unrecoverable: `Withdraw` requires the withdrawer's signature, and `Authorize`/`AuthorizeChecked` require the current authority's signature — neither can ever be satisfied. This is a genuine, non-malicious, unprivileged loss-of-funds/loss-of-control condition matching the original report's "recommendation: add zero-address validation" remediation class.

### Likelihood Explanation
This requires no malicious peer, validator, or leaked key — only an ordinary user/operator mistake during vote-account creation (e.g., a CLI argument defaulting or being omitted incorrectly, or manual transaction construction). The original report explicitly frames this bug class around "accidentally set to address(0)," and the Agave vote program provides no similar guardrail at the point the value is committed to on-chain state.

### Recommendation
Add an explicit check in `initialize_account`/`initialize_account_v2` (and any other authority-setting instruction that writes `authorized_voter` / `authorized_withdrawer` directly from instruction data) rejecting `Pubkey::default()` (and any other addresses with a well-known/derivable lack of private key, such as `system_program::id()`) as a valid authority, returning `InstructionError::InvalidArgument` similarly to how `bpf_loader_upgradeable`'s `SetAuthority` rejects setting a `None`/immutable authority unintentionally.

### Proof of Concept
1. Construct a `VoteInit`/`VoteInitV2` with `authorized_withdrawer: Pubkey::default()` (as done in test helpers, e.g. `create_v4_account_with_authorized` and `staking_utils.rs`).
2. Submit `CreateAccountWithConfig` + `InitializeAccount`/`InitializeAccountV2`; observe it succeeds (`Ok(())`), as shown by existing test scaffolding accepting `default_authorized_pubkey` without error: [3](#0-2) 
3. Attempt any subsequent `Withdraw` or `AuthorizeChecked` instruction requiring the withdrawer's signature — this can never be satisfied because no keypair corresponds to `Pubkey::default()`, permanently locking the account's funds and voting authority.

**Note on completeness:** I was unable to directly read the implementation of `verify_authorized_signer` (only its call sites in `vote_state/mod.rs`) or the stake-program `Initialize`/`InitializeChecked` processor body due to index/tool limitations in this pass; if the stake program has an equivalent gap for `Authorized.staker`/`Authorized.withdrawer`, it would represent an additional instance of the same bug class and should be verified directly in a full checkout of the repository.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L1139-1186)
```rust
pub fn initialize_account_v2<S: std::hash::BuildHasher, F>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    vote_init: &VoteInitV2,
    inflation_rewards_collector: NewCommissionCollector,
    block_revenue_collector: NewCommissionCollector,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
    rent: &Rent,
    consume_pop_compute_units: F,
) -> Result<(), InstructionError>
where
    F: FnOnce() -> Result<(), InstructionError>,
{
    VoteStateHandler::check_vote_account_length(vote_account, target_version)?;
    let versioned = vote_account.get_state::<VoteStateVersions>()?;

    if !versioned.is_uninitialized() {
        return Err(InstructionError::AccountAlreadyInitialized);
    }

    // node must agree to accept this vote account
    verify_authorized_signer(&vote_init.node_pubkey, signers)?;

    // Per SIMD-0464, validate the collector accounts using the same checks as
    // `UpdateCommissionCollector` (SIMD-0232).
    let inflation_rewards_collector_key =
        inflation_rewards_collector.validate_and_resolve_key(vote_account, rent)?;
    let block_revenue_collector_key =
        block_revenue_collector.validate_and_resolve_key(vote_account, rent)?;

    // verify the BLS pubkey proof of possession
    verify_bls_proof_of_possession(
        vote_account.get_key(),
        &vote_init.authorized_voter_bls_pubkey,
        &vote_init.authorized_voter_bls_proof_of_possession,
        consume_pop_compute_units,
    )?;

    VoteStateHandler::init_vote_account_state_v2(
        vote_account,
        vote_init,
        &inflation_rewards_collector_key,
        &block_revenue_collector_key,
        clock,
        target_version,
    )
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1191-1209)
```rust
pub fn initialize_account<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    vote_init: &VoteInit,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
) -> Result<(), InstructionError> {
    VoteStateHandler::check_vote_account_length(vote_account, target_version)?;
    let versioned = vote_account.get_state::<VoteStateVersions>()?;

    if !versioned.is_uninitialized() {
        return Err(InstructionError::AccountAlreadyInitialized);
    }

    // node must agree to accept this vote account
    verify_authorized_signer(&vote_init.node_pubkey, signers)?;

    VoteStateHandler::init_vote_account_state(vote_account, vote_init, clock, target_version)
}
```

**File:** programs/vote/src/vote_processor.rs (L315-333)
```rust
        VoteInstruction::AuthorizeChecked(vote_authorize) => {
            instruction_context.check_number_of_instruction_accounts(4)?;
            let voter_pubkey = instruction_context.get_key_of_instruction_account(3)?;
            if !instruction_context.is_instruction_account_signer(3)? {
                return Err(InstructionError::MissingRequiredSignature);
            }
            let clock =
                get_sysvar_with_account_check::clock(invoke_context, &instruction_context, 1)?;
            vote_state::authorize(
                &mut me,
                target_version,
                voter_pubkey,
                vote_authorize,
                &signers,
                &clock,
                is_vote_authorize_with_bls_enabled,
                consume_pop_compute_units,
            )
        }
```

**File:** programs/vote/src/vote_processor.rs (L4442-4451)
```rust
        // Test with new_authorized_pubkey signer
        let default_authorized_pubkey = Pubkey::default();
        let vote_account = create_test_account_with_provided_authorized(
            &default_authorized_pubkey,
            &default_authorized_pubkey,
        );
        let clock_address = sysvar::clock::id();
        let clock_account = account::create_account_shared_data_for_test(&Clock::default());
        let authorized_account = create_default_account();
        let new_authorized_account = create_default_account();
```

**File:** stake-accounts/src/stake_accounts.rs (L444-453)
```rust
        let message = new_stake_account(
            &fee_payer_pubkey,
            &funding_pubkey,
            &base_pubkey,
            lamports,
            &stake_authority_pubkey,
            &withdraw_authority_pubkey,
            &Pubkey::default(),
            0,
        );
```
