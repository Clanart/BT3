## Analysis

`calculate_non_circulating_supply` (used by both `getSupply` and `getLargestAccounts`) calls `bank.get_filtered_indexed_accounts` with the byte-limit parameter hard-coded to `None`: [1](#0-0) 

This differs from the RPC layer's own generic wrapper for the same primitive, `JsonRpcRequestProcessor::get_filtered_indexed_accounts`, which always forwards the configured `scan_results_limit_bytes` so that `index_scan_accounts` can abort the scan once accumulated result size crosses the configured threshold: [2](#0-1) [3](#0-2) 

The underlying index scan itself (`index_scan_accounts`) iterates every pubkey registered under the requested `IndexKey` with no inherent cap other than the caller-supplied byte limit, so the number of pubkeys visited is proportional to how many accounts are indexed under `stake::program::id()`: [4](#0-3) 

The owner filter at line 42 (`account.owner() == &stake::program::id()`) is a post-scan filter applied *after* every indexed pubkey has already been loaded via `do_load` — it does not reduce the number of index entries scanned, it only decides whether a loaded account is kept in the result. `test_get_filtered_indexed_accounts_limit_exceeded` in `runtime/src/bank/tests.rs` confirms that only an explicit `byte_limit_for_scan` (an `Option<usize>`) causes the scan to abort; when it is `None` (as in `non_circulating_supply.rs`), no such abort occurs: [5](#0-4) 

Important caveats that limit real-world severity:

1. This code path is only reached `if bank...account_indexes.contains(&AccountIndex::ProgramId)` — the secondary `ProgramId` index is **opt-in** (`--account-index program-id`) and is not enabled by default. If disabled, `calculate_non_circulating_supply` falls back to `bank.get_program_accounts`, which is a full unindexed accounts scan by owner and has the same unbounded cost characteristic that already exists for any `getProgramAccounts`-style query on Agave — a well-known, accepted expensive-call category. [6](#0-5) 
2. Creating "a very large number" of accounts owned by `stake::program::id()` requires paying rent-exempt minimum balance and transaction fees for each account — an economic throttle, though not a scan-time bound.
3. Impact is confined to a single RPC node that (a) has `--full-rpc-api` and the `ProgramId` secondary index enabled, and (b) is queried with `getSupply`/`getLargestAccounts`. There is no cross-validator or consensus effect.

Given the bounty's valid-impact list explicitly includes "single-client low-rate RPC crash/degradation," and the missing byte-limit in `non_circulating_supply.rs` is a genuine deviation from the pattern applied elsewhere in the RPC layer, this is a real, reportable weakness — though moderate/low severity due to the opt-in index requirement and rent-cost throttling.

### Title
Unbounded stake-program index scan in `calculate_non_circulating_supply` degrades `getSupply`/`getLargestAccounts` - (File: `runtime/src/non_circulating_supply.rs`)

### Summary
`calculate_non_circulating_supply`, invoked on every `getSupply` and `getLargestAccounts` RPC call, invokes `bank.get_filtered_indexed_accounts(&IndexKey::ProgramId(stake::program::id()), ..., None)` with `byte_limit_for_scan` fixed to `None`. Unlike the RPC layer's own `get_filtered_indexed_accounts` wrapper (used by `getProgramAccounts`), which always passes the operator-configured `scan_results_limit_bytes` bound, this internal call has no scan-size cap.

### Finding Description
`index_scan_accounts` loads every pubkey registered under the `IndexKey::ProgramId(stake::program::id())` secondary index and invokes the caller's scan function for each; the "owner == stake::program::id()" predicate in `non_circulating_supply.rs` line 42 is only a post-load filter, not a pre-filter that reduces index-scan cardinality. When `byte_limit_for_scan` is `None`, `accumulate_and_check_scan_result_size` never returns `true`, so the scan cannot abort regardless of how large the index entry set is. An unprivileged attacker who funds enough accounts owned by `stake::program::id()` (accounts need not even hold a valid `StakeStateV2`, since `deserialize_data::<StakeStateV2>().unwrap_or_default()` silently degrades invalid data to the default variant, which is not matched by the `Initialized`/`Stake` non-circulating arms but is still loaded and iterated) inflates the size of the `ProgramId` secondary index for that key, which is scanned end-to-end on every subsequent `getSupply`/`getLargestAccounts` request against nodes that enabled that secondary index.

### Impact Explanation
Every `getSupply`/`getLargestAccounts` call on an affected RPC node performs `do_load` for each attacker-created stake-owned pubkey with no scan-size circuit breaker, unlike the equivalent `getProgramAccounts` path. This raises per-call CPU/IO/latency proportional to attacker-controlled input, degrading responsiveness of that single RPC node — matching the "single-client low-rate RPC crash/degradation" impact category. It does not affect consensus, other validators, or funds.

### Likelihood Explanation
Requires the validator operator to have enabled `--account-index program-id` (not default) and expose `--full-rpc-api`. Requires the attacker to fund rent-exempt balances for a large number of accounts, providing an economic throttle. Given these preconditions, exploitation is straightforward and repeatable at any time.

### Recommendation
Pass the same `scan_results_limit_bytes`/configured byte limit into `calculate_non_circulating_supply`'s call to `bank.get_filtered_indexed_accounts` (as `rpc.rs`'s `get_filtered_indexed_accounts` wrapper already does for `getProgramAccounts`), so the scan can abort with `ScanError::Aborted` once results exceed the configured limit, and propagate that as a `RpcCustomError::ScanError` to the caller rather than allowing an unbounded scan.

### Proof of Concept
1. Run a validator/test cluster with `--account-index program-id` and `--full-rpc-api` enabled.
2. As an unprivileged client, submit many `SystemProgram::create_account` (or `Allocate`+`Assign`) transactions assigning `stake::program::id()` as owner for a large number of freshly funded pubkeys (paying rent-exempt minimum each time).
3. Confirm via `bank.get_filtered_indexed_accounts(&IndexKey::ProgramId(stake::program::id()), |_| true, None)` (mirroring `runtime/src/bank/tests.rs::test_get_filtered_indexed_accounts`) that the number of scanned/loaded entries grows linearly with attacker-created accounts and that no `Some(byte_limit)` is applied, in contrast to `test_get_filtered_indexed_accounts_limit_exceeded`, which shows the abort only fires when a limit is explicitly supplied.
4. Measure `getSupply`/`getLargestAccounts` latency before and after step 2 to observe degradation proportional to the injected account count.

### Citations

**File:** runtime/src/non_circulating_supply.rs (L29-47)
```rust
    let stake_accounts = if bank
        .rc
        .accounts
        .accounts_db
        .account_indexes
        .contains(&AccountIndex::ProgramId)
    {
        bank.get_filtered_indexed_accounts(
            &IndexKey::ProgramId(stake::program::id()),
            // The program-id account index checks for Account owner on inclusion. However, due to
            // the current AccountsDb implementation, an account may remain in storage as a
            // zero-lamport Account::Default() after being wiped and reinitialized in later
            // updates. We include the redundant filter here to avoid returning these accounts.
            |account| account.owner() == &stake::program::id(),
            None,
        )?
    } else {
        bank.get_program_accounts(&stake::program::id())?
    };
```

**File:** rpc/src/rpc.rs (L309-341)
```rust
    pub async fn get_filtered_indexed_accounts(
        &self,
        bank: &Arc<Bank>,
        index_key: &IndexKey,
        program_id: &Pubkey,
        filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let bank = Arc::clone(bank);
        let index_key = index_key.to_owned();
        let program_id = program_id.to_owned();
        let byte_limit_for_scans = self.config.scan_results_limit_bytes;
        let mut accounts = self
            .runtime
            .spawn_blocking(move || {
                bank.get_filtered_indexed_accounts(
                    &index_key,
                    |account| {
                        // The program-id account index checks for Account owner on inclusion.
                        // However, due to the current AccountsDb implementation, an account may
                        // remain in storage as a zero-lamport AccountSharedData::Default() after
                        // being wiped and reinitialized in later updates. We include the redundant
                        // filters here to avoid returning these accounts.
                        account.owner().eq(&program_id)
                            && filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                    },
                    byte_limit_for_scans,
                )
            })
            .await
            .expect("Failed to spawn blocking task")?;
```

**File:** accounts-db/src/accounts.rs (L396-433)
```rust
    pub fn load_by_index_key_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        index_key: &IndexKey,
        filter: F,
        byte_limit_for_scan: Option<usize>,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let sum = AtomicUsize::default();
        let config = ScanConfig::default().recreate_with_abort();
        let mut collector = Vec::new();
        let result = self
            .accounts_db
            .index_scan_accounts(
                ancestors,
                bank_id,
                *index_key,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        let use_account = filter(account);
                        if use_account
                            && Self::accumulate_and_check_scan_result_size(
                                &sum,
                                account,
                                &byte_limit_for_scan,
                            )
                        {
                            // total size of results exceeds size limit, so abort scan
                            config.abort();
                        }
                        use_account
                    });
                },
                &config,
            )
            .map(|_| collector);
        Self::maybe_abort_scan(result, &config)
    }
```

**File:** accounts-db/src/accounts_db.rs (L3398-3410)
```rust
        for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
            if config.is_aborted() {
                break;
            }
            if let Some((account, slot)) = self.do_load(
                ancestors,
                &pubkey,
                LoadHint::Unspecified,
                PopulateReadCache::False,
            ) {
                scan_func(Some((&pubkey, account, slot)));
            }
        }
```

**File:** runtime/src/bank/tests.rs (L3470-3502)
```rust
#[test]
fn test_get_filtered_indexed_accounts_limit_exceeded() {
    let (genesis_config, _mint_keypair) = create_genesis_config(500);
    let mut account_indexes = AccountSecondaryIndexes::default();
    account_indexes.indexes.insert(AccountIndex::ProgramId);
    let bank_config = BankTestConfig {
        accounts_db_config: AccountsDbConfig {
            account_indexes: Some(account_indexes),
            ..ACCOUNTS_DB_CONFIG_FOR_TESTING
        },
    };
    let bank = Arc::new(Bank::new_with_paths_for_tests(
        &genesis_config,
        Some(bank_config),
        vec![],
        None,
    ));

    let address = Pubkey::new_unique();
    let program_id = Pubkey::new_unique();
    let limit = 100;
    let account = AccountSharedData::new(1, limit, &program_id);
    bank.store_account(&address, &account);

    assert!(
        bank.get_filtered_indexed_accounts(
            &IndexKey::ProgramId(program_id),
            |_| true,
            Some(limit), // limit here will be exceeded, resulting in aborted scan
        )
        .is_err()
    );
}
```
