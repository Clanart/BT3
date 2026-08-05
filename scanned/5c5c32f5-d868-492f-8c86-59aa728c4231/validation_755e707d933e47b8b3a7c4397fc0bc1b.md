## Title
Custom Commission Collector Not Reset On Withdraw-Authority Transfer, Enabling Fund Diversion by a Removed Vote-Account Owner - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The vote program's SIMD-0232 "custom commission collector" feature lets the `authorized_withdrawer` of a vote account redirect block-revenue and inflation-rewards commission payouts to an arbitrary account via `update_commission_collector`. When the withdraw authority is later transferred to a new owner via `authorize(VoteAuthorize::Withdrawer, …)`, the `inflation_rewards_collector`/`block_revenue_collector` fields are **not reset**. The new owner inherits voting/withdraw control of the account, but commission payouts keep flowing to whatever address the *previous* owner configured, mirroring the UNCX "collector role not relinquished on lock transfer" bug class exactly.

### Finding Description
`update_commission_collector` lets the current `authorized_withdrawer` set `inflation_rewards_collector`/`block_revenue_collector` to any account satisfying the SIMD-0232 constraints (system-owned, rent-exempt, or the vote account itself): [1](#0-0) 

The validation logic that resolves the collector key only checks the target account's ownership/rent-exemption, not any relationship to `authorized_withdrawer` or `node_pubkey`: [2](#0-1) 

Separately, transferring vote-account ownership is done through `authorize` with `VoteAuthorize::Withdrawer`, which only reassigns `authorized_withdrawer` and never touches the collector fields: [3](#0-2) 

Compare this to `update_validator_identity`, which explicitly documents that before SIMD-0232 the collector was *always* kept in sync with `node_pubkey`, and after SIMD-0232 it is intentionally left untouched when identity rotates: [4](#0-3) 

This is precisely the UNCX pattern: a secondary "collector" role, settable by the current owner, that is silently decoupled from the primary ownership/authority transfer path (`authorize(Withdrawer, …)`), so a party that is no longer an authority over the account can continue directing collected value to itself. The unit tests confirm the collector deliberately survives `update_validator_identity` (identity rotation) when SIMD-0232 is enabled: [5](#0-4) 

but there is no equivalent test or code path showing the collector is reset when `authorized_withdrawer` — the actual economic owner — changes hands.

### Impact Explanation
A vote account is frequently sold/transferred (e.g., stake-pool operators, marketplaces for validator identities, or a validator changing custodians). If the seller, while still `authorized_withdrawer`, sets `block_revenue_collector`/`inflation_rewards_collector` to an account they control, the buyer receives full staker/voter/withdrawer control post-transfer but the block-revenue-sharing and inflation reward commission (paid out per epoch based on these fields, consumed downstream in `runtime/src/bank/fee_distribution.rs` and `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`) will continue to be paid to the former owner's address indefinitely, until the new owner notices and reissues `UpdateCommissionCollector`. This is a fund-diversion/fund-theft primitive: value that should accrue to the new (rightful) owner of the vote account is instead siphoned to the prior owner, with no signature or check from the current owner required to stop it.

### Likelihood Explanation
High for any transfer-of-ownership scenario where the buyer does not independently and immediately re-verify/re-set both collector fields after taking over `authorized_withdrawer`. The attack requires no privileged access or malicious validator/peer assumption — it is simply the seller (a normal, previously-legitimate accountholder) exploiting the absence of any reset logic in a standard instruction path (`Authorize`/`UpdateCommissionCollector` are both regular, permissionless vote-program instructions). No existing guard (only `NewCommissionCollector::validate_and_resolve_key`, which checks account ownership/rent-exemption/writability, not relation to current authorities) prevents this.

### Recommendation
When `authorized_withdrawer` is reassigned via `authorize(VoteAuthorize::Withdrawer, …)` (and possibly `update_validator_identity`/`update_node_pubkey`), reset `inflation_rewards_collector` and `block_revenue_collector` to a safe default (e.g., the vote account itself, or the new `authorized_withdrawer`), or require the new withdrawer to explicitly re-affirm/re-set the collector addresses as part of the ownership transfer. At minimum, document and enforce that a transfer of `authorized_withdrawer` implicitly revokes previously configured custom collectors.

### Proof of Concept
1. Vote account `V` is created; `authorized_withdrawer` = Alice, SIMD-0232 (`custom_commission_collector`) enabled.
2. Alice calls `UpdateCommissionCollector(CommissionKind::BlockRevenue)` setting the collector to `AliceCollector` (an account Alice controls), per `update_commission_collector`: [1](#0-0) 
3. Alice sells the vote account to Bob and calls `Authorize(Bob, VoteAuthorize::Withdrawer)`; per `authorize`'s `Withdrawer` branch, only `authorized_withdrawer` is updated: [3](#0-2) 
4. Bob now controls staking/voting/withdraw rights over `V`, but `block_revenue_collector` still equals `AliceCollector`.
5. On each subsequent epoch's block-revenue distribution (computed from `block_revenue_collector` in `runtime/src/bank/fee_distribution.rs`/`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), funds are paid to `AliceCollector` instead of Bob, with no signature from Bob required and no error raised — confirming the fund-diversion path is live and unguarded by the ownership-transfer instruction.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L727-731)
```rust
        VoteAuthorize::Withdrawer => {
            // current authorized withdrawer must say "yay"
            verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
            vote_state.set_authorized_withdrawer(*authorized);
        }
```

**File:** programs/vote/src/vote_state/mod.rs (L769-794)
```rust
/// Update the node_pubkey, requires signature of the authorized voter
pub fn update_validator_identity<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    node_pubkey: &Pubkey,
    signers: &HashSet<Pubkey, S>,
    custom_commission_collector_enabled: bool,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // current authorized withdrawer must say "yay"
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    // new node must say "yay"
    verify_authorized_signer(node_pubkey, signers)?;

    vote_state.set_node_pubkey(*node_pubkey);

    // Before SIMD-0232, block_revenue_collector is always synced with node_pubkey.
    // After SIMD-0232, the collector can be set independently.
    if !custom_commission_collector_enabled {
        vote_state.set_block_revenue_collector(*node_pubkey);
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L861-905)
```rust
pub enum NewCommissionCollector<'a, 'b> {
    VoteAccount,
    NewAccount(BorrowedInstructionAccount<'a, 'b>),
}

impl NewCommissionCollector<'_, '_> {
    /// Validates the collector per SIMD-0232 and returns its pubkey.
    ///
    /// The designated commission collector must either be equal to the vote
    /// account's address OR satisfy ALL of the following constraints:
    ///
    /// 1. Must be a system program owned account.
    /// 2. Must be rent-exempt.
    /// 3. Must not be a reserved account (checked via writable flag).
    pub fn validate_and_resolve_key(
        &self,
        vote_account: &BorrowedInstructionAccount,
        rent: &Rent,
    ) -> Result<Pubkey, InstructionError> {
        match self {
            NewCommissionCollector::VoteAccount => Ok(*vote_account.get_key()),
            NewCommissionCollector::NewAccount(collector_account) => {
                // 1. Must be a system program owned account.
                if collector_account.get_owner() != &system_program::id() {
                    return Err(InstructionError::InvalidAccountOwner);
                }

                // 2. Must be rent-exempt.
                if !rent.is_exempt(
                    collector_account.get_lamports(),
                    collector_account.get_data().len(),
                ) {
                    return Err(InstructionError::InsufficientFunds);
                }

                // 3. Must not be a reserved account (checked via writable flag).
                if !collector_account.is_writable() {
                    return Err(InstructionError::InvalidArgument);
                }

                Ok(*collector_account.get_key())
            }
        }
    }
}
```

**File:** programs/vote/src/vote_state/mod.rs (L907-933)
```rust
/// Update the vote account's commission collector (SIMD-0232).
pub fn update_commission_collector<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    new_collector: NewCommissionCollector,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    rent: &Rent,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    let new_collector_key = new_collector.validate_and_resolve_key(vote_account, rent)?;

    match kind {
        CommissionKind::InflationRewards => {
            vote_state.set_inflation_rewards_collector(new_collector_key);
        }
        CommissionKind::BlockRevenue => {
            vote_state.set_block_revenue_collector(new_collector_key);
        }
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L4167-4231)
```rust
    #[test]
    fn test_update_validator_identity_preserves_custom_block_revenue_collector() {
        // SIMD-0232 enabled.
        //
        // Once a validator has set a custom block_revenue_collector, rotating
        // the validator identity via UpdateValidatorIdentity must NOT clobber
        // the custom collector.
        let custom_commission_collector_enabled = true;

        let vote_pubkey = solana_pubkey::new_rand();
        let mut vote_state = vote_state_new_for_test(&vote_pubkey, VoteStateTargetVersion::V4);
        let node_pubkey = *vote_state.node_pubkey();
        let withdrawer_pubkey = *vote_state.authorized_withdrawer();

        // Seed a custom block_revenue_collector distinct from the node identity.
        let custom_collector = solana_pubkey::new_rand();
        vote_state.set_block_revenue_collector(custom_collector);

        let serialized = vote_state.serialize();
        let serialized_len = serialized.len();
        let rent = Rent::default();
        let lamports = rent.minimum_balance(serialized_len);
        let mut vote_account = AccountSharedData::new(lamports, serialized_len, &id());
        vote_account.set_data_from_slice(&serialized);

        let processor_account = AccountSharedData::new(0, 0, &solana_sdk_ids::native_loader::id());
        let mut transaction_context = TransactionContext::new(
            vec![(id(), processor_account), (node_pubkey, vote_account)],
            rent,
            0,
            0,
            1,
        );
        transaction_context
            .configure_top_level_instruction_for_tests(
                0,
                vec![InstructionAccount::new(1, false, true)],
                vec![],
            )
            .unwrap();
        let instruction_context = transaction_context.get_next_instruction_context().unwrap();
        let mut borrowed_account = instruction_context
            .try_borrow_instruction_account(0)
            .unwrap();

        let new_node_pubkey = solana_pubkey::new_rand();
        let signers: HashSet<Pubkey> = vec![withdrawer_pubkey, new_node_pubkey]
            .into_iter()
            .collect();

        update_validator_identity(
            &mut borrowed_account,
            VoteStateTargetVersion::V4,
            &new_node_pubkey,
            &signers,
            custom_commission_collector_enabled,
        )
        .unwrap();

        // node_pubkey updated, but block_revenue_collector preserved.
        let vote_state =
            VoteStateV4::deserialize(borrowed_account.get_data(), &new_node_pubkey).unwrap();
        assert_eq!(vote_state.node_pubkey, new_node_pubkey);
        assert_eq!(vote_state.block_revenue_collector, custom_collector);
        assert_ne!(vote_state.block_revenue_collector, new_node_pubkey);
```
