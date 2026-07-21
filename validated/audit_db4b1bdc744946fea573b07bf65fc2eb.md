### Title
Non-Atomic Proof-Verification Skip Allows Gateway Admission of Transactions with Unverified Proofs — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

`run_proof_verification` uses a non-atomic check-then-act pattern: it calls `contains_proof` and, if the result is `true`, skips ZK-proof verification entirely. Because the check and the subsequent store are not atomic, an attacker who has previously submitted one valid proof for a given `proof_facts` hash can later submit any number of transactions carrying the same `proof_facts` but a completely garbage `proof` blob, and the gateway will accept them without ever running the circuit verifier.

### Finding Description

`run_proof_verification` in `crates/apollo_transaction_converter/src/transaction_converter.rs` is the shared verification gate for both the gateway flow (`spawn_proof_verification`) and the consensus flow (`spawn_verify_and_store_proof`):

```rust
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

    if contains_proof {
        return Ok(false);   // ← verification unconditionally skipped
    }
    // ... starknet_proof_verifier::verify_proof(proof_facts, proof) ...
    Ok(true)
}
``` [1](#0-0) 

The `contains_proof` check is keyed on `proof_facts.hash()` — the hash of the `ProofFacts` vector — not on the `Proof` blob itself. [2](#0-1) 

`ProofManager::set_proof` also has its own `contains_proof` guard, so once a `proof_facts` hash is stored, any subsequent `set_proof` call for the same hash is a no-op:

```rust
pub async fn set_proof(&self, proof_facts: ProofFacts, proof: Proof) -> ... {
    if self.contains_proof(proof_facts.clone()).await? {
        return Ok(());
    }
    ...
}
``` [3](#0-2) 

**Attack sequence:**

1. Attacker submits **TX1** carrying valid `proof_facts_A` and a valid `proof_A`.  
   - `contains_proof(proof_facts_A)` → `false` → `verify_proof(proof_facts_A, proof_A)` passes → `proof_facts_A` stored in `ProofManager`.

2. Attacker submits **TX2** carrying the same `proof_facts_A` but a completely garbage `proof_B`.  
   - `contains_proof(proof_facts_A)` → `true` → **verification skipped** → `run_proof_verification` returns `

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-424)
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

**File:** crates/starknet_api/src/transaction/fields.rs (L643-645)
```rust
    pub fn hash(&self) -> Felt {
        HashChain::new().chain_iter(self.0.iter()).get_poseidon_hash()
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
