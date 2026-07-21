### Title
Missing `state_diff_commitment` Hash Verification in P2P Sync State Diff Assembly — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

The P2P sync client validates the total *length* of received state diff chunks against the `state_diff_length` field in the stored block header, but never verifies that the assembled `ThinStateDiff`'s Poseidon hash matches the `state_diff_commitment` (root) field in the same header. A malicious peer can send chunks that satisfy the length check while carrying wrong storage values, class hashes, or nonces. The corrupted state diff is written to storage and served authoritatively by every RPC endpoint that reads state.

---

### Finding Description

**Root cause — `parse_data_for_block`**

`StateDiffStreamBuilder::parse_data_for_block` in `crates/apollo_p2p_sync/src/client/state_diff.rs` reads the expected length from the stored header:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    ...
    .state_diff_length          // ← only the length field is read
    .ok_or(...)?;
```

It then accumulates chunks until `current_state_diff_len == target_state_diff_len` and returns the assembled diff:

```rust
if current_state_diff_len != target_state_diff_len {
    return Err(...WrongStateDiffLength...);
}
validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))   // ← returned with no hash check
``` [1](#0-0) 

The same stored header also carries `state_diff_commitment` — a Poseidon hash over the full diff content:

```rust
pub struct BlockHeaderCommitments {
    ...
    pub state_diff_commitment: StateDiffCommitment,   // PoseidonHash
    ...
}
``` [2](#0-1) 

`calculate_state_diff_hash` is the function that produces this commitment:

```rust
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    // Poseidon over deployed_contracts, declared_classes, storage_diffs, nonces …
}
``` [3](#0-2) 

`parse_data_for_block` never calls `calculate_state_diff_hash` on the assembled result and never compares it to `block_header.state_diff_commitment`. The `state_diff_commitment.root` field in the P2P protobuf (`StateDiffCommitment { state_diff_length, root }`) is therefore completely unused on the receiving side. [4](#0-3) 

**Analogous unverified path in central sync**

The central sync `store_state_diff` carries an explicit TODO acknowledging the same gap:

```rust
// TODO(dan): verifications - verify state diff against stored header.
``` [5](#0-4) 

**What the committer does — and why it does not close the gap for syncing nodes**

The `Committer` has a `verify_state_diff_hash` flag that, when `true`, recomputes the hash and rejects mismatches:

```rust
if self.config.verify_state_diff_hash {
    let calculated_commitment = calculate_state_diff_hash(&state_diff);
    if commitment != calculated_commitment {
        return Err(CommitterError::StateDiffHashMismatch { … });
    }
}
``` [6](#0-5) 

However, the committer is a separate component used by the *proposer* (batcher). A node running only the P2P sync path (a follower / RPC node) never invokes the committer on synced blocks. The wrong diff is stored and queried directly from storage by the RPC layer without any subsequent hash check.

---

### Impact Explanation

Once the corrupted `ThinStateDiff` is written via `append_state_diff`, every storage-backed RPC call for that block returns the attacker-chosen value:

- `starknet_getStorageAt` → attacker-controlled storage slot value  
- `starknet_getClassHashAt` → attacker-controlled class hash  
- `starknet_getNonce` → attacker-controlled nonce  

These are authoritative-looking wrong values served to wallets, dApps, and other sequencers that rely on this node's RPC. The corrupted diff also feeds into any subsequent state-root computation or proof-input generation performed by the same node.

Matching impact: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

Any peer the syncing node connects to can mount this attack. No special privilege is required beyond being an accepted P2P peer. The attacker only needs to:

1. Respond to the state diff query with chunks whose `len()` values sum to `target_state_diff_len`.
2. Ensure no duplicate keys (to pass `ConflictingStateDiffParts`).
3. Substitute any storage value, class hash, or nonce with an arbitrary felt.

The length check is the only gate; the Poseidon commitment is never evaluated.

---

### Recommendation

After the loop in `parse_data_for_block`, compute the Poseidon hash of the assembled diff and compare it to the commitment stored in the block header:

```rust
let expected_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("header must exist")
    .state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage {
        block_number,
        missing_field: "state_diff_commitment",
    })?;

let actual_commitment = calculate_state_diff_hash(&result);
if actual_commitment != expected_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffCommitmentMismatch {
        expected: expected_commitment,
        actual: actual_commitment,
    }));
}
```

Apply the same fix to `store_state_diff` in `crates/apollo_central_sync/src/lib.rs` (the existing TODO at line 442).

---

### Proof of Concept

1. Attacker node connects to a syncing Apollo node as a P2P peer.
2. Syncing node requests state diffs for block N via the P2P protocol.
3. Attacker responds with `StateDiffChunk::ContractDiff` entries whose `storage_diffs` contain attacker-chosen key→value pairs. The total `len()` of all chunks equals the `state_diff_length` stored in the block header; no key appears twice.
4. `parse_data_for_block` accepts the chunks (length matches, no conflicts), assembles the corrupted `ThinStateDiff`, and returns it.
5. `write_to_storage` calls `append_state_diff(block_number, corrupted_diff)`.
6. A subsequent `starknet_getStorageAt(block_number, contract, key)` RPC call returns the attacker-chosen value instead of the true on-chain value. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L26-40)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L128-137)
```rust
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct BlockHeaderCommitments {
    pub transaction_commitment: TransactionCommitment,
    pub event_commitment: EventCommitment,
    pub receipt_commitment: ReceiptCommitment,
    pub state_diff_commitment: StateDiffCommitment,
    // TODO(Yoni): rename to packed_lengths to match Cairo's BlockHeaderCommitments (make sure it
    // doesn't break anything).
    pub concatenated_counts: Felt,
}
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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/sync/common.proto (L6-9)
```text
message StateDiffCommitment {
    uint64 state_diff_length = 1;
    Hash root = 2;
}
```

**File:** crates/apollo_central_sync/src/lib.rs (L442-443)
```rust
        // TODO(dan): verifications - verify state diff against stored header.
        debug!("Storing state diff.");
```

**File:** crates/apollo_committer/src/committer.rs (L165-180)
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
        };
```
