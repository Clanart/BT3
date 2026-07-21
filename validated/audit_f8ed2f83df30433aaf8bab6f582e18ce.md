### Title
Proof Verification Bypassed for Any Transaction Once `proof_facts` Hash Is Cached — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

`run_proof_verification` in the `TransactionConverter` skips cryptographic proof verification whenever `contains_proof(proof_facts)` returns `true`. Because the `ProofManager` keys stored proofs by `hash(proof_facts)` — a value that is block-level (base block hash, program hash, config hash) and **not** transaction-specific — any attacker who knows a `proof_facts` value already stored in the `ProofManager` can submit an arbitrary transaction carrying those same `proof_facts` with garbage proof bytes and have the proof check silently skipped. The gateway accepts the transaction, the blockifier executes it, and the RPC later returns the stored (but wrong) proof for that transaction.

---

### Finding Description

**Root cause — `run_proof_verification` skips on cache hit without binding to the submitter or the current transaction:** [1](#0-0) 

```rust
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

    if contains_proof {
        return Ok(false);   // ← verification entirely skipped
    }
    // ... starknet_proof_verifier::verify_proof(proof_facts, proof) ...
}
```

The `ProofManager` stores one proof per `hash(proof_facts)`: [2](#0-1) 

`proof_facts` is a block-level structure — it contains `proof_version`, `program_hash`, `block_number`, `block_hash`, and `config_hash`: [3](#0-2) 

None of these fields bind to the specific transaction being submitted (no transaction hash, no sender address, no calldata commitment). Any two transactions proven against the same base block with the same program hash and config hash produce **identical** `proof_facts`.

**The stateless gateway validator only checks that proof and proof_facts are both present or both absent — it does not verify the proof cryptographically:** [4](#0-3) 

**The blockifier's `validate_proof_facts` validates structural fields (block hash, program hash, config hash) but never re-verifies the ZK proof:** [5](#0-4) 

**Attack sequence:**

1. Victim (User A) submits `T_A` with valid `proof_facts_B` (proven against base block B) and valid `proof_A`. `proof_A` passes `verify_proof` and is stored in `ProofManager` under key `hash(proof_facts_B)`.
2. Attacker (User B) submits `T_B` (different calldata, different sender) with the same `proof_facts_B` and arbitrary non-empty garbage bytes as `proof`.
3. `run_proof_verification` calls `contains_proof(proof_facts_B)` → `true` → returns `Ok(false)` without calling `verify_proof`.
4. `T_B` passes gateway stateless validation (proof and proof_facts are both non-empty).
5. Blockifier validates `proof_facts_B` structurally — passes, because the block hash and program hash are genuinely valid.
6. `T_B` is sequenced and executed.
7. When `T_B` is later retrieved via RPC, `convert_internal_rpc_tx_to_rpc_tx` fetches the proof from `ProofManager` by `proof_facts_B` and returns `proof_A` — a valid proof for `T_A`, not `T_B`: [6](#0-5) 

---

### Impact Explanation

- **Gateway admission:** A transaction carrying an invalid (garbage) ZK proof is accepted by the gateway and sequenced. The proof verification invariant — every transaction with non-empty `proof_facts` must carry a valid proof for *that* transaction — is broken.
- **RPC wrong value:** `starknet_getTransactionByHash` (and related endpoints) return `proof_A` as the proof for `T_B`. This is an authoritative-looking wrong value: the proof is cryptographically valid but proves a completely different transaction.

Both map to the allowed High-severity impacts: *"Mempool/gateway/RPC admission accepts invalid transactions"* and *"RPC … returns an authoritative-looking wrong value."*

---

### Likelihood Explanation

`proof_facts` values are public — they appear in submitted transactions and in RPC responses (the `proof_facts` field of `INVOKE_FUNCTION` transactions). Any observer of the mempool or chain can extract a valid `proof_facts` value and immediately reuse it. No privileged access is required. The only precondition is that at least one honest transaction with those `proof_facts` has already been processed, which is a normal operational state once client-side proving is in use.

---

### Recommendation

The cache-hit path must not silently skip verification. Two complementary fixes are needed:

1. **Bind proof facts to the transaction.** Include the transaction hash (or at minimum the sender address and nonce) in the `proof_facts` structure so that `hash(proof_facts)` is unique per transaction. This makes reuse of another transaction's proof facts structurally impossible.

2. **Verify the submitted proof even on cache hit, or reject mismatched proofs.** If the proof is already stored, compare the submitted proof bytes against the stored proof. If they differ, reject the transaction. This prevents a submitter from providing garbage bytes that are never checked.

---

### Proof of Concept

```
// Step 1: User A submits a valid client-side-proven transaction.
// proof_facts_B = [PROOF_VERSION_V1, VIRTUAL_SNOS, program_hash,
//                  VIRTUAL_OS_OUTPUT_VERSION, block_N, block_hash_N, config_hash]
// proof_A = <valid ZK proof for T_A>
// → run_proof_verification: contains_proof = false → verify_proof(proof_facts_B, proof_A) → OK
// → ProofManager stores proof_A under hash(proof_facts_B)

// Step 2: Attacker copies proof_facts_B, submits T_B with garbage proof bytes.
// proof_garbage = [0x01, 0x02, 0x03]  (non-empty, passes consistency check)
// → gateway stateless validator: has_proof_facts=true, has_proof=true → OK
// → run_proof_verification: contains_proof(proof_facts_B) = true → return Ok(false)
//    (verify_proof is NEVER called for proof_garbage)
// → blockifier validate_proof_facts: block hash matches, program hash allowed → OK
// → T_B is sequenced and executed

// Step 3: RPC query for T_B returns proof_A (User A's proof), not proof_garbage.
// starknet_getTransactionByHash(T_B.hash) →
//   convert_internal_rpc_tx_to_rpc_tx → get_proof(proof_facts_B) → proof_A
// Result: T_B appears to have a valid proof it never possessed.
```

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L210-231)
```rust
                // We expect the proof to be available here because it has already been verified
                // and stored by the proof manager in the gateway.
                let proof = if tx.proof_facts.is_empty() {
                    Proof::default()
                } else {
                    self.get_proof(&tx.proof_facts).await?
                };

                Ok(RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                    resource_bounds: tx.resource_bounds,
                    signature: tx.signature,
                    nonce: tx.nonce,
                    tip: tx.tip,
                    paymaster_data: tx.paymaster_data,
                    nonce_data_availability_mode: tx.nonce_data_availability_mode,
                    fee_data_availability_mode: tx.fee_data_availability_mode,
                    sender_address: tx.sender_address,
                    calldata: tx.calldata,
                    account_deployment_data: tx.account_deployment_data,
                    proof_facts: tx.proof_facts,
                    proof,
                })))
```

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

**File:** crates/starknet_api/src/transaction/fields.rs (L795-804)
```rust
/// Contains the required fields for valid SNOS proof facts.
///
/// A valid SNOS proof facts structure must include these fields as its first five entries.
pub struct SnosProofFacts {
    pub proof_version: ProofVersion,
    pub program_hash: StarkHash,
    pub block_number: BlockNumber,
    pub block_hash: BlockHash,
    pub config_hash: StarkHash,
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L249-263)
```rust
    fn validate_proof_facts_and_proof_consistency(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_facts = !tx.proof_facts.is_empty();
        let has_proof = !tx.proof.is_empty();
        if has_proof_facts != has_proof {
            return Err(StatelessTransactionValidatorError::ProofFactsAndProofConsistency {
                has_proof_facts,
                has_proof,
            });
        }
        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-351)
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

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }

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
        let virtual_os_config_hash = block_context.virtual_os_config_hash();
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
