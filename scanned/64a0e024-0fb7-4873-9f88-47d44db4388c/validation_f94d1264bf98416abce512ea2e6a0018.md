### Title
Unbounded `getProgramAccounts`/token-account scans bypass the only response-size guard, enabling single-client RPC OOM - (File: `rpc/src/rpc.rs`, `accounts-db/src/accounts.rs`)

### Summary
The Monad report's root cause is that a JSON-RPC server fully materializes a large in-memory result and only checks/enforces a size limit *after* serialization, so the peak memory allocation happens before any guard can reject the request. Agave has the same class of gap: `JsonRpcConfig` and `rpc_service.rs` only bound the *inbound* request body (`max_request_body_size`) [1](#0-0) , [2](#0-1) . There is no equivalent cap on outbound response size anywhere in `rpc_service.rs`. For the non-indexed `getProgramAccounts` path, the only in-process safeguard that exists in the codebase (`scan_results_limit_bytes`) is explicitly skipped, so a single unauthenticated client can force full materialization of an unbounded account set into memory with no size check at any point before the HTTP response is built.

### Finding Description
`JsonRpcConfig::scan_results_limit_bytes` is meant to bound the memory used by index scans [3](#0-2) , and it is honored by the *indexed* account-scan path via `get_filtered_indexed_accounts` → `load_by_index_key_with_filter`, which tracks accumulated bytes and aborts the scan when the limit is exceeded [4](#0-3) , [5](#0-4) .

However, `get_filtered_program_accounts` — the code path used whenever the `ProgramId` secondary index is not enabled for the queried program (the default validator configuration, since secondary indexes are opt-in) — deliberately does **not** pass any byte limit:

```
} else {
    // this path does not need to provide a mb limit because we only want to support secondary indexes
    let mut accounts = self
        .runtime
        .spawn_blocking(move || {
            bank.get_filtered_program_accounts(
                &program_id,
                |account: &AccountSharedData| { ... },
            )
``` [6](#0-5) 

`bank.get_filtered_program_accounts` forwards to `Accounts::load_by_program_with_filter`, which collects results into an unbounded `Vec<KeyedAccountSharedData>` with no size accounting at all: [7](#0-6) , [8](#0-7) .

The same unbounded pattern is used for `get_filtered_spl_token_accounts_by_owner`/`by_mint` (called from `get_token_accounts_by_owner`, `get_token_accounts_by_delegate`, and `get_token_largest_accounts`), none of which pass a byte limit either [9](#0-8) , [10](#0-9) .

Once this unbounded `Vec` is fully collected, `get_program_accounts` then re-encodes every account (base64/base58/jsonParsed, with `data_slice`) into `RpcKeyedAccount` before returning [11](#0-10) , doubling the memory footprint (raw account bytes + serialized/encoded string form) — exactly the "multiplicative memory footprint" pattern described in the Monad report. Finally, the whole `Vec<RpcKeyedAccount>` is serialized by `jsonrpc-core`'s `MetaIoHandler` into the HTTP response body with no size check anywhere in `rpc_service.rs`; only `max_request_body_size` is configured on the server builder [12](#0-11) .

### Impact Explanation
An unauthenticated RPC client issuing a single `getProgramAccounts` (or `getTokenAccountsByOwner`/`byDelegate`) request against a program owning a very large number of accounts (e.g. the SPL Token program, System Program, or any heavily used on-chain program) forces the validator's RPC service to:
1. Scan and copy every matching account's full `AccountSharedData` (including account `data`, which can be up to 10 MB each) into an in-memory `Vec` with **no configured or default byte limit**, unlike the indexed path.
2. Re-encode each account into a second in-memory representation (base64/base58/JSON).
3. Serialize the entire resulting `Vec<RpcKeyedAccount>` into one HTTP response with no size cap.

This can exhaust the RPC service's memory (and by extension the whole validator process's memory, since RPC runs in-process), leading to OOM-kill or severe degradation — a single-client, low-rate RPC crash/degradation, which matches the specified valid-impact criteria for non-privileged RPC DoS.

### Likelihood Explanation
High for RPC nodes with `--full-rpc-api` enabled and secondary indexes *not* configured for the targeted program — which is the common/default deployment for many public RPC endpoints that still expose `getProgramAccounts` without enabling `--account-index program-id`. No authentication, no rate limiting beyond the general RPC guard, and no special network position is required; a single crafted request suffices. Nodes that enable the `ProgramId`/`SplTokenOwner`/`SplTokenMint` secondary indexes for the targeted keys are partially protected via `scan_results_limit_bytes`, but that flag is optional (`Option<usize>`, `None` by default) and only wired into the indexed path — so even index-enabled nodes are unprotected unless the operator also explicitly sets `--accounts-index-scan-results-limit-mb`.

### Recommendation
- Add a byte-limit (using the existing `scan_results_limit_bytes` mechanism/`ScanConfig` abort logic already present in `accounts.rs`) to the non-indexed `get_filtered_program_accounts` path, `get_filtered_spl_token_accounts_by_owner`, and `get_filtered_spl_token_accounts_by_mint`, so unbounded scans abort early regardless of whether secondary indexes are enabled.
- Enforce `scan_results_limit_bytes` with a sane non-`None` default rather than leaving it optional/unset.
- Add an explicit outbound response-size cap in `rpc_service.rs` (mirroring `max_request_body_size`) so oversized responses are rejected/streamed rather than fully buffered and returned.
- Consider incremental/streaming encoding for large account-list responses instead of collecting the full `Vec<RpcKeyedAccount>` before serialization.

### Proof of Concept
1. Start a validator/test-validator with `--full-rpc-api` and without configuring `--account-index program-id` (or without `--accounts-index-scan-results-limit-mb`) — i.e. default account-index configuration.
2. Ensure the ledger/accounts-db contains (or wait for it to accumulate) a very large number of accounts owned by a common program (e.g., SPL Token program has millions of token accounts on mainnet-scale state).
3. Send a single unauthenticated JSON-RPC request:
   ```
   curl http://<rpc-host>:8899 -X POST -H "Content-Type: application/json" -d '
   {"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"]}'
   ```
4. Observe that `get_filtered_program_accounts` (rpc.rs:2254-2308) takes the non-indexed branch (2283-2301), fully materializes every matching account (no byte limit applied), and the RPC thread allocates memory proportional to total account data across the whole program before any encoding or size check occurs — with no downstream cap on the resulting HTTP response — causing memory pressure/OOM on the node from a single request.

Note: I was not able to execute this against a live large-state validator within this analysis to directly measure peak RSS; the finding is based on static code-path tracing showing the absence of any byte-limit or response-size guard on this path, in contrast to the indexed-scan path which does enforce one.

### Citations

**File:** rpc/src/rpc_service.rs (L665-668)
```rust
        let full_api = config.full_api;
        let max_request_body_size = config
            .max_request_body_size
            .unwrap_or(MAX_REQUEST_BODY_SIZE);
```

**File:** rpc/src/rpc_service.rs (L724-743)
```rust
                let server = ServerBuilder::with_meta_extractor(
                    io,
                    move |req: &hyper::Request<hyper::Body>| {
                        let xbigtable = req.headers().get("x-bigtable");
                        if xbigtable.is_some_and(|v| v == "disabled") {
                            request_processor.clone_without_bigtable()
                        } else {
                            request_processor.clone()
                        }
                    },
                )
                .event_loop_executor(runtime.handle().clone())
                .threads(1)
                .cors(DomainsValidation::AllowOnly(vec![
                    AccessControlAllowOrigin::Any,
                ]))
                .cors_max_age(86400)
                .request_middleware(request_middleware)
                .max_request_body_size(max_request_body_size)
                .start_http(&rpc_addr);
```

**File:** rpc/src/rpc.rs (L177-179)
```rust
    pub max_request_body_size: Option<usize>,
    /// If set, abort index scans whose accumulated results exceed this many bytes.
    pub scan_results_limit_bytes: Option<usize>,
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

**File:** rpc/src/rpc.rs (L652-666)
```rust
        let accounts = if is_known_spl_token_id(&program_id)
            && encoding == UiAccountEncoding::JsonParsed
        {
            get_parsed_token_accounts(Arc::clone(&bank), keyed_accounts.into_iter()).collect()
        } else {
            keyed_accounts
                .into_iter()
                .map(|(pubkey, account)| {
                    Ok(RpcKeyedAccount {
                        pubkey: pubkey.to_string(),
                        account: encode_account(&account, &pubkey, encoding, data_slice_config)?,
                    })
                })
                .collect::<Result<Vec<_>>>()?
        };
```

**File:** rpc/src/rpc.rs (L2076-2100)
```rust
    pub async fn get_token_largest_accounts(
        &self,
        mint: Pubkey,
        commitment: Option<CommitmentConfig>,
    ) -> Result<RpcResponse<Vec<RpcTokenAccountBalance>>> {
        let bank = self.bank(commitment);
        let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, &mint)?;
        if !is_known_spl_token_id(&mint_owner) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }

        let mut token_balances =
            BinaryHeap::<Reverse<(u64, Pubkey)>>::with_capacity(NUM_LARGEST_ACCOUNTS);
        for (address, account) in self
            .get_filtered_spl_token_accounts_by_mint(
                Arc::clone(&bank),
                mint_owner,
                mint,
                vec![],
                true,
            )
            .await?
        {
```

**File:** rpc/src/rpc.rs (L2132-2169)
```rust
    pub async fn get_token_accounts_by_owner(
        &self,
        owner: Pubkey,
        token_account_filter: TokenAccountsFilter,
        config: Option<RpcAccountInfoConfig>,
        sort_results: bool,
    ) -> Result<RpcResponse<Vec<RpcKeyedAccount>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice: data_slice_config,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Binary);
        let (token_program_id, mint) = get_token_program_id_and_mint(&bank, token_account_filter)?;

        let mut filters = vec![];
        if let Some(mint) = mint {
            // Optional filter on Mint address
            filters.push(RpcFilterType::Memcmp(Memcmp::new_raw_bytes(
                0,
                mint.to_bytes().into(),
            )));
        }

        let keyed_accounts = self
            .get_filtered_spl_token_accounts_by_owner(
                Arc::clone(&bank),
                token_program_id,
                owner,
                filters,
                sort_results,
            )
            .await?;
```

**File:** rpc/src/rpc.rs (L2283-2301)
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
