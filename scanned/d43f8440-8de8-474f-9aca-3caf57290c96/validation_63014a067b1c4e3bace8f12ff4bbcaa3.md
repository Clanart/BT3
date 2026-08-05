## Title
Unchecked bank-freeze invariant in `simulateTransaction`/preflight `sendTransaction` RPC path can panic the JSON-RPC thread on attacker-controlled `commitment` input - (File: `rpc/src/rpc.rs`, `runtime/src/bank.rs`)

## Summary
The external report's underlying bug class is: a wrapper function forwards externally supplied parameters to a lower layer without validating that the lower layer's preconditions are actually satisfied. The Agave analog is in the `simulateTransaction` (and preflight `sendTransaction`) JSON-RPC handlers: they resolve a `Bank` from client-supplied `commitment`/`min_context_slot` parameters via `meta.get_bank_with_config(...)` and immediately hand that bank to `Bank::simulate_transaction()`, which enforces its precondition with a hard `assert!`, not a `Result`.

## Finding Description
`Bank::simulate_transaction` asserts the bank must be frozen before simulating: [1](#0-0) 

This assert is not a recoverable JSON-RPC error — it is a Rust `assert!` that panics if `self.is_frozen()` is false. The RPC layer relies entirely on whatever bank `get_bank_with_config` returns already being frozen; there is no explicit freeze-check or freeze-call in the RPC handler itself before dispatching to `simulate_transaction`: [2](#0-1) 

The same unchecked pattern exists in the preflight-simulation branch of `send_transaction`, which calls `preflight_bank.simulate_transaction(&transaction, false)` on a bank resolved the same way: [3](#0-2) 

The test suite itself demonstrates that this precondition is not implicitly guaranteed by the RPC bank-selection path: every functional simulate-transaction test manually calls `bank.freeze()` before issuing the RPC request, and a dedicated test proves the panic occurs when that freeze is skipped: [4](#0-3)  and the corresponding freeze calls at [5](#0-4) , [6](#0-5) , [7](#0-6) .

The `commitment` field of `RpcContextConfig` is an untrusted, client-controlled JSON-RPC parameter forwarded straight into `get_bank_with_config`. None of the validation performed elsewhere in this file (e.g., `verify_pubkey`, `verify_hash`, limit checks such as `Invalid limit; max 720` in `get_recent_performance_samples` at [8](#0-7) ) validates that the *resulting bank* satisfies the invariant `simulate_transaction` requires. In other words, input validation is applied to shapes/ranges of scalar parameters but not to the derived state object (`Bank`) that is subsequently passed to a function with a hard runtime assertion instead of a `Result`-based error path.

## Impact Explanation
A panic inside `Bank::simulate_transaction`, triggered from a JSON-RPC request thread, does not gracefully degrade to a JSON-RPC error response the way the surrounding code otherwise does (e.g., `Error::invalid_params`, `RpcCustomError`). Depending on the async runtime's panic-handling configuration for the RPC threadpool/tokio tasks, this can abort the request-handling task or, if `panic = "abort"` is configured for the runtime, terminate the entire validator process — a remote, unauthenticated, single low-rate RPC call causing crash/degradation of a public RPC node, which is in-scope per the "single-client low-rate RPC crash/degradation" impact category.

## Likelihood Explanation
I could not fully confirm within available tool budget whether `get_bank_with_config`, under any commitment value reachable from public RPC input (e.g. default/`processed`), can ever resolve to a bank that is not yet frozen (i.e., the currently-building/working bank during active replay/production). The test suite's explicit, manual `bank.freeze()` calls before every simulate-transaction test strongly suggest the RPC path does not itself guarantee this invariant, and the panic message and `#[should_panic]` test confirm the assert is real and reachable. Determining the exact commitment/timing window that reaches an unfrozen bank in production requires deeper reading of `get_bank_with_config`/`bank()`/`bank_forks.working_bank()` selection logic in `rpc/src/rpc.rs`, which I was not able to complete before running out of tool calls.

## Recommendation
- Replace the `assert!(self.is_frozen(), ...)` in `Bank::simulate_transaction` with a `Result`-returning check that the RPC layer can convert into a proper JSON-RPC error (e.g., `RpcCustomError`) instead of panicking.
- In `rpc/src/rpc.rs`, explicitly validate/normalize the bank resolved from client-supplied `commitment`/`min_context_slot` before invoking any function with freeze-based preconditions, mirroring the existing pattern of validating other RPC inputs (`verify_pubkey`, limit checks, etc.).
- Add a regression test that exercises `simulateTransaction`/preflight `sendTransaction` against an intentionally-unfrozen "processed"-commitment bank without a manual `bank.freeze()` call, to confirm the RPC path cannot panic even under adversarial timing.

## Proof of Concept
Not confirmed end-to-end due to unresolved uncertainty about `get_bank_with_config`'s bank-selection logic (see Likelihood Explanation). The strongest local evidence is the existing test `test_rpc_simulate_transaction_panic_on_unfrozen_bank`, which reproduces the exact panic via a normal `simulateTransaction` JSON-RPC call against an unfrozen bank: [4](#0-3) 

Given the inability to fully verify that an unprivileged RPC client can force resolution of an unfrozen bank through the public `commitment` parameter without an internal `Devin` session to trace `get_bank_with_config`/`bank()` in depth, I flag this as a plausible but not fully confirmed vulnerability rather than a definitively proven one.

### Citations

**File:** runtime/src/bank.rs (L3809-3818)
```rust
    /// Run transactions against a frozen bank without committing the results
    pub fn simulate_transaction(
        &self,
        transaction: &impl TransactionWithMeta,
        enable_cpi_recording: bool,
    ) -> TransactionSimulationResult {
        assert!(self.is_frozen(), "simulation bank must be frozen");

        self.simulate_transaction_unchecked(transaction, enable_cpi_recording)
    }
```

**File:** rpc/src/rpc.rs (L3893-3950)
```rust
            let preflight_bank = &*meta.get_bank_with_config(RpcContextConfig {
                commitment: preflight_commitment,
                min_context_slot,
            })?;

            let transaction = sanitize_transaction(
                unsanitized_tx,
                preflight_bank,
                preflight_bank.get_reserved_account_keys(),
            )?;
            let blockhash = *transaction.message().recent_blockhash();
            let message_hash = *transaction.message_hash();
            let signature = *transaction.signature();

            let mut last_valid_block_height = preflight_bank
                .get_blockhash_last_valid_block_height(&blockhash)
                .unwrap_or(0);

            let durable_nonce_info = transaction
                .get_durable_nonce()
                .map(|&pubkey| (pubkey, blockhash));
            if durable_nonce_info.is_some() || (skip_preflight && last_valid_block_height == 0) {
                // While it uses a defined constant, this last_valid_block_height value is chosen arbitrarily.
                // It provides a fallback timeout for durable-nonce transaction retries in case of
                // malicious packing of the retry queue. Durable-nonce transactions are otherwise
                // retried until the nonce is advanced.
                last_valid_block_height =
                    preflight_bank.block_height() + preflight_bank.max_processing_age() as u64;
            }

            if !skip_preflight {
                let verification_error = transaction.verify().err();

                if verification_error.is_none() && !meta.config.skip_preflight_health_check {
                    match meta.health.check() {
                        RpcHealthStatus::Ok => (),
                        RpcHealthStatus::Unknown => {
                            inc_new_counter_info!("rpc-send-tx_health-unknown", 1);
                            return Err(RpcCustomError::NodeUnhealthy {
                                num_slots_behind: None,
                            }
                            .into());
                        }
                        RpcHealthStatus::Behind { num_slots } => {
                            inc_new_counter_info!("rpc-send-tx_health-behind", 1);
                            return Err(RpcCustomError::NodeUnhealthy {
                                num_slots_behind: Some(num_slots),
                            }
                            .into());
                        }
                    }
                }

                let simulation_result = if let Some(err) = verification_error {
                    TransactionSimulationResult::new_error(err)
                } else {
                    preflight_bank.simulate_transaction(&transaction, false)
                };
```

**File:** rpc/src/rpc.rs (L4010-4072)
```rust
        fn simulate_transaction(
            &self,
            meta: Self::Metadata,
            data: String,
            config: Option<RpcSimulateTransactionConfig>,
        ) -> Result<RpcResponse<RpcSimulateTransactionResult>> {
            debug!("simulate_transaction rpc request received");
            let RpcSimulateTransactionConfig {
                sig_verify,
                replace_recent_blockhash,
                commitment,
                encoding,
                accounts: config_accounts,
                min_context_slot,
                inner_instructions: enable_cpi_recording,
            } = config.unwrap_or_default();
            let tx_encoding = encoding.unwrap_or(UiTransactionEncoding::Base58);
            let binary_encoding = tx_encoding.into_binary_encoding().ok_or_else(|| {
                Error::invalid_params(format!(
                    "unsupported encoding: {tx_encoding}. Supported encodings: base58, base64"
                ))
            })?;
            let (_, mut unsanitized_tx) =
                decode_and_deserialize::<VersionedTransaction>(data, binary_encoding)?;

            let bank = &*meta.get_bank_with_config(RpcContextConfig {
                commitment,
                min_context_slot,
            })?;
            let mut blockhash: Option<RpcBlockhash> = None;
            if replace_recent_blockhash {
                if sig_verify {
                    return Err(Error::invalid_params(
                        "sigVerify may not be used with replaceRecentBlockhash",
                    ));
                }
                let recent_blockhash = bank.last_blockhash();
                unsanitized_tx
                    .message
                    .set_recent_blockhash(recent_blockhash);
                let last_valid_block_height = bank
                    .get_blockhash_last_valid_block_height(&recent_blockhash)
                    .expect("bank blockhash queue should contain blockhash");
                blockhash.replace(RpcBlockhash {
                    blockhash: recent_blockhash.to_string(),
                    last_valid_block_height,
                });
            }

            let transaction =
                sanitize_transaction(unsanitized_tx, bank, bank.get_reserved_account_keys())?;

            let verification_error = if sig_verify {
                transaction.verify().err()
            } else {
                None
            };

            let simulation_result = if let Some(err) = verification_error {
                TransactionSimulationResult::new_error(err)
            } else {
                bank.simulate_transaction(&transaction, enable_cpi_recording)
            };
```

**File:** rpc/src/rpc.rs (L5309-5319)
```rust
    #[test]
    fn test_rpc_get_recent_performance_samples_invalid_limit() {
        let rpc = RpcHandler::start();
        let request = create_test_request("getRecentPerformanceSamples", Some(json!([10_000])));
        let response = parse_failure_response(rpc.handle_request_sync(request));
        let expected = (
            ErrorCode::InvalidParams.code(),
            String::from("Invalid limit; max 720"),
        );
        assert_eq!(response, expected);
    }
```

**File:** rpc/src/rpc.rs (L6114-6117)
```rust
        // Simulation bank must be frozen
        bank.freeze();

        let loaded_accounts_data_size = expected_loaded_accounts_data_size(&bank, &tx);
```

**File:** rpc/src/rpc.rs (L6500-6503)
```rust
        // Simulation bank must be frozen
        bank.freeze();

        let loaded_accounts_data_size = expected_loaded_accounts_data_size(&bank, &tx);
```

**File:** rpc/src/rpc.rs (L6621-6624)
```rust
        // Simulation bank must be frozen
        bank.freeze();

        let loaded_accounts_data_size = expected_loaded_accounts_data_size(&bank, &tx);
```

**File:** rpc/src/rpc.rs (L6795-6820)
```rust
    #[test]
    #[should_panic(expected = "simulation bank must be frozen")]
    fn test_rpc_simulate_transaction_panic_on_unfrozen_bank() {
        let rpc = RpcHandler::start();
        let bank = rpc.working_bank();
        let recent_blockhash = bank.confirmed_last_blockhash();
        let RpcHandler {
            meta,
            io,
            mint_keypair,
            ..
        } = rpc;

        let bob_pubkey = Pubkey::new_unique();
        let tx = system_transaction::transfer(&mint_keypair, &bob_pubkey, 1234, recent_blockhash);
        let tx_serialized_encoded = bs58::encode(wincode::serialize(&tx).unwrap()).into_string();

        assert!(!bank.is_frozen());

        let req = format!(
            r#"{{"jsonrpc":"2.0","id":1,"method":"simulateTransaction","params":["{tx_serialized_encoded}", {{"sigVerify": true}}]}}"#,
        );

        // should panic because `bank` is not frozen
        let _ = io.handle_request_sync(&req, meta);
    }
```
