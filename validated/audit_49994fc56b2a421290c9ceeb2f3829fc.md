[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L907-921)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L4956-4993)
```rust
        // Should fail - new collector not system program owned.
        {
            let bad_collector = solana_pubkey::new_rand();
            let non_system_owner = solana_pubkey::new_rand();
            let bad_collector_account =
                AccountSharedData::new(collector_lamports, 0, &non_system_owner);
            let transaction_context = new_transaction_context(
                vec![
                    (id(), processor_account.clone()),
                    (vote_pubkey, vote_account.clone()),
                    (bad_collector, bad_collector_account),
                ],
                vec![
                    InstructionAccount::new(1, false, true),
                    InstructionAccount::new(2, false, true),
                ],
                &rent,
            );
            let instruction_context = transaction_context.get_next_instruction_context().unwrap();
            let mut borrowed_vote_account = instruction_context
                .try_borrow_instruction_account(0)
                .unwrap();

            assert_eq!(
                update_commission_collector(
                    &mut borrowed_vote_account,
                    target_version,
                    NewCommissionCollector::NewAccount(
                        instruction_context
                            .try_borrow_instruction_account(1)
                            .unwrap()
                    ),
                    CommissionKind::InflationRewards,
                    &signers,
                    &rent,
                ),
                Err(InstructionError::InvalidAccountOwner)
            );
```

**File:** programs/vote/src/vote_processor.rs (L334-360)
```rust
        VoteInstruction::InitializeAccountV2(vote_init_v2) => {
            if !is_init_account_v2_enabled {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(4)?;

            let inflation_rewards_collector =
                read_new_collector_account(&instruction_context, &me, 2)?;

            let block_revenue_collector = read_new_collector_account(&instruction_context, &me, 3)?;

            let sysvar_cache = invoke_context.environment_config.sysvar_cache();
            let clock = sysvar_cache.get_clock()?;
            let rent = sysvar_cache.get_rent()?;

            vote_state::initialize_account_v2(
                &mut me,
                target_version,
                &vote_init_v2,
                inflation_rewards_collector,
                block_revenue_collector,
                &signers,
                &clock,
                &rent,
                consume_pop_compute_units,
            )
```

**File:** programs/vote/src/vote_processor.rs (L2033-2058)
```rust
        // Should fail - new collector not system program owned.
        let non_system_owner = Pubkey::new_unique();
        let non_system_collector_pubkey = Pubkey::new_unique();
        let non_system_collector_account =
            AccountSharedData::new(collector_lamports, 0, &non_system_owner);
        let mut non_system_transaction_accounts = transaction_accounts.clone();
        non_system_transaction_accounts[1] =
            (non_system_collector_pubkey, non_system_collector_account);
        let non_system_instruction_accounts = vec![
            AccountMeta {
                pubkey: vote_pubkey,
                is_signer: false,
                is_writable: true,
            },
            AccountMeta {
                pubkey: non_system_collector_pubkey,
                is_signer: false,
                is_writable: true,
            },
            AccountMeta {
                pubkey: authorized_withdrawer,
                is_signer: true,
                is_writable: false,
            },
        ];
        let accounts = process_instruction(
```
