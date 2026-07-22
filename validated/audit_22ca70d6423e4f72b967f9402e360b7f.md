### Title
`CommitmentStateDiff` Omits Deprecated Class Declarations, Producing Wrong `state_diff_commitment` and `concatenated_counts` in Block Hash — (`crates/blockifier/src/state/cached_state.rs`, `crates/apollo_batcher/src/block_builder.rs`)

---

### Summary

`CommitmentStateDiff` has no `deprecated_declared_classes` field. The `From<CommitmentStateDiff> for ThinStateDiff` conversion therefore always sets `deprecated_declared_classes: Vec::new()`. `BlockExecutionArtifacts::new` feeds this truncated `ThinStateDiff` directly into `calculate_block_commitments`, which uses it to compute both the `state_diff_commitment` (Poseidon hash over the full diff) and `concatenated_counts` (which encodes `state_diff_length`). Both values are chained into the final block hash. Any block that contains a deprecated (Cairo 0) Declare transaction will therefore carry a wrong `state_diff_commitment`, a wrong `state_diff_length` inside `concatenated_counts`, and consequently a wrong block hash — breaking proof verification and L1 settlement.

---

### Finding Description

**Step 1 — `CommitmentStateDiff` has no deprecated-class field.**

`CommitmentStateDiff` tracks only `address_to_class_hash`, `address_to_nonce`, `storage_updates`, and `class_hash_to_compiled_class_hash`. There is no slot for deprecated (Cairo 0) class declarations. [1](#0-0) 

**Step 2 — Conversion to `ThinStateDiff` hard-codes an empty list.**

The `From<CommitmentStateDiff> for ThinStateDiff` impl unconditionally writes `deprecated_declared_classes: Vec::new()`, with a `TODO` acknowledging the gap. [2](#0-1) 

**Step 3 — `BlockExecutionArtifacts::new` passes the truncated diff to commitment calculation.**

`BlockExecutionArtifacts::new` converts `commitment_state_diff` to `ThinStateDiff` and immediately passes it to `calculate_block_commitments`. No deprecated-class data is injected before this call. [3](#0-2) 

**Step 4 — `calculate_block_commitments` derives both the state-diff hash and `state_diff_length` from the truncated diff.**

Inside `calculate_block_commitments`, `state_diff.len()` is used for `concatenated_counts` and `state_diff_length`, and `calculate_state_diff_hash(&state_diff)` is spawned as a task. Both consume the `ThinStateDiff` whose `deprecated_declared_classes` is always empty. [4](#0-3) 

**Step 5 — `calculate_state_diff_hash` chains deprecated classes into the Poseidon hash.**

The hash function explicitly chains `deprecated_declared_classes` into the commitment. An empty list produces a different hash than the correct non-empty list. [5](#0-4) 

**Step 6 — `ThinStateDiff::len()` counts deprecated classes; the wrong length propagates into `concatenated_counts`.**

`len()` adds `self.deprecated_declared_classes.len()` to the total. With an empty list the count is under-reported, so `concatenated_counts` encodes a wrong `state_diff_length`. [6](#0-5) 

**Step 7 — Both wrong values are chained into the block hash.**

`calculate_block_hash` chains `concatenated_counts` and `state_diff_commitment.0.0` into the Poseidon hash that becomes the canonical block hash. [7](#0-6) 

**Step 8 — The wrong `PartialBlockHashComponents` is stored and used for the proposal commitment.**

`BlockExecutionArtifacts::new` stores the `PartialBlockHashComponents` built from the wrong commitments. `decision_reached` reads them back and passes them to `commit_proposal_and_block`. [8](#0-7) [9](#0-8) 

---

### Impact Explanation

Any block that includes at least one deprecated Declare transaction (v0/v1) will have:

1. A `state_diff_commitment` that omits the declared class hashes — the Poseidon hash is computed over a shorter input than the actual state diff.
2. A `concatenated_counts` field whose embedded `state_diff_length` is smaller than the real length by the number of deprecated declarations.
3. A block hash derived from both wrong values.

The stored `ThinStateDiff` (written by `append_state_diff`) is also produced from the same `CommitmentStateDiff` conversion, so the committer's optional `verify_state_diff_hash` check passes silently — both sides of the comparison are equally wrong. The error surfaces only when an external verifier (L1 settlement contract, proof verifier, or syncing node) independently recomputes the block hash from the actual state diff.

This matches the **Critical** impact category: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.*

---

### Likelihood Explanation

Any unprivileged user can submit a deprecated Declare transaction (v0/v1) to the gateway. The sequencer accepts and executes it. No special privilege, flash loan, or price manipulation is required. The corruption is deterministic and reproducible on every block that contains such a transaction.

---

### Recommendation

Add a `deprecated_declared_classes: Vec<ClassHash>` field to `CommitmentStateDiff` and populate it from the blockifier's state tracking when a deprecated class is declared. Update `From<CommitmentStateDiff> for ThinStateDiff` to copy the field instead of hard-coding `Vec::new()`. This ensures `calculate_block_commitments` receives a complete `ThinStateDiff` and produces correct `state_diff_commitment`, `concatenated_counts`, and block hash values.

---

### Proof of Concept

1. Submit a `Declare` v0 or v1 transaction for a Cairo 0 contract class to the sequencer gateway.
2. Wait for the sequencer to include it in a block and call `decision_reached`.
3. Retrieve the `PartialBlockHashComponents` stored for that block.
4. Independently compute `calculate_state_diff_hash` over the full `ThinStateDiff` (with the deprecated class hash present).
5. Observe that the stored `state_diff_commitment` differs from the independently computed value, and that `concatenated_counts` encodes a `state_diff_length` that is smaller by 1 than the actual diff length.
6. Compute the correct block hash using the correct commitments and observe it differs from the hash stored by the sequencer.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L667-677)
```rust
#[cfg_attr(feature = "transaction_serde", derive(serde::Serialize, serde::Deserialize))]
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CommitmentStateDiff {
    // Contract instance attributes (per address).
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,

    // Global attributes.
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
```

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

**File:** crates/apollo_batcher/src/block_builder.rs (L168-182)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-341)
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

    // Spawn tasks for parallel execution; each measures its own duration.
    let transaction_task = spawn_measured_task(move || {
        calculate_transaction_commitment::<Poseidon>(&transaction_leaf_elements)
    });

    let event_task =
        spawn_measured_task(move || calculate_event_commitment::<Poseidon>(&event_leaf_elements));

    let receipt_task =
        spawn_measured_task(move || calculate_receipt_commitment::<Poseidon>(&receipt_elements));

    let state_diff_task = spawn_measured_task(move || calculate_state_diff_hash(&state_diff));

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

**File:** crates/apollo_batcher/src/batcher.rs (L782-795)
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
```
