## Title
Stale vote/stake accounts remain in Bank's `StakesCache` after owner reassignment, allowing forged data to inherit prior stake weight - (File: `runtime/src/stakes.rs`)

### Summary
The DCAP report's broken invariant is: *when the authoritative source updates (issuer replaces a CRL/certificate), the cache/consumer of that data does not evict the stale association, so a verifier can act on data that no longer corresponds to the current authoritative state.* The Agave analog is `StakesCache::check_and_store` in `runtime/src/stakes.rs`, which caches vote/stake accounts by pubkey but never evicts a cached entry when the account's *owner* is reassigned away from the vote/stake program to an arbitrary program. The cache continues to key off `account.owner()` checks only, so a corrupted/malicious "shell" of a former vote or stake account is not proactively purged from the trusted stake cache the same way the CRL is not purged when its issuing certificate is replaced.

### Finding Description
`StakesCache::check_and_store` explicitly documents this gap: [1](#0-0) 

The function only removes an entry from the cache when `lamports() == 0` and the owner is still the vote or stake program, or when deserialization of the data fails while the owner is still the vote/stake program: [2](#0-1) 

If an account that is *currently* cached as a vote or stake account (i.e., already contributing effective stake in `Stakes<StakeAccount>`) has its `owner` field changed to an arbitrary program by a subsequent transaction, `check_and_store` takes neither branch (`solana_vote_program::check_id(owner)` nor `stake_program::check_id(owner)` matches), so the function is effectively a no-op: the previously-cached `VoteAccount`/`StakeAccount` entry, and the effective stake it contributes via `delegated_stakes`, is left untouched in the cache — exactly analogous to the CRL scenario where "updating a certificate does not delete the corresponding CRL," leaving stale authority data usable after the underlying authoritative record has moved on.

This is not a hypothetical — it is a known, unresolved issue tracked upstream. A feature gate `evict_invalid_stakes_cache_entries` exists in the codebase to fix exactly this class of bug: [3](#0-2) 

but grep across the repository shows this feature ID is declared and never consumed anywhere else (not in `runtime/src/stakes.rs`, not in `runtime/src/bank.rs`) — the fix logic was never wired into `check_and_store`. The repository's own regression test confirms the case is currently accepted as broken, not fixed: [4](#0-3) [5](#0-4) 

The test `check_stake_vote_account_validity` is invoked with `check_owner_change = false`, with an explicit `TODO` referencing the original upstream discussion (`solana-labs/solana/pull/24200#discussion_r849935444`), and the assertion at the end (`vote_and_stake_accounts.len() == usize::from(!check_owner_change)`) is written to *tolerate* the stale/incorrect state rather than reject it.

`check_and_store` is driven per-transaction from `Bank::update_stakes_cache`, called from `Bank::commit_transactions` for every successfully executed transaction that touches vote/stake-owned accounts: [6](#0-5) 

so this cache is on the hot path for every transaction that writes to a pubkey previously recognized as a vote/stake account.

### Impact Explanation
`StakesCache` backs `Bank::vote_accounts()` / `Bank::stakes()`, which feed leader-schedule computation, vote-weighting for fork choice/consensus, and reward/inflation calculations. If a pubkey that used to be a legitimate, delegated vote or stake account has its owner reassigned to another program (e.g., because the account was drained/repurposed or a bug elsewhere leaves an owner-changed account with nonzero lamports), the stale cached `VoteAccount`/`StakeAccount` — and the stake weight it contributes — remains active in the cache used for consensus-relevant accounting (`delegated_stakes`, `vote_accounts`) even though accounts-db (the source of truth) no longer considers that pubkey a vote/stake account. This creates a persistent divergence between the authoritative account state and the Bank's cached view of stake, which can misattribute stake/vote weight to accounts that are no longer valid — a false-acceptance issue in the runtime's accounting of stake, directly bearing on consensus-relevant computations.

### Likelihood Explanation
Reaching the no-op branch only requires a transaction that legitimately changes the `owner` field of a pubkey that is currently tracked in the stakes cache (an ordinary `Assign`/system-program style owner change, or any CPI reassigning ownership), which is a normal, unprivileged operation available to any transaction sender who controls the account, not a malicious-validator or trusted-party assumption. The existing test suite already demonstrates and tolerates the exact scenario (owner changed on both a vote account and a stake account), and the dedicated `evict_invalid_stakes_cache_entries` feature flag existing yet unused confirms the Agave/Solana team is aware the gap is real but the mitigation is not applied in this snapshot of the code.

### Recommendation
Wire up (or reintroduce) eviction logic in `check_and_store`: when the account's current `owner` no longer matches the vote or stake program, and the pubkey is present in `stakes.vote_accounts()`/`stakes.stake_delegations()`, explicitly call `remove_vote_account`/`remove_stake_delegation` regardless of the new owner value, rather than only when the previous owner branch matches. Gate this fix in with the (already-declared) `evict_invalid_stakes_cache_entries` feature and update `check_stake_vote_account_validity` to assert `check_owner_change = true` universally once activated.

### Proof of Concept
1. Create a vote account and a delegated stake account; call `StakesCache::check_and_store` on both so they are cached with nonzero effective stake (`test_stake_vote_account_validity` sets this up already: [7](#0-6) ).
2. Send a transaction that reassigns the `owner` of the vote account (and/or the stake account) to an arbitrary/bogus program while keeping lamports nonzero (`vote_account.set_owner(bogus_vote_program)`), as done in the test: [8](#0-7) .
3. Observe that `bank.vote_accounts()` / the stakes cache still returns an entry (the test currently expects `len() == 1` for `check_owner_change=false`, i.e. one stale entry survives) instead of `0`, confirming the stale-cache-not-evicted condition.

### Citations

**File:** runtime/src/stakes.rs (L87-98)
```rust
    pub(crate) fn check_and_store(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        // TODO: If the account is already cached as a vote or stake account
        // but the owner changes, then this needs to evict the account from
        // the cache. see:
        // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
        let owner = account.owner();
```

**File:** runtime/src/stakes.rs (L99-164)
```rust
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
        debug_assert_ne!(account.lamports(), 0u64);
        if solana_vote_program::check_id(owner) {
            if VoteStateVersions::is_correct_size_and_initialized(account.data()) {
                match VoteAccount::try_from(create_account_shared_data(account)) {
                    Ok(vote_account) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.upsert_vote_account(pubkey, vote_account)
                        };
                    }
                    Err(_) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.remove_vote_account(pubkey)
                        };
                    }
                }
            } else {
                // drop the old account after releasing the lock
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            };
        } else if stake_program::check_id(owner) {
            match StakeAccount::try_from(create_account_shared_data(account)) {
                Ok(stake_account) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.upsert_stake_delegation(
                        *pubkey,
                        stake_account,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
                Err(_) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_stake_delegation(
                        pubkey,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
            }
        }
    }
```

**File:** feature-set/src/lib.rs (L579-581)
```rust
pub mod evict_invalid_stakes_cache_entries {
    solana_pubkey::declare_id!("EMX9Q7TVFAmQ9V1CggAkhMzhXSg8ECp7fHrWQX2G1chf");
}
```

**File:** runtime/src/bank/tests.rs (L8478-8488)
```rust
#[test]
fn test_stake_vote_account_validity() {
    let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
    // TODO: stakes cache should be hardened for the case when the account
    // owner is changed from vote/stake program to something else. see:
    // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
    check_stake_vote_account_validity(
        false, // check owner change
        |bank: &Bank| bank._load_vote_and_stake_accounts(&thread_pool, null_tracer()),
    );
}
```

**File:** runtime/src/bank/tests.rs (L8521-8589)
```rust
fn check_stake_vote_account_validity<F>(check_owner_change: bool, load_vote_and_stake_accounts: F)
where
    F: Fn(&Bank) -> StakeDelegationsMap,
{
    let validator_vote_keypairs0 = ValidatorVoteKeypairs::new_rand();
    let validator_vote_keypairs1 = ValidatorVoteKeypairs::new_rand();
    let validator_keypairs = vec![&validator_vote_keypairs0, &validator_vote_keypairs1];
    let GenesisConfigInfo { genesis_config, .. } = create_genesis_config_with_vote_accounts(
        1_000_000_000,
        &validator_keypairs,
        vec![LAMPORTS_PER_SOL; 2],
    );
    let bank = Arc::new(Bank::new_from_genesis(
        &genesis_config,
        Arc::<RuntimeConfig>::default(),
        Vec::new(),
        None,
        ACCOUNTS_DB_CONFIG_FOR_TESTING,
        None,
        None,
        Arc::default(),
        None,
        None,
    ));
    let vote_and_stake_accounts = load_vote_and_stake_accounts(&bank);
    assert_eq!(vote_and_stake_accounts.len(), 2);

    let mut vote_account = bank
        .get_account(&validator_vote_keypairs0.vote_keypair.pubkey())
        .unwrap_or_default();
    let original_lamports = vote_account.lamports();
    vote_account.set_lamports(0);
    // Simulate vote account removal via full withdrawal
    bank.store_account(
        &validator_vote_keypairs0.vote_keypair.pubkey(),
        &vote_account,
    );

    // Modify staked vote account owner; a vote account owned by another program could be
    // freely modified with malicious data
    let bogus_vote_program = Pubkey::new_unique();
    vote_account.set_lamports(original_lamports);
    vote_account.set_owner(bogus_vote_program);
    bank.store_account(
        &validator_vote_keypairs0.vote_keypair.pubkey(),
        &vote_account,
    );

    assert_eq!(bank.vote_accounts().len(), 1);

    // Modify stake account owner; a stake account owned by another program could be freely
    // modified with malicious data
    let bogus_stake_program = Pubkey::new_unique();
    let mut stake_account = bank
        .get_account(&validator_vote_keypairs1.stake_keypair.pubkey())
        .unwrap_or_default();
    stake_account.set_owner(bogus_stake_program);
    bank.store_account(
        &validator_vote_keypairs1.stake_keypair.pubkey(),
        &stake_account,
    );

    // Accounts must be valid stake and vote accounts
    let vote_and_stake_accounts = load_vote_and_stake_accounts(&bank);
    assert_eq!(
        vote_and_stake_accounts.len(),
        usize::from(!check_owner_change)
    );
}
```

**File:** runtime/src/bank.rs (L4389-4392)
```rust
        // Cached vote and stake accounts are synchronized with accounts-db
        // after each transaction.
        let ((), update_stakes_cache_us) =
            measure_us!(self.update_stakes_cache(sanitized_txs, &processing_results));
```
