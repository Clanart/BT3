Now I have a complete picture. Let me write up the finding.

---

### Title
`deprecated_declared_classes` Permanently Zeroed in Proposer-Path `ThinStateDiff` Causes Wrong `state_diff_commitment`, `concatenated_counts`, and `PartialBlockHash` for Blocks Containing Deprecated Class Declarations — (`File: crates/blockifier/src/state/cached_state.rs`)

### Summary

`CommitmentStateDiff` has no field for deprecated (Cairo 0) declared classes. The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally hard-codes `deprecated_declared_classes: Vec::new()`. Every downstream commitment computed from this `ThinStateDiff` — `state_diff_commitment`, `state_diff_length` inside `concatenated_counts`, and therefore `PartialBlockHash` — is wrong for any block that contains a Declare V0/V1 transaction. The stored `ThinStateDiff` in the MDBX database is also missing those class hashes, so the `deprecated_declared_classes_block` index is never populated from the proposer path.

### Finding Description

**Root cause — `CommitmentStateDiff` has no `deprecated_declared_classes` field**

`CommitmentStateDiff` is the blockifier's canonical output of a block execution:

```rust
// crates/blockifier/src/state/cached_state.rs
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
    // ← no deprecated_declared_classes field
}
``` [1](#0-0) 

`StateMaps` (the raw write-set) does track deprecated declarations via `declared_contracts: HashMap<ClassHash, bool>`, but `From<StateMaps> for CommitmentStateDiff` silently drops it:

```rust
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: ...,
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
            // diff.declared_contracts is dropped here
        }
    }
}
``` [2](#0-1) 

**Propagation — `From<CommitmentStateDiff> for ThinStateDiff` hard-codes the empty vec**

```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            ...
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),   // ← always empty
        }
    }
}
``` [3](#0-2) 

**Commitment computation uses this zeroed `ThinStateDiff`**

`BlockExecutionArtifacts::new` converts `CommitmentStateDiff` to `ThinStateDiff` and passes it directly to `calculate_block_commitments`:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
).await;
``` [4](#0-3) 

Inside `calculate_block_commitments`, two values are computed from the zeroed `ThinStateDiff`:

1. `state_diff_commitment` — via `calculate_state_diff_hash`, which chains `deprecated_declared_classes` into the Poseidon hash: [5](#0-4) 

2. `concatenated_counts` — via `concat_counts(…, state_diff.len(), …)`, where `ThinStateDiff::len()` adds `self.deprecated_declared_classes.len()`: [6](#0-5) [7](#0-6) 

Both wrong values are embedded in `BlockHeaderCommitments`, which feeds `PartialBlockHashComponents` and the final `PartialBlockHash` used for consensus: [8](#0-7) [9](#0-8) 

**Storage path is also affected**

`thin_state_diff()` (called in `decision_reached`) uses the same conversion, so the `ThinStateDiff` written to MDBX via `append_state_diff` also has `deprecated_declared_classes: Vec::new()`. The `deprecated_declared_classes_block` index is never populated from the proposer path: [10](#0-9) [11](#0-10) [12](#0-11) 

**Trigger — Declare V0/V1 transactions are accepted**

The RPC layer accepts `BroadcastedDeclareTransaction::V1` (deprecated class declaration) and converts it to `ExecutableTransactionInput::DeclareV1`, which the blockifier executes and records in `StateMaps.declared_contracts`. Any such transaction included in a proposed block triggers the bug: [13](#0-12) 

### Impact Explanation

For every block that contains at least one Declare V0 or V1 transaction:

- **`state_diff_commitment`** in `BlockHeaderCommitments` is wrong: the Poseidon hash is computed as if `deprecated_declared_classes` is empty, so the commitment does not cover the actual declared class hashes.
- **`concatenated_counts`** (packed `state_diff_length`) is wrong: the length is under-counted by the number of deprecated declared classes.
- **`PartialBlockHash`** (the consensus commitment) is wrong: it is derived from the above two incorrect values.
- **Stored `ThinStateDiff`** in MDBX is wrong: `deprecated_declared_classes` is always `[]`, so the `deprecated_declared_classes_block` index is never written from the proposer path, breaking state-diff-based lookups and sync verification.

The proof system (SNOS) computes the state diff commitment from the actual on-chain state, which would include the deprecated declared classes, causing a mismatch with the commitment stored in the block header. This breaks proof verification for any block containing a deprecated class declaration.

This matches the impact scope: **Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input** (Critical) and **Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution** (Critical).

### Likelihood Explanation

Deprecated (Cairo 0) class declarations via Declare V1 transactions are still valid Starknet transactions. The RPC layer explicitly handles them. Any user who submits a Declare V1 transaction that gets included in a proposed block triggers the bug. No privileged access is required.

### Recommendation

Add `deprecated_declared_classes` to `CommitmentStateDiff` and populate it from `StateMaps.declared_contracts` (filtering for entries where the value is `true` and `compiled_class_hashes` does not contain the key, i.e., Cairo 0 classes). Propagate this field through `From<CommitmentStateDiff> for ThinStateDiff` instead of hard-coding `Vec::new()`.

### Proof of Concept

1. Submit a Declare V1 transaction (deprecated Cairo 0 class) to the sequencer gateway.
2. The transaction is accepted and included in a proposed block.
3. `BlockExecutionArtifacts::new` is called; `ThinStateDiff::from(commitment_state_diff)` produces a `ThinStateDiff` with `deprecated_declared_classes: []`.
4. `calculate_block_commitments` computes `state_diff_commitment` = `Poseidon("STARKNET_STATE_DIFF0", …, 0 /* deprecated count */, …)` — missing the declared class hash.
5. The correct commitment (as computed by SNOS from the actual state) = `Poseidon("STARKNET_STATE_DIFF0", …, 1, class_hash_X, …)`.
6. The two values differ; the block header carries the wrong `state_diff_commitment` and wrong `concatenated_counts`.
7. The `PartialBlockHash` used for consensus is also wrong, and the stored `ThinStateDiff` in MDBX has `deprecated_declared_classes: []` instead of `[class_hash_X]`.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L669-677)
```rust
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

**File:** crates/apollo_batcher/src/block_builder.rs (L168-169)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L197-206)
```rust
    pub fn from_partial_block_hash_components(
        partial_block_hash_components: &PartialBlockHashComponents,
    ) -> StarknetApiResult<Self> {
        let block_hash = calculate_block_hash(
            partial_block_hash_components,
            Self::GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH,
            Self::PARENT_HASH_FOR_PARTIAL_BLOCK_HASH,
        )?;
        Ok(Self(block_hash.0))
    }
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

**File:** crates/apollo_batcher/src/batcher.rs (L767-767)
```rust
        let state_diff = block_execution_artifacts.thin_state_diff();
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

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L482-508)
```rust
            BroadcastedDeclareTransaction::V1(BroadcastedDeclareV1Transaction {
                r#type: _,
                contract_class,
                sender_address,
                nonce,
                max_fee,
                signature,
            }) => {
                let sn_api_contract_class =
                    user_deprecated_contract_class_to_sn_api(contract_class)?;
                let abi_length = calculate_deprecated_class_abi_length(&sn_api_contract_class)
                    .map_err(internal_server_error)?;
                Ok(Self::DeclareV1(
                    starknet_api::transaction::DeclareTransactionV0V1 {
                        max_fee,
                        signature,
                        nonce,
                        // The blockifier doesn't need the class hash, but it uses the SN_API
                        // DeclareTransactionV0V1 which requires it.
                        class_hash: ClassHash::default(),
                        sender_address,
                    },
                    sn_api_contract_class,
                    abi_length,
                    // TODO(yair): pass the right value for only_query field.
                    false,
                ))
```
