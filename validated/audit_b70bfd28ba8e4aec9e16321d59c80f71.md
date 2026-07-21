### Title
P2P Sync `HeaderStreamBuilder::parse_data_for_block` Accepts `SignedBlockHeader` Without Verifying Cryptographic Signature Against Sequencer Public Key, Enabling Malicious Peer to Inject Wrong State Root and Block Commitments — (`crates/apollo_p2p_sync/src/client/header.rs`)

---

### Summary

The P2P sync client accepts `SignedBlockHeader` messages from network peers and writes them directly to storage after checking only that the block number matches and that the signatures vector has exactly one element. The cryptographic signature is never verified against the known sequencer public key. A malicious peer can inject a header with an arbitrary `block_hash`, `state_diff_commitment`, gas prices, and sequencer address, then supply a matching state diff. The resulting wrong state is committed to the Patricia trie and served authoritatively by the RPC server.

---

### Finding Description

`HeaderStreamBuilder::parse_data_for_block` in `crates/apollo_p2p_sync/src/client/header.rs` performs two structural checks on a received `SignedBlockHeader`:

1. The `block_number` field matches the expected value.
2. The `signatures` vector has exactly `ALLOWED_SIGNATURES_LENGTH` (1) elements. [1](#0-0) 

Neither check verifies the cryptographic content of the signature. The function returns `Ok(Some(signed_block_header))` and `write_to_storage` immediately commits the header and its attached signature to the MDBX store. [2](#0-1) 

The function `verify_block_signature` exists in `starknet_api` and computes `poseidon_hash(block_hash, state_diff_commitment)` then verifies it against the sequencer's ECDSA public key. [3](#0-2) 

This function is used in the central sync path (guarded by `config.verify_blocks`) but is **never called** anywhere in the P2P sync pipeline. [4](#0-3) 

After the malicious header is stored, `StateDiffStreamBuilder::parse_data_for_block` reads `state_diff_length` from the stored header to determine how many state diff chunks to accept from the same peer. [5](#0-4) 

The accepted state diff is written to storage without any cross-check against the `state_diff_commitment` field in the header. [6](#0-5) 

The `SyncBlock` type itself documents that blocks arriving via this path are treated as trusted without carrying verification data. [7](#0-6) 

The `StateSyncRunner` passes the same `storage_reader` to both the P2P sync client and the RPC server, so whatever is written by the sync client is immediately served as authoritative state. [8](#0-7) 

---

### Impact Explanation

A malicious P2P peer can craft a `SignedBlockHeader` containing:
- An arbitrary `block_hash`
- An arbitrary `state_diff_commitment` = `poseidon_hash(malicious_state_diff)` (self-consistent)
- Arbitrary gas prices, sequencer address, timestamps, and `state_diff_length`
- Any two field elements as the `BlockSignature` (never checked)

The peer then supplies a matching `malicious_state_diff` whose length equals the declared `state_diff_length`. Both pass all structural checks and are committed to storage. The Patricia trie committer computes a new global root from the malicious state diff. If `verify_state_diff_hash` is enabled in the committer config, the check still passes because the peer crafted `state_diff_commitment = hash(malicious_state_diff)`. [9](#0-8) 

The resulting wrong global root, wrong storage values, wrong nonces, and wrong class hashes are then served by the RPC server for `starknet_getStorageAt`, `starknet_getStateUpdate`, `starknet_call`, fee estimation, and simulation — all returning authoritative-looking wrong values. This matches the **High** impact: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

---

### Likelihood Explanation

The P2P network is permissionless. Any node can connect and respond to SQMR header queries. The attacker needs only to be selected as the responding peer for a header query, which requires no privileged access. The attack is reachable by a malicious normal peer without any privileged keys.

---

### Recommendation

**Short term**: In `HeaderStreamBuilder::parse_data_for_block`, after structural validation, call `verify_block_signature` with the sequencer's known public key before returning `Ok(Some(...))`. The sequencer public key should be fetched from a trusted source (e.g., the L1 contract or a configured value) analogous to how `track_sequencer_public_key_changes` works in central sync.

**Long term**: Add a cross-check in `StateDiffStreamBuilder::parse_data_for_block` that verifies `hash(assembled_state_diff) == header.state_diff_commitment` before writing to storage, so that even if a header slips through, a mismatched state diff is rejected. Document the authentication and integrity requirements for every data type in the P2P sync pipeline (headers, state diffs, transactions, classes).

---

### Proof of Concept

```
Attacker node A connects to victim node V (running P2P sync).

1. V sends a HeaderQuery for block N.
2. A responds with SignedBlockHeader {
       block_header: BlockHeader {
           block_number: N,
           block_hash: 0xDEAD,                          // arbitrary
           state_diff_commitment: hash(evil_state_diff), // self-consistent
           state_diff_length: len(evil_state_diff),
           l1_gas_price: { price_in_wei: 0, ... },       // arbitrary
           sequencer: 0xATTACKER,                        // arbitrary
           ...
       },
       signatures: [BlockSignature { r: 1, s: 1 }],     // never verified
   }
3. V's parse_data_for_block checks:
   - block_number == N  ✓
   - signatures.len() == 1  ✓
   - (no signature crypto check)
   → writes header to storage

4. V sends a StateDiffQuery for block N.
5. A responds with evil_state_diff chunks (total length == state_diff_length from step 2).
6. V's parse_data_for_block checks:
   - current_state_diff_len reaches target_state_diff_len  ✓
   → writes evil_state_diff to storage

7. Committer computes global_root from evil_state_diff.
   If verify_state_diff_hash=true: hash(evil_state_diff) == state_diff_commitment ✓ (crafted)
   → wrong global_root committed to Patricia trie

8. RPC starknet_getStorageAt(contract, key, block=N) returns attacker-controlled value.
   RPC starknet_getStateUpdate(block=N) returns attacker-controlled state diff.
```

### Citations

**File:** crates/apollo_p2p_sync/src/client/header.rs (L28-51)
```rust
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            storage_writer
                .begin_rw_txn()?
                .append_header(
                    self.block_header.block_header_without_hash.block_number,
                    &self.block_header,
                )?
                .append_block_signature(
                    self.block_header.block_header_without_hash.block_number,
                    self
                    .signatures
                    // In the future we will support multiple signatures.
                    .first()
                    // The verification that the size of the vector is 1 is done in the data
                    // verification.
                    .expect("Vec::first should return a value on a vector of size 1"),
                )?
                .commit()?;
            STATE_SYNC_HEADER_MARKER.set_lossy(
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L104-120)
```rust
            if block_number
                != signed_block_header.block_header.block_header_without_hash.block_number
            {
                return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered {
                    expected_block_number: block_number,
                    actual_block_number: signed_block_header
                        .block_header
                        .block_header_without_hash
                        .block_number,
                }));
            }
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
            Ok(Some(signed_block_header))
```

**File:** crates/starknet_api/src/block.rs (L717-730)
```rust
pub fn verify_block_signature(
    sequencer_pub_key: &SequencerPublicKey,
    signature: &BlockSignature,
    state_diff_commitment: &GlobalRoot,
    block_hash: &BlockHash,
) -> Result<bool, BlockVerificationError> {
    let message_hash = Poseidon::hash_array(&[block_hash.0, state_diff_commitment.0]);
    verify_message_hash_signature(&message_hash, &signature.0, &sequencer_pub_key.0).map_err(
        |err| BlockVerificationError::BlockSignatureVerificationFailed {
            block_hash: *block_hash,
            error: err,
        },
    )
}
```

**File:** crates/apollo_central_sync/src/lib.rs (L226-249)
```rust
    async fn track_sequencer_public_key_changes(&mut self) -> StateSyncResult {
        let sequencer_pub_key = self.central_source.get_sequencer_pub_key().await?;
        match self.sequencer_pub_key {
            // First time setting the sequencer public key.
            None => {
                info!("Sequencer public key set to {sequencer_pub_key:?}.");
                self.sequencer_pub_key = Some(sequencer_pub_key);
            }
            Some(cur_key) => {
                if cur_key != sequencer_pub_key {
                    warn!(
                        "Sequencer public key changed from {cur_key:?} to {sequencer_pub_key:?}."
                    );
                    // TODO(Yair): Add alert.
                    self.sequencer_pub_key = Some(sequencer_pub_key);
                    return Err(StateSyncError::SequencerPubKeyChanged {
                        old: cur_key,
                        new: sequencer_pub_key,
                    });
                }
            }
        };
        Ok(())
    }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L27-39)
```rust
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

**File:** crates/apollo_state_sync_types/src/state_sync_types.rs (L11-27)
```rust
/// A block that came from the state sync.
/// Contains all the data needed to update the state of the system about this block.
///
/// Blocks that came from the state sync are trusted. Therefore, SyncBlock doesn't contain data
/// needed for verifying the block
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncBlock {
    pub state_diff: ThinStateDiff,
    // TODO(Matan): decide if we want block hash, parent block hash and full classes here.
    pub account_transaction_hashes: Vec<TransactionHash>,
    pub l1_transaction_hashes: Vec<TransactionHash>,
    pub block_header_without_hash: BlockHeaderWithoutHash,
    /// The commitments are required to calculate the partial block hash.
    /// In Starknet versions prior to 0.13.2, the commitments are not included in the block header.
    /// Therefore, it is optional.
    pub block_header_commitments: Option<BlockHeaderCommitments>,
}
```

**File:** crates/apollo_state_sync/src/runner/mod.rs (L183-190)
```rust
        let rpc_server_future = spawn_rpc_server(
            &rpc_config,
            shared_highest_block.clone(),
            pending_data.clone(),
            pending_classes.clone(),
            storage_reader.clone(),
            Some(class_manager_client.clone()),
        );
```

**File:** crates/apollo_committer/src/committer_test.rs (L314-326)
```rust
#[tokio::test]
async fn verify_state_diff_hash_succeeds() {
    let mut committer = new_test_committer().await;
    committer.config.verify_state_diff_hash = true;
    let state_diff = get_state_diff(1);
    let state_diff_commitment = Some(calculate_state_diff_hash(&state_diff));
    let height = BlockNumber(0);
    committer
        .commit_block(CommitBlockRequest { state_diff, state_diff_commitment, height })
        .await
        .unwrap();
    assert_eq!(committer.offset, BlockNumber(height.0 + 1));
}
```
