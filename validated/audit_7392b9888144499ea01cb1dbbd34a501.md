## Confirmed finding: unbounded, unlimited `getProgramAccounts` scan lacks any size/byte limit on the general (non-secondary-index) path

### Title
`get_filtered_program_accounts()` (`getProgramAccounts` RPC path) performs an unbounded full-scan with no `byte_limit_for_scan`, unlike the SPL-token/index-key path - ([File: accounts-db/src/accounts.rs])

### Summary
This is a direct structural analog of the `getTickState()` bug: a public getter that walks an unbounded collection (all inserted ticks vs. all accounts owned by a program) with no caller-supplied cap on the amount of work done, risking resource exhaustion. In Agave, `Accounts::load_by_program` / `load_by_program_with_filter` — the code path used by the `getProgramAccounts` RPC method when no SPL-token owner/mint filter applies — calls `AccountsDb::scan_accounts` with `ScanConfig::default()` and no `byte_limit_for_scan`, so it will collect every account owned by `program_id` into memory regardless of size [1](#0-0) . This contrasts with the sibling `load_by_index_key_with_filter` used for `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`, which explicitly tracks accumulated result size via `accumulate_and_check_scan_result_size` and aborts the scan with `ScanError::Aborted` once `byte_limit_for_scan` is exceeded [2](#0-1) .

### Finding Description
`JsonRpcRequestProcessor::get_program_accounts` dispatches to one of three internal calls depending on whether an SPL-token owner/mint filter is detected: `get_filtered_spl_token_accounts_by_owner`, `get_filtered_spl_token_accounts_by_mint`, or, for the general (non-SPL) case, `get_filtered_program_accounts` [3](#0-2) . Only the SPL-token-filtered paths route through `load_by_index_key_with_filter`, which enforces a `byte_limit_for_scan` and aborts (`ScanResult::Err(ScanError::Aborted(...))`) if the accumulated result size in bytes exceeds it [4](#0-3) . The general path, `Bank::get_filtered_program_accounts`, forwards straight to `rc.accounts.load_by_program_with_filter`, which has no such parameter at all and simply collects every matching account into an in-memory `Vec<KeyedAccountSharedData>` [5](#0-4) [6](#0-5) .

Unlike `getBlocks`/`getBlocksWithLimit`, which explicitly bound the scan range via `MAX_GET_CONFIRMED_BLOCKS_RANGE` and return `Error::invalid_params` for out-of-range requests [7](#0-6) , and unlike `get_confirmed_signatures_for_address2`, which is caller-bounded by an explicit `limit` parameter used to break out of the reverse RocksDB iterator loop [8](#0-7) , `getProgramAccounts` for a non-token program has no analogous cap: any account owner filter, no matter how many accounts are owned by that program, is fully materialized in memory and returned in one response. This mirrors the reported `getTickState()` flaw exactly: the getter walks "all inserted" state with no starting index/count parameter to bound iteration, relying only on the (broken/inconsistent) assumption that callers will supply narrow filters.

### Impact Explanation
A client can call `getProgramAccounts` against a widely-used program (e.g., a large DEX, lending, or system-owned account set) without narrow `memcmp`/`dataSize` filters, forcing the validator's JSON-RPC thread to scan and serialize potentially hundreds of thousands of accounts with no server-side circuit breaker on the number of bytes/accounts returned. Because `optimize_filters`/`verify_filters` only validate filter *shape*, not scan-result size, and because the byte-limit protection (`byte_limit_for_scan`) exists in the codebase but is never wired into `get_filtered_program_accounts`, this is squarely a single-client, low-rate RPC degradation/crash vector (large memory allocation for the collected `Vec`, large JSON serialization, and blocking of the async RPC worker) rather than a benign, well-mitigated pattern. This matches the explicitly allowed "single-client low-rate RPC crash/degradation" impact category.

### Likelihood Explanation
Likelihood is high for triggering degradation: the request requires only a syntactically valid `program_id` and no special permissions or malicious peer/validator assumptions — any unprivileged RPC client can issue it. The only mitigating factor in practice is operator-side configuration (rate limiting, `--rpc-max-request-body-size`, disabling `getProgramAccounts`), none of which are enforced in this code path itself; the code as written imposes no in-process safeguard analogous to `byte_limit_for_scan`.

### Recommendation
Thread an explicit `byte_limit_for_scan` (or an account-count cap) through `Bank::get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `AccountsDb::scan_accounts`, mirroring the `accumulate_and_check_scan_result_size` / `maybe_abort_scan` pattern already implemented for `load_by_index_key_with_filter`, and surface a clear `RpcCustomError` (analogous to the "Slot range too large" error used by `get_blocks`) when the scan is aborted for exceeding the limit.

### Proof of Concept
1. Identify (or deploy) a program that owns a very large number of accounts on the target cluster (e.g., a heavily-used token/lending/DEX program).
2. Issue `getProgramAccounts` against that `program_id` with no `filters` and default `encoding` (`Binary`).
3. Observe: `JsonRpcRequestProcessor::get_program_accounts` routes to `get_filtered_program_accounts` (no SPL owner/mint filter present) [9](#0-8) , which calls `Bank::get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` with `ScanConfig::default()` and no size cap [6](#0-5) .
4. The full account set is materialized and base64/base64+zstd/binary-encoded in one response, consuming proportionally large CPU/memory on the validator's RPC service and potentially stalling/crashing that RPC node for the duration of the request, with no server-side abort mechanism analogous to the one that exists for the SPL-token filtered variants.

### Citations

**File:** accounts-db/src/accounts.rs (L317-358)
```rust
    pub fn load_by_program(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        program_id: &Pubkey,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let mut collector = Vec::new();
        self.accounts_db
            .scan_accounts(
                ancestors,
                bank_id,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        account.owner() == program_id
                    })
                },
                &ScanConfig::default(),
            )
            .map(|_| collector)
    }

    pub fn load_by_program_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        program_id: &Pubkey,
        filter: F,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let mut collector = Vec::new();
        self.accounts_db
            .scan_accounts(
                ancestors,
                bank_id,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        account.owner() == program_id && filter(account)
                    })
                },
                &ScanConfig::default(),
            )
            .map(|_| collector)
    }
```

**File:** accounts-db/src/accounts.rs (L366-394)
```rust
    /// Accumulate size of (pubkey + account) into sum.
    /// Return true iff sum > 'byte_limit_for_scan'
    fn accumulate_and_check_scan_result_size(
        sum: &AtomicUsize,
        account: &AccountSharedData,
        byte_limit_for_scan: &Option<usize>,
    ) -> bool {
        if let Some(byte_limit_for_scan) = byte_limit_for_scan.as_ref() {
            let added = Self::calc_scan_result_size(account);
            sum.fetch_add(added, Ordering::Relaxed)
                .saturating_add(added)
                > *byte_limit_for_scan
        } else {
            false
        }
    }

    fn maybe_abort_scan(
        result: ScanResult<Vec<KeyedAccountSharedData>>,
        config: &ScanConfig,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        if config.is_aborted() {
            ScanResult::Err(ScanError::Aborted(
                "The accumulated scan results exceeded the limit".to_string(),
            ))
        } else {
            result
        }
    }
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

**File:** rpc/src/rpc.rs (L623-651)
```rust
        let keyed_accounts = {
            if let Some(owner) = get_spl_token_owner_filter(&program_id, &filters)? {
                self.get_filtered_spl_token_accounts_by_owner(
                    Arc::clone(&bank),
                    program_id,
                    owner,
                    filters,
                    sort_results,
                )
                .await?
            } else if let Some(mint) = get_spl_token_mint_filter(&program_id, &filters)? {
                self.get_filtered_spl_token_accounts_by_mint(
                    Arc::clone(&bank),
                    program_id,
                    mint,
                    filters,
                    sort_results,
                )
                .await?
            } else {
                self.get_filtered_program_accounts(
                    Arc::clone(&bank),
                    program_id,
                    filters,
                    sort_results,
                )
                .await?
            }
        };
```

**File:** rpc/src/rpc.rs (L1459-1474)
```rust
        let end_slot = min(
            end_slot.unwrap_or_else(|| start_slot.saturating_add(MAX_GET_CONFIRMED_BLOCKS_RANGE)),
            if commitment.is_finalized() {
                highest_super_majority_root
            } else {
                self.get_bank_with_config(config)?.slot()
            },
        );
        if end_slot < start_slot {
            return Ok(vec![]);
        }
        if end_slot - start_slot > MAX_GET_CONFIRMED_BLOCKS_RANGE {
            return Err(Error::invalid_params(format!(
                "Slot range too large; max {MAX_GET_CONFIRMED_BLOCKS_RANGE}"
            )));
        }
```

**File:** runtime/src/bank.rs (L5121-5132)
```rust
    pub fn get_filtered_program_accounts<F: Fn(&AccountSharedData) -> bool>(
        &self,
        program_id: &Pubkey,
        filter: F,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        self.rc.accounts.load_by_program_with_filter(
            &self.ancestors,
            self.bank_id,
            program_id,
            filter,
        )
    }
```

**File:** ledger/src/blockstore.rs (L4672-4686)
```rust
        // Iterate until limit is reached
        while address_signatures.len() < limit {
            if let Some(((key_address, slot, transaction_index, signature), _)) = iterator.next() {
                if slot < lowest_slot {
                    break;
                }
                if key_address == address {
                    if self.is_root(slot) || confirmed_unrooted_slots.contains(&slot) {
                        address_signatures.push((slot, signature, transaction_index));
                    }
                    continue;
                }
            }
            break;
        }
```
