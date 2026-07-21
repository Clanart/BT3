### Title
Silent Proof-Manager Storage Failure in Gateway Allows Proof-Facts Invoke Transactions Into Mempool Without Stored Proof, Breaking Proposer Proposal Construction - (File: `crates/apollo_gateway/src/gateway.rs`)

---

### Summary

The gateway's two-step proof handling (verify → store) silently swallows storage errors. When `store_proof_in_proof_manager` fails, the error is only logged and the transaction is still forwarded to the mempool. Later, when the proposer converts the internal transaction back to RPC format for broadcasting to validators, `convert_internal_rpc_tx_to_rpc_tx` calls `get_proof`, which returns `None` because the proof was never persisted. This breaks proposal construction for any block containing such a transaction.

---

### Finding Description

**Step 1 — Verify-only, no storage (gateway flow).**
`convert_rpc_tx_to_internal_rpc_tx` calls `spawn_proof_verification`, which only verifies the proof and returns a `VerificationHandle` carrying the raw `(ProofFacts, Proof)` pair. It does **not** store the proof. [1](#0-0) 

**Step 2 — Deferred storage, error silently swallowed.**
After stateful validation passes, `add_tx_inner` calls `store_proof_and_spawn_archiving`. Inside that function, `store_proof_in_proof_manager` is awaited. If it returns `Err`, the error is only logged; the function continues and returns `Some((archive_handle, tx_hash))` as if storage succeeded. [2](#0-1) 

The caller `add_tx_inner` then unconditionally adds the transaction to the mempool: [3](#0-2) 

**Step 3 — Downstream invariant violation.**
`convert_internal_rpc_tx_to_rpc_tx` carries the comment *"We expect the proof to be available here because it has already been verified and stored by the proof manager in the gateway."* It calls `get_proof`, which returns `None` when the proof was never stored, producing `TransactionConverterError::ProofNotFound`. [4](#0-3) 

This function is invoked by `convert_internal_consensus_tx_to_consensus_tx` whenever the proposer serialises its mempool transactions into `ConsensusTransaction` (RPC format) for broadcasting to validators. [5](#0-4) 

**Contrast with the consensus path (correct).**
`convert_consensus_tx_to_internal_consensus_tx` uses `spawn_verify_and_store_proof`, which atomically verifies **and** stores the proof before returning. The gateway path deliberately splits these two steps but fails to propagate the storage error. [6](#0-5) 

---

### Impact Explanation

A transaction with non-empty `proof_facts` that passes all gateway validations (stateless + stateful + proof cryptographic verification) is accepted and placed in the mempool. The gateway returns a success response to the user. However, because the proof is absent from the proof manager, the proposer cannot reconstruct the full `ConsensusTransaction` (RPC form) needed to broadcast the proposal to validators. Any block proposal that includes such a transaction will fail at the conversion step, causing the proposer to be unable to finalise the proposal. This matches **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing**.

---

### Likelihood Explanation

The trigger requires two concurrent conditions:
1. A user submits an `InvokeV3` transaction with non-empty `proof_facts` and a valid proof (unprivileged, normal user action).
2. The `FsProofStorage` write fails — e.g., the persistent root filesystem is full, the directory is not writable, or the atomic `rename` fails for a reason other than a concurrent write of the same proof.

The `FsProofStorage` implementation uses `tokio::fs::rename` and propagates `std::io::Error` directly; any I/O failure satisfies condition 2. [7](#0-6) 

---

### Recommendation

Propagate the error from `store_proof_in_proof_manager` instead of swallowing it. If the proof cannot be persisted, the gateway must reject the transaction with an internal error rather than forwarding it to the mempool:

```rust
// In store_proof_and_spawn_archiving, change:
Err(e) => {
    error!("Failed to set proof in proof manager: {}", e);
}
// To:
Err(e) => {
    return Err(/* appropriate GatewayError wrapping e */);
}
```

Alternatively, make `add_tx_inner` check the return value of `store_proof_and_spawn_archiving` and abort if proof storage failed, consistent with how proof verification failure already aborts the flow.

---

### Proof of Concept

1. Submit an `InvokeV3` RPC transaction with valid, non-empty `proof_facts` and a valid `proof` to the gateway.
2. Arrange for the `FsProofStorage` write to fail (e.g., fill the filesystem or revoke write permissions on the `persistent_root` directory) between the moment proof verification completes and `store_proof_in_proof_manager` is called.
3. Observe: the gateway returns `HTTP 200` with a transaction hash (success).
4. Observe: the transaction appears in the mempool.
5. When the proposer calls `convert_internal_consensus_tx_to_consensus_tx` on this transaction, `get_proof` returns `None`, and the call returns `TransactionConverterError::ProofNotFound { facts_hash: … }`.
6. The proposer cannot include the transaction in any proposal; the transaction is permanently stuck in the mempool. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L140-154)
```rust
    async fn get_proof(&self, proof_facts: &ProofFacts) -> TransactionConverterResult<Proof> {
        let start_time = Instant::now();
        let proof_facts_hash = proof_facts.hash();
        let proof = self
            .proof_manager_client
            .get_proof(proof_facts.clone())
            .await?
            .ok_or(TransactionConverterError::ProofNotFound { facts_hash: proof_facts_hash });
        let duration = start_time.elapsed();
        info!(
            "Getting the proof from the proof manager took: {duration:?} for proof facts hash: \
             {proof_facts_hash:?}"
        );
        proof
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L169-182)
```rust
    async fn convert_internal_consensus_tx_to_consensus_tx(
        &self,
        tx: InternalConsensusTransaction,
    ) -> TransactionConverterResult<ConsensusTransaction> {
        match tx {
            InternalConsensusTransaction::RpcTransaction(tx) => self
                .convert_internal_rpc_tx_to_rpc_tx(tx)
                .await
                .map(ConsensusTransaction::RpcTransaction),
            InternalConsensusTransaction::L1Handler(tx) => {
                Ok(ConsensusTransaction::L1Handler(tx.tx))
            }
        }
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L209-216)
```rust
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                // We expect the proof to be available here because it has already been verified
                // and stored by the proof manager in the gateway.
                let proof = if tx.proof_facts.is_empty() {
                    Proof::default()
                } else {
                    self.get_proof(&tx.proof_facts).await?
                };
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L256-265)
```rust
    async fn convert_rpc_tx_to_internal_rpc_tx(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<VerificationHandle>)> {
        let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
        let verification_handle = proof_data
            .map(|(proof_facts, proof)| self.spawn_proof_verification(proof_facts, proof))
            .transpose()?;
        Ok((internal_tx, verification_handle))
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L446-471)
```rust
    fn spawn_verify_and_store_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> VerifyAndStoreProofTask {
        let pmc = self.proof_manager_client.clone();
        let proof_facts_hash = proof_facts.hash();
        tokio::spawn(async move {
            let verified =
                Self::run_proof_verification(proof_facts.clone(), proof.clone(), pmc.clone())
                    .await?;

            if !verified {
                return Ok(());
            }

            let start = Instant::now();
            pmc.set_proof(proof_facts, proof).await?;
            let duration = start.elapsed();
            CONSENSUS_PROOF_MANAGER_STORE_LATENCY.record(duration.as_secs_f64());
            info!(
                "Proof manager store took: {duration:?} for proof facts hash: {proof_facts_hash:?}"
            );
            Ok(())
        })
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L181-239)
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

**File:** crates/apollo_gateway/src/gateway.rs (L250-266)
```rust
        let store_result = self
            .transaction_converter
            .store_proof_in_proof_manager(proof_facts.clone(), proof.clone())
            .await;
        match store_result {
            Ok(proof_manager_store_duration) => {
                GATEWAY_PROOF_MANAGER_STORE_LATENCY
                    .record(proof_manager_store_duration.as_secs_f64());
                info!(
                    "Proof manager store in the gateway took: {proof_manager_store_duration:?} \
                     for tx hash: {tx_hash:?}"
                );
            }
            Err(e) => {
                error!("Failed to set proof in proof manager: {}", e);
            }
        }
```

**File:** crates/apollo_proof_manager/src/proof_storage.rs (L115-138)
```rust
    async fn write_proof_atomically(
        &self,
        facts_hash: Felt,
        proof: Proof,
    ) -> FsProofStorageResult<()> {
        // Write proof to a temporary directory.
        let (_tmp_root, tmp_dir) = self.create_tmp_dir(facts_hash).await?;
        self.write_proof_to_file(&tmp_dir, &proof).await?;

        // Atomically rename directory to persistent one.
        // If a concurrent write already placed the proof at the persistent path, the rename
        // will fail (e.g. ENOTEMPTY on Linux). Since proofs are deterministic for a given
        // facts_hash, the existing proof is identical and we can safely treat this as success.
        let persistent_dir = self.get_persistent_dir_with_create(facts_hash).await?;
        match tokio::fs::rename(&tmp_dir, &persistent_dir).await {
            Ok(()) => Ok(()),
            Err(_)
                if tokio::fs::try_exists(persistent_dir.join("proof")).await.unwrap_or(false) =>
            {
                Ok(())
            }
            Err(e) => Err(e.into()),
        }
    }
```
