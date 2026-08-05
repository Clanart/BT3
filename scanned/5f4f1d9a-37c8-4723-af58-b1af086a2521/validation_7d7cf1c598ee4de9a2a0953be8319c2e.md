### Title
Unbounded, cheaply-grown vote-account set is fully iterated (no pagination) on every `getVoteAccounts` RPC call - ([File: rpc/src/rpc.rs])

### Summary
The reported bug class is: a permissionless, cheaply-growable collection is iterated in its entirety on every call to a hot-path function, so an attacker who can add elements to the collection cheaply can make that function's cost (and eventually the collection's storage) grow without bound. In Agave, `RpcSol::get_vote_accounts` reproduces this exact pattern against `Bank::vote_accounts()`: any account owned by the vote program that is correctly sized/initialized is admitted into the bank-wide `vote_accounts` map with no stake requirement and no upper bound, and `get_vote_accounts` walks the *entire* map on every invocation, with no pagination and no cap on the number of entries scanned.

### Finding Description
`RpcSol::get_vote_accounts` retrieves `bank.vote_accounts()` and then iterates it with `.iter().filter_map(...)` over every entry to build `current_vote_accounts` / `delinquent_vote_accounts`, doing per-entry work (deserializing the vote state view, walking `epoch_credits_iter()`, string-formatting pubkeys, etc.): [1](#0-0) 

There is no limit on the number of vote accounts scanned and no pagination parameter in `RpcGetVoteAccountsConfig`; the only bounding that exists is on the *epoch-credits history per account* (`MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY`), not on the *number of accounts* processed: [2](#0-1) 

The underlying set being iterated, `Stakes::vote_accounts`, admits any vote-program-owned account regardless of stake, as long as it is the correct size and initialized — this is exactly analogous to a permissionless "create a set entry" operation in the MetaVesT report: [3](#0-2) 

Creating a vote account only requires paying rent-exemption for `VoteStateV4::size_of()` bytes and submitting an `InitializeAccount`/`InitializeAccountV2` instruction — no stake delegation, no admin permission, and no protocol-level cap on how many such accounts can exist: [4](#0-3) 

By contrast, Agave engineers *have* recognized and mitigated this exact class of unbounded-iteration risk elsewhere: the epoch-reward/leader-schedule path explicitly filters the vote-account set down to `MAX_ALPENGLOW_VOTE_ACCOUNTS` before any full iteration (VAT filtering) precisely to keep per-epoch consensus-critical iteration bounded: [5](#0-4) 

`get_vote_accounts` has no equivalent guard — it operates on `bank.vote_accounts()`, the *unfiltered* cache (the same one populated by `unfiltered_distribution_vote_accounts` via `stakes_cache.activate_epoch`), so the RPC-facing full-scan surface is left unbounded even though the consensus-facing surface was hardened.

### Impact Explanation
An unprivileged, unstaked account creator can grow the vote-account set by repeatedly submitting cheap `CreateAccount` + `InitializeAccount` transactions. Each such vote account persists in `bank.vote_accounts()` indefinitely (it is only removed by an explicit `Withdraw` to zero balance). As this set grows, every `getVoteAccounts` call — a widely used, single-client-triggerable JSON-RPC method with no special permissions — does strictly more work: full iteration, per-account vote-state deserialization, epoch-credits slicing, and result-vector construction, with no cap. This matches the explicitly allowed "single-client low-rate RPC crash/degradation" impact class: a single caller invoking `getVoteAccounts` against a validator whose vote-account set has been inflated by a cheap, unprivileged campaign can experience severe latency, excessive CPU/memory use per request, and potential RPC-thread starvation/crash, without needing any validator/admin privilege or malicious-peer assumption.

### Likelihood Explanation
Likelihood is credible: creating a vote account requires only paying account-rent-exemption for `VoteStateV4::size_of()` and submitting a permissionless system-program `CreateAccount` followed by `InitializeAccount`/`InitializeAccountV2` — both of which are ordinary, unprivileged instructions available to any funded keypair. There is no protocol-enforced cap on the number of vote accounts that can exist system-wide, and unlike stake accounts (whose economic weight is naturally bounded by minimum delegation amounts and are filtered via VAT for consensus paths), unstaked vote accounts incur no such filtering on the RPC surface. The attack is purely economic (transaction fees + rent) rather than requiring any protocol flaw beyond the missing pagination/limit.

### Recommendation
Add pagination/limit parameters to `RpcGetVoteAccountsConfig` (e.g., `limit`/`offset` or a cursor) and/or cap the number of vote accounts scanned per call in `get_vote_accounts`, similar to how the existing `MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY` bounds per-account epoch-credits size. Consider also applying the same admission/eligibility filtering used for VAT (e.g., minimum-stake or minimum-balance thresholds) to the RPC-visible vote-account set so that unstaked, cheaply created accounts cannot inflate the structure that `getVoteAccounts` must fully scan.

### Proof of Concept
1. Fund a keypair and repeatedly submit `system_instruction::create_account` + `vote_instruction::create_account_with_config`/`initialize_account` (paying only the rent-exemption for `VoteStateV4::size_of()` bytes) to create N new, unstaked vote accounts targeting the same validator's bank — see `cli/src/vote.rs::process_create_vote_account` for the exact instruction sequence used by legitimate tooling [6](#0-5) .
2. Repeat until N is large (e.g., tens/hundreds of thousands of accounts); each of these persists in `bank.vote_accounts()` because `Stakes::new_from_accounts_for_genesis`/the runtime equivalent admits any correctly-sized, vote-program-owned, initialized account with no stake check [3](#0-2) .
3. Call `getVoteAccounts` against the target RPC node and observe response latency/CPU growing linearly with N, since `get_vote_accounts` performs a full, unpaginated iteration over `bank.vote_accounts()` on every call [1](#0-0) .

### Citations

**File:** rpc/src/rpc.rs (L1171-1201)
```rust
        let vote_accounts = bank.vote_accounts();
        let epoch_vote_accounts = bank
            .epoch_vote_accounts(bank.get_epoch_and_slot_index(bank.slot()).0)
            .ok_or_else(Error::invalid_request)?;
        let delinquent_validator_slot_distance = config
            .delinquent_slot_distance
            .unwrap_or(DELINQUENT_VALIDATOR_SLOT_DISTANCE);
        let (current_vote_accounts, delinquent_vote_accounts): (
            Vec<RpcVoteAccountInfo>,
            Vec<RpcVoteAccountInfo>,
        ) = vote_accounts
            .iter()
            .filter_map(|(vote_pubkey, (activated_stake, account))| {
                if let Some(filter_by_vote_pubkey) = filter_by_vote_pubkey
                    && *vote_pubkey != filter_by_vote_pubkey
                {
                    return None;
                }

                let vote_state_view = account.vote_state_view();
                let last_vote = vote_state_view.last_voted_slot().unwrap_or(0);
                let num_epoch_credits = vote_state_view.num_epoch_credits();
                let epoch_credits = vote_state_view
                    .epoch_credits_iter()
                    .skip(
                        num_epoch_credits
                            .saturating_sub(MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY),
                    )
                    .map(Into::into)
                    .collect();

```

**File:** runtime/src/stakes.rs (L298-304)
```rust
            if solana_vote_program::check_id(account.owner()) {
                if VoteStateVersions::is_correct_size_and_initialized(account.data())
                    && let Ok(vote_account) =
                        VoteAccount::try_from(create_account_shared_data(account))
                {
                    vote_accounts.insert(*pubkey, (0, vote_account));
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

**File:** runtime/src/bank.rs (L1781-1793)
```rust
        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, &filtered_distribution_vote_accounts);
        let (rewards_calculation, update_rewards_with_thread_pool_time_us) =
```

**File:** cli/src/vote.rs (L993-1075)
```rust
    let required_balance = rpc_client
        .get_minimum_balance_for_rent_exemption(VoteStateV4::size_of())
        .await?
        .max(1);
    let amount = SpendAmount::Some(required_balance);

    let fee_payer = config.signers[fee_payer];
    let nonce_authority = config.signers[nonce_authority];
    let space = VoteStateV4::size_of() as u64;

    let compute_unit_limit = match blockhash_query {
        BlockhashQuery::Static(_) | BlockhashQuery::Validated(_, _) => ComputeUnitLimit::Default,
        BlockhashQuery::Rpc(_) => ComputeUnitLimit::Simulated,
    };

    // Derive BLS keypair from the identity keypair and generate proof of
    // possession for VoteInitV2.
    let bls_data = if use_v2 {
        let derived_bls_keypair =
            BLSKeypair::derive_from_signer(identity_account, BLS_KEYPAIR_DERIVE_SEED).map_err(
                |e| CliError::BadParameter(format!("Failed to derive BLS keypair: {e}")),
            )?;
        let (bls_pubkey, bls_proof_of_possession) =
            create_bls_proof_of_possession(&vote_account_address, &derived_bls_keypair);
        Some((bls_pubkey, bls_proof_of_possession))
    } else {
        None
    };

    let build_message = |lamports| {
        let mut create_vote_account_config = CreateVoteAccountConfig {
            space,
            ..CreateVoteAccountConfig::default()
        };
        let to = if let Some(seed) = seed {
            create_vote_account_config.with_seed = Some((&vote_account_pubkey, seed));
            &vote_account_address
        } else {
            &vote_account_pubkey
        };

        let ixs = if use_v2 {
            let (bls_pubkey, bls_proof_of_possession) = bls_data.unwrap();
            let vote_init = VoteInitV2 {
                node_pubkey: identity_pubkey,
                authorized_voter: authorized_voter.unwrap_or(identity_pubkey),
                authorized_voter_bls_pubkey: bls_pubkey,
                authorized_voter_bls_proof_of_possession: bls_proof_of_possession,
                authorized_withdrawer,
                inflation_rewards_commission_bps: inflation_rewards_commission_bps
                    .or_else(|| commission.map(|c| (c as u16).saturating_mul(100))) // u16::MAX > u8::MAX * 100
                    .unwrap_or(10000),
                block_revenue_commission_bps: block_revenue_commission_bps.unwrap_or(10000),
            };
            let inflation_rewards_collector_key = inflation_rewards_collector
                .copied()
                .unwrap_or(vote_account_address);
            let block_revenue_collector_key =
                block_revenue_collector.copied().unwrap_or(identity_pubkey);
            vote_instruction::create_account_with_config_v2(
                &config.signers[0].pubkey(),
                to,
                &vote_init,
                &inflation_rewards_collector_key,
                &block_revenue_collector_key,
                lamports,
                create_vote_account_config,
            )
        } else {
            let vote_init = VoteInit {
                node_pubkey: identity_pubkey,
                authorized_voter: authorized_voter.unwrap_or(identity_pubkey),
                authorized_withdrawer,
                commission: commission.unwrap_or(100),
            };
            vote_instruction::create_account_with_config(
                &config.signers[0].pubkey(),
                to,
                &vote_init,
                lamports,
                create_vote_account_config,
            )
        };
```
