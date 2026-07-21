### Title
Silent Omission of `deprecated_declared_classes` in `CommitmentStateDiff → ThinStateDiff` Conversion Produces Wrong `state_diff_commitment` and `concatenated_counts` in Block Hash — (`File: crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally sets `deprecated_declared_classes: Vec::new()`. When a block contains deprecated (Cairo 0) Declare transactions, the `ThinStateDiff` fed into `calculate_block_commitments` is silently truncated. This causes the sequencer to compute a wrong `state_diff_commitment` and a wrong `state_diff_length` field inside `concatenated_counts`, both of which are chained into the final Poseidon block hash. The SNOS/prover, which operates on the full state diff, will compute a different block hash, breaking the commitment invariant.

---

### Finding Description

**Root cause — silent data loss in type conversion**

`CommitmentStateDiff` (produced by the blockifier from `StateMaps`) has no `deprecated_declared_classes` field. The `From` implementation hard-codes an empty `Vec`:

```rust
// crates/blockifier/src/state/cached_state.rs  lines 690-702
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
``` [1](#0-0) 

**Propagation into block commitments**

`BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff.clone())` and passes the result directly to `calculate_block_commitments`:

```rust
// crates/apollo_batcher/src/block_builder.rs  lines 160-166
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),   // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [2](#0-1) 

Inside `calculate_block_commitments`, two values are derived from the truncated diff:

1. `concatenated_counts = concat_counts(n_txs, n_events, state_diff.len(), l1_da_mode)` — `state_diff.len()` counts `deprecated_declared_classes.len()`, which is 0 instead of N.
2. `state_diff_commitment = calculate_state_diff_hash(&state_diff)` — the hash chains `deprecated_declared_classes`, so it is computed over an empty list instead of the actual N class hashes. [3](#0-2) 

`ThinStateDiff::len()` explicitly counts `deprecated_declared_classes`:

```rust
// crates/starknet_api/src/state.rs  lines 110-121
pub fn len(&self) -> usize {
    let mut result = 0usize;
    result += self.deployed_contracts.len();
    result += self.class_hash_to_compiled_class_hash.len();
    result += self.deprecated_declared_classes.len();   // ← 0 instead of N
    result += self.nonces.len();
    for (_contract_address, storage_diffs) in &self.storage_diffs {
        result += storage_diffs.len();
    }
    result
}
``` [4](#0-3) 

`calculate_state_diff_hash` also chains the deprecated declared classes: [5](#0-4) 

Both wrong values are stored in `PartialBlockHashComponents.header_commitments` and are later chained into the final Poseidon block hash by `calculate_block_hash`: [6](#0-5) 

---

### Impact Explanation

For any block that contains at least one deprecated (Cairo 0) Declare transaction:

| Value | Sequencer computes | Correct value |
|---|---|---|
| `state_diff_commitment` | Poseidon hash over diff **without** deprecated declared classes | Poseidon hash over diff **with** deprecated declared classes |
| `state_diff_length` inside `concatenated_counts` | `actual_len − N` | `actual_len` |
| Final `block_hash` | Wrong (uses both wrong fields above) | Correct |

The SNOS/prover operates on the full state diff and will compute a different block hash, causing proof verification to fail. The block hash stored in the sequencer's storage and broadcast to peers is permanently wrong for those blocks.

This matches the impact category: **Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input** (specifically: wrong `state_diff_commitment`, wrong `concatenated_counts`, and wrong final `block_hash`).

---

### Likelihood Explanation

Deprecated Declare transactions (DeclareV0 / DeclareV1) are still valid Starknet transaction types. Any unprivileged user can submit one. No special role or network position is required. The bug fires deterministically on the first such transaction included in a block.

---

### Recommendation

`CommitmentStateDiff` must be extended to carry `deprecated_declared_classes`, or the blockifier's `StateMaps` must track them, so that the `From` conversion can populate the field correctly instead of hard-coding `Vec::new()`. Until then, the TODO comment at line 699 of `crates/blockifier/src/state/cached_state.rs` represents an active commitment-integrity defect, not merely a structural cleanup item.

---

### Proof of Concept

1. Submit a `DeclareV1` transaction (Cairo 0 class) to the sequencer gateway.
2. Wait for the transaction to be included in a block.
3. Retrieve the block's `state_diff_commitment` from the sequencer's RPC (`starknet_getStateUpdate`).
4. Independently compute `calculate_state_diff_hash` over the full `ThinStateDiff` that includes the deprecated declared class hash.
5. Observe that the two values differ by exactly the contribution of the deprecated declared class entry.
6. Retrieve the block hash and verify it does not match the value computed by the SNOS using the correct state diff.

The discrepancy is deterministic and reproducible for every block containing at least one deprecated Declare transaction.

### Citations

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L29-42)
```rust
/// ).
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
