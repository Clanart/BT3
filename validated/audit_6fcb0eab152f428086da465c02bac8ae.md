## Title
Unbounded, permissionless growth of the live `vote_accounts` map lets a low-cost attacker degrade `getVoteAccounts` and every code path that iterates `Bank::vote_accounts()` - (File: `runtime/src/stakes.rs`, `runtime/src/bank.rs`, `rpc/src/rpc.rs`)

### Summary
The external report's broken invariant is: an unprivileged actor can add itself to a persistent, iterated-over collection (`s.genesisValidators`) for a cost that is far below the value the collateral requirement is supposed to enforce, and every future operation that loops over that collection (`LibStaking.depositWithConfirm`) pays for the spam. The Agave analog is the bank's live `vote_accounts` map inside `Stakes`/`StakesCache`. Creating a vote account only requires paying for rent-exemption of a `VoteStateV4` account; it carries **no minimum-stake requirement** (unlike stake *delegation*, which is protected by `get_minimum_delegation`). This unfiltered, un-capped map is what `Bank::vote_accounts()` returns, and it is iterated in full by hot RPC paths such as `getVoteAccounts`, degrading the RPC surface as the map grows.

### Finding Description
`get_minimum_delegation` in `runtime/src/stake_utils.rs` shows that Agave does gate *stake delegation* with a minimum amount (1 SOL once the v5 stake program feature is active, or 1 lamport before that): [1](#0-0) 

However, there is no equivalent gate on simply *creating a vote account* and having it registered in the bank's live stake/vote bookkeeping. `Stakes::stake_delegations_vec`/`vote_accounts()` and the underlying `VoteAccountsHashMap` are populated unconditionally whenever a vote-program-owned account is stored, via `Bank::update_stakes_cache`, which is invoked for every successfully processed transaction that touches a vote/stake-owned account: [2](#0-1) 

The resulting map is exposed unfiltered as `Bank::vote_accounts()`: [3](#0-2) 

Crucially, this raw map is *not* the same as the VAT-filtered, size-capped snapshot (`MAX_ALPENGLOW_VOTE_ACCOUNTS`, capped at 2000) that is used for `epoch_stakes`/leader-schedule computation: [4](#0-3) 
That cap only applies to the VAT-filtered "distribution" set used for epoch stakes and rewards; it does not bound the live `stakes_cache.stakes().vote_accounts()` map that backs `Bank::vote_accounts()`.

The unbounded map is fully iterated on every call to the `getVoteAccounts` RPC method, which is a normal, permissionless, single-client RPC endpoint: [5](#0-4) [6](#0-5) 

Other hot paths iterate the same unfiltered structure, e.g. `SnapshotMinimizer::get_vote_accounts` and `Stakes::highest_staked_node`: [7](#0-6) [8](#0-7) [9](#0-8) 

None of these guards enforce a minimum stake or a cap on the number of distinct vote accounts that can exist; they only filter which accounts are *counted toward epoch/leader-schedule stake*, not how many can be created and iterated in the live stakes cache.

### Impact Explanation
Because vote account creation costs only the rent-exemption for a `VoteStateV4`-sized account (a comparatively small, and in principle reclaimable via `Withdraw`, amount) and requires no delegated stake, an attacker can create arbitrarily many vote accounts over time at the marginal cost of transaction fees plus rent, mirroring the report's "the only cost hindering this spamming ... is the gas cost." Every such account permanently inflates the `vote_accounts` `HashMap` inside `Stakes`, which is looped over in full on every `getVoteAccounts` RPC call and in other bank-wide scans (snapshot minimization, `highest_staked_node`). This causes progressively worse latency/CPU cost for that RPC method and for any validator's or RPC node's iteration over `bank.vote_accounts()`, which matches the accepted "single-client low-rate RPC crash/degradation" impact category - a legitimate client repeatedly calling `getVoteAccounts` (or any node serving that RPC) pays an ever-increasing cost caused entirely by unprivileged, permissionless account creation, with no attacker collusion or trusted-role assumption required.

### Likelihood Explanation
Likelihood is moderate: the barrier is only the rent-exempt balance for a vote account (much smaller than a full stake delegation) and standard transaction fees, and there is no on-chain limit on the number of vote accounts one identity/authority can create. This is analogous to, but weaker than, the original report because Solana's cap (`MAX_ALPENGLOW_VOTE_ACCOUNTS`) protects the *consensus-relevant* stakes computation, leaving only the RPC/bookkeeping surface exposed - which is exactly the class of impact this task scopes as valid (RPC degradation), while explicitly excluding consensus-affecting or trusted-validator paths.

### Recommendation
Cap or paginate the map returned by `Bank::vote_accounts()`/consumed by `getVoteAccounts`, or require a minimum balance/stake for a vote account to be included in the live-iterated set (mirroring the existing VAT filter already applied to the epoch-stakes distribution snapshot). Alternatively, add server-side rate limiting/pagination to the `getVoteAccounts` RPC method so that the cost of a single call does not scale unbounded with the number of on-chain vote accounts.

### Proof of Concept
1. Repeatedly submit `create_account_with_config`/`VoteInit` transactions (see `cli/src/vote.rs::process_create_vote_account`, `runtime/src/stake_utils.rs`) to create N vote accounts, each funded with only the rent-exempt minimum for `VoteStateV4::size_of()` (no delegated stake needed): [10](#0-9) 
2. Each such account is unconditionally inserted into the bank's live `vote_accounts` map via `update_stakes_cache`/`check_and_store` on transaction success.
3. Call the `getVoteAccounts` RPC method against a node; observe that its runtime scales linearly with the total number of vote accounts ever created network-wide, since it iterates the entire unfiltered `bank.vote_accounts()` map on every invocation: [11](#0-10) 
4. Repeating step 1 at scale progressively degrades this RPC path for any client calling it, at a cost to the attacker limited to rent + transaction fees per account.

### Citations

**File:** runtime/src/stake_utils.rs (L15-27)
```rust
/// The minimum stake amount that can be delegated, in lamports.
/// When this feature is added, it will be accompanied by an upgrade to the BPF Stake Program.
/// NOTE: This is also used to calculate the minimum balance of a delegated stake account,
/// which is the rent exempt reserve _plus_ the minimum stake delegation.
#[inline(always)]
pub fn get_minimum_delegation(upgrade_bpf_stake_program_to_v5_is_active: bool) -> u64 {
    if upgrade_bpf_stake_program_to_v5_is_active {
        const MINIMUM_DELEGATION_SOL: u64 = 1;
        MINIMUM_DELEGATION_SOL * LAMPORTS_PER_SOL
    } else {
        1
    }
}
```

**File:** runtime/src/bank.rs (L2648-2656)
```rust
    fn maybe_burn_vat_from_staked_accounts(&mut self, epoch_stakes: &VersionedEpochStakes) {
        let feature_snapshot = self.feature_set.snapshot();
        if !feature_snapshot.alpenglow {
            return;
        }

        let vat_to_burn_per_epoch = self.vat_to_burn_per_epoch();
        let vote_accounts = epoch_stakes.stakes().vote_accounts();
        debug_assert!(vote_accounts.len() <= 2000);
```

**File:** runtime/src/bank.rs (L5756-5792)
```rust
    fn update_stakes_cache(
        &self,
        txs: &[impl SVMMessage],
        processing_results: &[TransactionProcessingResult],
    ) {
        debug_assert_eq!(txs.len(), processing_results.len());
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        txs.iter()
            .zip(processing_results)
            .filter_map(|(tx, processing_result)| {
                processing_result
                    .processed_transaction()
                    .map(|processed_tx| (tx, processed_tx))
            })
            .filter_map(|(tx, processed_tx)| {
                processed_tx
                    .executed_transaction()
                    .map(|executed_tx| (tx, executed_tx))
            })
            .filter(|(_, executed_tx)| executed_tx.was_successful())
            .flat_map(|(tx, executed_tx)| {
                let num_account_keys = tx.account_keys().len();
                let loaded_tx = &executed_tx.loaded_transaction;
                loaded_tx.accounts.iter().take(num_account_keys)
            })
            .for_each(|(pubkey, account)| {
                // note that this could get timed to: self.rc.accounts.accounts_db.stats.stakes_cache_check_and_store_us,
                //  but this code path is captured separately in ExecuteTimingType::UpdateStakesCacheUs
                self.stakes_cache.check_and_store(
                    pubkey,
                    account,
                    new_warmup_cooldown_rate_epoch,
                    use_fixed_point_stake_math,
                );
            });
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

**File:** rpc/src/rpc.rs (L1155-1183)
```rust
    fn get_vote_accounts(
        &self,
        config: Option<RpcGetVoteAccountsConfig>,
    ) -> Result<RpcVoteAccountStatus> {
        let config = config.unwrap_or_default();

        let filter_by_vote_pubkey = if let Some(ref vote_pubkey) = config.vote_pubkey {
            Some(verify_pubkey(vote_pubkey)?)
        } else {
            None
        };

        let bank = self.bank(config.commitment);
        let commission_rate_in_basis_points = bank
            .feature_set
            .is_active(&agave_feature_set::commission_rate_in_basis_points::id());
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
```

**File:** rpc/src/rpc.rs (L1224-1246)
```rust
            .partition(|vote_account_info| {
                if bank.slot() >= delinquent_validator_slot_distance {
                    vote_account_info.last_vote > bank.slot() - delinquent_validator_slot_distance
                } else {
                    vote_account_info.last_vote > 0
                }
            });

        let keep_unstaked_delinquents = config.keep_unstaked_delinquents.unwrap_or_default();
        let delinquent_vote_accounts = if !keep_unstaked_delinquents {
            delinquent_vote_accounts
                .into_iter()
                .filter(|vote_account_info| vote_account_info.activated_stake > 0)
                .collect::<Vec<_>>()
        } else {
            delinquent_vote_accounts
        };

        Ok(RpcVoteAccountStatus {
            current: current_vote_accounts,
            delinquent: delinquent_vote_accounts,
        })
    }
```

**File:** runtime/src/snapshot_minimizer.rs (L134-145)
```rust
    /// Used to get vote and node pubkeys in `minimize`
    /// Add all pubkeys from vote accounts and nodes to `minimized_account_set`
    fn get_vote_accounts(&self) {
        self.bank
            .vote_accounts()
            .par_iter()
            .for_each(|(pubkey, (_stake, vote_account))| {
                self.minimized_account_set.insert(*pubkey);
                self.minimized_account_set
                    .insert(*vote_account.node_pubkey());
            });
    }
```

**File:** runtime/src/stakes.rs (L693-699)
```rust
    pub(crate) fn highest_staked_node(&self) -> Option<SlotLeader> {
        let (vote_address, vote_account) = self.vote_accounts.find_max_by_delegated_stake()?;
        Some(SlotLeader {
            id: *vote_account.node_pubkey(),
            vote_address: *vote_address,
        })
    }
```

**File:** vote/src/vote_account.rs (L297-302)
```rust
    pub fn find_max_by_delegated_stake(&self) -> Option<(&Pubkey, &VoteAccount)> {
        let key = |(pubkey, (stake, _vote_account)): &(_, &(u64, _))| (*stake, *pubkey);
        let (vote_address, (_stake, vote_account)) = self.vote_accounts.iter().max_by_key(key)?;
        Some((vote_address, vote_account))
    }

```

**File:** cli/src/vote.rs (L993-1001)
```rust
    let required_balance = rpc_client
        .get_minimum_balance_for_rent_exemption(VoteStateV4::size_of())
        .await?
        .max(1);
    let amount = SpendAmount::Some(required_balance);

    let fee_payer = config.signers[fee_payer];
    let nonce_authority = config.signers[nonce_authority];
    let space = VoteStateV4::size_of() as u64;
```
