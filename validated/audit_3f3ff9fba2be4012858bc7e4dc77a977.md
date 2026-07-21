### Title
Transaction Prover Permanently Rejects Valid Invoke V3 Transactions That Write `Felt::ZERO` to Storage — (`crates/starknet_transaction_prover/src/running/committer_utils.rs`)

### Summary

`validate_virtual_os_state_diff` unconditionally rejects any execution state diff that contains a storage update whose value is `Felt::ZERO`. Because writing zero to a storage slot is a fully valid Starknet operation (e.g., clearing an ERC20 allowance, resetting a counter, setting a boolean flag to false), any Invoke V3 transaction that performs such a write can never be proven via `starknet_proveTransaction`. The prover returns an authoritative-looking `InvalidStateDiff` error for a transaction that the sequencer accepted, executed, and included in a finalized block.

### Finding Description

`validate_virtual_os_state_diff` in `crates/starknet_transaction_prover/src/running/committer_utils.rs` enforces three constraints on the execution state diff before committing it to the Patricia trie:

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

The check treats any storage write of `Felt::ZERO` as a "storage deletion" and returns an error. This is called unconditionally from `create_commitment_infos_with_state_changes`:

```rust
let committer_state_diff = state_maps_to_committer_state_diff(state_diff.clone());
validate_virtual_os_state_diff(&committer_state_diff)?;
``` [2](#0-1) 

`create_commitment_infos_with_state_changes` is invoked from `get_storage_proofs` whenever `config.include_state_changes == true`, which is the default:

```rust
impl Default for StorageProofConfig {
    fn default() -> Self {
        Self { include_state_changes: true }
    }
}
``` [3](#0-2) 

The full production call chain is:

`VirtualSnosProver::prove_transaction` → `run_and_prove` → `Runner::run_virtual_os` → `Runner::create_virtual_os_hints` → `RpcStorageProofsProvider::get_storage_proofs` → `create_commitment_infos_with_state_changes` → `validate_virtual_os_state_diff`. [4](#0-3) 

The analog to the "single borrower" pattern is exact:

| Original (LiquidationManager) | Sequencer analog |
|---|---|
| `while (trovesRemaining > 0 && troveCount > 1)` — loop body skipped when only 1 trove | `if value.0 == Felt::ZERO { return Err(...) }` — proving skipped when storage write is zero |
| `totals.totalDebtInSequence` stays 0 → revert "nothing to liquidate" | `validate_virtual_os_state_diff` returns `Err` → prover returns "Storage deletion not allowed" |
| Single borrower can never be liquidated | Transaction writing zero to storage can never be proven |

### Impact Explanation

Any user who submits an Invoke V3 transaction that writes `Felt::ZERO` to any storage slot — a routine operation for clearing ERC20 allowances, resetting counters, or toggling boolean flags — will receive a permanent `InvalidStateDiff` error from `starknet_proveTransaction`. The transaction is valid, accepted by the gateway, executed by the blockifier, and included in a finalized block, but the prover returns an authoritative-looking error claiming the state diff is invalid. This matches the **High** impact scope: "RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."

The `state_diff` fed into `validate_virtual_os_state_diff` comes directly from `block_state.to_state_diff().state_maps` after executing the user's transaction: [5](#0-4) 

There is no escape path: `StorageProofConfig::default()` always sets `include_state_changes: true`, so the validation is always reached for normal proving requests.

### Likelihood Explanation

Writing `Felt::ZERO` to storage is a common, everyday operation. Any ERC20 `approve(spender, 0)` call, any counter reset, or any flag clear produces a zero-valued storage update in the state diff. The condition is triggered by normal user activity with no special privileges required. The trigger is unprivileged and deterministic.

### Recommendation

Remove the blanket rejection of `Felt::ZERO` storage values from `validate_virtual_os_state_diff`. Writing zero to storage is a valid Starknet state transition; it is semantically equivalent to deleting the slot but is not prohibited by the protocol. If the virtual OS genuinely cannot handle storage deletions (i.e., the Patricia trie committer panics on zero-value leaves), the fix must be applied at the committer level — not by silently blocking valid transactions at the prover admission layer. The constraint should be documented with a reference to the specific OS limitation it guards, and a test should be added that proves a transaction writing `Felt::ZERO` to storage.

### Proof of Concept

1. Deploy a contract with a storage variable (e.g., an ERC20 token).
2. Submit an Invoke V3 transaction that calls `approve(spender, 0)` or any function that writes `0` to a storage slot.
3. The sequencer accepts and executes the transaction; it appears in a finalized block.
4. Call `starknet_proveTransaction` with the transaction's block and hash.
5. The prover executes the transaction via `VirtualBlockExecutor`, obtains a `state_diff` containing `{contract_address: {storage_key: Felt::ZERO}}`, converts it via `state_maps_to_committer_state_diff`, and calls `validate_virtual_os_state_diff`.
6. `validate_virtual_os_state_diff` returns `Err(ProofProviderError::InvalidStateDiff("Storage deletion not allowed: ..."))`.
7. `starknet_proveTransaction` returns an error for a transaction that is valid and finalized.

The exact corrupted value is the prover's response: instead of a valid `(proof, proof_facts, l2_to_l1_messages)` tuple, the RPC returns `InvalidStateDiff` — an authoritative-looking wrong result for a valid, sequenced transaction. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L63-100)
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

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L171-175)
```rust
impl Default for StorageProofConfig {
    fn default() -> Self {
        Self { include_state_changes: true }
    }
}
```

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L332-333)
```rust
        let committer_state_diff = state_maps_to_committer_state_diff(state_diff.clone());
        validate_virtual_os_state_diff(&committer_state_diff)?;
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
