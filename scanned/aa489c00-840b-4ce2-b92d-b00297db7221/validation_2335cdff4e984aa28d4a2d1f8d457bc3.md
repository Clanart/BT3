## Title
Unbounded permissionless vote-account registration inflates the `Stakes` vote-account cache, causing an unbounded per-block scan in tower fork-choice (`collect_vote_lockouts`) - (File: `runtime/src/stakes.rs`, `core/src/consensus.rs`)

### Summary
The CoreDAO bug is a classic "unbounded permissionless registration feeds an unbounded hot-loop" pattern: anyone can call `register()` with no cap on `candidateSet.length`, and that array is then iterated in full by `turnRound()` on every round, so the loop's gas cost grows without bound. Agave has a structurally identical primitive: anyone can create a vote account (`VoteInit`) for the cost of the rent-exempt reserve, and there is no limit on how many live vote accounts can exist in `Stakes.vote_accounts`. That map is iterated in full by `Tower::collect_vote_lockouts`, which runs on the hot consensus/fork-choice path, not once per epoch like CoreDAO's `turnRound()`, but on essentially every bank-freeze during replay.

### Finding Description
`StakesCache::check_and_store` unconditionally inserts *any* vote-program-owned account with non-zero lamports into `vote_accounts`, with no limit on the number of entries: [1](#0-0) 

This cache backs `Bank::vote_accounts()`: [2](#0-1) 

`Tower::collect_vote_lockouts` — invoked to compute fork weights/lockouts for every frozen bank during replay — iterates the *entire* `VoteAccountsHashMap` unconditionally, before the zero-stake short-circuit inside the loop body: [3](#0-2) 

The only size guard that exists in the codebase, `VoteAccounts::clone_and_filter_for_vat` with `MAX_ALPENGLOW_VOTE_ACCOUNTS`, is applied *only* to the epoch-boundary reward-distribution/VAT snapshot used for Alpenglow certificate weighting: [4](#0-3) [5](#0-4) 

It does **not** bound the raw `stakes_cache`/`vote_accounts` map that `Bank::vote_accounts()` exposes, nor does it bound the tower/fork-choice path (`collect_vote_lockouts`), which runs on every slot regardless of whether Alpenglow is active. `VoteAccounts::staked_nodes()` similarly walks every entry in the map (not just staked ones) to compute the non-zero count before allocating: [6](#0-5) 

Because vote-account creation only costs the rent-exempt reserve for a `VoteStateV3`/`VoteStateV4`-sized account (recoverable later via `Withdraw`), and there is no protocol-level cap analogous to CoreDAO's `CANDIDATE_COUNT_LIMIT`, an attacker can cheaply mint an unbounded number of zero-stake (or minimal-stake) vote accounts. Every one of them stays resident in `Stakes.vote_accounts` forever (removal only happens when the account's lamports drop to 0), so the set can only grow, exactly mirroring the CoreDAO `candidateSet` growth pattern.

### Impact Explanation
`collect_vote_lockouts` is on the validator's core replay/fork-choice path, executed for every frozen bank across the `ProgressMap`, i.e., far more frequently than CoreDAO's per-round `turnRound()`. As the attacker-inflated `vote_accounts` map grows, the per-bank iteration cost (hash map traversal, per-entry `TowerVoteState` construction skipped only after the stake check) grows linearly with the number of registered vote accounts, degrading replay throughput cluster-wide and, at sufficient scale, threatening to make block processing/replay unable to keep up with the network — a non-RPC remote resource-exhaustion / consensus-degradation vector reachable by any unprivileged sender of ordinary `VoteInit` transactions.

### Likelihood Explanation
The attacker primitive requires no special privileges: only fee-payer funds sufficient to cover the vote-account rent-exempt reserve per account, plus the transaction fee for `InitializeAccount`. Because the reserve is later withdrawable, capital is not destroyed, only temporarily locked, so the attack can be repeated/amplified cheaply and the map's growth is monotonic in practice (no natural expiry).

### Recommendation
Introduce a cap analogous to CoreDAO's fix: bound the number of vote accounts retained/iterated in `Stakes.vote_accounts` (or at minimum ensure `collect_vote_lockouts` and other hot per-block consensus paths operate against a bounded/pre-filtered snapshot, similarly to how `clone_and_filter_for_vat` bounds the VAT snapshot), and/or require an economically meaningful minimum stake/bond to keep a vote account resident in the cache, evicting unstaked or below-threshold vote accounts more aggressively than "lamports == 0".

### Proof of Concept
1. Attacker generates N (e.g., hundreds of thousands) of keypairs and, for each, submits a `VoteInit` instruction creating a new vote account funded to the rent-exempt minimum.
2. Each such account passes `VoteStateVersions::is_correct_size_and_initialized` and is inserted into `Stakes.vote_accounts` via `StakesCache::check_and_store` (`runtime/src/stakes.rs:87-127`) — no global count check exists.
3. On every subsequent bank freeze, `Tower::collect_vote_lockouts` (`core/src/consensus.rs:425-437`) iterates the now N-sized `vote_accounts` map for every validator replaying the ledger, even though the vast majority have zero stake and are skipped only inside the loop body (after being visited).
4. Repeating step 1 lets the attacker grow N arbitrarily since created accounts are never automatically evicted while they retain lamports, driving per-bank fork-choice computation cost upward without bound, unlike the bounded `clone_and_filter_for_vat` path used only for Alpenglow reward distribution.

### Citations

**File:** runtime/src/stakes.rs (L87-127)
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
```

**File:** runtime/src/bank.rs (L1781-1791)
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
```

**File:** runtime/src/bank.rs (L5796-5799)
```rust
    pub fn vote_accounts(&self) -> Arc<VoteAccountsHashMap> {
        let stakes = self.stakes_cache.stakes();
        Arc::from(stakes.vote_accounts())
    }
```

**File:** core/src/consensus.rs (L425-437)
```rust
        let total_votes = vote_accounts
            .values()
            .filter(|(voted_stake, _)| *voted_stake != 0)
            .map(|(_, account)| account.vote_state_view().votes_len())
            .sum();
        // Flat list of intervals of lockouts of the form {voter, start, end}.
        let mut lockout_intervals = LockoutIntervals::with_capacity(total_votes);
        let mut my_latest_landed_vote = None;
        for (&key, (voted_stake, account)) in vote_accounts.iter() {
            let voted_stake = *voted_stake;
            if voted_stake == 0 {
                continue;
            }
```

**File:** vote/src/vote_account.rs (L176-197)
```rust
    pub fn staked_nodes(&self) -> Arc<HashMap</*node_pubkey:*/ Pubkey, /*stake:*/ u64>> {
        self.staked_nodes
            .get_or_init(|| {
                // Count non-zero stake accounts for optimal capacity allocation
                let non_zero_count = self
                    .vote_accounts
                    .values()
                    .filter(|(stake, _)| *stake != 0)
                    .count();

                let mut staked_nodes = HashMap::with_capacity(non_zero_count);

                for (stake, vote_account) in self.vote_accounts.values() {
                    if *stake != 0 {
                        *staked_nodes.entry(*vote_account.node_pubkey()).or_default() += *stake;
                    }
                }

                Arc::new(staked_nodes)
            })
            .clone()
    }
```

**File:** vote/src/vote_account.rs (L212-244)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
        }

        let valid_len = entries_to_sort.len();
        if entries_to_sort.len() > max_vote_accounts {
            // Find the cutoff stake using partial sort (more efficient than full sort).
            let (_, cutoff_entry, _) =
                entries_to_sort.select_nth_unstable_by(max_vote_accounts, |a, b| b.2.cmp(&a.2));
            let floor_stake = cutoff_entry.2;

            // Per SIMD 357, we remove all vote accounts with stake smaller or equal to
            // the first truncated one.
            entries_to_sort.retain(|(_, _, stake)| *stake > floor_stake);
        }
```
