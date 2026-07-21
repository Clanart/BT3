### Title
Deprecated Cairo 0 Class Declarations Silently Dropped from `CommitmentStateDiff` → Wrong State Diff Commitment and Block Hash - (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<StateMaps> for CommitmentStateDiff` conversion silently discards `declared_contracts` (Cairo 0 / deprecated class declarations). The downstream `From<CommitmentStateDiff> for ThinStateDiff` then hardcodes `deprecated_declared_classes: Vec::new()`. Every block produced by the sequencer therefore carries a `ThinStateDiff` with an empty `deprecated_declared_classes` list, regardless of how many Cairo 0 class-declaration transactions were executed. This causes the state diff commitment, the `state_diff_length` packed into `concatenated_counts`, and ultimately the block hash to be wrong whenever a deprecated class declaration is included in a block.

---

### Finding Description

**Root cause — `From<StateMaps> for CommitmentStateDiff` drops `declared_contracts`**

`StateMaps` tracks Cairo 0 class declarations in `declared_contracts: HashMap<ClassHash, bool>`. The conversion to `CommitmentStateDiff` ignores this field entirely:

```rust
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
            // diff.declared_contracts is silently dropped
        }
    }
}
``` [1](#0-0) 

**Propagation — `From<CommitmentStateDiff> for ThinStateDiff` hardcodes empty vec**

```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff.class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
}
``` [2](#0-1) 

**Both block-production call sites use this conversion**

`BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff.clone())` to feed `calculate_block_commitments`, and `thin_state_diff()` uses the same conversion to produce the `ThinStateDiff` committed to storage: [3](#0-2) [4](#0-3) 

**Corrupted commitment chain**

`calculate_state_diff_hash` chains `deprecated_declared_classes` into the Poseidon hash. With an empty vec it always chains `[count=0]`, even when Cairo 0 classes were declared: [5](#0-4) [6](#0-5) 

`ThinStateDiff::len()` also counts `deprecated_declared_classes.len()`, so `state_diff_length` (and therefore `concatenated_counts` packed into the block hash) is under-counted by exactly the number of Cairo 0 class declarations in the block: [7](#0-6) 

`concatenated_counts` is chained directly into `calculate_block_hash`: [8](#0-7) 

---

### Impact Explanation

For every block that contains at least one Cairo 0 (`DECLARE` v0/v1) class-declaration transaction:

1. **Wrong `state_diff_commitment`** — the Poseidon hash over the state diff omits the deprecated class hashes, producing a value that does not match the actual state change.
2. **Wrong `state_diff_length` / `concatenated_counts`** — the packed field in the block hash is under-counted, making the block hash itself wrong.
3. **Wrong block hash** — both the `state_diff_commitment` and `concatenated_counts` fields feed `calculate_block_hash`, so the final block hash is incorrect.
4. **Wrong proof inputs** — SNOS and the transaction prover receive a `state_diff_commitment` and block hash that do not correspond to the actual executed state, breaking proof soundness.

This matches the allowed impact scope: *"Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input"* and *"Incorrect fee, gas, bouncer, resource accounting…"* (via wrong `state_diff_length`).

---

### Likelihood Explanation

Cairo 0 class declarations are submitted via `DECLARE` v0/v1 transactions. The blockifier still executes them (it tracks them in `StateMaps.declared_contracts`). No explicit rejection of these transaction types was found in the gateway or mempool admission code in this repository. Any unprivileged user who submits a valid `DECLARE` v0/v1 transaction that passes validation can trigger this path. The sequencer silently produces a wrong commitment for every such block.

---

### Recommendation

1. Add `deprecated_declared_classes` to `CommitmentStateDiff` and populate it from `StateMaps.declared_contracts` in `From<StateMaps> for CommitmentStateDiff`.
2. Remove the `deprecated_declared_classes: Vec::new()` hardcoding in `From<CommitmentStateDiff> for ThinStateDiff` and propagate the field correctly.
3. If Cairo 0 class declarations are intentionally unsupported in the new sequencer, add an explicit rejection at the gateway/mempool admission layer and add an assertion in `From<StateMaps> for CommitmentStateDiff` that `declared_contracts` is empty.

---

### Proof of Concept

1. Submit a `DECLARE` v0 transaction declaring a Cairo 0 contract class `C` with class hash `H`.
2. The blockifier executes it; `StateMaps.declared_contracts` contains `{H: true}`.
3. `CommitmentStateDiff::from(state_maps)` drops `declared_contracts`; `CommitmentStateDiff.class_hash_to_compiled_class_hash` does not contain `H`.
4. `ThinStateDiff::from(commitment_state_diff)` sets `deprecated_declared_classes = []`.
5. `calculate_state_diff_hash(&thin_state_diff)` chains `[count=0]` for deprecated classes → commitment `X`.
6. The correct commitment (including `H`) would be `Y ≠ X`.
7. `ThinStateDiff::len()` returns `N` (not counting `H`); `concatenated_counts` encodes `state_diff_length = N`.
8. `calculate_block_hash` uses `X` and `N`, producing block hash `BH_wrong`.
9. The actual state (Patricia trie) includes `H` in the class trie, so the state root corresponds to a state that includes `H`, but the block hash and state diff commitment do not reflect this — the proof system receives inconsistent inputs.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L679-688)
```rust
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
        }
    }
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

**File:** crates/apollo_batcher/src/block_builder.rs (L198-201)
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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L71-80)
```rust
fn chain_deprecated_declared_classes(
    deprecated_declared_classes: &[ClassHash],
    hash_chain: HashChain,
) -> HashChain {
    let mut sorted_deprecated_declared_classes = deprecated_declared_classes.to_vec();
    sorted_deprecated_declared_classes.sort_unstable();
    hash_chain
        .chain(&sorted_deprecated_declared_classes.len().into())
        .chain_iter(sorted_deprecated_declared_classes.iter().map(|class_hash| &class_hash.0))
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-281)
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
```
