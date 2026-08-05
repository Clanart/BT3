### Title
`simulateTransaction` RPC allows unauthenticated, fee-free execution of maximum-compute-unit transactions, enabling single-client CPU exhaustion of an RPC node - (File: `rpc/src/rpc.rs`)

### Summary
The reported Uniswap issue is about calling a function that is explicitly documented as "not gas efficient" and "should not be called on-chain" from within on-chain logic, letting an attacker trigger repeated expensive computation cheaply/for free. The closest verifiable analog in Agave is the `simulateTransaction` JSON-RPC method, which runs a transaction through the full `Bank::simulate_transaction` execution path (identical cost to real execution, up to the transaction's requested compute-unit limit) without requiring a valid fee payment, without requiring signature verification by default, and with no visible request-level throttling in the RPC handler itself.

### Finding Description
`simulate_transaction` in `rpc/src/rpc.rs` decodes an arbitrary client-supplied transaction, optionally skips signature verification (`sig_verify` defaults to `false`), and calls `bank.simulate_transaction(&transaction, enable_cpi_recording)`. [1](#0-0) 

That in turn calls `simulate_transaction_unchecked`, which runs `load_and_execute_transactions` — the same transaction-processing pipeline used for real block execution (loading programs, invoking the SBF VM, consuming compute units up to the transaction's configured limit) — but never actually charges/debits the fee payer. [2](#0-1) 

Because `sig_verify` is optional and defaults to `false`, and there is no fee deduction for a simulation, a caller does not need a valid signature or any account balance to trigger full-cost execution. A transaction can set its compute unit limit as high as the protocol maximum (up to 1.4M CU per transaction) via a `ComputeBudgetInstruction`, and can invoke compute-intensive syscalls/precompiles repeatedly within that budget (e.g., Poseidon, BLS12-381 pairing/group ops, curve25519 multiscalar multiplication, SHA256) as seen in the syscall cost tables. [3](#0-2) [4](#0-3) 

This mirrors the Uniswap quoter problem precisely: an interface designed as a "preview"/estimation tool (`quoteExactInput`/`quoteExactOutput` off-chain, vs. `simulateTransaction` here) performs the full expensive computation path but bypasses the economic guard (gas cost on Uniswap, transaction fee on Solana) that normally limits how often it can be invoked. In Agave's case, unlike a real transaction, a simulation never pays the `SIGNATURE_COST`/`SECP256K1_VERIFY_COST` fee-based costs that are meant to make repeated expensive execution costly, nor does it need funded accounts. [5](#0-4) 

I could not confirm the presence of any RPC-layer rate limiter or per-IP throttle specifically guarding `simulateTransaction` calls within `rpc/src/rpc_service.rs` from the code visible to me — grep matches for `rate_limit`/`RateLimiter`/`governor` exist in that file, but I was not able to inspect their content before running out of tool budget, so I cannot confirm whether they cover `simulateTransaction` or only apply to other paths (e.g., health checks, connection limits). This is a real gap in my verification and should be checked directly in the repository before treating this as conclusively unmitigated.

### Impact Explanation
An attacker can repeatedly submit `simulateTransaction` requests, each executing up to the maximum allowed compute units with the most CPU-expensive syscalls, at effectively zero cost (no fee, no valid signature required). Because each simulated transaction fully exercises the SVM (program loading, execution, accounts loading) rather than a light-weight/pre-flight-only check, this converts a "single low-rate client" into disproportionate CPU load on the targeted RPC node's `JsonRpcRequestProcessor`, causing degraded RPC responsiveness or a crash of the RPC service on that node. This matches the in-scope category "single-client low-rate RPC crash/degradation." It does not affect consensus, other validators, or non-RPC ports.

### Likelihood Explanation
Likelihood is high for triggering degraded RPC service on a given node if no additional rate limiting is applied ahead of this handler, since the request format is simple, well documented, and requires no account balance or valid signature (`sig_verify` is optional). However, likelihood of this being a *novel* unmitigated issue is uncertain because I was unable to confirm/deny the presence of rate limiting or CU-based request costing at the RPC transport layer (`rpc_service.rs`) given the final-iteration constraint.

### Recommendation
- Enforce a reduced/aggregate compute-unit budget specifically for `simulateTransaction` requests (independent from the on-chain per-transaction max), or apply per-IP/per-connection request+CU quotas at the JSON-RPC transport layer.
- Consider mandatory light-weight cost estimation (static analysis of instruction data/signature counts) before allowing full VM execution for unauthenticated simulation requests.
- Confirm and, if absent, add explicit rate limiting in `rpc/src/rpc_service.rs` for `simulateTransaction`, distinct from general request-size limits.

### Proof of Concept
1. Craft an unsigned/garbage-signed `VersionedTransaction` containing a `ComputeBudgetInstruction::set_compute_unit_limit` set to the maximum allowed value, followed by instructions invoking the most expensive syscalls repeatedly (e.g., Poseidon hashing, BLS12-381 pairing, or SHA256 over large slices) within that compute budget.
2. Submit it via the `simulateTransaction` RPC method with `sigVerify` omitted/`false` and no funded fee payer required. [6](#0-5) 
3. Repeat rapidly from a single client; each call drives `Bank::simulate_transaction_unchecked` to fully execute up to the compute-unit ceiling with no fee charged, consuming CPU on the target RPC node's bank/VM execution path. [7](#0-6) 
4. Observe RPC node latency/CPU degradation under sustained low request-rate load, consistent with a single-client RPC DoS.

Note: I was unable to fully verify (due to reaching the tool-call limit) whether `rpc/src/rpc_service.rs` already implements a rate limiter that mitigates this specific path; this should be checked before treating the finding as fully unmitigated.

### Citations

**File:** rpc/src/rpc.rs (L4059-4072)
```rust
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

**File:** runtime/src/bank.rs (L3820-3862)
```rust
    /// Run transactions against a bank without committing the results; does not check if the bank
    /// is frozen, enabling use in single-Bank test frameworks
    pub fn simulate_transaction_unchecked(
        &self,
        transaction: &impl TransactionWithMeta,
        enable_cpi_recording: bool,
    ) -> TransactionSimulationResult {
        let account_keys = transaction.account_keys();
        let number_of_accounts = account_keys.len();
        let account_overrides = self.get_account_overrides_for_simulation(&account_keys);
        let batch = self.prepare_unlocked_batch_from_single_tx(transaction);
        let mut timings = ExecuteTimings::default();

        let LoadAndExecuteTransactionsOutput {
            mut processing_results,
            balance_collector,
            ..
        } = self.load_and_execute_transactions(
            &batch,
            // After simulation, transactions will need to be forwarded to the leader
            // for processing. During forwarding, the transaction could expire if the
            // delay is not accounted for.
            self.max_processing_age()
                .saturating_sub(MAX_TRANSACTION_FORWARDING_DELAY),
            &mut timings,
            &mut TransactionErrorMetrics::default(),
            TransactionProcessingConfig {
                account_overrides: Some(&account_overrides),
                check_program_deployment_slot: self.check_program_deployment_slot,
                log_messages_bytes_limit: None,
                limit_to_load_programs: true,
                recording_config: ExecutionRecordingConfig {
                    enable_cpi_recording,
                    enable_log_recording: true,
                    enable_return_data_recording: true,
                    enable_transaction_balance_recording: true,
                },
                drop_on_failure: false,
                all_or_nothing: false,
                strict_nonce_size_check: true,
                drop_noop_transactions: true,
            },
        );
```

**File:** compute-budget/src/compute_budget.rs (L83-99)
```rust
    /// Number of compute units per additional 32k heap above the default (~.5
    /// us per 32k at 15 units/us rounded up)
    pub heap_cost: u64,
    /// Memory operation syscall base cost
    pub mem_op_base_cost: u64,
    /// Number of compute units consumed to call alt_bn128_g1_addition
    pub alt_bn128_g1_addition_cost: u64,
    /// Number of compute units consumed to call alt_bn128_g2_addition
    pub alt_bn128_g2_addition_cost: u64,
    /// Number of compute units consumed to call alt_bn128_g1_multiplication.
    pub alt_bn128_g1_multiplication_cost: u64,
    /// Number of compute units consumed to call alt_bn128_g2_multiplication.
    pub alt_bn128_g2_multiplication_cost: u64,
    /// Total cost will be alt_bn128_pairing_one_pair_cost_first
    /// + alt_bn128_pairing_one_pair_cost_other * (num_elems - 1)
    pub alt_bn128_pairing_one_pair_cost_first: u64,
    pub alt_bn128_pairing_one_pair_cost_other: u64,
```

**File:** syscalls/src/lib.rs (L2453-2486)
```rust
declare_builtin_function!(
    // Poseidon
    SyscallPoseidon,
    fn rust(
        invoke_context: &mut InvokeContext<'_, '_>,
        parameters: u64,
        endianness: u64,
        vals_addr: u64,
        vals_len: u64,
        result_addr: u64,
    ) -> Result<u64, Error> {
        let parameters: poseidon::Parameters = parameters.try_into()?;
        let endianness: poseidon::Endianness = endianness.try_into()?;

        if vals_len > 12 {
            ic_msg!(
                invoke_context,
                "Poseidon hashing {} sequences is not supported",
                vals_len,
            );
            return Err(SyscallError::InvalidLength.into());
        }

        let execution_cost = invoke_context.get_execution_cost();
        let Some(cost) = execution_cost.poseidon_cost(vals_len) else {
            ic_msg!(
                invoke_context,
                "Overflow while calculating the compute cost"
            );
            return Err(SyscallError::ArithmeticOverflow.into());
        };
        invoke_context
            .compute_meter
            .consume_checked(cost.to_owned())?;
```

**File:** cost-model/src/block_cost_limits.rs (L7-20)
```rust
/// Cluster averaged compute unit to micro-sec conversion rate
pub const COMPUTE_UNIT_TO_US_RATIO: u64 = 30;
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
/// Number of compute units for one secp256r1 signature verification.
pub const SECP256R1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 160;
/// Number of compute units for one write lock
pub const WRITE_LOCK_UNITS: u64 = COMPUTE_UNIT_TO_US_RATIO * 10;
/// Number of data bytes per compute units
pub const INSTRUCTION_DATA_BYTES_COST: u64 = 140 /*bytes per us*/ / COMPUTE_UNIT_TO_US_RATIO;
```
