### Title
Gateway Admission Bypasses Sender-Address Block List, Allowing Blocked Addresses to Transact — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

---

### Summary

`StatelessTransactionValidator::validate()` contains an explicit, unimplemented TODO to check whether the sender address is blocked. Because the check is absent, any address that the operator intends to block can still submit Invoke, Declare, and DeployAccount transactions through the gateway. Those transactions pass both stateless and stateful validation, enter the mempool, and are sequenced into blocks. The only blocking mechanism that exists lives in the separate `starknet_transaction_prover` service and is never consulted during gateway admission.

---

### Finding Description

`StatelessTransactionValidator::validate()` is the first enforcement point for every inbound `RpcTransaction`. It checks resource bounds, calldata/signature sizes, DA modes, Sierra version, and proof consistency — but it explicitly skips the sender-address block check:

```rust
// TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
``` [1](#0-0) 

The gateway's `add_tx_inner` calls `stateless_tx_validator.validate(&tx)` and then `stateful_transaction_validator.extract_state_nonce_and_run_validations`. Neither path consults a block list for Invoke or DeployAccount transactions. [2](#0-1) 

The only address-level restriction that exists at the gateway is `check_declare_permissions`, which enforces an **allowlist** (`authorized_declarer_accounts`) exclusively for Declare transactions. It is not a block list and does not cover Invoke or DeployAccount. [3](#0-2) 

A separate blocking mechanism does exist, but it lives entirely inside `VirtualSnosProver::prove_transaction()` in the `starknet_transaction_prover` crate. It calls an external `starknet_checkTransaction` JSON-RPC service and returns `TransactionBlocked` only at proving time — long after the transaction has already been admitted to the mempool and sequenced. [4](#0-3) [5](#0-4) 

The `StatelessTransactionValidatorConfig` struct has no field for a block list; there is no configuration surface through which a blocked-address set could be plumbed into the validator even if the operator wanted to. [6](#0-5) 

---

### Impact Explanation

A blocked sender address can submit any transaction type (Invoke, Declare, DeployAccount) to the gateway. The transaction passes stateless validation, passes stateful validation (nonce/fee checks), enters the mempool, and is included in a sequenced block. The operator's intent to block the address is a no-op at the admission layer. This matches the **High** impact scope: *Mempool/gateway/RPC admission accepts invalid transactions before sequencing.*

---

### Likelihood Explanation

Any user who knows their address has been added to the operator's block list can trivially bypass it by submitting directly to the gateway RPC endpoint. No special privilege or knowledge of internal state is required — the sender address is a field in every submitted transaction. The TODO comment has been present since at least 1 May 2024, indicating the gap is known and persistent.

---

### Recommendation

Add a `blocked_sender_addresses: HashSet<ContractAddress>` (or equivalent) field to `StatelessTransactionValidatorConfig` and enforce it at the top of `StatelessTransactionValidator::validate()`, before any other check, for all transaction types including DeployAccount (where the deployer address is the derived contract address). This mirrors the pattern already used for `validate_empty_account_deployment_data` and `validate_empty_paymaster_data`, which were added to the stateless validator specifically to enforce OS-level invariants early.

```rust
pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
    if let Some(sender) = tx.sender_address() {
        if self.config.blocked_sender_addresses.contains(&sender) {
            return Err(StatelessTransactionValidatorError::BlockedSenderAddress { sender });
        }
    }
    // ... existing checks
}
```

---

### Proof of Concept

1. Operator adds address `0xABCD` to the intended block list (currently no enforcement path exists in the gateway config).
2. User at `0xABCD` submits an `RpcTransaction::Invoke` with a valid nonce and resource bounds to the gateway's `add_tx` endpoint.
3. `StatelessTransactionValidator::validate()` runs: `validate_contract_address` passes (address is syntactically valid), all other checks pass. The TODO block-list check is absent — `Ok(())` is returned.
4. `StatefulTransactionValidator` checks nonce and fee bounds — both pass.
5. Transaction is forwarded to the mempool via `mempool_client.add_tx(...)`.
6. The batcher picks the transaction up and includes it in the next block.
7. The `starknet_transaction_prover` is a separate service; if it is not configured or if `blocking_check_fail_open = true`, the transaction is proved without issue. Even if the prover blocks it, the transaction is already sequenced and committed to the state diff. [1](#0-0) [7](#0-6)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-54)
```rust
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

**File:** crates/apollo_gateway/src/gateway.rs (L181-240)
```rust
    async fn add_tx_inner(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        let mut metric_counters = GatewayMetricHandle::new(&tx, &p2p_message_metadata);
        metric_counters.count_transaction_received();

        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;

        let tx_signature = tx.signature().clone();
        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;

        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator()
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let proof_archive_handle =
            self.store_proof_and_spawn_archiving(proof_data, internal_tx.tx_hash).await;

        let gateway_output = create_gateway_output(&internal_tx);

        let add_tx_args = AddTransactionArgsWrapper {
            args: AddTransactionArgs::new(internal_tx, nonce),
            p2p_message_metadata,
        };
        let mempool_client_result = self.mempool_client.add_tx(add_tx_args).await;
        match mempool_client_result_to_deprecated_gw_result(&tx_signature, mempool_client_result) {
            Ok(()) => {}
            Err(e) => {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        };

        metric_counters.transaction_sent_to_mempool();

        // We await proof archiving only after the transaction is sent to the mempool to avoid
        // delays.
        Self::await_proof_archiving(proof_archive_handle).await;

        Ok(gateway_output)
    }

```

**File:** crates/apollo_gateway/src/gateway.rs (L319-345)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
        Ok(())
    }
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L153-181)
```rust
    pub async fn prove_transaction(
        &self,
        block_id: BlockId,
        transaction: RpcTransaction,
    ) -> Result<ProveTransactionResult, VirtualSnosProverError> {
        let start_time = Instant::now();

        // Validate block_id is not pending.
        if matches!(block_id, BlockId::Pending) {
            return Err(VirtualSnosProverError::ValidationError(
                "Pending blocks are not supported; only finalized blocks can be proven."
                    .to_string(),
            ));
        }

        let invoke_v3 = extract_rpc_invoke_tx(transaction.clone())?;
        validate_transaction_input(&invoke_v3, self.validate_zero_fee_fields)?;
        let invoke_tx = InvokeTransaction::V3(invoke_v3.into());

        let result = match &self.blocking_check_client {
            None => self.run_and_prove(block_id, vec![invoke_tx]).await?,
            Some(client) => {
                self.prove_with_blocking_check(client, block_id, transaction, invoke_tx).await?
            }
        };

        info!(total_duration_ms = %start_time.elapsed().as_millis(), "prove_transaction completed");
        Ok(result)
    }
```

**File:** crates/starknet_transaction_prover/src/blocking_check.rs (L80-126)
```rust
    /// Sends `starknet_checkTransaction` to the external service and interprets the response.
    pub(crate) async fn check_transaction(
        &self,
        block_id: BlockId,
        transaction: RpcTransaction,
    ) -> BlockingCheckResult {
        let request_body = JsonRpcRequest {
            jsonrpc: "2.0",
            method: "starknet_checkTransaction",
            params: CheckTransactionParams { block_id, transaction },
            id: 1,
        };

        let response =
            match self.http_client.post(self.url.as_str()).json(&request_body).send().await {
                Ok(resp) => resp,
                Err(err) => {
                    warn!("Blocking check request failed: {err}");
                    return BlockingCheckResult::Inconclusive;
                }
            };

        let body = match response.text().await {
            Ok(text) => text,
            Err(err) => {
                warn!("Failed to read blocking check response body: {err}");
                return BlockingCheckResult::Inconclusive;
            }
        };

        let json_rpc_response: JsonRpcResponse = match serde_json::from_str(&body) {
            Ok(parsed) => parsed,
            Err(err) => {
                warn!("Failed to parse blocking check response: {err}");
                return BlockingCheckResult::Inconclusive;
            }
        };

        match json_rpc_response.error {
            None => BlockingCheckResult::Allowed,
            Some(err) if err.code == BLOCKED_ERROR_CODE => BlockingCheckResult::Blocked,
            Some(err) => {
                warn!("Blocking check returned non-blocking error code: {}", err.code);
                BlockingCheckResult::Inconclusive
            }
        }
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L139-155)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_200_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 8, usize::MAX),
            allow_client_side_proving: false,
            max_proof_size: 480000,
        }
    }
}
```
