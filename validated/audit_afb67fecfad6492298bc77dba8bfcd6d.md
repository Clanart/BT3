### Title
`CommitmentStateDiff` → `ThinStateDiff` Conversion Silently Drops `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and Block Hash for Any Block Containing Cairo 0 Declarations - (`File: crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion in `crates/blockifier/src/state/cached_state.rs` unconditionally sets `deprecated_declared_classes: Vec::new()`. This field is a required input to `calculate_state_diff_hash`, which is part of the Poseidon block hash chain. Any block that includes a deprecated (Cairo 0) `DECLARE` transaction will have a wrong `state_diff_commitment`, wrong `concatenated_counts` (packed lengths), and therefore a wrong block hash and wrong `ProposalCommitment`. This is the direct sequencer analog of the external bug: a required parameter is silently omitted from a commitment/hash computation.

---

### Finding Description

**Root cause — the omission:** [1](#0-0) 

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
            deprecated_declared_classes: Vec::new(),   // ← always empty
        }
    }
}
```

`CommitmentStateDiff` has no `deprecated_declared_classes` field, so the conversion hardcodes an empty `Vec`. The TODO comment acknowledges this is a structural gap, not an intentional design choice.

**How `deprecated_declared_classes` is required by the hash:**

`calculate_state_diff_hash` chains the deprecated class list into the Poseidon hash: [2](#0-1) 

```
Poseidon("STARKNET_STATE_DIFF0", deployed_contracts, declared_classes,
         deprecated_declared_classes,   // ← required field
         1, 0, storage_diffs, nonces)
```

`ThinStateDiff::len()` also counts `deprecated_declared_classes.len()`, which feeds `concatenated_counts` (the packed lengths field in the block hash): [3](#0-2) 

**The corrupted commitment path in `BlockExecutionArtifacts::new`:** [4](#0-3) 

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
).await;
```

The resulting `header_commitments.state_diff_commitment` is wrong. It is stored in `PartialBlockHashComponents`: [5](#0-4) 

and chained into the final block hash at position 6 (`state_diff_commitment`) and position 5 (`concatenated_counts`).

**The `ProposalCommitment` (consensus agreement value) is also wrong:** [6](#0-5) 

```rust
pub fn commitment(&self) -> ProposalCommitment {
    ProposalCommitment {
        partial_block_hash: PartialBlockHash::from_partial_block_hash_components(
            &self.partial_block_hash_components,
        )...
    }
}
```

**The stored `ThinStateDiff` is also wrong:**

`thin_state_diff()` uses the same conversion: [7](#0-6) 

So the state diff written to MDBX storage via `decision_reached` → `commit_proposal_and_block` is also missing `deprecated_declared_classes`.

**Contrast with the correct path** (`ThinStateDiff::from_state_diff`), which properly populates the field: [8](#0-7) 

---

### Impact Explanation

For any block containing a deprecated (Cairo 0) `DECLARE` transaction:

1. **Wrong `state_diff_commitment`** stored in the block header and returned by RPC (`starknet_getBlockWithTxHashes`, `starknet_getStateUpdate`). This is an authoritative-looking wrong value.
2. **Wrong `concatenated_counts`** (packed lengths field), because `ThinStateDiff::len()` returns a count that is too low by the number of deprecated class declarations.
3. **Wrong block hash** — both `state_diff_commitment` and `concatenated_counts` are chained into the Poseidon block hash.
4. **Wrong `ProposalCommitment`** — the partial block hash used for consensus agreement is derived from the same wrong components, meaning validators agree on a commitment that does not match the actual state diff.
5. **Proof failure** — the SNOS/prover independently computes `state_diff_hash` from the actual state diff (which includes deprecated classes). The prover's value will not match the sequencer's stored `state_diff_commitment`, causing proof verification to fail for any such block.
6. **Wrong state diff in storage** — the `ThinStateDiff` persisted to MDBX is missing `deprecated_declared_classes`, so any downstream consumer (state sync, re-execution, RPC `starknet_getStateUpdate`) gets an incomplete state diff.

---

### Likelihood Explanation

Cairo 0 `DECLARE` transactions (deprecated declares) are still valid on Starknet and can be submitted by any unprivileged user. A single such transaction included in a block is sufficient to trigger the bug. The trigger requires no special privileges, no malicious peer, and no privileged configuration — only a standard deprecated class declaration transaction reaching the sequencer's mempool and being included in a block.

---

### Recommendation

`CommitmentStateDiff` must be extended to track deprecated declared class hashes, or the conversion must be supplied with the deprecated class list from a separate source. The `From<CommitmentStateDiff> for ThinStateDiff` impl must not hardcode `deprecated_declared_classes: Vec::new()`. The blockifier's execution path for deprecated `DECLARE` transactions must populate this field in `CommitmentStateDiff` so that `calculate_state_diff_hash` and `ThinStateDiff::len()` receive the correct inputs.

---

### Proof of Concept

1. Submit a Cairo 0 `DECLARE` transaction (deprecated declare) to the sequencer gateway.
2. The transaction is included in a block. `BlockExecutionArtifacts::new` is called.
3. `ThinStateDiff::from(commitment_state_diff.clone())` produces a `ThinStateDiff` with `deprecated_declared_classes = []`.
4. `calculate_block_commitments` calls `calculate_state_diff_hash` on this diff, producing `state_diff_commitment = Poseidon(..., 0_deprecated_classes, ...)` (count = 0, no class hashes).
5. The correct value would be `Poseidon(..., 1_deprecated_class, class_hash_X, ...)`.
6. The wrong `state_diff_commitment` is stored in `BlockHeaderCommitments`, propagated into `PartialBlockHashComponents`, and used to compute both the `ProposalCommitment` (consensus) and the final block hash.
7. The SNOS prover, computing the state diff hash from the actual executed state, obtains a different value and rejects the proof.
8. RPC `starknet_getBlockWithTxHashes` returns the wrong `state_diff_commitment` for this block.

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

**File:** crates/starknet_api/src/state.rs (L94-99)
```rust
                deprecated_declared_classes: diff
                    .deprecated_declared_classes
                    .keys()
                    .copied()
                    .collect(),
                nonces: diff.nonces,
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

**File:** crates/apollo_batcher/src/block_builder.rs (L198-201)
```rust
    pub fn thin_state_diff(&self) -> ThinStateDiff {
        // TODO(Ayelet): Remove the clones.
        ThinStateDiff::from(self.commitment_state_diff.clone())
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L203-210)
```rust
    pub fn commitment(&self) -> ProposalCommitment {
        ProposalCommitment {
            partial_block_hash: PartialBlockHash::from_partial_block_hash_components(
                &self.partial_block_hash_components,
            )
            .expect("Unable to calculate the proposal commitment"),
        }
    }
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
