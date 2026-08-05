### Title
Unbounded, unindexed `getProgramAccounts` scan lets a single low-rate RPC client exhaust CPU/memory - ([File: rpc/src/rpc.rs])

### Summary
`getFilteredProgramAccounts` in `rpc/src/rpc.rs` scans **every account in AccountsDB owned by an arbitrary, caller-supplied `program_id`**, with no cap on the number of accounts or bytes returned, whenever the `ProgramId` secondary index is not enabled (the default unless a validator operator explicitly opts in with `--account-index program-id`). This is the same broken invariant as the report's `_pendingInterests` loop over `_approvedBorrowers`: an attacker-influenceable collection is iterated in full inside a function reachable on a hot/exposed path, with no maximum-size guard, so growth of the underlying set turns a single call into a resource-exhaustion vector.

### Finding Description
`get_filtered_program_accounts` explicitly documents the missing bound: [1](#0-0) 
```
} else {
    // this path does not need to provide a mb limit because we only want to support secondary indexes
    let mut accounts = self
        .runtime
        .spawn_blocking(move || {
            bank.get_filtered_program_accounts(
                &program_id,
                |account: &AccountSharedData| { ... }
            )
            ...
        })
        ...
}
``` [2](#0-1) 

Compare this to the indexed sibling path, `get_filtered_indexed_accounts`, which does pass a byte limit (`self.config.scan_results_limit_bytes`) that aborts the scan if the accumulated result exceeds it: [3](#0-2) 

`bank.get_filtered_program_accounts` forwards straight into `Accounts::load_by_program_with_filter`, which calls `AccountsDb::scan_accounts` with no `byte_limit_for_scan` at all: [4](#0-3) [5](#0-4) 

`scan_accounts` then walks the entire cached write set plus the entire accounts index for every pubkey visible from the bank's ancestors, materializing a full copy of every matching account's data into an in-memory `Vec<KeyedAccountSharedData>`: [6](#0-5) 

There is no per-request limit here analogous to the report's recommended "maximum length check." The only existing guard, `scan_results_limit_bytes`, is wired up solely for the secondary-indexed path (`get_filtered_indexed_accounts`); the non-indexed path used when `AccountIndex::ProgramId` is disabled (the default configuration for most validators/RPC nodes) has no such check, despite scanning the *entire* accounts set, not a narrower per-key index list.

Because `program_id` is fully attacker-controlled (any pubkey, including the System Program, Token Program, or any arbitrary account owner an attacker chooses), and because anyone can cheaply create large numbers of accounts owned by a given program (e.g., spam accounts owned by System Program, or a novel program they deploy and own many accounts under), the size of the scanned/returned set is effectively unbounded and grows with on-chain state that unprivileged users control. This directly parallels `_approvedBorrowers`/`_pendingInterests`: an admin-agnostic collection whose growth is externally influenceable, iterated fully in a function reachable through a routine, non-privileged path, with no size ceiling.

### Impact Explanation
This falls squarely within the accepted "single-client low-rate RPC crash/degradation" impact category. A single unauthenticated `getProgramAccounts` call against a program that owns a very large number of accounts (or one crafted to own many) forces the RPC node to:
- Hold the `scan_tracker`/`ScanGuard` and iterate the full accounts cache + accounts index,
- Clone/deserialize every matching account's raw data into memory,
- Allocate and return a potentially multi-gigabyte response,

all inside a `spawn_blocking` task on the node's thread pool. Repeated or even single such requests can starve the runtime, exhaust memory, and degrade or crash the RPC service for all other users of that node — without requiring any stake, signature, or transaction submission (fully unprivileged).

### Likelihood Explanation
Likelihood is high for RPC nodes that have not enabled `--account-index program-id` (the common/default state, since secondary indexes carry their own memory overhead and many operators disable them). The attacker needs only network access to the JSON-RPC endpoint and the ability to name (or create) a program with many owned accounts — both of which require no special privilege, no stake, and no on-chain cost beyond ordinary account creation. This is a long-standing, well-documented Solana RPC DoS surface, and the code in this snapshot still lacks a size/byte cap on the non-indexed scan path, as the comment at `rpc/src/rpc.rs:2284` acknowledges.

### Recommendation
Apply the same `scan_results_limit_bytes` (or a dedicated limit) to the non-indexed `get_filtered_program_accounts` path in `rpc/src/rpc.rs`, mirroring what `get_filtered_indexed_accounts` already does, so that `AccountsDb::scan_accounts` aborts once accumulated result size/count exceeds a configured maximum regardless of whether a secondary index is enabled. Additionally consider requiring at least one selective filter (e.g., data-size or memcmp filter) or enforcing pagination/`dataSlice` for unfiltered `getProgramAccounts` calls when no secondary index exists, and document/default a conservative scan limit for RPC nodes that expose `AccountsScan`.

### Proof of Concept
1. Run/point at an RPC node that does not have `--account-index program-id` enabled (default).
2. Choose (or deploy) a program `P` and create a very large number of accounts owned by `P` (or simply target `system_program::id()`, which will own a huge number of accounts on any populated cluster/localnet).
3. Issue a single `getProgramAccounts` JSON-RPC request for `P` with no filters:
   ```json
   {"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["<P>"]}
   ```
4. Observe that the request path `AccountsScanImpl::get_program_accounts` → `JsonRpcRequestProcessor::get_program_accounts` → `get_filtered_program_accounts` (rpc/src/rpc.rs:2254) takes the `else` branch (no `AccountIndex::ProgramId`), calls `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `AccountsDb::scan_accounts` with no byte/count limit, causing full materialization of every owned account into memory before any size check occurs — degrading or crashing the node under memory/CPU pressure with a single call.

### Citations

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

**File:** rpc/src/rpc.rs (L2252-2260)
```rust
    /// Use a set of filters to get an iterator of keyed program accounts from a bank
    #[allow(clippy::result_large_err)]
    async fn get_filtered_program_accounts(
        &self,
        bank: Arc<Bank>,
        program_id: Pubkey,
        mut filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> RpcCustomResult<Vec<(Pubkey, AccountSharedData)>> {
```

**File:** rpc/src/rpc.rs (L2283-2307)
```rust
        } else {
            // this path does not need to provide a mb limit because we only want to support secondary indexes
            let mut accounts = self
                .runtime
                .spawn_blocking(move || {
                    bank.get_filtered_program_accounts(
                        &program_id,
                        |account: &AccountSharedData| {
                            filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                        },
                    )
                    .map_err(|e| RpcCustomError::ScanError {
                        message: e.to_string(),
                    })
                })
                .await
                .expect("Failed to spawn blocking task")?;
            if sort_results {
                // Avoid copying pubkeys (using Ord::cmp(a, b) silences clippy::unnecessary_sort_by).
                accounts.sort_unstable_by(|(addr_a, _), (addr_b, _)| Ord::cmp(addr_a, addr_b));
            }
            Ok(accounts)
        }
```

**File:** accounts-db/src/accounts.rs (L317-336)
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
```

**File:** accounts-db/src/accounts.rs (L338-358)
```rust
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

**File:** accounts-db/src/accounts_db.rs (L3259-3346)
```rust
    /// Scans all accounts visible from `ancestors`, invoking `scan_func` for each.
    /// Pre-scans the write cache to capture entries not yet flushed to the accounts index, then
    /// deduplicates against the index scan, calling `scan_func` with the newest version of each
    /// account
    pub(crate) fn scan_accounts<F>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        mut scan_func: F,
        config: &ScanConfig,
    ) -> ScanResult<()>
    where
        F: FnMut(Option<(&Pubkey, AccountSharedData, Slot)>),
    {
        // Register this scan so that slots needed by the scan are not cleaned out from under us.
        let scan_guard = ScanGuard::try_new(&self.scan_tracker, bank_id, || self.max_root())
            .ok_or(ScanError::SlotRemoved {
                slot: ancestors.max_slot(),
                bank_id,
            })?;

        // If the scan's ancestors are all rooted, drop them and scan roots only
        // Scan Guard max root must be used as the scan guard guarantees that
        // the account state as of max root is persisted in the database
        let max_root_ancestors = Ancestors::from(vec![scan_guard.max_root()]);
        let ancestors = if scan_guard.should_use_ancestors(ancestors) {
            ancestors
        } else {
            &max_root_ancestors
        };

        // Step 1: Pre-scan the cache index to find the newest visible cached version of each
        // pubkey. Hold the Arc<CachedAccount> to keep the data alive even if the cache flushes
        // between now and step 3 (Arc clone is just a refcount bump).
        let cached_pubkeys = self.accounts_cache.cached_pubkeys();
        let mut cached_versions =
            HashMap::with_capacity_and_hasher(cached_pubkeys.len(), PubkeyHasherBuilder::default());
        for pubkey in cached_pubkeys {
            if config.is_aborted() {
                break;
            }

            if let Some((cached_account, slot)) =
                self.accounts_cache.load_latest(&pubkey, ancestors)
            {
                cached_versions.insert(pubkey, (cached_account, slot));
            }
        }

        // Step 2: Scan the accounts_index. For each pubkey, return the newest version found in
        // either the storage or the cache. If both versions are the same, use the cached version
        // to avoid a redundant load from storage.
        // Bound max_root by ancestors.min_slot() so that roots from slots
        // beyond the querying bank's ancestor chain are not visible.
        let mut max_root = scan_guard.max_root();
        if let Some(min) = ancestors.min_slot() {
            max_root = max_root.min(min);
        }
        self.accounts_index.scan_accounts(
            ancestors,
            max_root,
            |pubkey, (account_info, slot)| {
                if let Some((cached_account, cache_slot)) = cached_versions.remove(pubkey)
                    && cache_slot >= slot
                {
                    scan_func(Some((pubkey, cached_account.account.clone(), cache_slot)));
                    return;
                }

                let mut account_accessor =
                    self.get_account_accessor(slot, &account_info.storage_location());

                let account_slot = account_accessor.get_loaded_account(|loaded_account| {
                    (pubkey, loaded_account.take_account(), slot)
                });
                scan_func(account_slot)
            },
            config,
        );

        // Step 3: Call scan_func on cache-only entries — pubkeys that exist in the cache but not
        // in the accounts index at all.
        for (pubkey, (cached_account, slot)) in cached_versions {
            if config.is_aborted() {
                break;
            }
            scan_func(Some((&pubkey, cached_account.account.clone(), slot)));
        }
```
