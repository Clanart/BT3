### Title
`run_proof_verification` Drops the `proof` Argument on Cache Hit, Admitting Transactions with Unverified Proofs — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

`run_proof_verification` is the shared proof-verification gate for both the gateway and consensus flows. When the proof manager already contains an entry for the submitted `proof_facts`, the function returns `Ok(false)` immediately, **silently dropping the `proof` argument without ever checking it**. An attacker who has previously submitted one valid proof for a given `ProofFacts` key can subsequently submit any number of transactions carrying an arbitrary, invalid proof under the same key; every one of those transactions passes the verification gate and is admitted to the mempool.

---

### Finding Description

**Root cause — the dropped parameter** [1](#0-0) 

```rust
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,                          // ← accepted but never used on the fast path
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

    if contains_proof {
        return Ok(false);                  // ← proof is dropped; no check performed
    }
    // ... actual verify_proof(proof_facts, proof) only reached on first submission
```

This is the direct sequencer analog of M-12: `safeTransferFrom` accepted `_data` but dropped it without performing the `IERC721Receiver` check. Here, `run_proof_verification` accepts `proof` but drops it without calling `verify_proof` whenever the proof-facts key is already present in the proof manager.

**Gateway flow — the proof is stored after the skipped check** [2](#0-1) 

After `await_verification_task_and_extract_proof_data` returns `Ok(Some((PF, P_invalid)))`, `store_proof_and_spawn_archiving` calls `store_proof_in_proof_manager(PF, P_invalid)`. `ProofManager::set_proof` also short-circuits on `contains_proof`, so `P_invalid` is never written to the proof manager — but the comment on line 248 states *"Proof is verified during conversion to internal tx"*, which is false for this path. The transaction is then forwarded to the mempool. [3](#0-2) 

**Blockifier pre-validation — only metadata is checked, not the proof** [4](#0-3) 

`validate_proof_facts` validates `program_hash`, `block_hash`, `block_number`, and `config_hash` — all metadata fields from `SnosProofFacts`. It never calls `verify_proof`. The actual cryptographic proof is only checked in `run_proof_verification`, which was already bypassed.

**Consensus flow — same bypass, same consequence** [5](#0-4) 

`spawn_verify_and_store_proof` calls `run_proof_verification` first; if `verified == false` (cache hit), it returns `Ok(())` without storing anything. The transaction is forwarded to the batcher with an unverified proof.

---

### Impact Explanation

**Impact: High — Mempool/gateway admission accepts invalid transactions before sequencing.**

An attacker who has previously submitted one valid `(PF, P_valid)` pair can thereafter submit arbitrarily many transactions carrying `(PF, P_garbage)`. Each such transaction:

1. Passes `run_proof_verification` (cache hit → `Ok(false)`).
2. Passes `validate_proof_facts` in the blockifier (metadata is valid).
3. Is admitted to the mempool and included in a block.
4. Causes the proof archive to receive `P_garbage` (the archive writer is called with the attacker-supplied proof, not the stored valid one).

The proof is the cryptographic attestation that the transaction was correctly executed in the referenced block. Bypassing it allows a transaction to carry a false execution attestation, violating the invariant that every admitted client-side-proving transaction has a verified proof.

---

### Likelihood Explanation

**Likelihood: Medium.**

The only precondition is that the attacker has previously submitted one valid transaction with the target `ProofFacts` key — a realistic, unprivileged action. After that single setup step, the bypass is deterministic and repeatable for any number of subsequent transactions sharing the same key.

---

### Recommendation

`run_proof_verification` must verify the submitted `proof` regardless of whether the proof facts are already stored. The cache check should only skip the expensive cryptographic verification when the **same** proof is being re-submitted, not when any proof is submitted for a known key. One correct approach:

```rust
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    // Always verify; only skip storing if already present.
    starknet_proof_verifier::verify_proof(proof_facts.clone(), proof.clone())
        .map_err(TransactionConverterError::ProofVerification)?;

    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
    if contains_proof {
        return Ok(false); // verified but not stored (already present)
    }
    Ok(true) // verified and needs to be stored
}
```

Alternatively, store the proof hash alongside the proof facts and compare it on cache hit, rejecting mismatches.

---

### Proof of Concept

1. Submit `T1 = Invoke V3(proof_facts=PF, proof=P_valid)` to the gateway. `run_proof_verification` verifies `P_valid` and returns `Ok(true)`. `store_proof_in_proof_manager` stores `P_valid` under `hash(PF)`.

2. Submit `T2 = Invoke V3(proof_facts=PF, proof=P_garbage)` to the gateway (same `PF`, different proof).

3. `run_proof_verification(PF, P_garbage, pmc)`:
   - `pmc.contains_proof(PF)` → `true` (step 1 stored it).
   - Returns `Ok(false)`. `P_garbage` is never passed to `verify_proof`.

4. `await_verification_task_and_extract_proof_data` returns `Ok(Some((PF, P_garbage)))`.

5. `store_proof_and_spawn_archiving(Some((PF, P_garbage)), tx_hash_T2)`:
   - `store_proof_in_proof_manager(PF, P_garbage)` → `set_proof` short-circuits (already stored). `P_garbage` not written to proof manager.
   - `proof_archive_writer.set_proof(PF, P_garbage)` is spawned — archive receives the invalid proof.

6. `T2` is forwarded to the mempool. `validate_proof_facts` in the blockifier passes (metadata is valid). `T2` is included in a block with an unverified proof.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-407)
```rust
    async fn run_proof_verification(
        proof_facts: ProofFacts,
        proof: Proof,
        proof_manager_client: SharedProofManagerClient,
    ) -> Result<bool, TransactionConverterError> {
        let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

        if contains_proof {
            return Ok(false);
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

**File:** crates/apollo_gateway/src/gateway.rs (L241-266)
```rust
    async fn store_proof_and_spawn_archiving(
        &self,
        proof_data: Option<(ProofFacts, Proof)>,
        tx_hash: TransactionHash,
    ) -> ProofArchiveHandle {
        let (proof_facts, proof) = proof_data?;

        // Proof is verified during conversion to internal tx. It is stored here, after
        // validation, to avoid storing proofs for rejected transactions.
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-347)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;

        // Validate the config hash.
        let chain_info = &block_context.chain_info;
        // TODO(Meshi): Cache this computation as part of the chain context.
        let virtual_os_config_hash = OsChainInfo::from(chain_info)
            .compute_virtual_os_config_hash()
            .expect("Failed to compute OS config hash");
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }

        Ok(())
    }
```
