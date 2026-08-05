### Title
Unbounded, permissionlessly-inflatable iteration in `getVoteAccounts` RPC handler enables single-client compute/latency DoS - (File: `rpc/src/rpc.rs`)

### Summary
The C4 report describes a `for` loop with no pagination/limit that scans an attacker-inflatable list on every call, allowing a single unprivileged caller to trigger unbounded work and DoS the node. The Agave analog is `JsonRpcRequestProcessor::get_vote_accounts` in [1](#0-0) , which iterates over the *entire* `bank.vote_accounts()` map on every RPC call with no cap, no pagination, and no offset/limit parameters — unlike sibling RPC methods in the same file that explicitly cap their scan/return size (`get_slot_leaders` enforces `MAX_GET_SLOT_LEADERS` [2](#0-1) ; `get_recent_performance_samples` enforces `PERFORMANCE_SAMPLES_LIMIT` [3](#0-2) ).

### Finding Description
`bank.vote_accounts()` returns `stakes_cache.stakes().vote_accounts()`, which is the full set of vote-program-owned accounts tracked by the bank's `StakesCache`, not just staked/active validators: [4](#0-3) .

`StakesCache::check_and_store` inserts **any** account owned by the vote program into this map as soon as it exists with nonzero lamports and a correctly-sized/initialized vote state, entirely independent of whether any stake is delegated to it: [5](#0-4) . It is only removed when the account's lamports drop to zero: [6](#0-5) .

Creating a vote account is a fully permissionless, unprivileged operation available to any funded account (System Program `CreateAccount` + Vote Program `InitializeAccount`) — it does not require any stake delegation, validator identity, or admin approval. Each such account, once created, becomes a permanent entry in `vote_accounts` (until the owner deliberately drains/closes it).

The RPC handler `get_vote_accounts` then does an `O(n)` scan of this entire map on **every single call**, with no limit, no offset, and no pagination: [7](#0-6) 

For each entry it deserializes the vote state view, computes `last_voted_slot`, and copies/collects the epoch-credits history (bounded per-account by `MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY`, but that bound is per-account, not global). The `filter_by_vote_pubkey` parameter narrows the *returned* result but does **not** short-circuit the scan — the `filter_map`/`partition` chain still visits every entry in `vote_accounts` regardless of the filter, so passing a specific `vote_pubkey` does not reduce the cost of the call.

This is structurally identical to the reported `BatchRequests.sendWithdrawalRequests` bug: an unbounded loop over a list that grows via a cheap, permissionless action, executed synchronously by a single, unauthenticated caller, with no offset/limit "pagination" mechanism to cap per-call cost — exactly the mitigation the original report recommended and that other Agave RPC endpoints in the same file already implement but this one omits.

### Impact Explanation
An attacker who creates a large number of vote accounts (cheap: only rent-exemption lamports for a vote-program-owned account, no stake required) permanently inflates `bank.vote_accounts()`. From then on, every `getVoteAccounts` RPC call issued by *any* client (not just the attacker) does `O(n)` work over the full, ever-growing set on the RPC node handling the request — deserializing vote-state views and copying epoch-credit vectors for each entry. Because the underlying `vote_accounts` map is a persistent bank-level structure, this degradation is not transient: it affects all future calls until the bloating accounts are removed, and the cost scales purely with attacker-controlled account count, not attacker-provided rate. A single low-rate `getVoteAccounts` request from one client is sufficient to trigger the full, unbounded scan, matching the "single-client low-rate RPC crash/degradation" impact category.

### Likelihood Explanation
High: creating vote-program-owned accounts requires no special privilege, no validator identity, and no stake, only enough lamports to satisfy rent exemption for a vote account — a low, one-time cost that can be repeated arbitrarily many times by any wallet. Once created, the bloat is durable in the bank's `StakesCache` for the account's lifetime. `getVoteAccounts` is a standard, widely enabled Full RPC method (no special flag beyond default RPC enablement), making the endpoint broadly reachable.

### Recommendation
Add offset/limit (pagination) parameters to `RpcGetVoteAccountsConfig`/`get_vote_accounts`, mirroring the bounded pattern already used by `get_slot_leaders` (`MAX_GET_SLOT_LEADERS`) and `get_recent_performance_samples` (`PERFORMANCE_SAMPLES_LIMIT`) in the same file. When `filter_by_vote_pubkey` is set, look the entry up directly (e.g., via `bank.get_vote_account`) instead of scanning the whole map. Consider bounding or rate-limiting the number of vote accounts considered per call independent of total vote-account count, and evaluate whether `vote_accounts()` cache pruning (e.g. for zero-stake, never-voted accounts) is warranted at the bank level.

### Proof of Concept
1. Fund a wallet and repeatedly submit `CreateAccount` (System Program) + `InitializeAccount` (Vote Program) transactions to create N (e.g. tens of thousands) vote accounts with no stake delegated to them. This is cheap and fully permissionless — no admin/validator role required.
2. Each created account is inserted into `stakes_cache.vote_accounts()` via `StakesCache::check_and_store` [8](#0-7)  and stays there indefinitely.
3. Issue a single `getVoteAccounts` JSON-RPC request (even from an unrelated client) against a node that has processed these transactions.
4. `get_vote_accounts` in `rpc/src/rpc.rs` iterates the now-bloated `vote_accounts` map in full, with cost proportional to N and no way to bound or paginate the scan: [9](#0-8) 
5. As N grows, this single call's CPU time and memory allocation (`current_vote_accounts`/`delinquent_vote_accounts` `Vec`s, per-entry vote-state deserialization) grows unboundedly, degrading or blocking the RPC node's thread handling that request and any others contending for the same resources.

Note: I did not have direct access to a live cluster or benchmark harness to measure the exact wall-clock cost per additional vote account or confirm the precise rent-exemption lamport cost in this codebase snapshot; this assessment is based on static code-path analysis of `rpc/src/rpc.rs`, `runtime/src/bank.rs`, and `runtime/src/stakes.rs`.

### Citations

**File:** rpc/src/rpc.rs (L1155-1158)
```rust
    fn get_vote_accounts(
        &self,
        config: Option<RpcGetVoteAccountsConfig>,
    ) -> Result<RpcVoteAccountStatus> {
```

**File:** rpc/src/rpc.rs (L1171-1230)
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

                Some(RpcVoteAccountInfo {
                    vote_pubkey: vote_pubkey.to_string(),
                    node_pubkey: vote_state_view.node_pubkey().to_string(),
                    activated_stake: *activated_stake,
                    commission: if commission_rate_in_basis_points {
                        // Derive percent from native bps, clamping to u8::MAX.
                        let bps = vote_state_view.inflation_rewards_commission();
                        bps.div_ceil(100).min(u8::MAX as u16) as u8
                    } else {
                        vote_state_view.commission()
                    },
                    inflation_rewards_commission_bps: Some(if commission_rate_in_basis_points {
                        vote_state_view.inflation_rewards_commission()
                    } else {
                        vote_state_view.commission() as u16 * 100
                    }),
                    root_slot: vote_state_view.root_slot().unwrap_or(0),
                    epoch_credits,
                    epoch_vote_account: epoch_vote_accounts.contains_key(vote_pubkey),
                    last_vote,
                })
            })
            .partition(|vote_account_info| {
                if bank.slot() >= delinquent_validator_slot_distance {
                    vote_account_info.last_vote > bank.slot() - delinquent_validator_slot_distance
                } else {
                    vote_account_info.last_vote > 0
                }
            });
```

**File:** rpc/src/rpc.rs (L3110-3115)
```rust
            let limit = limit as usize;
            if limit > MAX_GET_SLOT_LEADERS {
                return Err(Error::invalid_params(format!(
                    "Invalid limit; max {MAX_GET_SLOT_LEADERS}"
                )));
            }
```

**File:** rpc/src/rpc.rs (L3689-3695)
```rust
            let limit = limit.unwrap_or(PERFORMANCE_SAMPLES_LIMIT);

            if limit > PERFORMANCE_SAMPLES_LIMIT {
                return Err(Error::invalid_params(format!(
                    "Invalid limit; max {PERFORMANCE_SAMPLES_LIMIT}"
                )));
            }
```

**File:** runtime/src/bank.rs (L5794-5799)
```rust
    /// current vote accounts for this bank along with the stake
    ///   attributed to each account
    pub fn vote_accounts(&self) -> Arc<VoteAccountsHashMap> {
        let stakes = self.stakes_cache.stakes();
        Arc::from(stakes.vote_accounts())
    }
```

**File:** runtime/src/stakes.rs (L99-106)
```rust
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
```

**File:** runtime/src/stakes.rs (L117-143)
```rust
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
```
