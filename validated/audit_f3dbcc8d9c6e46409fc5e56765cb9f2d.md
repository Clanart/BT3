### Title
P2P Sync State Diff Written to Storage Without Hash Verification Against Header Commitment - (File: crates/apollo_p2p_sync/src/client/state_diff.rs)

### Summary
`parse_data_for_block` in the P2P sync client validates only the *length* of a received state diff against the header's `state_diff_length`, but never verifies the Poseidon hash against the header's `state_diff_commitment`. A malicious peer can send chunks with the correct total length but wrong content; the assembled `ThinStateDiff` is written to MDBX storage without any integrity check, corrupting the authoritative state that every downstream consumer (RPC, blockifier, committer) reads.

### Finding Description

In `crates/apollo_p2p_sync/src/client/state_diff.rs`, `parse_data_for_block` reads `state_diff_length` from the already-stored (sequencer-signed) block header and uses it as the sole acceptance criterion: [1](#0-0) 

After the length check passes and `validate_deprecated_declared_classes_non_conflicting` succeeds, the assembled `ThinStateDiff` is returned and immediately persisted: [2](#0-1) 

The block header already carries a `state_diff_commitment` (a Poseidon hash over the full diff, computed by `calculate_state_diff_hash`): [3](#0-2) 

This commitment is part of the sequencer-signed header and is the authoritative guard. `parse_data_for_block` never reads it and never calls `calculate_state_diff_hash` on the assembled result. The analog to the external report is exact: the "pause guard" (`state_diff_commitment` check) is present in the system but is simply not applied at the point where state is written, allowing the write to proceed unchecked.

The downstream path that *does* check the hash is the committer's `commit_or_load`, gated by the `verify_state_diff_hash` config flag: [4](#0-3) 

However,

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L26-39)
```rust
impl BlockData for (ThinStateDiff, BlockNumber) {
    #[latency_histogram("p2p_sync_state_diff_write_to_storage_latency_seconds", true)]
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-107)
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

**File:** crates/apollo_committer/src/committer.rs (L265-280)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(state_diff);
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
            None => calculate_state_diff_hash(state_diff),
        };
```
