### Title
P2P State Diff Sync Validates Length But Not Commitment Hash, Allowing Wrong State Diff Acceptance - (File: crates/apollo_p2p_sync/src/client/state_diff.rs)

### Summary

The `parse_data_for_block` function in the P2P sync client validates the assembled `ThinStateDiff` only against the `state_diff_length` integer from the stored block header, but never validates it against the `state_diff_commitment` Poseidon hash that is also present in the same header. A peer can send state diff chunks whose total entry count matches the header's `state_diff_length` but whose content (storage values, nonces, class hashes) is entirely different, causing the syncing node to store a wrong state diff, compute a wrong global root, and serve wrong authoritative state via RPC.

### Finding Description

In `crates/apollo_p2p_sync/src/client/state_diff.rs`, `parse_data_for_block` reads `target_state_diff_len` from the stored block header and collects `StateDiffChunk` messages from the peer until `current_state_diff_len == target_state_diff_len`:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("A header with number lower than the header marker is missing")
    .state_diff_length          // ← only this field is read
    ...

while current_state_diff_len < target_state_diff_len {
    ...
    current_state_diff_len += state_diff_chunk.len();
    unite_state_diffs(&mut result, state_diff_chunk)?;
}
...
validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))   // ← returned without commitment check
``` [1](#0-0) 

The same `get_block_header` call returns a `StorageBlockHeader` that contains both `state_diff_length: Option<usize>` **and** `state_diff_commitment: Option<StateDiffCommitment>`: [2](#0-1) 

`state_diff_commitment` is a Poseidon hash computed by `calculate_state_diff_hash` over the full content of the `ThinStateDiff` — deployed contracts, declared classes, deprecated declared classes, storage diffs, and nonces: [3](#0-2) 

The `state_diff_length` field is only a count of entries (`ThinStateDiff::len()`): [4](#0-3) 

Because only the count is checked, a peer can craft chunks whose individual `len()` values sum to `target_state_diff_len` but whose actual field values (storage slot values, nonces, class hashes) differ from the canonical state diff. The assembled `ThinStateDiff` passes all checks and is written to storage via `append_state_diff` without any cryptographic verification.

The analog to the OFT report is exact: OFT validates `amountReceivedLD` (destination count) but not `amountSentLD` (origin amount); here the sync client validates the entry count (`state_diff_length`) but not the cryptographic commitment (`state_diff_commitment`) — the "sent" side of the state diff transfer.

### Impact Explanation

The wrong `ThinStateDiff` is stored by `write_to_storage`: [5](#0-4) 

The committer subsequently reads this stored state diff and applies it to the Patricia trie to compute the global root: [6](#0-5) 

The resulting wrong global root is stored as authoritative and feeds into `calculate_block_hash`. All RPC state reads (`starknet_getStorageAt`, `starknet_getNonce`, `starknet_getClassHashAt`) return wrong values derived from the corrupted state. This matches the **High** impact scope: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value*, and potentially **Critical**: *Wrong state, storage value from blockifier/syscall/execution logic for accepted input* when the corrupted state is used for subsequent block execution.

### Likelihood Explanation

Any node that connects to the syncing node as a P2P peer can trigger this. In a permissionless Starknet P2P network, connecting as a peer requires no privilege. The attack requires only that the malicious peer responds to a state diff query with chunks whose lengths sum correctly but whose content is altered. The header's `state_diff_commitment` is already stored and available at the time of validation — the check is simply absent.

### Recommendation

After assembling the final `ThinStateDiff` and before returning it, compute `calculate_state_diff_hash(&result)` and compare it against the `state_diff_commitment` from the block header. If the header's `state_diff_commitment` is `None` (old blocks), skip the check. If it is `Some(expected)` and the computed hash differs, return `Err(ParseDataError::BadPeer(...))`.

```rust
// After validate_deprecated_declared_classes_non_conflicting(&result)?;
if let Some(expected_commitment) = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .and_then(|h| h.state_diff_commitment)
{
    let computed = calculate_state_diff_hash(&result);
    if computed != expected_commitment {
        return Err(ParseDataError::BadPeer(
            BadPeerError::WrongStateDiffCommitment { ... }
        ));
    }
}
```

### Proof of Concept

1. Syncing node issues a `StateDiff` query for block N to a malicious peer.
2. The syncing node already has the block header stored with `state_diff_length = 3` and `state_diff_commitment = H_canonical`.
3. The malicious peer sends three `StateDiffChunk::ContractDiff` chunks, each with `len() = 1`, totalling 3 — matching `target_state_diff_len`.
4. The chunks contain a storage update `(address_A, key_K) → value_WRONG` instead of the canonical `value_CORRECT`.
5. `parse_data_for_block` accepts the diff: length check passes, `validate_deprecated_declared_classes_non_conflicting` passes (no deprecated classes involved).
6. `write_to_storage` calls `append_state_diff(block_number, wrong_diff)`.
7. The committer applies `wrong_diff` to the Patricia trie, producing `global_root_WRONG ≠ global_root_CANONICAL`.
8. `starknet_getStorageAt(address_A, key_K, block_N)` returns `value_WRONG`.
9. Any transaction executing against block N's state reads `value_WRONG` from storage, producing wrong execution results. [7](#0-6) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L28-39)
```rust
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
            Ok(())
        }
        .boxed()
    }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L58-110)
```rust
        async move {
            let mut result = ThinStateDiff::default();
            let mut prev_result_len = 0;
            let mut current_state_diff_len = 0;
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;

            while current_state_diff_len < target_state_diff_len {
                let maybe_state_diff_chunk = state_diff_chunks_response_manager
                    .next()
                    .await
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
                let Some(state_diff_chunk) = maybe_state_diff_chunk?.0 else {
                    if current_state_diff_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                            expected_length: target_state_diff_len,
                            possible_lengths: vec![current_state_diff_len],
                        }));
                    }
                };
                prev_result_len = current_state_diff_len;
                if state_diff_chunk.is_empty() {
                    return Err(ParseDataError::BadPeer(BadPeerError::EmptyStateDiffPart));
                }
                // It's cheaper to calculate the length of `state_diff_part` than the length of
                // `result`.
                current_state_diff_len += state_diff_chunk.len();
                unite_state_diffs(&mut result, state_diff_chunk)?;
            }

            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
            }

            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
        }
        .boxed()
    }
```

**File:** crates/apollo_storage/src/header.rs (L98-107)
```rust
    /// The state diff commitment, if available.
    pub state_diff_commitment: Option<StateDiffCommitment>,
    /// The transaction commitment, if available.
    pub transaction_commitment: Option<TransactionCommitment>,
    /// The event commitment, if available.
    pub event_commitment: Option<EventCommitment>,
    /// The receipt commitment, if available.
    pub receipt_commitment: Option<ReceiptCommitment>,
    /// The length of the state diff, if available.
    pub state_diff_length: Option<usize>,
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L30-41)
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

**File:** crates/apollo_committer/src/committer.rs (L207-222)
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
            ),
            (
                ForestMetadataType::StateDiffHash(DbBlockNumber(height)),
                serialize_felt_no_packing(state_diff_commitment.0.0),
            ),
```
