### Title
Hardcoded `deprecated_declared_classes: Vec::new()` in `CommitmentStateDiff`→`ThinStateDiff` Conversion Produces Wrong State Diff Commitment and Block Hash — (`crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally hard-codes `deprecated_declared_classes: Vec::new()`. Because `deprecated_declared_classes` is an input to both `calculate_state_diff_hash` and `ThinStateDiff::len` (which feeds `concatenated_counts`), any block that contains a Cairo 0 (deprecated) class declaration will produce a wrong `state_diff_commitment` and a wrong `concatenated_counts` field, and therefore a wrong final block hash. That wrong block hash is written to the block-hash contract's storage and is later used to validate `SnosProofFacts` carried by Invoke V3 transactions.

---

### Finding Description

**Root cause — hardcoded empty list (analog to hardcoded zero):**

```rust
// crates/blockifier/src/state/cached_state.rs  L690-701
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff
                .class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),   // ← always empty
        }
    }
}
```

`CommitmentStateDiff` has no field for deprecated (Cairo 0) declared classes, so the conversion silently drops them.

**Downstream propagation:**

1. `BlockExecutionArtifacts::new` calls `calculate_block_commitments` with `ThinStateDiff::from(commitment_state_diff.clone())`. [1](#0-0) 

2. `calculate_block_commitments` passes the `ThinStateDiff` to both `calculate_state_diff_hash` (which chains `deprecated_declared_classes` into the Poseidon hash) and `concat_counts` (which uses `state_diff.len()`, which counts `deprecated_declared_classes.len()`). [2](#0-1) [3](#0-2) [4](#0-3) 

3. Both the wrong `state_diff_commitment` and the wrong `concatenated_counts` are chained into `calculate_block_hash`, producing a wrong block hash. [5](#0-4) 

4. `decision_reached` stores the wrong `state_diff_commitment` and the wrong `PartialBlockHashComponents` in storage. [6](#0-5) 

5. `finalize_commitment_output` later calls `calculate_block_hash` with those wrong components, producing a wrong final block hash that is written to storage. [7](#0-6) 

6. `validate_proof_block_hash` reads the stored (wrong) block hash and compares it against the hash embedded in a transaction's `SnosProofFacts`. [8](#0-7) 

---

### Impact Explanation

**Wrong storage value / wrong commitment (Critical):** For every block that includes a deprecated class declaration, the `state_diff_commitment`, `concatenated_counts`, and final block hash stored in the block-hash contract's storage are incorrect. The block-hash contract storage slot `block_hash_contract_address[block_number]` holds the wrong value.

**Valid proof facts rejected (High):** An Invoke V3 transaction whose `SnosProofFacts` carries the cryptographically correct block hash for such a block will fail `validate_proof_block_hash` because the stored hash does not match. The transaction is rejected before sequencing even though it is legitimate.

**Invalid proof facts accepted for hash check (Critical):** A transaction whose `SnosProofFacts` carries the wrong (stored) block hash will pass the hash-equality check. Combined with a proof generated against the wrong hash, this bypasses the block-hash binding that is the primary integrity guarantee of the client-side proving path.

---

### Likelihood Explanation

The trigger is a Cairo 0 (deprecated) class declaration transaction reaching the sequencer. The gateway config `reject_future_declare_txs` defaults to `true`, which blocks this at the gateway in default deployments. However:

- The flag is operator-configurable and may be disabled.
- The blockifier itself still accepts `TransactionVersion::ZERO` and `TransactionVersion::ONE` declare transactions.
- Any node that syncs blocks from a peer (via `add_sync_block`) that contains a deprecated class declaration will also compute the wrong block hash, because the same conversion is used. [9](#0-8) 

---

### Recommendation

Add a `deprecated_declared_classes` field to `CommitmentStateDiff` (mirroring `StateMaps.declared_contracts`) and populate it during execution. Remove the hardcoded `Vec::new()` in the `From<CommitmentStateDiff> for ThinStateDiff` conversion. The TODO comment at that line already acknowledges this debt. [10](#0-9) 

---

### Proof of Concept

1. Submit a Declare V1 (Cairo 0) transaction to a sequencer with `reject_future_declare_txs: false`.
2. The transaction is executed; `CommitmentStateDiff` is built from `StateMaps` — `deprecated_declared_classes` is absent from `CommitmentStateDiff`.
3. `BlockExecutionArtifacts::new` converts to `ThinStateDiff` with `deprecated_declared_classes: Vec::new()`.
4. `calculate_state_diff_hash` produces hash H_wrong (missing the declared class hash).
5. `ThinStateDiff::len()` returns N instead of N+1, so `concatenated_counts` encodes the wrong `state_diff_length`.
6. `calculate_block_hash` produces block_hash_wrong ≠ block_hash_correct.
7. `block_hash_wrong` is written to storage slot `block_hash_contract_address[block_number]`.
8. At block `block_number + STORED_BLOCK_HASH_BUFFER + 1`, submit an Invoke V3 transaction with `SnosProofFacts` containing `block_hash = block_hash_correct` (the true hash). `validate_proof_block_hash` reads `block_hash_wrong` from storage, the comparison fails, and the transaction is rejected despite being valid.
9. Alternatively, submit `SnosProofFacts` with `block_hash = block_hash_wrong`; the hash check passes, binding the proof to a commitment that does not reflect the true state diff.

### Citations

**File:** crates/apollo_batcher/src/block_builder.rs (L160-166)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-282)
```rust
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-327)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );

    let n_txs = transactions_data.len();
    let n_events = event_leaf_elements.len();
    let state_diff_length = state_diff.len();
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L30-42)
```rust
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    let mut hash_chain = HashChain::new();
    hash_chain = hash_chain.chain(&STARKNET_STATE_DIFF0);
    hash_chain = chain_deployed_contracts(&state_diff.deployed_contracts, hash_chain);
    hash_chain = chain_declared_classes(&state_diff.class_hash_to_compiled_class_hash, hash_chain);
    hash_chain =
        chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
    hash_chain = hash_chain.chain(&Felt::ONE) // placeholder.
        .chain(&Felt::ZERO); // placeholder.
    hash_chain = chain_storage_diffs(&state_diff.storage_diffs, hash_chain);
    hash_chain = chain_nonces(&state_diff.nonces, hash_chain);
    StateDiffCommitment(PoseidonHash(hash_chain.get_poseidon_hash()))
}
```

**File:** crates/starknet_api/src/state.rs (L110-121)
```rust
    pub fn len(&self) -> usize {
        let mut result = 0usize;
        result += self.deployed_contracts.len();
        result += self.class_hash_to_compiled_class_hash.len();
        result += self.deprecated_declared_classes.len();
        result += self.nonces.len();

        for (_contract_address, storage_diffs) in &self.storage_diffs {
            result += storage_diffs.len();
        }
        result
    }
```

**File:** crates/apollo_batcher/src/batcher.rs (L782-802)
```rust
        let partial_block_hash_components =
            block_execution_artifacts.partial_block_hash_components();
        let state_diff_commitment =
            partial_block_hash_components.header_commitments.state_diff_commitment;
        let parent_proposal_commitment = self.get_parent_proposal_commitment(height)?;
        self.commit_proposal_and_block(
            height,
            state_diff.clone(),
            block_execution_artifacts.address_to_nonce(),
            block_execution_artifacts.execution_data.consumed_l1_handler_tx_hashes,
            block_execution_artifacts.execution_data.rejected_tx_hashes,
            StorageCommitmentBlockHash::Partial(partial_block_hash_components),
        )
        .await?;

        self.write_commitment_results_and_add_new_task(
            height,
            state_diff.clone(), // TODO(Nimrod): Remove the clone here.
            Some(state_diff_commitment),
        )
        .await?;
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L520-525)
```rust
                let block_hash = calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?;
                Ok(FinalBlockCommitment { height, block_hash: Some(block_hash), global_root })
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L262-289)
```rust
    fn validate_proof_block_hash(
        proof_block_hash: Felt,
        proof_block_number: u64,
        os_constants: &OsConstants,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        if proof_block_hash == Felt::ZERO {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof block hash is zero for block {proof_block_number}."
            )));
        }

        // Compare the proof's block hash with the stored block hash.
        let block_hash_contract_address =
            os_constants.os_contract_addresses.block_hash_contract_address();

        let stored_block_hash = state
            .get_storage_at(block_hash_contract_address, StorageKey::from(proof_block_number))?;

        if stored_block_hash != proof_block_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Block hash mismatch for block {proof_block_number}. Proof block hash: \
                 {proof_block_hash}, stored block hash: {stored_block_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L240-251)
```rust
impl Default for StatefulTransactionValidatorConfig {
    fn default() -> Self {
        StatefulTransactionValidatorConfig {
            validate_resource_bounds: true,
            max_allowed_nonce_gap: 200,
            reject_future_declare_txs: true,
            max_nonce_for_validation_skip: Nonce(Felt::ONE),
            min_gas_price_percentage: 100,
            versioned_constants_overrides: None,
        }
    }
}
```

**File:** crates/blockifier/src/state/cached_state.rs (L690-702)
```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff
                .class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
}
```
