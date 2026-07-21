### Title
Zero-Storage-Write in Valid Transaction Permanently Blocks Transaction Prover Proof Generation — (`File: crates/starknet_transaction_prover/src/running/committer_utils.rs`)

---

### Summary

`validate_virtual_os_state_diff` unconditionally rejects any storage update whose value is `Felt::ZERO`. Because `state_maps_to_committer_state_diff` passes the raw blockifier `StorageView` through `From<StorageView>` without filtering zero values, any valid Starknet transaction that writes `Felt::ZERO` to a storage slot (a normal "clear/reset" operation) causes the entire transaction-prover pipeline to abort with `ProofProviderError::InvalidStateDiff`. The prover cannot recover; the transaction can never be proven.

---

### Finding Description

**Root cause — `validate_virtual_os_state_diff`** [1](#0-0) 

The function iterates every `(address, key, value)` triple in the state diff and returns an error the moment any `value == Felt::ZERO`.

**No filtering in the conversion path**

`state_maps_to_committer_state_diff` feeds the blockifier's raw `StorageView` directly into the committer `StateDiff`: [2](#0-1) 

The `From<StorageView>` implementation inserts every `(key, value)` pair verbatim — zero values are **not** filtered: [3](#0-2) 

**Call site — production proving path**

`validate_virtual_os_state_diff` is called unconditionally inside `create_commitment_infos_with_state_changes`, which is the default path (`include_state_changes = true`) taken by `RpcStorageProofsProvider::get_storage_proofs`: [4](#0-3) [5](#0-4) 

**State diff origin — virtual block executor**

The `state_diff` fed into this path comes from `block_state.to_state_diff()?.state_maps` after executing the user's transactions: [6](#0-5) 

---

### Impact Explanation

Any unprivileged user can submit a valid `InvokeTransaction` whose Cairo contract writes `Felt::ZERO` to a storage slot (e.g., resetting a counter, clearing an allowance, zeroing a flag). The blockifier executes the transaction successfully and records the zero write in `state_diff.storage`. When the prover pipeline converts that diff and calls `validate_virtual_os_state_diff`, it immediately returns `Err(ProofProviderError::InvalidStateDiff(...))`. The `get_storage_proofs` call propagates the error upward through `create_virtual_os_hints` → `run_virtual_os` → `run_and_prove`, so no proof is ever produced for that transaction. The prover service returns an error to the caller for a transaction that was sequenced and executed correctly.

This matches the allowed impact: **High — RPC execution / transaction prover returns an authoritative-looking wrong value (an error) for a valid, accepted transaction.**

---

### Likelihood Explanation

Writing zero to storage is a routine Starknet operation (clearing an ERC-20 allowance after a full spend, resetting a nonce-like counter, zeroing a flag after use). Any contract that performs such an operation will trigger this path. No special privilege, no adversarial setup, and no coordination is required — a single ordinary `invoke` transaction suffices.

---

### Recommendation

Filter zero-valued storage entries **before** they reach `validate_virtual_os_state_diff`. The correct place is inside `state_maps_to_committer_state_diff`, mirroring the filtering already applied in the OS flow tests:

```rust
// In state_maps_to_committer_state_diff:
storage_updates: StorageDiff::from(StorageView(state_maps.storage))
    .into_iter()
    .map(|(address, updates)| {
        (
            address,
            updates
                .into_iter()
                .filter(|(_, value)| *value != Felt::ZERO)  // <-- add this
                .map(|(key, value)| (StarknetStorageKey(key), StarknetStorageValue(value)))
                .collect(),
        )
    })
    .filter(|(_, updates): &(_, IndexMap<_, _>)| !updates.is_empty()) // drop empty maps
    .collect(),
```

Alternatively, move the zero-value filtering into `validate_virtual_os_state_diff` itself, or document and enforce the invariant at the blockifier `to_state_diff()` boundary so that zero writes are stripped before they enter the prover pipeline.

---

### Proof of Concept

1. Deploy a Cairo 1 contract with a storage variable `val: felt252`.
2. In a first transaction, write `val = 1` (non-zero).
3. In a second transaction, write `val = 0` (clear the slot).
4. Submit the second transaction to the `VirtualSnosProver` via the RPC endpoint.
5. Internally, `VirtualBlockExecutor::execute` runs the transaction successfully; `to_state_diff()` returns `storage = {(contract_addr, val_key): Felt::ZERO}`.
6. `state_maps_to_committer_state_diff` wraps this as `StarknetStorageValue(Felt::ZERO)`.
7. `validate_virtual_os_state_diff` hits the check at line 75 and returns `Err(ProofProviderError::InvalidStateDiff("Storage deletion not allowed ..."))`.
8. `create_commitment_infos_with_state_changes` propagates the error; `get_storage_proofs` returns `Err`; `run_virtual_os` returns `Err`; the prover returns an error to the caller.
9. The transaction — which was validly sequenced and executed — can never be proven. [7](#0-6) [8](#0-7) [3](#0-2)

### Citations

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L37-61)
```rust
pub fn state_maps_to_committer_state_diff(state_maps: StateMaps) -> StateDiff {
    StateDiff {
        address_to_class_hash: state_maps.class_hashes,
        address_to_nonce: state_maps.nonces,
        class_hash_to_compiled_class_hash: state_maps
            .compiled_class_hashes
            .into_iter()
            .map(|(class_hash, compiled_class_hash)| {
                (class_hash, CompiledClassHash(compiled_class_hash.0))
            })
            .collect(),
        storage_updates: StorageDiff::from(StorageView(state_maps.storage))
            .into_iter()
            .map(|(address, updates)| {
                (
                    address,
                    updates
                        .into_iter()
                        .map(|(key, value)| (StarknetStorageKey(key), StarknetStorageValue(value)))
                        .collect(),
                )
            })
            .collect(),
    }
}
```

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L69-82)
```rust
pub(crate) fn validate_virtual_os_state_diff(
    state_diff: &StateDiff,
) -> Result<(), ProofProviderError> {
    // validate no storage deletions.
    for (address, storage_diffs) in &state_diff.storage_updates {
        for (key, value) in storage_diffs {
            if value.0 == Felt::ZERO {
                return Err(ProofProviderError::InvalidStateDiff(format!(
                    "Storage deletion not allowed: try to delete storage at address {address:?}, \
                     key {key:?}"
                )));
            }
        }
    }
```

**File:** crates/blockifier/src/state/cached_state.rs (L304-317)
```rust
impl From<StorageView> for IndexMap<ContractAddress, IndexMap<StorageKey, Felt>> {
    fn from(storage_view: StorageView) -> Self {
        let mut storage_updates = Self::new();
        for ((address, key), value) in storage_view.into_iter() {
            storage_updates
                .entry(address)
                .and_modify(|map| {
                    map.insert(key, value);
                })
                .or_insert_with(|| IndexMap::from([(key, value)]));
        }

        storage_updates
    }
```

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L330-333)
```rust
        // Convert the blockifier state maps to committer state diff and validate is stands with
        // the virtual OS assumptions.
        let committer_state_diff = state_maps_to_committer_state_diff(state_diff.clone());
        validate_virtual_os_state_diff(&committer_state_diff)?;
```

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L534-543)
```rust
        let commitment_infos = match config.include_state_changes {
            true => {
                Self::create_commitment_infos_with_state_changes(
                    &rpc_proof,
                    &query,
                    &extended_initial_reads,
                    &execution_data.state_diff,
                )
                .await?
            }
```

**File:** crates/starknet_transaction_prover/src/running/virtual_block_executor.rs (L298-306)
```rust
        // Get state diff (changes made by transactions).
        let state_diff = block_state
            .to_state_diff()
            .map_err(|e| {
                VirtualBlockExecutorError::TransactionExecutionError(format!(
                    "Failed to get state diff: {e}"
                ))
            })?
            .state_maps;
```
