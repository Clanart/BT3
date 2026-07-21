### Title
`CommitmentStateDiff`→`ThinStateDiff` Conversion Silently Drops `deprecated_declared_classes`, Producing Wrong `state_diff_commitment`, `state_diff_length`, and Block Hash - (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

`BlockExecutionArtifacts::new` computes the block's `state_diff_commitment` and `concatenated_counts` (which encodes `state_diff_length`) by converting `CommitmentStateDiff` to `ThinStateDiff`. That conversion unconditionally sets `deprecated_declared_classes: Vec::new()`. Any deprecated (Cairo 0) class declarations executed in the block are therefore absent from the commitment hash, from the `state_diff_length` packed into `concat_counts`, and from the `ThinStateDiff` written to storage. The resulting `PartialBlockHashComponents` — and every downstream artifact derived from it — is structurally wrong for any block that contains a deprecated-declare transaction.

---

### Finding Description

**Root cause — the dropped field**

`CommitmentStateDiff` has no `deprecated_declared_classes` field:

```rust
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
``` [1](#0-0) 

The `From<StateMaps> for CommitmentStateDiff` conversion silently discards `StateMaps::declared_contracts` (the field that records every class declaration, including deprecated ones): [2](#0-1) 

Consequently, `From<CommitmentStateDiff> for ThinStateDiff` hard-codes the field to an empty vector, with only a TODO comment acknowledging the omission:

```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            ...
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
}
``` [3](#0-2) 

**Propagation into the commitment pipeline**

`BlockExecutionArtifacts::new` feeds this truncated `ThinStateDiff` directly into `calculate_block_commitments`:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [4](#0-3) 

`calculate_block_commitments` passes the truncated diff to both `calculate_state_diff_hash` and `concat_counts`:

```rust
let concatenated_counts = concat_counts(
    transactions_data.len(),
    event_leaf_elements.len(),
    state_diff.len(),   // ThinStateDiff::len() counts deprecated_declared_classes.len()
    l1_da_mode,
);
...
let state_diff_task = spawn_measured_task(move || calculate_state_diff_hash(&state_diff));
``` [5](#0-4) 

`calculate_state_diff_hash` explicitly chains `deprecated_declared_classes` into the Poseidon hash:

```rust
hash_chain = chain_deprecated_declared_classes(
    &state_diff.deprecated_declared_classes, hash_chain
);
``` [6](#0-5) 

`ThinStateDiff::len()` counts `deprecated_declared_classes`:

```rust
result += self.deprecated_declared_classes.len();
``` [7](#0-6) 

Because the proposer's `ThinStateDiff` always has `deprecated_declared_classes = []`, both the `state_diff_commitment` and the `state_diff_length` packed into `concatenated_counts` are wrong for any block containing a deprecated-declare transaction.

**The same truncated diff is written to storage**

`thin_state_diff()` uses the same conversion:

```rust
pub fn thin_state_diff(&self) -> ThinStateDiff {
    ThinStateDiff::from(self.commitment_state_diff.clone())
}
``` [8](#0-7) 

`decision_reached` stores this diff and the wrong commitment: [9](#0-8) 

The wrong `state_diff_commitment` is also embedded in `PartialBlockHashComponents`, which feeds `calculate_block_hash`: [10](#0-9) 

**Confirming deprecated-declare transactions are reachable**

The RPC execution layer explicitly handles the case and manually reconstructs the missing field:

```rust
pub fn induced_state_diff(..., deprecated_declared_class_hash: Option<ClassHash>) -> ... {
    let blockifier_state_diff = CommitmentStateDiff::from(...);
    Ok(ThinStateDiff {
        ...
        deprecated_declared_classes: deprecated_declared_class_hash
            .map_or_else(Vec::new, |class_hash| vec![class_hash]),
        ...
    })
}
``` [11](#0-10) 

The `StarknetClientStateDiff` conversion also drops the field with an explicit TODO:

```rust
old_declared_contracts: Default::default(),
// TODO(Aviv): Verify that we ignore those fields in purpose.
``` [12](#0-11) 

---

### Impact Explanation

For any block in which a deprecated (Cairo 0) class is declared:

1. **Wrong `state_diff_commitment`** — `calculate_state_diff_hash` produces a hash over a diff that omits the deprecated class hashes. The value stored in the block header and used in `calculate_block_hash` is incorrect.
2. **Wrong `state_diff_length` / `concatenated_counts`** — `concat_counts` packs a length that is too small by the number of deprecated declared classes. This corrupts the `concatenated_counts` field chained into the block hash.
3. **Wrong block hash** — both corrupted fields feed `calculate_block_hash`, so the final block hash is wrong.
4. **Wrong `ThinStateDiff` in storage** — the diff written to MDBX is missing the deprecated class entries, so the Patricia trie commitment, P2P sync chunks, and SNOS proof inputs all operate on an incomplete state diff.
5. **Consensus / sync divergence** — validators receiving the block via P2P reconstruct the `ThinStateDiff` from `DeprecatedDeclaredClass` chunks (which do carry the class hashes), so their locally computed `state_diff_commitment` and `state_diff_length` will differ from the proposer's, causing commitment mismatch.

This matches the allowed impact: *"Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input"* and *"Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution."*

---

### Likelihood Explanation

Deprecated (Cairo 0) declare transactions are still part of the Starknet transaction type set and the blockifier executes them. The gateway and mempool do not explicitly reject them. The `induced_state_diff` test in `apollo_rpc_execution` demonstrates the full execution path. Any user who submits a valid deprecated-declare transaction that passes gateway validation will trigger the bug in the proposer.

---

### Recommendation

`CommitmentStateDiff` must be extended to carry deprecated declared classes, or the `From<CommitmentStateDiff> for ThinStateDiff` conversion must be replaced with a richer conversion that sources `deprecated_declared_classes` from `StateMaps::declared_contracts` (filtering for entries where the class hash is absent from `compiled_class_hashes`, i.e., Cairo 0 classes). The same fix must be applied to `BlockExecutionArtifacts::thin_state_diff()` and to `StarknetClientStateDiff::from(StateMaps)`.

---

### Proof of Concept

1. Submit a `DeclareTransaction` (deprecated/Cairo 0 class) through the gateway. The blockifier executes it and records `StateMaps::declared_contracts[class_hash] = true`.
2. `BlockExecutionSummary::state_diff` is built via `CommitmentStateDiff::from(state_maps)`, which drops `declared_contracts`.
3. `BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff)` → `deprecated_declared_classes = []`.
4. `calculate_block_commitments` computes `state_diff_commitment = Poseidon(..., chain_deprecated_declared_classes(&[], ...), ...)` — the class hash is absent.
5. `concat_counts` packs `state_diff_length = N` instead of `N + 1`.
6. `calculate_block_hash` chains the wrong `state_diff_commitment` and wrong `concatenated_counts`.
7. A validator that receives the block via P2P sync reconstructs `ThinStateDiff` with `deprecated_declared_classes = [class_hash]`, computes `state_diff_commitment'` ≠ `state_diff_commitment`, and rejects the block or stores a divergent state root.

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

**File:** crates/apollo_batcher/src/batcher.rs (L784-802)
```rust
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

**File:** crates/apollo_batcher/src/cende_client_types.rs (L635-641)
```rust
            old_declared_contracts: Default::default(),
            nonces: state_maps.nonces.into_iter().collect(),
            // TODO(Aviv): Verify that we ignore those fields in purpose.
            replaced_classes: Default::default(),
            migrated_compiled_classes: Default::default(),
        })
    }
```
