### Title
Deprecated (Cairo 0) Class Declarations Silently Dropped from `CommitmentStateDiff`, Producing Wrong State-Diff Commitment and Block Hash — (`crates/blockifier/src/state/cached_state.rs`)

---

### Summary

When the blockifier executes a Cairo 0 (`DeclareV0`/`V1`) class-declaration transaction, the declared class hash is recorded in `StateMaps::declared_contracts` but is **never transferred into `CommitmentStateDiff`**. The `From<CommitmentStateDiff> for ThinStateDiff` conversion then hard-codes `deprecated_declared_classes: Vec::new()`. Every downstream consumer — `calculate_state_diff_hash`, `ThinStateDiff::len`, `concatenated_counts`, and ultimately `calculate_block_hash` — therefore operates on a structurally incomplete state diff, producing a wrong `state_diff_commitment` and a wrong block hash for any block that contains a Cairo 0 declaration.

---

### Finding Description

**Root cause — `CommitmentStateDiff` has no `deprecated_declared_classes` field.**

`StateMaps` tracks Cairo 0 declarations as `declared_contracts: HashMap<ClassHash, bool>`. The conversion to `CommitmentStateDiff` silently discards this field:

```rust
// crates/blockifier/src/state/cached_state.rs  lines 723-731
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
            // diff.declared_contracts is dropped — no field for it
        }
    }
}
``` [1](#0-0) 

Cairo 1 declarations also write to `compiled_class_hashes`, so they survive into `class_hash_to_compiled_class_hash`. Cairo 0 declarations write **only** to `declared_contracts` and have no `compiled_class_hash` entry, so they vanish entirely.

**Propagation — `ThinStateDiff` always carries an empty list.**

```rust
// crates/blockifier/src/state/cached_state.rs  lines 756-767
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            ...
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
}
``` [2](#0-1) 

**Commitment construction — wrong hash and wrong length.**

`BlockExecutionArtifacts::new` calls `calculate_block_commitments` with `ThinStateDiff::from(commitment_state_diff.clone())`: [3](#0-2) 

`calculate_block_commitments` passes this `ThinStateDiff` to `calculate_state_diff_hash`, which chains `deprecated_declared_classes` into the Poseidon hash: [4](#0-3) 

It also calls `state_diff.len()`, which counts `deprecated_declared_classes.len()`: [5](#0-4) 

The resulting `state_diff_commitment` and `concatenated_counts` (which encodes `state_diff_length`) are both chained into the final block hash: [6](#0-5) 

**Trigger — any Cairo 0 declare transaction.**

The blockifier still handles `DeclareTransaction::V0` and `V1`: [7](#0-6) 

A `DeclareV1` transaction is accepted by the gateway client: [8](#0-7) 

---

### Impact Explanation

For every block that contains at least one Cairo 0 class declaration:

1. `calculate_state_diff_hash` hashes `deprecated_declared_classes = []` instead of the actual list → **wrong `state_diff_commitment`**.
2. `ThinStateDiff::len()` returns a count that is too small by the number of deprecated declarations → **wrong `concatenated_counts`** (the packed field encoding tx-count, event-count, state-diff-length, and DA mode).
3. Both wrong values are chained into `calculate_block_hash` → **wrong block hash**.
4. Any external prover, verifier, or sync peer that independently computes the state diff hash from the actual on-chain state diff will obtain a different value, causing proof verification failure or acceptance of a commitment that does not match the executed state.

This matches: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input* (wrong state-diff commitment for an accepted block) and *High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value* (RPC `starknet_getBlockWithTxHashes` returns a wrong block hash and wrong state-diff commitment).

---

### Likelihood Explanation

Cairo 0 (`DeclareV0`/`V1`) transactions are still accepted by the sequencer code path. Any user who submits a `DeclareV1` transaction triggers the bug. The sequencer will silently produce a wrong commitment for that block. No privileged access is required.

---

### Recommendation

Add `deprecated_declared_classes` to `CommitmentStateDiff` and populate it from `StateMaps::declared_contracts` (filtering for entries where the value is `true` and no corresponding `compiled_class_hashes` entry exists, i.e., Cairo 0 classes). Remove the `deprecated_declared_classes: Vec::new()` hard-coding in `From<CommitmentStateDiff> for ThinStateDiff` and propagate the field correctly.

---

### Proof of Concept

1. Submit a `DeclareV1` transaction declaring a Cairo 0 class with hash `H`.
2. The blockifier executes it; `StateMaps::declared_contracts` contains `{H: true}`; `StateMaps::compiled_class_hashes` is empty for `H`.
3. `From<StateMaps> for CommitmentStateDiff` drops `declared_contracts`; `CommitmentStateDiff::class_hash_to_compiled_class_hash` does not contain `H`.
4. `From<CommitmentStateDiff> for ThinStateDiff` sets `deprecated_declared_classes: Vec::new()`.
5. `calculate_state_diff_hash` chains `[0]` (count = 0) for deprecated classes instead of `[1, H]`.
6. `ThinStateDiff::len()` returns `N` instead of `N+1`.
7. `concatenated_counts` encodes the wrong `state_diff_length`.
8. `calculate_block_hash` produces a hash that differs from what any independent verifier (prover, sync peer) would compute from the actual state diff containing `H`.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L723-731)
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

**File:** crates/blockifier/src/transaction/transactions.rs (L163-175)
```rust
        match &self.tx {
            starknet_api::transaction::DeclareTransaction::V0(_)
            | starknet_api::transaction::DeclareTransaction::V1(_) => {
                if context.tx_context.block_context.versioned_constants.disable_cairo0_redeclaration
                {
                    try_declare(self, state, class_hash, None)?
                } else {
                    // We allow redeclaration of the class for backward compatibility.
                    // In the past, we allowed redeclaration of Cairo 0 contracts since there was
                    // no class commitment (so no need to check if the class is already declared).
                    state.set_contract_class(class_hash, self.contract_class().try_into()?)?;
                }
            }
```

**File:** crates/apollo_starknet_client/src/writer/objects/transaction.rs (L198-212)
```rust
/// A declare transaction of a Cairo-v0 (deprecated) contract class that can be added to Starknet
/// through the Starknet gateway.
/// It has a serialization format that the Starknet gateway accepts in the `add_transaction`
/// HTTP method.
#[derive(Debug, Default, Deserialize, Serialize, Clone, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DeclareV1Transaction {
    pub contract_class: DeprecatedContractClass,
    pub sender_address: ContractAddress,
    pub nonce: Nonce,
    pub max_fee: Fee,
    pub version: TransactionVersion,
    pub signature: TransactionSignature,
    pub r#type: DeclareType,
}
```
