### Title
`CommitmentStateDiff → ThinStateDiff` Conversion Always Zeroes `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and `concatenated_counts` in Block Hash — (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

`CommitmentStateDiff` has no `deprecated_declared_classes` field. The `From<CommitmentStateDiff> for ThinStateDiff` implementation hard-codes that field to `Vec::new()`. Because the batcher feeds this converted `ThinStateDiff` directly into `calculate_block_commitments`, every block that contains a deprecated (Cairo 0) class declaration will have both its `state_diff_commitment` and its `concatenated_counts` (`state_diff_length` slot) computed over an incomplete state diff. The resulting `PartialBlockHashComponents` — and therefore the block hash committed to storage and signed by consensus — is wrong.

---

### Finding Description

**Root cause — structural omission analogous to the external precision-loss bug.**

`CommitmentStateDiff` tracks only the four fields that affect the Patricia trie:

```rust
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
``` [1](#0-0) 

Deprecated (Cairo 0) class declarations have no compiled class hash and therefore never appear in `class_hash_to_compiled_class_hash`. The `From` impl acknowledges this with a TODO and hard-codes the field to empty:

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
``` [2](#0-1) 

**Propagation into block-hash computation.**

`BlockExecutionArtifacts::new` calls `calculate_block_commitments` with exactly this converted diff:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),   // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [3](#0-2) 

`calculate_block_commitments` derives two block-hash inputs from the diff:

1. **`state_diff_commitment`** — via `calculate_state_diff_hash`, which chains `deprecated_declared_classes` into the Poseidon hash: [4](#0-3) 

2. **`concatenated_counts`** — via `concat_counts(…, state_diff.len(), …)`, where `ThinStateDiff::len()` adds `deprecated_declared_classes.len()`: [5](#0-4) 

Both values are chained into the final block hash:

```rust
.chain(&block_commitments.concatenated_counts)
.chain(&block_commitments.state_diff_commitment.0.0)
``` [6](#0-5) 

**Analogy to the external bug.**

| External (Beedle) | Sequencer |
|---|---|
| `_calculateInterest` divides twice → interest truncated to 0 | `CommitmentStateDiff → ThinStateDiff` drops `deprecated_declared_classes` → count truncated to 0 |
| `totalDebt` underestimated | `state_diff_length` underestimated; `state_diff_commitment` missing entries |
| `loanRatio` check bypassed | Block hash wrong; commitment signed by consensus is wrong |

---

### Impact Explanation

For any block that includes at least one `DeclareV1` (deprecated Cairo 0 class) transaction:

- `state_diff_commitment` is computed over a diff that omits those declarations → the Poseidon hash is wrong.
- `concatenated_counts` encodes a `state_diff_length` that is smaller than the true length by the number of deprecated declarations → the packed field is wrong.
- Both wrong values are fed into `calculate_block_hash` and stored as the canonical `PartialBlockHashComponents` for that block. [7](#0-6) 

The `thin_state_diff()` accessor used by the batcher's `decision_reached` path also derives from the same conversion, so the state diff written to storage is also missing the deprecated declarations: [8](#0-7) 

Downstream effects:
- The block hash committed to storage and used as `previous_block_hash` in the next block is wrong.
- Any SNOS/prover run that independently computes `state_diff_commitment` from the full state diff will disagree with the sequencer's value, causing proof-fact mismatches.
- RPC `starknet_getStateUpdate` returns a `state_diff_commitment` that does not match the on-chain block hash.

This matches the allowed impact: **"Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."**

---

### Likelihood Explanation

`DeclareV1` transactions (deprecated Cairo 0 class declarations) are still valid Starknet transaction types accepted by the gateway. Any unprivileged user can submit one. The moment such a transaction is included in a block, the block hash is silently wrong. No special timing, race condition, or privileged access is required.

---

### Recommendation

`CommitmentStateDiff` must be extended with a `deprecated_declared_classes` field, populated by the blockifier when it processes `DeclareV1` transactions. The `From<CommitmentStateDiff> for ThinStateDiff` impl should then propagate that field instead of hard-coding `Vec::new()`. Until that structural change is made, `BlockExecutionArtifacts::new` should obtain the deprecated declared classes from a separate source (e.g., the `StateMaps.declared_contracts` set filtered to Cairo 0 classes) and merge them into the `ThinStateDiff` before calling `calculate_block_commitments`.

---

### Proof of Concept

1. Submit a `DeclareV1` transaction declaring any Cairo 0 contract class.
2. Wait for the transaction to be included in a block (block `N`).
3. Query `starknet_getBlockWithTxHashes` for block `N` and record `block_hash`.
4. Independently recompute the block hash using the full `ThinStateDiff` (with `deprecated_declared_classes` populated from the transaction receipt).
5. Observe that the two hashes differ by exactly the contribution of the deprecated declared class to `state_diff_commitment` and `concatenated_counts`.

Concretely, `ThinStateDiff::len()` for a block with one deprecated declaration returns `N` (correct), but the value passed to `concat_counts` is `N - 1` (wrong), because `deprecated_declared_classes` is `[]` in the batcher path. [5](#0-4) [9](#0-8)

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

**File:** crates/blockifier/src/state/cached_state.rs (L756-767)
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

**File:** crates/apollo_batcher/src/block_builder.rs (L178-194)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            #[cfg(feature = "os_input")]
            initial_reads,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L210-213)
```rust
    pub fn thin_state_diff(&self) -> ThinStateDiff {
        // TODO(Ayelet): Remove the clones.
        ThinStateDiff::from(self.commitment_state_diff.clone())
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L259-262)
```rust
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-323)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );
```
