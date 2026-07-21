### Title
`validate_virtual_os_state_diff` Unconditionally Rejects Zero-Value Storage Writes, Causing `starknet_proveTransaction` to Fail for Valid Invoke Transactions — (`crates/starknet_transaction_prover/src/running/committer_utils.rs`)

### Summary

The `validate_virtual_os_state_diff` function in the transaction prover's storage-proof pipeline treats any storage entry whose value is `Felt::ZERO` as an illegal "storage deletion" and returns `ProofProviderError::InvalidStateDiff`. Writing zero to a storage slot is a valid, accepted Starknet operation. The blockifier executes such transactions without error, but the `starknet_proveTransaction` RPC endpoint rejects them at the proof-generation stage, returning an authoritative-looking error for a transaction that is entirely valid.

### Finding Description

`validate_virtual_os_state_diff` enforces three hard constraints before the Patricia-trie committer runs:

1. No storage entry may have value `Felt::ZERO` ("storage deletion").
2. `address_to_class_hash` must be empty ("no contract deployments").
3. `class_hash_to_compiled_class_hash` must be empty ("no class declarations"). [1](#0-0) 

This function is called unconditionally inside `create_commitment_infos_with_state_changes`, which is the code path taken whenever `StorageProofConfig::include_state_changes` is `true` (the default). [2](#0-1) 

`create_commitment_infos_with_state_changes` is invoked from `RpcStorageProofsProvider::get_storage_proofs`, which is the storage-proof step of the `starknet_proveTransaction` RPC handler. [3](#0-2) 

The RPC endpoint itself is defined as `starknet_proveTransaction` in the `ProvingRpc` trait. [4](#0-3) 

**Why the constraint exists (and why it is still a bug):** The code comment in `add_dummy_nodes_for_orphan_hashes` explains that the RPC storage proof does not supply preimage data for sibling nodes, so the committer cannot traverse those nodes when a deletion is present. The workaround is to insert dummy binary nodes for orphan hashes — but this workaround is only safe when no deletions occur. The validation therefore gates the entire flow. [5](#0-4) 

The problem is that the Starknet protocol and the blockifier both permit writing `Felt::ZERO` to a storage slot (it is the canonical way to clear a storage variable). The virtual block executor executes such transactions successfully and records the zero-value write in `state_diff`. The state diff then flows into `state_maps_to_committer_state_diff` and immediately hits the zero-value guard, producing an error — even though the transaction itself is perfectly valid. [6](#0-5) 

The analog to the external report is direct: just as `BasicActions` lacked `allowSAFE()` so the proxy could never grant management rights (blocking all downstream operations), `validate_virtual_os_state_diff` lacks the ability to handle zero-value storage writes, so the entire proof-generation chain is blocked for any transaction that clears a storage variable.

### Impact Explanation

Any user who submits a valid Invoke V3 transaction to `starknet_proveTransaction` that writes `Felt::ZERO` to a storage slot receives a `ProofProviderError::InvalidStateDiff` error. The endpoint returns an authoritative-looking failure for a transaction the sequencer itself would accept and execute. This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

Clearing a storage variable (setting it to zero) is a routine pattern in Starknet contracts: resetting counters, removing mapping entries, revoking approvals, zeroing balances. Any contract that performs such an operation will produce a zero-value storage write in its state diff. The trigger requires no privilege — any user can call `starknet_proveTransaction` with such a transaction.

### Recommendation

One of the following mitigations should be applied:

1. **Fetch full sibling preimages:** Extend the RPC storage-proof query to include the preimage of every sibling node referenced in the proof, so the committer can traverse them during deletions. This removes the need for the zero-value guard entirely.
2. **Filter zero-value writes before committing:** Strip zero-value storage entries from the state diff before passing it to `commit_state_diff`. This is semantically correct for the Patricia trie (writing zero is equivalent to deleting the leaf), but requires the committer to be configured to handle absent leaves gracefully.
3. **Return a clear, documented error:** If the limitation is intentional for the current release, replace the opaque `InvalidStateDiff` error with a documented `UnsupportedOperation` variant and surface it clearly in the API documentation so callers are not misled.

### Proof of Concept

```
1. Deploy a contract that stores a non-zero value at storage key K.
2. Construct a valid Invoke V3 transaction that calls a function
   which writes Felt::ZERO to storage key K (e.g., `storage_write(K, 0)`).
3. Submit the transaction to `starknet_proveTransaction` targeting the
   block where the contract was deployed.
4. The virtual block executor (RpcVirtualBlockExecutor::execute) runs
   the transaction successfully; state_diff.storage contains (addr, K) → 0.
5. state_maps_to_committer_state_diff converts this to
   StateDiff { storage_updates: { addr: { K: StarknetStorageValue(Felt::ZERO) } } }.
6. validate_virtual_os_state_diff iterates storage_updates, finds value.0 == Felt::ZERO,
   and returns Err(ProofProviderError::InvalidStateDiff(
       "Storage deletion not allowed: try to delete storage at address ..., key ..."
   )).
7. get_storage_proofs propagates the error; starknet_proveTransaction
   returns an RPC error for a transaction the blockifier considers valid.
``` [7](#0-6) [8](#0-7)

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

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L246-281)
```rust
/// Adds dummy binary nodes for orphan child hashes that are referenced but have no preimage.
///
/// RPC storage proofs include sibling hashes for verification but don't provide their preimages.
/// The committer needs to traverse these nodes when deletions are allowed. Since we don't allow
/// deletions, we insert dummy binary nodes (with zero hashes) to satisfy the committer's
/// traversal requirements without requiring full preimages.
fn add_dummy_nodes_for_orphan_hashes(
    db_map: &mut DbHashMap,
    nodes: &IndexMap<Felt, MerkleNode, impl BuildHasher>,
) -> Result<(), ProofProviderError> {
    // Build set of hashes that have preimages in current proof batch.
    let has_preimage: HashSet<&Felt> = nodes.keys().collect();

    // Create dummy binary node value (both children point to zero hash).
    let dummy_hash = HashOutput(Felt::ZERO);
    let dummy_binary = FactDbFilledNode::<StarknetStorageValue>(FilledNode {
        hash: dummy_hash,
        data: NodeData::Binary(BinaryData { left_data: dummy_hash, right_data: dummy_hash }),
    });
    let dummy_value = dummy_binary.serialize()?;

    // Insert dummy nodes for orphan child hashes.
    for (_, node) in nodes {
        match node {
            MerkleNode::BinaryNode(bn) => {
                add_dummy_node_for_orphan_child(db_map, &bn.left, &has_preimage, &dummy_value);
                add_dummy_node_for_orphan_child(db_map, &bn.right, &has_preimage, &dummy_value);
            }
            MerkleNode::EdgeNode(en) => {
                add_dummy_node_for_orphan_child(db_map, &en.child, &has_preimage, &dummy_value);
            }
        }
    }

    Ok(())
}
```

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L317-343)
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

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L534-545)
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
            false => Self::create_commitment_infos_without_state_changes(&rpc_proof, &query)?,
        };
```

**File:** crates/starknet_transaction_prover/src/server/rpc_api.rs (L15-37)
```rust
#[rpc(server, namespace = "starknet")]
pub trait ProvingRpc {
    /// Returns the spec version (serves as lightweight health check).
    ///
    /// Returns "0.10.1" for Starknet RPC v0.10 compatibility.
    #[method(name = "specVersion")]
    async fn spec_version(&self) -> RpcResult<String>;

    /// Proves a transaction on top of the specified block.
    ///
    /// # Parameters
    /// - `block_id`: The block to execute the transaction on.
    /// - `transaction`: The transaction to prove (must be an Invoke transaction).
    ///
    /// # Returns
    /// The proof, proof facts, and L2-to-L1 messages.
    #[method(name = "proveTransaction")]
    async fn prove_transaction(
        &self,
        block_id: BlockId,
        transaction: RpcTransaction,
    ) -> RpcResult<ProveTransactionResult>;
}
```
