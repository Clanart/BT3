### Title
Proof Manager `contains_proof` Failure Is a Single Point of Failure for All Client-Side Proving Transactions — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

`run_proof_verification` calls `proof_manager_client.contains_proof()` as a cache-deduplication check before performing cryptographic proof verification. The call is made with the `?` operator, so any transient error from the proof manager (network blip, service restart, storage I/O error) propagates immediately as a fatal `TransactionConverterError::ProofManagerClientError`. This makes the proof manager a single point of failure for every transaction that carries proof facts: the gateway rejects all such transactions, and the consensus validator rejects proposals that contain them, even though the `contains_proof` check is purely an optimisation (skip re-verification of an already-stored proof) and the cryptographic verification could still proceed independently.

### Finding Description

`run_proof_verification` is the shared verification path used by both the gateway flow (`spawn_proof_verification`) and the consensus flow (`spawn_verify_and_store_proof`):

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
    //                                                                              ^
    //  Any ProofManagerClientError propagates here — the cryptographic
    //  verification below is never reached.

    if contains_proof {
        return Ok(false); // skip re-verification
    }

    tokio::task::spawn_blocking(move || {
        starknet_proof_verifier::verify_proof(proof_facts, proof)
    })
    .await
    .expect("proof verification task panicked")?;

    Ok(true)
}
``` [1](#0-0) 

The `contains_proof` call is a cache/deduplication optimisation: if the proof is already stored, skip the expensive cryptographic step. Its failure does not mean the proof is invalid; it means the storage layer is temporarily unreachable. The correct fallback is to treat the result as `false` (not cached) and proceed with verification. Instead, the `?` operator converts any `ProofManagerClientError` into a hard failure.

**Gateway flow** — `spawn_proof_verification` wraps this in a `JoinHandle` that is immediately awaited in `await_verification_task_and_extract_proof_data`, which propagates the error to `add_tx`: [2](#0-1) 

The gateway maps the error to `StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::InvalidProof)` and rejects the transaction. A test explicitly confirms this rejection path: [3](#0-2) 

**Consensus flow** — `spawn_verify_and_store_proof` also calls `run_proof_verification` with `?`, and additionally calls `pmc.set_proof(...).await?` after successful verification: [4](#0-3) 

These `VerifyAndStoreProofTask` handles are collected during proposal validation and awaited at fin: [5](#0-4) 

A proof manager error in either `contains_proof` or `set_proof` causes the task to return `Err(TransactionConverterError::ProofManagerClientError(...))`, which is surfaced at fin and can cause the proposal to be rejected even though the proof itself is cryptographically valid.

The inconsistency is visible by contrast: the gateway's `store_proof_and_spawn_archiving` already handles `set_proof` failure gracefully (logs and continues), but the consensus path does not: [6](#0-5) 

### Impact Explanation

Any transient unavailability of the proof manager — service restart, filesystem I/O error, network partition in a distributed deployment — causes:

1. **Gateway**: every incoming `InvokeV3` transaction carrying `proof_facts` is rejected with `InvalidProof`, even though the proof may be perfectly valid. This is a denial-of-service on the entire client-side proving feature.
2. **Consensus validator**: `VerifyAndStoreProofTask` failures at fin can cause the validator to reject a valid proposal that contains proof-bearing transactions, potentially stalling consensus rounds.

This matches the allowed impact: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

### Likelihood Explanation

The proof manager is a separate microservice (`apollo_proof_manager`) backed by filesystem storage (`FsProofStorage`). In the distributed deployment mode, it is reached over a remote component client. Any of the following triggers the bug: a rolling restart of the proof manager pod, a full disk on the proof storage volume, a transient network partition between the gateway/orchestrator and the proof manager, or a concurrent write collision that surfaces as an `IoError`. None of these require a privileged attacker; they are ordinary operational events.

### Recommendation

Treat a `contains_proof` error as a cache miss and fall back to performing the cryptographic verification:

```rust
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    // Treat a client error as "not cached" and proceed with verification.
    let contains_proof = proof_manager_client
        .contains_proof(proof_facts.clone())
        .await
        .unwrap_or_else(|e| {
            warn!(
                "Failed to check proof cache; proceeding with verification. Error: {e}"
            );
            false
        });

    if contains_proof {
        return Ok(false);
    }

    tokio::task::spawn_blocking(move || {
        starknet_proof_verifier::verify_proof(proof_facts, proof)
    })
    .await
    .expect("proof verification task panicked")?;

    Ok(true)
}
```

Similarly, in `spawn_verify_and_store_proof`, the `set_proof` call should be made non-fatal (log and continue), consistent with how the gateway already handles it in `store_proof_and_spawn_archiving`.

### Proof of Concept

1. Deploy the sequencer in distributed mode with the proof manager as a separate service.
2. Submit an `InvokeV3` transaction with valid `proof_facts` and a valid `proof`.
3. Before the gateway processes the transaction, stop the proof manager service (simulating a restart or I/O error).
4. Observe: `add_tx` returns `StarknetErrorCode::InvalidProof` — the transaction is rejected despite the proof being valid.
5. Restart the proof manager; the same transaction is now accepted.
6. The same sequence applies to the consensus validator: a proposal containing proof-bearing transactions is rejected at fin when the proof manager is unreachable, even though the proofs are cryptographically sound.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L395-424)
```rust
    /// Runs proof verification: checks if the proof already exists, and if not, verifies it.
    /// Returns `true` if verification was performed, `false` if skipped (proof already stored).
    /// This is the shared verification logic used by both gateway and consensus flows.
    async fn run_proof_verification(
        proof_facts: ProofFacts,
        proof: Proof,
        proof_manager_client: SharedProofManagerClient,
    ) -> Result<bool, TransactionConverterError> {
        let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

        if contains_proof {
            return Ok(false);
        }

        let proof_facts_hash = proof_facts.hash();
        let verify_start = Instant::now();
        tokio::task::spawn_blocking(move || {
            starknet_proof_verifier::verify_proof(proof_facts, proof)
        })
        .await
        .expect("proof verification task panicked")?;
        let verify_duration = verify_start.elapsed();
        PROOF_VERIFICATION_LATENCY.record(verify_duration.as_secs_f64());
        info!(
            "Proof verification took: {verify_duration:?} for proof facts hash: \
             {proof_facts_hash:?}"
        );

        Ok(true)
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

**File:** crates/apollo_gateway/src/gateway_test.rs (L504-547)
```rust
#[rstest]
#[tokio::test]
async fn test_add_tx_fails_when_proof_verification_fails(mut mock_dependencies: MockDependencies) {
    let tx_args = invoke_args_with_client_side_proving();

    // Setup transaction converter mock that returns a failing verification task.
    setup_transaction_converter_mock_with_failed_verification(
        &mut mock_dependencies.mock_transaction_converter,
        &tx_args,
    );

    // Setup state: fund account and store proof block hash.
    let state_reader =
        &mut mock_dependencies.state_reader_factory.state_reader.blockifier_state_reader;
    let address = tx_args.get_internal_tx().contract_address();
    fund_account(
        &mock_dependencies.config.static_config.chain_info,
        address,
        VALID_ACCOUNT_BALANCE,
        state_reader,
    );

    let block_hash_state_maps = generate_block_hash_storage_updates();
    state_reader.storage_view.extend(block_hash_state_maps.storage);

    // Run add_tx and verify it fails.
    let AddTxResults { result, metric_handle_for_queries, metrics } =
        run_add_tx_and_extract_metrics(mock_dependencies, &tx_args).await;

    // Assert the transaction was received but failed.
    assert_eq!(
        metric_handle_for_queries.get_metric_value(GATEWAY_TRANSACTIONS_RECEIVED, &metrics),
        1
    );
    assert_eq!(
        metric_handle_for_queries.get_metric_value(GATEWAY_TRANSACTIONS_FAILED, &metrics),
        1
    );

    // Assert the error is an internal error due to proof verification failure.
    let error = result.unwrap_err();
    assert_eq!(error.code, StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::InvalidProof));
    assert_eq!(error.message, "Proof verification error: Proof verification failed: test error");
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L502-509)
```rust
            // Separate internal transactions from verification and store proof tasks. Each task
            // verifies the proof and stores it in the proof manager. Tasks are collected
            // and awaited later in the fin case.
            let (txs, tasks): (
                Vec<InternalConsensusTransaction>,
                Vec<Option<VerifyAndStoreProofTask>>,
            ) = conversion_results.into_iter().unzip();
            verify_and_store_proof_tasks.extend(tasks.into_iter().flatten());
```
