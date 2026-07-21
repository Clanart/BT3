### Title
`deprecated_declared_classes` Silently Zeroed in Block Hash Commitment Calculation — (`File: crates/apollo_batcher/src/block_builder.rs`)

### Summary

`BlockExecutionArtifacts::new` converts `CommitmentStateDiff` → `ThinStateDiff` via `ThinStateDiff::from(CommitmentStateDiff)`, which hard-codes `deprecated_declared_classes: Vec::new()`. Because `calculate_block_commitments` feeds this `ThinStateDiff` into both `calculate_state_diff_hash` and `ThinStateDiff::len()` (used for `state_diff_length` in `packed_lengths`), any block that contains a deprecated (Cairo 0) class declaration will produce a `state_diff_commitment` and a `state_diff_length` that are both wrong. Both fields are committed into the block hash, so the block hash itself is wrong for every such block.

---

### Finding Description

**Step 1 – `CommitmentStateDiff` has no `deprecated_declared_classes` field.** [1](#0-0) 

`CommitmentStateDiff` tracks `address_to_class_hash`, `address_to_nonce`, `storage_updates`, and `class_hash_to_compiled_class_hash`. There is no field for deprecated (Cairo 0) declared classes.

**Step 2 – The `From` impl always emits an empty `deprecated_declared_classes`.** [2](#0-1) 

The conversion explicitly writes `deprecated_declared_classes: Vec::new()` with a `TODO` comment acknowledging the gap.

**Step 3 – `BlockExecutionArtifacts::new` uses this lossy conversion for commitment calculation.** [3](#0-2) 

`ThinStateDiff::from(commitment_state_diff.clone())` is passed directly to `calculate_block_commitments`. At this point `deprecated_declared_classes` is always `[]`.

**Step 4 – `calculate_block_commitments` uses `state_diff.len()` for `state_diff_length` and `calculate_state_diff_hash` for `state_diff_commitment`.** [4](#0-3) 

`state_diff.len()` counts `deprecated_declared_classes.len()` as part of the total: [5](#0-4) 

**Step 5 – `calculate_state_diff_hash` explicitly hashes `deprecated_declared_classes`.** [6](#0-5) 

The hash chains `chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, ...)`. When the list is always empty, the hash diverges from the correct value.

**Step 6 – Both wrong values enter the block hash.**

`state_diff_commitment` and `packed_lengths` (which encodes `state_diff_length`) are both fed into `calculate_block_hash`: [7](#0-6) 

**Step 7 – The `finalize_block` path never captures deprecated declared classes.** [8](#0-7) 

`block_state.to_state_diff()?.state_maps` is converted to `CommitmentStateDiff` via `StateMaps::into()`. `StateMaps` has no `deprecated_declared_classes` field, so the information is lost before it ever reaches `BlockExecutionArtifacts`.

**Contrast with the RPC simulation path**, which correctly threads the class hash through separately: [9](#0-8) 

The block-production path has no equivalent mechanism.

---

### Impact Explanation

For every block that contains at least one deprecated (Cairo 0) class declaration:

1. `state_diff_commitment` stored in the block header is computed over a state diff that omits those class hashes — it is cryptographically wrong.
2. `state_diff_length` packed into `packed_lengths` is too low by the count of deprecated declared classes.
3. The final block hash is therefore wrong.

Any RPC consumer calling `starknet_getBlockWithTxHashes`, `starknet_getStateUpdate`, or any method that returns `state_diff_commitment` or the block hash receives an authoritative-looking but incorrect value. Downstream verifiers (L1 verifier, proof systems, light clients) that recompute the state diff commitment from the raw state diff will observe a mismatch.

Impact: **High — RPC returns an authoritative-looking wrong value** (`state_diff_commitment`, `state_diff_length`, block hash).

---

### Likelihood Explanation

Deprecated declare transactions (Cairo 0 `DECLARE` v1) remain valid on Starknet. Any unprivileged user can submit one. The gateway accepts them (the `induced_state_diff` helper in `execution_utils.rs` explicitly handles the `deprecated_declared_class_hash` parameter, confirming the execution path exists). A single such transaction in any block is sufficient to trigger the wrong commitment.

---

### Recommendation

`BlockExecutionArtifacts::new` must receive the list of deprecated declared class hashes produced during block execution and pass them into the `ThinStateDiff` used for `calculate_block_commitments`. Two concrete options:

1. **Add `deprecated_declared_classes: Vec<ClassHash>` to `BlockExecutionSummary`** and populate it from the transaction executor (analogous to how `induced_state_diff` receives `deprecated_declared_class_hash`). Then construct the `ThinStateDiff` with the real list instead of `Vec::new()`.

2. **Add `deprecated_declared_classes` to `CommitmentStateDiff`** and populate it during `finalize_block`, then remove the `TODO` in the `From` impl.

Either way, the `ThinStateDiff` passed to `calculate_block_commitments` must match the `ThinStateDiff` that would be reconstructed from the full `StateDiff` (as done in `ThinStateDiff::from_state_diff`).

---

### Proof of Concept

```
1. Submit a valid deprecated Declare (v1) transaction for a Cairo 0 class to the sequencer.
2. The transaction is executed; `finalize_block` produces a `BlockExecutionSummary`
   whose `state_diff: CommitmentStateDiff` has no deprecated_declared_classes field.
3. `BlockExecutionArtifacts::new` calls
       ThinStateDiff::from(commitment_state_diff.clone())
   which sets deprecated_declared_classes = [].
4. `calculate_block_commitments` computes:
       state_diff_commitment = hash(... deprecated_declared_classes=[] ...)   // WRONG
       state_diff_length     = len(... deprecated_declared_classes=0 ...)     // WRONG
5. The block hash is finalized with these wrong values.
6. A verifier that independently computes
       calculate_state_diff_hash(&correct_thin_state_diff)
   (where correct_thin_state_diff.deprecated_declared_classes = [declared_class_hash])
   obtains a different hash, proving the mismatch.
```

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L700-710)
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

**File:** crates/blockifier/src/state/cached_state.rs (L756-768)
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

**File:** crates/apollo_batcher/src/block_builder.rs (L170-176)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-357)
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

    // Wait for all tasks to complete.
    let (
        (transaction_commitment, transaction_duration),
        (event_commitment, event_duration),
        (receipt_commitment, receipt_duration),
        (state_diff_commitment, state_diff_duration),
    ) = tokio::try_join!(transaction_task, event_task, receipt_task, state_diff_task)
        .expect("Failed to join block commitments tasks.");

    let commitments = BlockHeaderCommitments {
        transaction_commitment,
        event_commitment,
        receipt_commitment,
        state_diff_commitment,
        concatenated_counts,
    };
```

**File:** crates/starknet_api/src/state.rs (L111-122)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L37-39)
```text
        hash_update_single(header_commitments.packed_lengths);
        hash_update_single(header_commitments.state_diff_commitment);
        hash_update_single(header_commitments.transaction_commitment);
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L275-305)
```rust
    let state_diff = block_state.to_state_diff()?.state_maps;

    #[cfg(feature = "os_input")]
    let initial_reads = block_state.get_os_initial_reads()?;

    let compressed_state_diff = if block_context.versioned_constants.enable_stateful_compression {
        Some(compress(&state_diff, block_state, alias_contract_address)?.into())
    } else {
        None
    };

    // Take CasmHashComputationData from bouncer,
    // and verify that class hashes are the same.
    let casm_hash_computation_data_sierra_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_sierra_gas());
    let casm_hash_computation_data_proving_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_proving_gas());

    assert_eq!(
        casm_hash_computation_data_sierra_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>(),
        casm_hash_computation_data_proving_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>()
    );

    Ok(BlockExecutionSummary {
        state_diff: state_diff.into(),
```

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L130-145)
```rust
pub fn induced_state_diff(
    transactional_state: &mut CachedState<MutRefState<'_, CachedState<ExecutionStateReader>>>,
    deprecated_declared_class_hash: Option<ClassHash>,
) -> ExecutionResult<ThinStateDiff> {
    let blockifier_state_diff =
        CommitmentStateDiff::from(transactional_state.to_state_diff()?.state_maps);

    Ok(ThinStateDiff {
        deployed_contracts: blockifier_state_diff.address_to_class_hash,
        storage_diffs: blockifier_state_diff.storage_updates,
        class_hash_to_compiled_class_hash: blockifier_state_diff.class_hash_to_compiled_class_hash,
        deprecated_declared_classes: deprecated_declared_class_hash
            .map_or_else(Vec::new, |class_hash| vec![class_hash]),
        nonces: blockifier_state_diff.address_to_nonce,
    })
}
```
