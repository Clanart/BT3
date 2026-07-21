### Title
`CommitmentStateDiff`→`ThinStateDiff` Silently Drops Deprecated Declared Classes, Corrupting State Diff Commitment and Block Hash — (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally hardcodes `deprecated_declared_classes: Vec::new()`. When the sequencer produces a block containing a Cairo 0 (deprecated) class declaration, the `ThinStateDiff` fed into `calculate_block_commitments` is missing those class hashes. This yields a wrong `StateDiffCommitment` and a wrong `state_diff_length` inside `concat_counts`, both of which are chained into `calculate_block_hash`, producing a block hash that does not commit to the actual state changes.

---

### Finding Description

`CommitmentStateDiff` has no `deprecated_declared_classes` field. Its `From<StateMaps>` constructor only captures `class_hash_to_compiled_class_hash` (Cairo 1 classes). Cairo 0 class declarations produce no entry in `StateMaps.compiled_class_hashes`, so they are invisible to `CommitmentStateDiff`. [1](#0-0) 

The hardcoded `Vec::new()` is explicitly acknowledged as a known structural gap:

```rust
// TODO(AlonH): Remove this when the structure of storage diffs changes.
deprecated_declared_classes: Vec::new(),
```

`BlockExecutionArtifacts::new()` calls `ThinStateDiff::from(commitment_state_diff.clone())` and passes the result to `calculate_block_commitments`: [2](#0-1) 

Inside `calculate_block_commitments`, two values are derived from this `ThinStateDiff`:

1. **`state_diff_commitment`** — `calculate_state_diff_hash` chains `deprecated_declared_classes` into the Poseidon hash. With an empty list, the commitment is wrong for any block that actually declared a Cairo 0 class. [3](#0-2) 

2. **`state_diff_length`** — `ThinStateDiff::len()` adds `deprecated_declared_classes.len()` to the total. With an empty list, the count is too low. [4](#0-3) 

Both values are chained into `calculate_block_hash` via `concat_counts` and `state_diff_commitment.0.0`: [5](#0-4) 

The same corrupted `ThinStateDiff` is also written to storage via `append_state_diff`, so the stored state diff is also missing the deprecated declared classes: [6](#0-5) 

---

### Impact Explanation

Any block containing a Cairo 0 `Declare` transaction will have a `state_diff_commitment` and `state_diff_length` that omit the declared class hashes. The resulting block hash does not commit to the actual state changes. Any independent verifier (OS/prover, L1 contract, syncing node) that recomputes the state diff hash from the actual state changes will observe a mismatch. This matches the scope criterion: **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

The `partial_block_hash_components` stored and broadcast to consensus carries the corrupted `state_diff_commitment`: [7](#0-6) 

---

### Likelihood Explanation

Cairo 0 `Declare` transactions are still valid Starknet transactions. The `StatelessTransactionValidator` validates declare transactions but does not explicitly block Cairo 0 declarations. Any unprivileged user can submit a deprecated `Declare` transaction to trigger this path. The gateway accepts it, the blockifier executes it, and the class hash is stored in state — but never flows into `StateMaps.compiled_class_hashes`, so it is invisible to `CommitmentStateDiff` and the downstream commitment pipeline.

---

### Recommendation

Add a `deprecated_declared_classes: Vec<ClassHash>` field to `CommitmentStateDiff` and populate it during Cairo 0 `Declare` transaction execution. Update `From<CommitmentStateDiff> for ThinStateDiff` to propagate this field instead of hardcoding `Vec::new()`. The `StateMaps` struct should similarly gain a `deprecated_declared_classes` field so the information is not lost at the execution layer.

---

### Proof of Concept

1. Submit a Cairo 0 `Declare` transaction to the sequencer gateway.
2. The blockifier executes it; the class hash is stored in state but produces no entry in `StateMaps.compiled_class_hashes`.
3. `CommitmentStateDiff::from(state_maps)` captures nothing for the deprecated class.
4. `ThinStateDiff::from(commitment_state_diff)` sets `deprecated_declared_classes: Vec::new()`.
5. `calculate_state_diff_hash` chains an empty list, producing a `StateDiffCommitment` that differs from the correct value (which would include the class hash).
6. `state_diff.len()` returns a count that is too low by the number of deprecated declared classes declared in the block.
7. `concat_counts(tx_count, event_count, wrong_state_diff_length, da_mode)` encodes the wrong length into the block hash preimage.
8. `calculate_block_hash` chains the wrong `state_diff_commitment` and `concatenated_counts`, producing a block hash that does not reflect the actual state changes.
9. Any independent verifier recomputing the state diff hash from the actual on-chain state will observe a mismatch, breaking proof verification and L1 settlement for that block.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L690-701)
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
```

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

**File:** crates/apollo_storage/src/state/mod.rs (L563-573)
```rust
        for class_hash in thin_state_diff.deprecated_declared_classes.iter() {
            // Cairo0 classes can be declared in different blocks. The first block to declare the
            // class is recorded here.
            if deprecated_declared_classes_block_table.get(&self.txn, class_hash)?.is_none() {
                deprecated_declared_classes_block_table.insert(
                    &self.txn,
                    class_hash,
                    &block_number,
                )?;
            }
        }
```

**File:** crates/apollo_batcher/src/batcher.rs (L782-801)
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
```
