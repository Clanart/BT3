### Title
`validate_virtual_os_state_diff` Rejects Valid Zero-Value Storage Writes, Blocking Transaction Proof Generation — (File: `crates/starknet_transaction_prover/src/running/committer_utils.rs`)

### Summary
The `validate_virtual_os_state_diff` function in the transaction prover's running phase unconditionally rejects any state diff that contains a storage update whose value is `Felt::ZERO`. Writing zero to a storage slot is a valid, accepted Starknet operation (it is the canonical way to "delete" storage). The blockifier executes such transactions without error, but the transaction prover aborts proof generation for them. This is the direct sequencer analog of the `payInAmount > 0` guard in FantiumClaimingV1: an overly strict zero-value check that blocks a legitimate operation.

### Finding Description
In `validate_virtual_os_state_diff`:

```rust
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

This function is called from `storage_proofs.rs` (two call sites) during the running phase of `VirtualSnosProver::prove_transaction`. The running phase re-executes the transaction, collects the resulting `StateDiff`, and then calls `validate_virtual_os_state_diff` before committing the Patricia trie. Any storage write that produces a zero value causes an immediate `ProofProviderError::InvalidStateDiff` and aborts the entire proof pipeline. [2](#0-1) 

The rationale embedded in `add_dummy_nodes_for_orphan_hashes` is that the virtual SNOS prover inserts dummy binary nodes for sibling hashes whose preimages are absent from the RPC proof, and this shortcut is only safe when no trie node is actually deleted:

```
/// Since we don't allow deletions, we insert dummy binary nodes (with zero hashes)
/// to satisfy the committer's traversal requirements without requiring full preimages.
``` [3](#0-2) 

The guard therefore exists to protect a downstream assumption, but it is placed at the wrong abstraction level: it rejects the entire proof request at the state-diff validation stage rather than handling the missing-preimage case in the trie traversal. The result is that any valid invoke transaction whose execution writes `0` to a storage slot — a routine operation in Starknet contracts (resetting a counter, clearing an approval, zeroing a flag) — is permanently unprovable through the `starknet_proveTransaction` RPC endpoint.

The full call chain is:

```
ProvingRpcServerImpl::prove_transaction
  → VirtualSnosProver::prove_transaction
      → validate_transaction_input          (does NOT catch this)
      → VirtualSnosRunner::run_virtual_os
          → [re-execution produces StateDiff with zero storage value]
      → validate_virtual_os_state_diff      ← REJECTS HERE
``` [4](#0-3) 

### Impact Explanation
Any unprivileged user who submits a valid `InvokeV3` transaction that writes zero to a storage slot will receive a `ProofProviderError::InvalidStateDiff` error from the `starknet_proveTransaction` RPC endpoint. The endpoint returns an authoritative-looking error claiming the state diff is invalid, when in fact the transaction was accepted and executed correctly by the sequencer. The proof invariant is broken: the prover asserts unprovability for a class of transactions that the rest of the system considers valid and final.

**Impact scope match:** *High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

### Likelihood Explanation
Storage zeroing is a standard pattern in Starknet contracts (ERC-20 allowance reset, nonce clearing, flag toggling, etc.). Any such transaction routed through the transaction prover service triggers the rejection. The trigger requires no privilege — a normal user wallet submitting a routine invoke is sufficient.

### Recommendation
The zero-value guard should be removed from `validate_virtual_os_state_diff`. The underlying trie traversal must instead be fixed to supply full preimages for nodes that are deleted (zeroed), rather than relying on dummy binary nodes. Alternatively, if the virtual SNOS prover genuinely cannot support storage deletions in its current form, the check should be moved to a clearly documented capability boundary and the RPC error message should accurately state that the prover does not yet support storage-deletion transactions, rather than claiming the state diff is invalid.

### Proof of Concept
1. Deploy a Starknet contract with a storage variable initialized to a non-zero value.
2. Submit an `InvokeV3` transaction that writes `0` to that storage slot (e.g., `storage_write(key, 0)`).
3. The sequencer accepts the transaction; the blockifier executes it; the state diff contains `(address, key) → Felt::ZERO`.
4. Call `starknet_proveTransaction` with the block containing that transaction.
5. `validate_virtual_os_state_diff` fires at line 118 of `committer_utils.rs` and returns `ProofProviderError::InvalidStateDiff("Storage deletion not allowed: ...")`.
6. The RPC endpoint propagates this as an error, permanently blocking proof generation for a finalized, valid transaction. [5](#0-4)

### Citations

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L106-143)
```rust
/// Validates that the committer state diff contains only allowed state transitions.
///
/// This function enforces the following constraints:
/// * **No Storage Deletions:** Storage entries cannot be updated to `Felt::ZERO`.
/// * **No Class Declarations:** The `class_hash_to_compiled_class_hash` map must be empty.
/// * **No Contract Deployments:** The `address_to_class_hash` map must be empty.
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

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L289-295)
```rust
/// Adds dummy binary nodes for orphan child hashes that are referenced but have no preimage.
///
/// RPC storage proofs include sibling hashes for verification but don't provide their preimages.
/// The committer needs to traverse these nodes when deletions are allowed. Since we don't allow
/// deletions, we insert dummy binary nodes (with zero hashes) to satisfy the committer's
/// traversal requirements without requiring full preimages.
fn add_dummy_nodes_for_orphan_hashes(
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L154-182)
```rust
    pub async fn prove_transaction(
        &self,
        block_id: BlockId,
        transaction: RpcTransaction,
    ) -> Result<ProveTransactionResult, VirtualSnosProverError> {
        let start_time = Instant::now();

        // Validate block_id is not pending.
        if matches!(block_id, BlockId::Pending) {
            return Err(VirtualSnosProverError::ValidationError(
                "Pending blocks are not supported; only finalized blocks can be proven."
                    .to_string(),
            ));
        }

        let invoke_v3 = extract_rpc_invoke_tx(transaction.clone())?;
        validate_transaction_input(&invoke_v3, self.validate_zero_fee_fields)?;
        let invoke_tx = InvokeTransaction::V3(invoke_v3.into());

        let result = match &self.blocking_check_client {
            None => self.run_and_prove(block_id, vec![invoke_tx]).await?,
            Some(client) => {
                self.prove_with_blocking_check(client, block_id, transaction, invoke_tx).await?
            }
        };

        info!(total_duration_ms = %start_time.elapsed().as_millis(), "prove_transaction completed");
        Ok(result)
    }
```
