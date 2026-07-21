### Title
Missing State Diff Commitment Hash Verification Before Storage in P2P Sync and Central Sync - (`crates/apollo_p2p_sync/src/client/state_diff.rs`, `crates/apollo_central_sync/src/lib.rs`)

### Summary
The sequencer's state diff ingestion paths — both P2P sync and central (feeder gateway) sync — store incoming state diffs without verifying their Poseidon hash against the `state_diff_commitment` already recorded in the stored block header. This is the direct analog to M-37: just as GMX allowed swap/ADL operations to proceed without checking whether the market was enabled, the sequencer allows state diffs to be committed to storage without checking whether their content matches the authoritative commitment already anchored in the block header.

### Finding Description

**P2P sync path** (`crates/apollo_p2p_sync/src/client/state_diff.rs`):

`parse_data_for_block` reads `state_diff_length` from the stored header and uses it as the only acceptance criterion for the incoming state diff chunks. [1](#0-0) 

After accumulating all chunks it calls only `validate_deprecated_declared_classes_non_conflicting`, which checks for duplicate deprecated class hashes. [2](#0-1) 

The stored block header carries a `state_diff_commitment` field (a Poseidon hash of the canonical state diff). [3](#0-2)  That commitment is **never read** during state diff parsing; `parse_data_for_block` only reads `state_diff_length`. A malicious peer can therefore send a state diff whose length matches the header but whose content is entirely fabricated. The assembled `ThinStateDiff` is then written directly to storage: [4](#0-3) 

Additionally, the P2P header parser only checks that the signature array has the expected *length*; it does not cryptographically verify the sequencer's signature. [5](#0-4)  This means a malicious peer can also supply a fabricated header with an arbitrary `state_diff_commitment` and a matching fabricated state diff, bypassing even the length guard.

**Central sync path** (`crates/apollo_central_sync/src/lib.rs`):

`store_state_diff` contains an explicit unimplemented TODO acknowledging the same missing check: [6](#0-5) 

The function proceeds to call `append_state_diff` without ever comparing `calculate_state_diff_hash(&thin_state_diff)` against the `state_diff_commitment` stored in the block header. [7](#0-6) 

The committer does have an optional `verify_state_diff_hash` flag, but it is opt-in and applies only to the batcher's commitment pipeline, not to the sync ingestion path. [8](#0-7) 

### Impact Explanation

A wrong `ThinStateDiff` written to storage propagates through every downstream consumer:

1. **State root / global root**: The Patricia trie committer derives the global root from the state diff. A corrupted diff produces a wrong global root, which is then stored as `ForestMetadataType::StateRoot` and returned to the batcher. [9](#0-8) 

2. **Block hash**: `calculate_block_hash` chains `state_root` and `state_diff_commitment` into the block hash. Both values are wrong, so the block hash is wrong. [10](#0-9) 

3. **RPC responses**: `get_storage_at`, `get_nonce_at`, `get_class_hash_at`, and `starknet_getStateUpdate` all read from the corrupted storage, returning authoritative-looking wrong values. [11](#0-10) 

4. **Proof inputs / SNOS**: `create_commitment_infos` feeds `previous_state_roots` and `new_state_roots` derived from the corrupted trie into the OS hint processor, producing wrong Patricia proof facts. [12](#0-11) 

5. **Transaction prover storage proofs**: `RpcStorageProofsProvider::get_storage_proofs` uses the wrong global root as the anchor for all Merkle proofs fed to the virtual SNOS prover. [13](#0-12) 

### Likelihood Explanation

- **P2P sync**: Any node that can establish a P2P connection is an unprivileged trigger. No cryptographic barrier prevents a peer from sending a length-correct but content-wrong state diff. The header signature is checked only for array length, not validity.
- **Central sync**: Requires a compromised or buggy feeder gateway (privileged), but the TODO comment confirms the check is intentionally deferred, not intentionally omitted.

### Recommendation

In `parse_data_for_block` (P2P sync), after assembling the full `ThinStateDiff`, read `state_diff_commitment` from the stored header and compare it against `calculate_state_diff_hash(&result)`. Reject the peer if they differ.

```rust
let stored_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("header must exist")
    .state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage { ... })?;

let computed = calculate_state_diff_hash(&result);
if computed != stored_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffCommitmentMismatch { ... }));
}
```

Apply the same check in `store_state_diff` in central sync to resolve the existing TODO. [6](#0-5) 

Also verify the sequencer signature on headers in the P2P path, not just the signature array length. [5](#0-4) 

### Proof of Concept

1. A malicious peer connects to the node's P2P port.
2. It sends a `SignedBlockHeader` for block N with `state_diff_length = 1` and any `state_diff_commitment` value (signature array length is correct so it passes the only check).
3. The header is stored. The state diff stream opens.
4. The peer sends one `StateDiffChunk::DeprecatedDeclaredClass` entry (length = 1, matching `state_diff_length`). The chunk contains a fabricated class hash.
5. `parse_data_for_block` returns `Ok(Some((fabricated_diff, block_N)))` — no commitment hash check occurs. [14](#0-13) 
6. `write_to_storage` calls `append_state_diff(block_N, fabricated_diff)`, persisting the wrong state. [4](#0-3) 
7. Subsequent RPC calls to `starknet_getStateUpdate` for block N return the fabricated state diff as authoritative. The global root derived from this diff is wrong, corrupting all downstream proof inputs and block hash computations.

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L33-35)
```rust
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-70)
```rust
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L99-107)
```rust
            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
            }

            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L115-119)
```rust
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L146-146)
```rust
                state_diff_commitment: Some(header_commitments.state_diff_commitment),
```

**File:** crates/apollo_central_sync/src/lib.rs (L442-442)
```rust
        // TODO(dan): verifications - verify state diff against stored header.
```

**File:** crates/apollo_central_sync/src/lib.rs (L548-549)
```rust
            let mut txn = writer.begin_rw_txn()?;
            txn = txn.append_state_diff(block_number, thin_state_diff)?;
```

**File:** crates/apollo_committer/src/committer.rs (L165-179)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(&state_diff);
                    if commitment != calculated_commitment {
                        return Err(CommitterError::StateDiffHashMismatch {
                            provided_commitment: commitment,
                            calculated_commitment,
                            height,
                        });
                    }
                }
                commitment
            }
            None => calculate_state_diff_hash(&state_diff),
```

**File:** crates/apollo_committer/src/committer.rs (L207-217)
```rust
        let (filled_forest, global_root) =
            self.commit_state_diff(state_diff, &mut block_measurements).await?;
        let next_offset = height.unchecked_next();
        let metadata = HashMap::from([
            (
                ForestMetadataType::CommitmentOffset,
                DbValue(DbBlockNumber(next_offset).serialize().to_vec()),
            ),
            (
                ForestMetadataType::StateRoot(DbBlockNumber(height)),
                serialize_felt_no_packing(global_root.0),
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L34-38)
```text
        hash_update_single(state_root);
        hash_update_single(block_info.sequencer_address);
        hash_update_single(block_info.block_timestamp);
        hash_update_single(header_commitments.packed_lengths);
        hash_update_single(header_commitments.state_diff_commitment);
```

**File:** crates/apollo_state_sync/src/lib.rs (L218-236)
```rust
    async fn get_storage_at(
        &self,
        block_number: BlockNumber,
        contract_address: ContractAddress,
        storage_key: StorageKey,
    ) -> StateSyncResult<Felt> {
        let storage_reader = self.storage_reader.clone();

        let txn = storage_reader.begin_ro_txn()?;
        verify_synced_up_to(&txn, block_number)?;

        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let state_reader = txn.get_state_reader()?;

        verify_contract_deployed(&state_reader, state_number, contract_address)?;

        let res = state_reader.get_storage_at(state_number, &contract_address, &storage_key)?;

        Ok(res)
```

**File:** crates/starknet_os/src/commitment_infos.rs (L70-82)
```rust
pub async fn create_commitment_infos(
    previous_state_roots: &StateRoots,
    new_state_roots: &StateRoots,
    commitments: &mut MapStorage,
    initial_reads_keys: &StateChangesKeys,
) -> Result<StateCommitmentInfos, CommitmentInfosError> {
    let (previous_contract_states, new_storage_roots) = get_previous_states_and_new_storage_roots(
        initial_reads_keys.modified_contracts.iter().copied(),
        previous_state_roots.contracts_trie_root_hash,
        new_state_roots.contracts_trie_root_hash,
        commitments,
    )
    .await?;
```

**File:** crates/starknet_transaction_prover/src/running/storage_proofs.rs (L327-329)
```rust
        // Get initial state roots from RPC proof.
        let contracts_trie_root_hash = HashOutput(rpc_proof.global_roots.contracts_tree_root);
        let classes_trie_root_hash = HashOutput(rpc_proof.global_roots.classes_tree_root);
```
