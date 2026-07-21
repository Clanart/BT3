### Title
`skip_stateful_validations` Bypasses Account Signature Verification for Invoke Transactions When Any Prior Transaction Exists in Mempool — (`File: crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The `skip_stateful_validations` function in the gateway's stateful validator skips the `__validate__` entry point — the only place where an account's transaction signature is cryptographically verified — for any invoke transaction with `nonce=1` when `account_tx_in_pool_or_recent_block` returns `true`. The guard check is documented as a proxy for "a deploy_account transaction exists," but it returns `true` for **any** transaction from that account in the pool, including a regular nonce-0 invoke. An attacker can exploit this to submit an invoke transaction with a forged or invalid signature that is accepted into the mempool without signature verification.

---

### Finding Description

The gateway's `extract_state_nonce_and_run_validations` method calls `run_pre_validation_checks`, which calls `skip_stateful_validations`. That function returns `true` (skip) when all three conditions hold:

```
tx is Invoke  AND  tx.nonce == 1  AND  account_nonce == 0
AND  account_tx_in_pool_or_recent_block(sender) == true
``` [1](#0-0) 

When `skip_validate=true`, `run_validate_entry_point` sets `ExecutionFlags { validate: false }` and calls `StatefulValidator::validate`, which delegates to `perform_validations`. [2](#0-1) 

Inside `perform_validations`, for an Invoke transaction with `validate=false`, the code runs `perform_pre_validation_stage` (nonce increment in a temporary cached state, fee bounds, proof facts) and then **returns early** — the `__validate__` entry point is never called and `PostValidationReport::verify` is never run. [3](#0-2) 

The guard `account_tx_in_pool_or_recent_block` is implemented as:

```rust
self.state.contains_account(account_address)
    || self.tx_pool.contains_account(account_address)
``` [4](#0-3) 

`tx_pool.contains_account` returns `true` for **any** transaction type from that address — not only `DeployAccount`. The code comment claims this is sufficient because the account "either has a deploy_account transaction or transactions with future nonces that passed validations," but the second branch is incorrect: a nonce-0 invoke having passed validation does not imply a nonce-1 invoke with a different (or absent) signature is valid.

The stateless validator does not verify signatures cryptographically — it only checks signature field length. [5](#0-4) 

The mempool's `validate_tx` checks only for duplicate hashes and nonce ordering — no signature check. [6](#0-5) 

Therefore, when `skip_validate=true`, **no component in the pipeline verifies the transaction signature**.

---

### Impact Explanation

An invoke transaction carrying an invalid or forged signature is accepted by the gateway and inserted into the mempool without any cryptographic verification of the signer. This matches:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

The invalid transaction will fail during batcher execution (the account contract's `__validate__` will reject it), but it has already been admitted, consuming mempool slots and batcher resources, and can be included in a block proposal before being rejected.

---

### Likelihood Explanation

The trigger conditions are easy to satisfy in normal operation:

1. Account A is deployed; its on-chain nonce is `0` (no committed transactions yet).
2. Account A submits a valid invoke with `nonce=0`; it enters the mempool.
3. An attacker submits an invoke for account A with `nonce=1` and an **invalid signature**.

Step 2 is a routine state for any newly deployed account that has submitted its first transaction. The attacker does not need to control account A — they only need to know its address and that it has a pending nonce-0 transaction.

---

### Recommendation

`skip_stateful_validations` should only suppress `__validate__` when the account genuinely cannot be validated because it does not yet exist on-chain. The guard should be tightened to confirm that the transaction in the pool is specifically a `DeployAccount` transaction for the same address, rather than accepting any transaction type. Alternatively, the function should verify that the account contract class does not yet exist in state before skipping validation.

---

### Proof of Concept

1. Deploy account A (class hash `C`, salt `S`). Account A's on-chain nonce is `0`.
2. Submit a valid `InvokeV3` from account A with `nonce=0` to the gateway. It passes stateless and stateful validation and enters the mempool. `tx_pool.contains_account(A)` is now `true`.
3. Craft a second `InvokeV3` from account A with `nonce=1` and a **garbage signature** (e.g., `[0x1, 0x2]`).
4. Submit it to the gateway.
   - `validate_nonce`: `account_nonce=0`, `tx_nonce=1`, within `max_allowed_nonce_gap` → passes.
   - `validate_by_mempool`: no duplicate hash or nonce conflict → passes.
   - `skip_stateful_validations`: `tx.nonce()==1`, `account_nonce==0`, `account_tx_in_pool_or_recent_block(A)==true` → returns `true`.
   - `run_validate_entry_point(skip_validate=true)`: `ExecutionFlags { validate: false }` → `__validate__` is never called.
5. The gateway returns success. The invalid transaction is now in the mempool. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L131-152)
```rust
    async fn extract_state_nonce_and_run_validations(
        &mut self,
        executable_tx: &ExecutableTransaction,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<Nonce> {
        let account_nonce =
            self.get_nonce_from_state(executable_tx.contract_address()).await.map_err(|e| {
                // TODO(noamsp): Fix this. Need to map the errors better.
                StarknetError::internal_with_signature_logging(
                    format!(
                        "Failed to get nonce for sender address {}",
                        executable_tx.contract_address()
                    ),
                    &executable_tx.signature(),
                    e,
                )
            })?;
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L279-315)
```rust
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };

        let account_tx = AccountTransaction { tx: executable_tx.clone(), execution_flags };

        // Build block context.
        let mut versioned_constants = VersionedConstants::get_versioned_constants(
            self.config.versioned_constants_overrides.clone(),
        );
        // The validation of a transaction is not affected by the casm hash migration.
        versioned_constants.disable_casm_hash_migration();

        let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
        block_info.block_number = block_info.block_number.unchecked_next();
        let block_context = BlockContext::new(
            block_info,
            self.chain_info.clone(),
            versioned_constants,
            BouncerConfig::max(),
        );

        // Move state into the blocking task and run CPU-heavy validation.
        let state_reader_and_contract_manager = self.take_state_reader_and_contract_manager();

        let cur_span = Span::current();
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
            })
        })
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L401-432)
```rust
async fn skip_stateful_validations(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<bool> {
    if let ExecutableTransaction::Invoke(ExecutableInvokeTransaction { tx, .. }) = tx {
        // check if the transaction nonce is 1, meaning it is post deploy_account, and the
        // account nonce is zero, meaning the account was not deployed yet.
        if tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO) {
            let account_address = tx.sender_address();
            debug!("Checking if deploy_account transaction exists for account {account_address}.");
            // We verify that a deploy_account transaction exists for this account. It is sufficient
            // to check if the account exists in the mempool since it means that either it has a
            // deploy_account transaction or transactions with future nonces that passed
            // validations.
            return mempool_client
                .account_tx_in_pool_or_recent_block(tx.sender_address())
                .await
                .map_err(|err| mempool_client_err_to_deprecated_gw_err(&tx.signature(), err))
                .inspect(|exists| {
                    if *exists {
                        debug!("Found deploy_account transaction for account {account_address}.");
                    } else {
                        debug!(
                            "No deploy_account transaction found for account {account_address}."
                        );
                    }
                });
        }
    }

    Ok(false)
```

**File:** crates/blockifier/src/blockifier/stateful_validator.rs (L76-95)
```rust
            ApiTransaction::Invoke(_) => {
                let tx_context = Arc::new(self.tx_executor.block_context.to_tx_context(&tx));
                tx.perform_pre_validation_stage(self.state(), &tx_context)?;
                if !tx.execution_flags.validate {
                    return Ok(());
                }

                // `__validate__` call.
                let (_optional_call_info, actual_cost) = self.validate(&tx, tx_context.clone())?;

                // Post validations.
                PostValidationReport::verify(
                    &tx_context,
                    &actual_cost,
                    tx.execution_flags.charge_fee,
                )?;

                Ok(())
            }
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L390-396)
```rust
    pub fn validate_tx(&mut self, args: ValidationArgs) -> MempoolResult<()> {
        let tx_reference = (&args).into();
        self.validate_incoming_tx(tx_reference, args.account_nonce)?;
        self.handle_fee_escalation(tx_reference, true)?;

        Ok(())
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L658-661)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-54)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```
