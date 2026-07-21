### Title
Silently Swallowed `store_proof_in_proof_manager` Error Admits Transaction Without Stored Proof, Breaking RPC Serving and P2P Propagation - (File: crates/apollo_gateway/src/gateway.rs)

### Summary

In `GenericGateway::store_proof_and_spawn_archiving`, the `Err` branch from `store_proof_in_proof_manager` is only logged; execution continues unconditionally, the function returns `Some((handle, tx_hash))`, and the transaction is admitted to the mempool. Because the proof is never written to the local `ProofManager`, any subsequent call to `convert_internal_rpc_tx_to_rpc_tx` (RPC serving, P2P propagation) that invokes `get_proof` returns `ProofNotFound`, making the transaction permanently unserviceable via RPC and unpropagateable to peers.

### Finding Description

`store_proof_and_spawn_archiving` calls `store_proof_in_proof_manager` and pattern-matches the result:

```rust
match store_result {
    Ok(proof_manager_store_duration) => { /* record metric */ }
    Err(e) => {
        error!("Failed to set proof in proof manager: {}", e);
        // ← no return, no propagation
    }
}
// execution falls through unconditionally
let handle = tokio::spawn(async move { ... });
Some((handle, tx_hash))   // ← always Some
``` [1](#0-0) 

The caller `add_tx_inner` then unconditionally sends the transaction to the mempool: [2](#0-1) 

The proof is stored in the local `ProofManager` (filesystem + in-memory cache) by `store_proof_in_proof_manager`, which delegates to `proof_manager_client.set_proof`: [3](#0-2) 

When the transaction is later served via RPC or propagated via P2P, `convert_internal_rpc_tx_to_rpc_tx` calls `get_proof`, which queries only the local `ProofManager`: [4](#0-3) 

If the proof was never stored, `get_proof` returns `ProofNotFound`: [5](#0-4) 

### Impact Explanation

A transaction with `proof_facts` is admitted to the mempool and sequenced normally (blockifier's `validate_proof_facts` checks the block hash from state, not the proof manager). However, every downstream operation that needs to reconstruct the full `RpcTransaction` — including `starknet_getTransactionByHash`, `starknet_getTransactionByBlockIdAndIndex`, and P2P gossip propagation — calls `convert_internal_rpc_tx_to_rpc_tx`, which fails with `ProofNotFound`. The transaction is permanently stuck: it exists in the mempool and may be sequenced, but it cannot be served via RPC or propagated to peers.

This matches: **High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value** (the RPC endpoint returns an error for a transaction that was accepted and is in the mempool/block).

### Likelihood Explanation

Any transient I/O error on the proof manager's filesystem (disk full, permission error, rename failure) during the `set_proof` call triggers this path. The gateway continues serving new transactions, so the failure is silent from the operator's perspective beyond a single log line. The condition is reachable without any privileged access — it requires only a filesystem-level fault on the sequencer node.

### Recommendation

Propagate the error from `store_proof_in_proof_manager` instead of swallowing it. Either:

1. Return `Err` from `store_proof_and_spawn_archiving` (making it `-> Result<ProofArchiveHandle, ...>`) and reject the transaction at the gateway level, or
2. Retry the store before admitting the transaction, ensuring the proof is durably written before the transaction enters the mempool.

```rust
Err(e) => {
    error!("Failed to set proof in proof manager: {}", e);
    return None; // or propagate as Err
}
``` [6](#0-5) 

### Proof of Concept

1. Submit an `InvokeV3` transaction carrying non-empty `proof_facts` and a valid `proof` to the gateway.
2. Inject a fault that causes `FsProofStorage::write_proof_atomically` to return an `IoError` (e.g., make the proof storage directory read-only).
3. Observe: the gateway logs `"Failed to set proof in proof manager"` but returns `Ok(GatewayOutput::Invoke(...))` — the transaction is accepted.
4. Query `starknet_getTransactionByHash` with the returned hash.
5. Observe: the RPC call fails with `ProofNotFound` because `convert_internal_rpc_tx_to_rpc_tx` → `get_proof` → `proof_manager_client.get_proof` returns `None` → `ProofNotFound`. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L214-230)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L320-328)
```rust
    async fn store_proof_in_proof_manager(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> TransactionConverterResult<Duration> {
        let start = Instant::now();
        self.proof_manager_client.set_proof(proof_facts, proof).await?;
        Ok(start.elapsed())
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

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L54-66)
```rust
    pub async fn set_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> Result<(), FsProofStorageError> {
        if self.contains_proof(proof_facts.clone()).await? {
            return Ok(());
        }
        let facts_hash = proof_facts.hash();
        self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
        self.cache.insert(facts_hash, proof);
        Ok(())
    }
```
