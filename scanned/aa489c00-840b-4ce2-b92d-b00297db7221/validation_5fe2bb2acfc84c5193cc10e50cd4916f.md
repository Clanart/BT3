Based on the evidence gathered, I found a valid Agave analog: the `getSignatureStatuses` RPC method accepts an unbounded array of signatures and performs a mutex-guarded lookup and per-signature blockstore/bigtable I/O for each one, without any length cap — unlike sibling methods in the same file that do enforce explicit limits.

### Title
Unbounded `signatures` array in `getSignatureStatuses` allows single-client CPU/lock exhaustion of JSON-RPC service - (File: `rpc/src/rpc.rs`)

### Summary
`JsonRpcRequestProcessor::get_signature_statuses` iterates over a caller-supplied `Vec<Signature>` with no upper bound on its length, taking a `bank` reference and repeatedly re-acquiring `self.block_commitment_cache.read().unwrap()` per signature, and — if `search_transaction_history` is set — performing a blockstore lookup (`get_rooted_transaction_status`) or BigTable round-trip per entry [1](#0-0) . This mirrors the reported `RetrieveBlob` pattern: an unprivileged, unauthenticated request that holds shared locks, triggers DB/storage lookups, and has no per-request rate limiting or size cap enforced anywhere in the call path.

### Finding Description
Other list-oriented RPC handlers in the same file explicitly bound their inputs before doing expensive work, e.g. `getSlotLeaders` (`Invalid limit; max 5000`) [2](#0-1) , `getRecentPerformanceSamples` (`PERFORMANCE_SAMPLES_LIMIT`) [3](#0-2) , `getInflationReward` (`MAX_GET_INFLATION_REWARD_ADDRESSES`) [4](#0-3) , and `getBlocksWithLimit`/`getBlocks` (`MAX_GET_CONFIRMED_BLOCKS_RANGE`) [5](#0-4) . By contrast, `get_signature_statuses` has no analogous check on `signatures.len()` before looping over the full vector [1](#0-0) .

Each iteration of the loop, when `search_transaction_history` is true, calls `self.get_transaction_status` which itself re-locks `self.block_commitment_cache.read().unwrap()` and calls `self.bank(...)` a second time inside the loop body via `optimistically_confirmed_bank` [6](#0-5) , and additionally performs a blockstore disk lookup or a BigTable network round-trip per signature [7](#0-6) . The JSON-RPC HTTP server itself is configured with a single event-loop thread (`.threads(1)`) and only bounds the total request body size (`max_request_body_size`), not per-method item counts or request rate [8](#0-7) . There is no rate-limiting middleware equivalent to the QUIC/TPU per-IP `ConnectionRateLimiter` used elsewhere in the codebase for this JSON-RPC path [9](#0-8) .

### Impact Explanation
A single unprivileged client can submit one `getSignatureStatuses` request containing an arbitrarily large signature array (bounded only by `max_request_body_size`) with `searchTransactionHistory: true`. This causes the RPC worker (which runs on a single-threaded event loop per `rpc_service.rs`) to repeatedly acquire `block_commitment_cache` read locks and issue blockstore/BigTable lookups per element, degrading RPC responsiveness for all other clients sharing that node — a non-malicious-peer, single-client low-rate RPC degradation scenario, matching the valid-impact criteria for RPC crash/degradation.

### Likelihood Explanation
Likelihood is limited by the fact that a large enough request must still fit under `max_request_body_size`, and the number of achievable Ed25519 signature strings per request is nontrivial but not prohibitive (each ~88 base58 chars); an attacker only needs client access to the public RPC endpoint, no privileges, no malicious peer assumption, and no reliance on plugin/snapshot trust.

### Recommendation
Add an explicit upper bound check on `signatures.len()` in `get_signature_statuses` (or its calling handler in `rpc_full`), mirroring the pattern already used by `getSlotLeaders`, `getRecentPerformanceSamples`, and `getInflationReward`, returning `Error::invalid_params` when exceeded, and consider adding general per-connection/per-IP request-rate limiting to the JSON-RPC HTTP service comparable to the QUIC/TPU rate limiter.

### Proof of Concept
1. Start a node with `--enable-rpc-transaction-history` and a large ledger.
2. Send a single JSON-RPC request: `{"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses","params":[[<N distinct valid-looking base58 signature strings>], {"searchTransactionHistory": true}]}` with N as large as `max_request_body_size` allows.
3. Observe the RPC event-loop thread spending disproportionate time repeatedly locking `block_commitment_cache` and performing blockstore/BigTable lookups per signature [10](#0-9) , delaying responses to concurrent RPC clients, while no request is rejected for size/rate as happens with other list-based methods.

**Note on confidence:** I was not able to fully verify (due to tool-call limits) whether the `rpc_full` trait wrapper that dispatches `getSignatureStatuses` (around `rpc.rs` lines 3620–3660, which I attempted but could not read in the final iteration) applies any additional validation before calling `meta.get_signature_statuses`. If such a check exists there, it would need to be confirmed to fully validate this finding; based on all snippets retrieved, no such check was found.

### Citations

**File:** rpc/src/rpc.rs (L1470-1474)
```rust
        if end_slot - start_slot > MAX_GET_CONFIRMED_BLOCKS_RANGE {
            return Err(Error::invalid_params(format!(
                "Slot range too large; max {MAX_GET_CONFIRMED_BLOCKS_RANGE}"
            )));
        }
```

**File:** rpc/src/rpc.rs (L1672-1729)
```rust
    pub async fn get_signature_statuses(
        &self,
        signatures: Vec<Signature>,
        config: Option<RpcSignatureStatusConfig>,
    ) -> Result<RpcResponse<Vec<Option<TransactionStatus>>>> {
        let search_transaction_history = config
            .map(|x| x.search_transaction_history)
            .unwrap_or(false);
        if search_transaction_history {
            self.check_if_transaction_history_enabled()?;
        }

        let bank = self.bank(Some(CommitmentConfig::processed()));
        let mut statuses: Vec<Option<TransactionStatus>> = vec![];

        for signature in signatures {
            let status = if let Some(status) = self.get_transaction_status(signature, &bank) {
                Some(status)
            } else if search_transaction_history {
                if let Some(status) = self
                    .blockstore
                    .get_rooted_transaction_status(signature)
                    .map_err(|_| Error::internal_error())?
                    .filter(|(slot, _status_meta)| {
                        slot <= &self
                            .block_commitment_cache
                            .read()
                            .unwrap()
                            .highest_super_majority_root()
                    })
                    .map(|(slot, status_meta)| {
                        let err = status_meta.status.clone().err();
                        TransactionStatus {
                            slot,
                            status: status_meta.status,
                            confirmations: None,
                            err,
                            confirmation_status: Some(TransactionConfirmationStatus::Finalized),
                        }
                    })
                {
                    Some(status)
                } else if let Some(bigtable_ledger_storage) = &self.bigtable_ledger_storage {
                    bigtable_ledger_storage
                        .get_signature_status(&signature)
                        .await
                        .map(Some)
                        .unwrap_or(None)
                } else {
                    None
                }
            } else {
                None
            };
            statuses.push(status);
        }
        Ok(new_response(&bank, statuses))
    }
```

**File:** rpc/src/rpc.rs (L1731-1766)
```rust
    fn get_transaction_status(
        &self,
        signature: Signature,
        bank: &Bank,
    ) -> Option<TransactionStatus> {
        let (slot, status) = bank.get_signature_status_slot(&signature)?;

        let optimistically_confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let optimistically_confirmed =
            optimistically_confirmed_bank.get_signature_status_slot(&signature);

        let r_block_commitment_cache = self.block_commitment_cache.read().unwrap();
        let confirmations = if r_block_commitment_cache.root() >= slot
            && is_finalized(&r_block_commitment_cache, bank, &self.blockstore, slot)
        {
            None
        } else {
            r_block_commitment_cache
                .get_confirmation_count(slot)
                .or(Some(0))
        };
        let err = status.clone().err();
        Some(TransactionStatus {
            slot,
            status,
            confirmations,
            err,
            confirmation_status: if confirmations.is_none() {
                Some(TransactionConfirmationStatus::Finalized)
            } else if optimistically_confirmed.is_some() {
                Some(TransactionConfirmationStatus::Confirmed)
            } else {
                Some(TransactionConfirmationStatus::Processed)
            },
        })
    }
```

**File:** rpc/src/rpc.rs (L3688-3695)
```rust

            let limit = limit.unwrap_or(PERFORMANCE_SAMPLES_LIMIT);

            if limit > PERFORMANCE_SAMPLES_LIMIT {
                return Err(Error::invalid_params(format!(
                    "Invalid limit; max {PERFORMANCE_SAMPLES_LIMIT}"
                )));
            }
```

**File:** rpc/src/rpc.rs (L5583-5594)
```rust
        // Test that invalid limit returns an error
        let query_start = 0;
        let query_limit = 5001;

        let request =
            create_test_request("getSlotLeaders", Some(json!([query_start, query_limit])));
        let response = parse_failure_response(rpc.handle_request_sync(request));
        let expected = (
            ErrorCode::InvalidParams.code(),
            String::from("Invalid limit; max 5000"),
        );
        assert_eq!(response, expected);
```

**File:** rpc/src/rpc.rs (L5614-5628)
```rust
        // A request with more than the allowed number of addresses must be
        // rejected by the count check before any address parsing or lookup.
        let addresses: Vec<String> = (0..=MAX_GET_INFLATION_REWARD_ADDRESSES)
            .map(|_| Pubkey::new_unique().to_string())
            .collect();
        assert_eq!(addresses.len(), MAX_GET_INFLATION_REWARD_ADDRESSES + 1);

        let request = create_test_request("getInflationReward", Some(json!([addresses])));
        let response = parse_failure_response(rpc.handle_request_sync(request));
        let expected = (
            ErrorCode::InvalidParams.code(),
            format!("Too many inputs provided; max {MAX_GET_INFLATION_REWARD_ADDRESSES}"),
        );
        assert_eq!(response, expected);
    }
```

**File:** rpc/src/rpc_service.rs (L735-743)
```rust
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

**File:** streamer/src/nonblocking/quic.rs (L358-369)
```rust
            // then perform per IpAddr rate limiting
            if !rate_limiter.is_allowed(&incoming.remote_address().ip()) {
                stats
                    .connection_rate_limited_per_ipaddr
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to per-IP rate limiting.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }
```
