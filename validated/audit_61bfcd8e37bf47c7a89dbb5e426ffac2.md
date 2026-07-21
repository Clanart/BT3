### Title
Strict Zero-Value Storage Check in `validate_virtual_os_state_diff` Blocks Proof Generation for Valid Transactions That Write Zero to Storage — (File: `crates/starknet_transaction_prover/src/running/committer_utils.rs`)

---

### Summary

`validate_virtual_os_state_diff` unconditionally rejects any execution state diff that contains a storage update whose value is `Felt::ZERO`. Because writing zero to a storage slot is a fully valid Starknet operation (the canonical way to reset/clear a storage variable), any valid `InvokeV3` transaction that performs such a write will be executed successfully by the blockifier but will cause the transaction prover's storage-proof and commitment-info pipeline to return a hard error, permanently preventing that transaction from being proven.

---

### Finding Description

In `crates/starknet_transaction_prover/src/running/committer_utils.rs`, `validate_virtual_os_state_diff` iterates over every `(address, key, value)` triple in the converted state diff and returns `ProofProviderError::InvalidStateDiff` the moment any `value.0 == Felt::ZERO` is encountered:

```rust
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
``` [1](#0-0) 

This function is called unconditionally inside `create_commitment_infos_with_state_changes` (the `include_state_changes = true` branch), immediately after converting the blockifier `StateMaps` to the committer `StateDiff`:

```rust
let committer_state_diff = state_maps_to_committer_state_diff(state_diff.clone());
validate_virtual_os_state_diff(&committer_state_diff)?;
``` [2](#0-1) 

The error propagates up through `get_storage_proofs` → `create_virtual_os_hints` → the runner, aborting proof generation entirely. [3](#0-2) 

The blockifier's `VirtualBlockExecutor::execute` collects the post-execution `state_diff` via `block_state.to_state_diff().state_maps`, which faithfully records every storage write including writes of zero: [4](#0-3) 

There is no filtering of zero values at the blockifier level; `state_maps_to_committer_state_diff` also preserves them verbatim: [5](#0-4) 

The design rationale for the restriction is documented in `add_dummy_nodes_for_orphan_hashes`: the virtual OS proof path inserts dummy Patricia nodes for sibling hashes that have no preimage, and this shortcut is only safe when no leaf is actually deleted (set to zero). However, the check is applied as a hard gate on the entire state diff rather than as a conditional bypass, so it blocks proving even when the zero write is to a slot that was already zero (a no-op deletion) or when the Patricia proof for that slot is fully available. [6](#0-5) 

---

### Impact Explanation

Any valid `InvokeV3` transaction whose execution writes `Felt::ZERO` to any storage slot — a routine operation for resetting counters, clearing ERC-20 allowances, zeroing flags, etc. — will:

1. Be accepted by the gateway and mempool without error.
2. Be executed successfully by the blockifier, producing a correct state diff and receipt.
3. Fail irrecoverably inside the transaction prover's `get_storage_proofs` call with `ProofProviderError::InvalidStateDiff`, preventing the generation of Patricia commitment infos and therefore preventing the OS hints and ZK proof from being produced.

The resulting proof commitment infos (`contract_state_commitment_info`, `address_to_storage_commitment_info`) are never populated, so the `VirtualOsBlockInput` is never assembled and the virtual OS run never starts. The transaction is silently un-provable despite being fully valid on-chain.

This matches the **High** impact category: the transaction prover returns an authoritative-looking error for a valid, accepted transaction, and the proof pipeline is blocked for an entire class of legitimate user operations.

---

### Likelihood Explanation

Writing zero to storage is a standard Starknet pattern. Any contract that:
- Clears an ERC-20 allowance (`approve(spender, 0)`)
- Resets a stored counter or flag to zero
- Deletes a mapping entry by writing its default value

will produce a zero-valued storage entry in the state diff. No special privileges or adversarial intent are required; any ordinary user submitting such a transaction triggers the issue.

---

### Recommendation

The zero-value guard exists because the dummy-node shortcut in `add_dummy_nodes_for_orphan_hashes` is only safe when no Patricia leaf is deleted. The fix should be scoped to that structural requirement rather than applied as a blanket rejection of all zero writes:

1. **Preferred**: Fetch full sibling preimages from the RPC proof for storage slots being set to zero, so the Patricia committer can traverse them correctly without dummy nodes. Remove the zero-value check from `validate_virtual_os_state_diff`.
2. **Interim**: Filter zero-valued storage updates out of the state diff passed to the committer (treating them as no-ops for the Patricia trie update) while still recording them in the OS execution output, and document this as a known limitation.
3. At minimum, document that the transaction prover cannot handle storage-deletion writes and surface this as a clear, actionable error to callers rather than an opaque `InvalidStateDiff`.

---

### Proof of Concept

```
1. Deploy any Cairo 1 contract with a storage variable, e.g.:
       #[storage]
       struct Storage { value: u128 }
       fn reset(ref self: ContractState) { self.value.write(0); }

2. First invoke: write a non-zero value (value = 42).
   → Blockifier executes OK; state diff: {slot → 42}.
   → Prover succeeds: validate_virtual_os_state_diff passes.

3. Second invoke: call reset() to write zero.
   → Blockifier executes OK; state diff: {slot → 0}.
   → Runner calls get_storage_proofs(include_state_changes=true).
   → create_commitment_infos_with_state_changes calls
       validate_virtual_os_state_diff(&committer_state_diff).
   → value.0 == Felt::ZERO  →  Err(ProofProviderError::InvalidStateDiff(
         "Storage deletion not allowed: try to delete storage at address …, key …"))
   → get_storage_proofs returns Err; create_virtual_os_hints returns Err.
   → Proof generation aborts. Transaction is permanently un-provable.
```

The root cause is at: [7](#0-6) 

called from: [8](#0-7)

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

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L69-100)
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
    // validate no contract deployments (or replaced classes).
    if !state_diff.address_to_class_hash.is_empty() {
        return Err(ProofProviderError::InvalidStateDiff(format!(
            "Contract deployments not allowed: try to deploy contracts(address to class hash): \
             {0:?}",
            state_diff.address_to_class_hash
        )));
    }
    // validate no contract declarations (or compiled class hash updates).
    if !state_diff.class_hash_to_compiled_class_hash.is_empty() {
        return Err(ProofProviderError::InvalidStateDiff(format!(
            "Contract declarations not allowed: try to declare classes(class hash to compiled \
             class hash): {0:?}",
            state_diff.class_hash_to_compiled_class_hash
        )));
    }
    Ok(())
}
```

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L246-251)
```rust
/// Adds dummy binary nodes for orphan child hashes that are referenced but have no preimage.
///
/// RPC storage proofs include sibling hashes for verification but don't provide their preimages.
/// The committer needs to traverse these nodes when deletions are allowed. Since we don't allow
/// deletions, we insert dummy binary nodes (with zero hashes) to satisfy the committer's
/// traversal requirements without requiring full preimages.
```

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L317-342)
```rust
    pub(crate) async fn create_commitment_infos_with_state_changes(
        rpc_proof: &RpcStorageProof,
        query: &RpcStorageProofsQuery,
        extended_initial_reads: &StateMaps,
        state_diff: &StateMaps,
    ) -> Result<StateCommitmentInfos, ProofProviderError> {
        // Build FactsDb from RPC proofs and execution initial reads.
        let mut facts_db =
            create_facts_db_from_storage_proof(rpc_proof, query, extended_initial_reads)?;

        // Get initial state roots from RPC proof.
        let contracts_trie_root_hash = HashOutput(rpc_proof.global_roots.contracts_tree_root);
        let classes_trie_root_hash = HashOutput(rpc_proof.global_roots.classes_tree_root);
        // Convert the blockifier state maps to committer state diff and validate is stands with
        // the virtual OS assumptions.
        let committer_state_diff = state_maps_to_committer_state_diff(state_diff.clone());
        validate_virtual_os_state_diff(&committer_state_diff)?;

        // Commit state diff using the committer.
        let new_roots = commit_state_diff(
            &mut facts_db,
            contracts_trie_root_hash,
            classes_trie_root_hash,
            committer_state_diff,
        )
        .await?;
```

**File:** crates/starknet_transaction_prover/src/running/runner.rs (L200-210)
```rust
        // Fetch classes and storage proofs in parallel.
        let (classes, storage_proofs) = tokio::join!(
            classes_provider.get_classes(&execution_data.executed_class_hashes),
            storage_proofs_provider.get_storage_proofs(
                block_number,
                &execution_data,
                storage_proof_config
            )
        );
        let classes = classes?;
        let storage_proofs = storage_proofs?;
```

**File:** crates/starknet_transaction_prover/src/running/virtual_block_executor.rs (L299-306)
```rust
        let state_diff = block_state
            .to_state_diff()
            .map_err(|e| {
                VirtualBlockExecutorError::TransactionExecutionError(format!(
                    "Failed to get state diff: {e}"
                ))
            })?
            .state_maps;
```
