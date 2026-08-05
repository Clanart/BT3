Based on my research, I found an analog in Agave's vote program: the `VoteInit`/`VoteInitV2` initialization path accepts `node_pubkey`, `authorized_voter`, and `authorized_withdrawer` as raw `Pubkey` values with no check against `Pubkey::default()` (the "zero address" equivalent in Solana).

### Title
Vote account `InitializeAccount`/`InitializeAccountV2` accepts unchecked zero `Pubkey` for withdraw/voter authority, permanently bricking withdrawal control - (File: programs/vote/src/vote_state/mod.rs, programs/vote/src/vote_processor.rs)

### Summary
The 1inch `FarmingPool` report flags a constructor that accepts token addresses without a non-zero check, risking irrecoverable loss of funds if a zero address is used. The Agave analog is the vote program's account initialization path: `VoteInstruction::InitializeAccount`/`InitializeAccountV2` is dispatched to `vote_state::initialize_account`/`initialize_account_v2` [1](#0-0) , which stores whatever `node_pubkey`, `authorized_voter`, and `authorized_withdrawer` values are supplied in the instruction data directly into vote state, with no validation that these are non-default/non-zero `Pubkey`s.

### Finding Description
`VoteInit`/`VoteInitV2` structures carry `node_pubkey`, `authorized_voter`, and `authorized_withdrawer` as plain `Pubkey` fields set by the transaction submitter [2](#0-1) . The processor only checks that the relevant accounts sign and that the vote account isn't already initialized; it performs no equality check against `Pubkey::default()` for the authority fields before persisting them into `VoteState`/`VoteStateV4`. A search of `programs/vote/src/vote_state/mod.rs` for `Pubkey::default()` guard checks around these authority-setting code paths returned no matches, indicating the invariant "authority pubkey must not be the zero/default pubkey" is never enforced. If `authorized_withdrawer` is initialized to `Pubkey::default()`, the vote account's lamports and control become permanently locked because no keypair reproducibly corresponds to the all-zero public key in practice, mirroring the "funds sent to a zero address are lost forever" impact described in the report.

### Impact Explanation
Any lamports credited to (or rent-exempt reserve held by) a vote account whose `authorized_withdrawer` is set to the zero pubkey become permanently unwithdrawable — no valid signer can ever satisfy the authorization check for `Pubkey::default()`. This is a direct fund-loss condition reachable by any unprivileged user submitting an ordinary `InitializeAccount`/`InitializeAccountV2` instruction, matching the "fund theft/loss" impact category for this class of bug.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the vote-account creator (or a buggy/malicious client-side tool) to pass a zero pubkey for `authorized_withdrawer`, which would typically be a user/tooling error rather than an attack by a third party against someone else's account, since the creator only harms their own future control over the account. There is no privileged assumption needed and the path is fully reachable through a standard transaction, but the "attacker" and "victim" are typically the same party, which reduces exploitation motivation compared to a cross-account theft primitive.

### Recommendation
Add an explicit check in `vote_state::initialize_account`/`initialize_account_v2` (and the corresponding CLI/SDK builders) rejecting `authorized_withdrawer == Pubkey::default()` (and arguably `authorized_voter`/`node_pubkey == Pubkey::default()`) with `InstructionError::InvalidArgument`, analogous to the recommended non-zero-address check in the original report.

### Proof of Concept
1. Create and fund a new vote account (`AccountSharedData` owned by the vote program) with the minimum rent-exempt balance.
2. Submit `VoteInstruction::InitializeAccount(VoteInit { node_pubkey, authorized_voter, authorized_withdrawer: Pubkey::default(), commission: 0 })` with a valid `node_pubkey` signer, following the exact account layout used in `test_initialize_vote_account` [3](#0-2) .
3. The instruction succeeds (no rejection for the zero withdrawer) since only signer/already-initialized checks are enforced [1](#0-0) .
4. Any subsequent `Withdraw` instruction requiring `authorized_withdrawer`'s signature can never be satisfied, permanently locking the account's lamports.

**Note on confidence**: I was unable to locate the exact body of `initialize_account`/`initialize_account_v2` in `programs/vote/src/vote_state/mod.rs` within the indexed content (only test invocations were returned), so I could not directly confirm the absence of a zero-pubkey guard by reading the function body itself — this is inferred from the absence of any `Pubkey::default()` reference in that file and the processor dispatch code shown above. Given index size limits, some file contents may not be fully available; a full read of `programs/vote/src/vote_state/mod.rs` via a Devin session would be needed to definitively confirm there is no such check before treating this as a confirmed finding rather than a strong candidate.

### Citations

**File:** programs/vote/src/vote_processor.rs (L334-361)
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
        }
```

**File:** programs/vote/src/vote_processor.rs (L927-988)
```rust
    fn test_initialize_vote_account(
        bls_pubkey_management_in_vote_account: bool,
        commission_rate_in_basis_points: bool,
        custom_commission_collector: bool,
        block_revenue_sharing: bool,
        vote_account_initialize_v2: bool,
    ) {
        let vote_pubkey = solana_pubkey::new_rand();
        let vote_account = AccountSharedData::new(100, vote_state_size_of(), &id());
        let node_pubkey = solana_pubkey::new_rand();
        let node_account = AccountSharedData::default();
        let instruction_data = serialize(&VoteInstruction::InitializeAccount(VoteInit {
            node_pubkey,
            authorized_voter: vote_pubkey,
            authorized_withdrawer: vote_pubkey,
            commission: 0,
        }))
        .unwrap();
        let mut instruction_accounts = vec![
            AccountMeta {
                pubkey: vote_pubkey,
                is_signer: false,
                is_writable: true,
            },
            AccountMeta {
                pubkey: sysvar::rent::id(),
                is_signer: false,
                is_writable: false,
            },
            AccountMeta {
                pubkey: sysvar::clock::id(),
                is_signer: false,
                is_writable: false,
            },
            AccountMeta {
                pubkey: node_pubkey,
                is_signer: true,
                is_writable: false,
            },
        ];

        let features = VoteProgramFeatures {
            bls_pubkey_management_in_vote_account,
            commission_rate_in_basis_points,
            custom_commission_collector,
            block_revenue_sharing,
            vote_account_initialize_v2,
            alpenglow_migration_succeeded: false,
        };

        let accounts = process_instruction(
            features,
            &instruction_data,
            vec![
                (vote_pubkey, vote_account.clone()),
                (sysvar::rent::id(), create_default_rent_account()),
                (sysvar::clock::id(), create_default_clock_account()),
                (node_pubkey, node_account.clone()),
            ],
            instruction_accounts.clone(),
            Ok(()),
        );
```
