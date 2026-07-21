### Title
Silent proof-storage failure in `store_proof_and_spawn_archiving` allows proof-facts transaction into mempool without stored proof — (File: `crates/apollo_gateway/src/gateway.rs`)

---

### Summary

`GenericGateway::store_proof_and_spawn_archiving` silently swallows a `store_proof_in_proof_manager` failure: the error is only logged, and the transaction is unconditionally forwarded to the mempool. This breaks the invariant that every accepted Invoke V3 transaction carrying `proof_facts` has its proof durably stored in the proof manager. When the proposer later tries to re-broadcast the block, `convert_internal_rpc_tx_to_rpc_tx` calls `get_proof`, which returns `ProofNotFound`, causing the reproposal to fail.

---

### Finding Description

**Root cause — unchecked return value in the gateway admission path**

In `store_proof_and_spawn_archiving`:

```rust
// crates/apollo_gateway/src/gateway.rs  lines 250-266
let store_result = self
    .transaction_converter
    .store_proof_in_proof_manager(proof_facts.clone(), proof.clone())
    .await;
match store_result {
    Ok(proof_manager_store_duration) => { /* record metric */ }
    Err(e) => {
        error!("Failed to set proof in proof manager: {}", e);
        // ← execution continues; no early return
    }
}
// transaction is sent to mempool regardless
``` [1](#0-0) 

The `Err` arm logs the failure but does **not** return an error. Control falls through to the mempool submission at line 223. [2](#0-1) 

**Broken invariant — explicitly documented in the converter**

`convert_internal_rpc_tx_to_rpc_tx` carries a comment that makes the assumption explicit:

```rust
// We expect the proof to be available here because it has already been verified
// and stored by the proof manager in the gateway.
let proof = if tx.proof_facts.is_empty() {
    Proof::default()
} else {
    self.get_proof(&tx.proof_facts).await?   // ← returns ProofNotFound if missing
};
``` [3](#0-2) 

`get_proof` maps a `None` result to `TransactionConverterError::ProofNotFound`:

```rust
.ok_or(TransactionConverterError::ProofNotFound { facts_hash: proof_facts_hash });
``` [4](#0-3) 

**Downstream failure path — reproposal**

When the proposer re-broadcasts a decided block, `send_reproposal` calls `convert_internal_consensus_tx_to_consensus_tx` for every transaction in the block:

```rust
let transactions = futures::future::join_all(batch.iter().map(|tx| {
    transaction_converter.convert_internal_consensus_tx_to_consensus_tx(tx.clone())
}))
.await
.into_iter()
.collect::<Result<Vec<_>, _>>()?;   // ← propagates ProofNotFound as ReproposeError
``` [5](#0-4) 

`convert_internal_consensus_tx_to_consensus_tx` delegates to `convert_internal_rpc_tx_to_rpc_tx`, which calls `get_proof`. Because the proof was never stored, the entire reproposal fails. [6](#0-5) 

**Trigger conditions**

The proof manager can fail to store for any transient I/O reason: disk full, filesystem permission error, or a temporary proof-manager service outage. The `FsProofStorage::write_proof_atomically` path involves `tokio::fs::rename` and `tokio::fs::create_dir_all`, both of which can return OS errors. [7](#0-6) 

Any user who submits a valid Invoke V3 transaction with non-empty `proof_facts` during such a window can trigger this condition without any privileged access.

---

### Impact Explanation

**Scope: High — Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

The gateway accepts a transaction that carries `proof_facts` but whose proof is absent from the proof manager. The transaction is structurally valid (proof cryptographically verified before this point), but the sequencer's own reproposal invariant is violated: the proposer cannot reconstruct the full `RpcTransaction` from its internal representation, so `send_reproposal` returns a `ReproposeError`. Depending on consensus timing, this can cause the proposer to fail to re-broadcast a decided block to validators, stalling or forking consensus for that height.

---

### Likelihood Explanation

Medium. The proof manager uses filesystem storage (`FsProofStorage`). Disk-full events, I/O errors, or a brief proof-manager restart are realistic production conditions. The window is narrow (only transactions submitted during the failure), but the failure is silent — no alert is raised to the operator beyond an `error!` log line, and the transaction proceeds normally into the mempool.

---

### Recommendation

Return an error from `store_proof_and_spawn_archiving` (and propagate it out of `add_tx_inner`) when `store_proof_in_proof_manager` fails, instead of logging and continuing:

```rust
Err(e) => {
    error!("Failed to set proof in proof manager: {}", e);
    return None; // or propagate as GatewayError
}
```

This mirrors the pattern used for the proof-verification step (`await_verification_task_and_extract_proof_data`), which correctly propagates errors and rejects the transaction. [8](#0-7) 

---

### Proof of Concept

1. Submit a valid Invoke V3 transaction with non-empty `proof_facts` and a valid `proof` to the gateway.
2. Arrange for `store_proof_in_proof_manager` to fail (e.g., make the proof-manager filesystem read-only, or inject a mock that returns `Err`).
3. Observe that the gateway logs `"Failed to set proof in proof manager"` but returns `Ok` to the caller and forwards the transaction to the mempool.
4. Wait for the batcher to include the transaction in a block and for `decision_reached` to be called.
5. Trigger reproposal (e.g., by having the proposer re-broadcast the block).
6. Observe that `send_reproposal` → `convert_internal_consensus_tx_to_consensus_tx` → `convert_internal_rpc_tx_to_rpc_tx` → `get_proof` returns `TransactionConverterError::ProofNotFound { facts_hash: … }`, causing the reproposal to fail.

The exact corrupted value is the missing proof entry in `FsProofStorage` for the `facts_hash` derived from the transaction's `proof_facts`, which causes `ProofManager::get_proof` to return `Ok(None)` and `TransactionConverter::get_proof` to return `Err(ProofNotFound)`. [9](#0-8) [4](#0-3)

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L219-230)
```rust
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

**File:** crates/apollo_gateway/src/gateway.rs (L379-401)
```rust
    async fn await_verification_task_and_extract_proof_data(
        &self,
        verification_handle: Option<VerificationHandle>,
        tx_signature: &TransactionSignature,
    ) -> Result<Option<(ProofFacts, Proof)>, StarknetError> {
        let Some(handle) = verification_handle else {
            return Ok(None);
        };

        handle
            .verification_task
            .await
            .map_err(|e| {
                warn!("Proof verification task panicked: {}", e);
                StarknetError::internal_with_logging("Proof verification task panicked:", &e)
            })?
            .map_err(|e| {
                warn!("Proof verification failed: {}", e);
                transaction_converter_err_to_deprecated_gw_err(tx_signature, e)
            })?;

        Ok(Some((handle.proof_facts, handle.proof)))
    }
```

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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L210-216)
```rust
                // We expect the proof to be available here because it has already been verified
                // and stored by the proof manager in the gateway.
                let proof = if tx.proof_facts.is_empty() {
                    Proof::default()
                } else {
                    self.get_proof(&tx.proof_facts).await?
                };
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1069-1078)
```rust
    for batch in txs.iter() {
        let transactions = futures::future::join_all(batch.iter().map(|tx| {
            // transaction_converter is an external dependency (class manager) and so
            // we can't assume success on reproposal.
            transaction_converter.convert_internal_consensus_tx_to_consensus_tx(tx.clone())
        }))
        .await
        .into_iter()
        .collect::<Result<Vec<_>, _>>()?;
        stream_sender.send(ProposalPart::Transactions(TransactionBatch { transactions })).await?;
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

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L68-83)
```rust
    pub async fn get_proof(
        &self,
        proof_facts: ProofFacts,
    ) -> Result<Option<Proof>, FsProofStorageError> {
        let facts_hash = proof_facts.hash();
        // Check cache first.
        if let Some(proof) = self.cache.get(&facts_hash) {
            return Ok(Some(proof));
        }
        // Fallback to filesystem.
        let proof = self.proof_storage.get_proof(facts_hash).await?;
        if let Some(proof) = &proof {
            self.cache.insert(facts_hash, proof.clone());
        }
        Ok(proof)
    }
```
