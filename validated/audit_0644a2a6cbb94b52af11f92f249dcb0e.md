### Title
`CommitmentStateDiff`→`ThinStateDiff` Conversion Silently Drops `deprecated_declared_classes`, Causing Wrong State Diff Commitment and Block Hash - (`crates/blockifier/src/state/cached_state.rs`)

---

### Summary

`CommitmentStateDiff` has no `deprecated_declared_classes` field. The `From<CommitmentStateDiff> for ThinStateDiff` conversion hardcodes that field to `Vec::new()`. `BlockExecutionArtifacts::new()` feeds this incomplete `ThinStateDiff` into `calculate_block_commitments()`, which computes both the `state_diff_commitment` hash and the `state_diff_length` packed into `concat_counts`. Both values are wrong whenever a block contains a deprecated (Cairo 0) Declare transaction, producing a wrong block hash and a wrong stored state diff.

---

### Finding Description

**Two parallel representations, one missing a field.**

`CommitmentStateDiff` (blockifier) tracks four categories of state change:

```rust
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
```

`ThinStateDiff` (starknet_api) tracks five:

```rust
pub struct ThinStateDiff {
    pub deployed_contracts: IndexMap<ContractAddress, ClassHash>,
    pub storage_diffs: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
    pub deprecated_declared_classes: Vec<ClassHash>,   // ← absent from CommitmentStateDiff
    pub nonces: IndexMap<ContractAddress, Nonce>,
}
```

The conversion between them hardcodes the missing field:

```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff.class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),   // ← always empty
        }
    }
}
```

**How the wrong value propagates into the block hash.**

`BlockExecutionArtifacts::new()` calls this conversion immediately before computing the block commitment:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
).await;
```

Inside `calculate_block_commitments`, two values are derived from the incomplete diff:

1. **`state_diff_commitment`** — `calculate_state_diff_hash` chains `deprecated_declared_classes` into the Poseidon hash:
   ```rust
   hash_chain = chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
   ```
   With an empty vec, the hash omits all deprecated class entries.

2. **`state_diff_length`** — `ThinStateDiff::len()` counts `deprecated_declared_classes.len()`:
   ```rust
   result += self.deprecated_declared_classes.len();
   ```
   This count is packed into `concat_counts` (the single felt encoding tx count | event count | state diff length | DA mode), which is also chained into the block hash.

Both wrong values are stored in `partial_block_hash_components` and later used by `calculate_block_hash` to produce the final block hash.

**The stored `ThinStateDiff` is also wrong.**

`thin_state_diff()` uses the same conversion:

```rust
pub fn thin_state_diff(&self) -> ThinStateDiff {
    ThinStateDiff::from(self.commitment_state_diff.clone())  // deprecated_declared_classes = []
}
```

This is the value passed to `commit_proposal_and_block` and ultimately to `append_state_diff` in storage. The stored diff is missing deprecated declared classes, so every downstream consumer — RPC `starknet_getStateUpdate`, P2P sync state diff chunks, proof inputs — receives an incomplete diff.

**The upstream source is silently dropped.**

`StateMaps` does track deprecated declarations via `declared_contracts: HashMap<ClassHash, bool>`. The conversion `CommitmentStateDiff::from(StateMaps)` simply ignores that field entirely, so the information is lost before it can reach `ThinStateDiff`.

---

### Impact Explanation

For any block that includes a deprecated (Cairo 0) Declare transaction:

- **Wrong `state_diff_commitment`** embedded in the block hash — the Poseidon hash of the state diff omits the deprecated class entries.
- **Wrong `state_diff_length`** in `concat_counts` — the packed felt encoding the state diff size is smaller than the true value, corrupting the block hash.
- **Wrong `ThinStateDiff` in storage** — `deprecated_declared_classes` is empty, so `starknet_getStateUpdate` returns an incorrect state update, P2P sync propagates an incomplete diff to peers, and any proof system consuming the stored diff receives wrong input.

This matches: *Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input* (wrong state diff commitment and block hash for accepted deprecated Declare transactions) and *High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value* (wrong `starknet_getStateUpdate` response).

---

### Likelihood Explanation

Deprecated Declare transactions (v0 and v1) remain valid in the Starknet protocol. Any unprivileged user can submit one. The gateway accepts them, the blockifier executes them, and the batcher includes them in blocks. No special privilege or coordination is required to trigger the flaw.

---

### Recommendation

Add `deprecated_declared_classes: Vec<ClassHash>` to `CommitmentStateDiff` and populate it from `StateMaps.declared_contracts` (keys where value is `true`) in `CommitmentStateDiff::from(StateMaps)`. Update `From<CommitmentStateDiff> for ThinStateDiff` to propagate the field instead of hardcoding `Vec::new()`. Remove the `TODO(AlonH)` comment once the fix is in place.

---

### Proof of Concept

1. Submit a deprecated Declare transaction (v0 or v1) to the sequencer gateway.
2. Wait for the transaction to be included in a block.
3. Call `starknet_getStateUpdate` for that block — `deprecated_declared_classes` will be empty even though the transaction was accepted.
4. Independently compute `calculate_state_diff_hash` over the returned `ThinStateDiff` and compare it to the `state_diff_commitment` stored in the block header — they will match each other (both wrong) but will diverge from the correct hash computed by including the deprecated class entry.
5. Verify `concat_counts` in the block header encodes a `state_diff_length` that is `N` less than the true length, where `N` is the number of deprecated classes declared in the block.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_batcher/src/block_builder.rs (L159-166)
```rust
        // TODO(Ayelet): Remove the clones.
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

**File:** crates/starknet_api/src/state.rs (L109-121)
```rust
    /// This has the same value as `state_diff_length` in the corresponding `BlockHeader`.
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
