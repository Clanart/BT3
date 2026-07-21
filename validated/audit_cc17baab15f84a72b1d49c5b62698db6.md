### Title
`CommitmentStateDiff` Missing `deprecated_declared_classes` Causes Wrong `state_diff_commitment`, `concatenated_counts`, and Block Hash — (`crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally hardcodes `deprecated_declared_classes: Vec::new()`. Because `CommitmentStateDiff` has no field for deprecated (Cairo 0) class declarations, any block that contains a `DeclareV0`/`DeclareV1` transaction will produce a `ThinStateDiff` that silently omits those declarations. Every downstream commitment — `state_diff_commitment`, `state_diff_length` inside `concatenated_counts`, `PartialBlockHashComponents`, and the final `block_hash` — is therefore computed over an incomplete state diff.

---

### Finding Description

**Root cause — missing field in `CommitmentStateDiff`:**

`CommitmentStateDiff` is assembled from `StateMaps` after block execution:

```rust
// crates/blockifier/src/state/cached_state.rs
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
    // ← no deprecated_declared_classes field
}
```

`StateMaps` tracks `compiled_class_hashes` (Cairo 1 declarations) but has no slot for deprecated Cairo 0 class declarations. Consequently, when `CommitmentStateDiff` is converted to `ThinStateDiff`, the field is hardcoded to empty:

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
``` [1](#0-0) 

**Propagation through `BlockExecutionArtifacts::new`:**

`BlockExecutionArtifacts::new` calls `calculate_block_commitments` with `ThinStateDiff::from(commitment_state_diff.clone())`:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [2](#0-1) 

**Two commitment values are corrupted:**

Inside `calculate_block_commitments`, the truncated `ThinStateDiff` is used in two ways:

1. `calculate_state_diff_hash` chains `deprecated_declared_classes` into the Poseidon hash — with an empty list the hash is wrong:

```rust
hash_chain = chain_deprecated_declared_classes(
    &state_diff.deprecated_declared_classes,   // always []
    hash_chain
);
``` [3](#0-2) 

2. `state_diff.len()` counts `deprecated_declared_classes.len()` toward `state_diff_length`, which is packed into `concatenated_counts`:

```rust
result += self.deprecated_declared_classes.len();   // always 0
``` [4](#0-3) 

Both the wrong `state_diff_commitment` and the wrong `concatenated_counts` are stored in `BlockHeaderCommitments`, which is embedded in `PartialBlockHashComponents`: [5](#0-4) 

`calculate_block_hash` then chains both into the final block hash:

```rust
.chain(&block_commitments.concatenated_counts)
.chain(&block_commitments.state_diff_commitment.0.0)
``` [6](#0-5) 

**The committer's verification does not catch the error:**

The committer re-derives the state diff hash from the same `ThinStateDiff` produced by `BlockExecutionArtifacts::thin_state_diff()`, which also has empty `deprecated_declared_classes`. Both sides of the comparison are equally wrong, so `StateDiffHashMismatch` is never raised: [7](#0-6) 

---

### Impact Explanation

For every block that contains at least one `DeclareV0`/`DeclareV1` transaction:

- `state_diff_commitment` stored in `BlockHeaderCommitments` is wrong (missing deprecated class entries in the Poseidon hash).
- `state_diff_length` packed into `concatenated_counts` is wrong (count is too low by the number of deprecated declarations).
- `PartialBlockHashComponents` carries both wrong values.
- `calculate_block_hash` produces a wrong `BlockHash` that is stored in storage and broadcast as the `ProposalCommitment` during consensus.

This is a **Critical** wrong-commitment result: accepted transactions produce an authoritative block hash that does not match the actual state diff, breaking the integrity guarantee of the block hash chain and any downstream proof or sync consumer that relies on it.

---

### Likelihood Explanation

`DeclareV0` and `DeclareV1` are standard Starknet transaction types that the blockifier supports (`DeclareTransactionV0V1`). Any unprivileged user who can submit such a transaction to the gateway can trigger the mismatch. No special privilege or coordination is required.

---

### Recommendation

Add `deprecated_declared_classes` to `CommitmentStateDiff` (and to `StateMaps`) so that the blockifier captures Cairo 0 class declarations during execution. Update `From<CommitmentStateDiff> for ThinStateDiff` to propagate the field instead of hardcoding `Vec::new()`. Remove the `TODO(AlonH)` comment once the fix is in place.

---

### Proof of Concept

1. Submit a `DeclareV1` transaction declaring a Cairo 0 class to the sequencer gateway.
2. The blockifier executes it; `StateMaps` records no deprecated class declaration; `CommitmentStateDiff` is built without it.
3. `BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff)` → `deprecated_declared_classes = []`.
4. `calculate_block_commitments` computes:
   - `state_diff_commitment` = Poseidon hash over a diff that omits the declared class → **wrong value**.
   - `concatenated_counts` encodes `state_diff_length` that is N-1 too small → **wrong value**.
5. `PartialBlockHashComponents` stores both wrong values; `calculate_block_hash` produces a **wrong `BlockHash`**.
6. The wrong hash is stored via `set_global_root_and_block_hash` and returned as the `ProposalCommitment` to consensus peers — every downstream consumer (proof verifier, state sync, RPC `starknet_getBlockWithTxHashes`) receives an incorrect authoritative block hash.

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L128-137)
```rust
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct BlockHeaderCommitments {
    pub transaction_commitment: TransactionCommitment,
    pub event_commitment: EventCommitment,
    pub receipt_commitment: ReceiptCommitment,
    pub state_diff_commitment: StateDiffCommitment,
    // TODO(Yoni): rename to packed_lengths to match Cairo's BlockHeaderCommitments (make sure it
    // doesn't break anything).
    pub concatenated_counts: Felt,
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L260-262)
```rust
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
```

**File:** crates/apollo_committer/src/committer.rs (L165-180)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(&state_diff);
                    if commitment != calculated_commitment {
                        return Err(CommitterError::StateDiffHashMismatch {
                            provided_commitment: commitment,
                            calculated_commitment,
                            height,
                        });
                    }
                }
                commitment
            }
            None => calculate_state_diff_hash(&state_diff),
        };
```
