### Title
P2P Sync Client Accepts State Diffs Without Verifying Hash Against Header Commitment, Enabling Any Peer to Corrupt Stored State - (File: crates/apollo_p2p_sync/src/client/state_diff.rs)

---

### Summary

The P2P sync client validates received state diff chunks only for **length** (against `state_diff_length` from the stored header) and structural integrity (no duplicate keys). It never verifies the Poseidon hash of the assembled `ThinStateDiff` against the `state_diff_commitment` stored in the block header. Simultaneously, the header itself is accepted from any peer with only a block-number equality check and a signature-count check — the signature bytes are never cryptographically verified, and the parent-hash chain is explicitly unimplemented (TODO). Any single connected peer can therefore supply a header with an arbitrary `state_diff_commitment` and a matching-length but content-corrupted state diff, causing the node to persist wrong storage values, nonces, and class hashes as authoritative state.

---

### Finding Description

**Step 1 — Header accepted without signature or parent-hash verification.**

`parse_data_for_block` in `HeaderStreamBuilder` checks only that the received block number matches the expected value and that exactly `ALLOWED_SIGNATURES_LENGTH` signatures are present. The signature bytes themselves are never verified against the block hash, and the parent-hash chain check is explicitly deferred:

```rust
// TODO(shahak): Check that parent_hash is the same as the previous block's hash
// and handle reverts.
if block_number != signed_block_header.block_header.block_header_without_hash.block_number {
    return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered { ... }));
}
if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
    return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength { ... }));
}
Ok(Some(signed_block_header))
``` [1](#0-0) 

A peer can therefore supply any `state_diff_commitment`, `state_diff_length`, and `block_hash` in the header and it will be stored verbatim.

**Step 2 — State diff accepted with length check only, no hash verification.**

`parse_data_for_block` in `StateDiffStreamBuilder` reads `target_state_diff_len` from the already-stored header, accumulates chunks until the length matches, and validates structural integrity (no conflicting keys). It then returns the assembled `ThinStateDiff` without ever computing `calculate_state_diff_hash` and comparing it to the `state_diff_commitment` in the header:

```rust
while current_state_diff_len < target_state_diff_len {
    ...
    current_state_diff_len += state_diff_chunk.len();
    unite_state_diffs(&mut result, state_diff_chunk)?;
}
if current_state_diff_len != target_state_diff_len {
    return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength { ... }));
}
validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))
``` [2](#0-1) 

The `state_diff_commitment` field stored in the header is never consulted during state diff assembly.

**Step 3 — `SyncBlock` is explicitly designed to be trusted without verification.**

The type comment in `state_sync_types.rs` confirms the design intent:

```rust
/// Blocks that came from the state sync are trusted. Therefore, SyncBlock doesn't contain data
/// needed for verifying the block
``` [3](#0-2) 

**Step 4 — Corrupted state diff flows into the Patricia trie and RPC.**

The assembled `ThinStateDiff` is written to `apollo_storage`. The committer's `verify_state_diff_hash` guard (`crates/apollo_committer/src/committer.rs`) is only exercised on the batcher's `commit_proposal` path; the P2P sync client writes directly to storage, bypassing that check entirely. [4](#0-3) 

The consensus orchestrator's `try_sync` path then reads the corrupted `SyncBlock` from state sync and forwards it to the batcher via `add_sync_block`, which constructs `PartialBlockHashComponents` directly from the peer-supplied `block_header_commitments` without re-deriving them from the actual state diff: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A malicious peer sends:
1. A `SignedBlockHeader` with correct `block_number`, one dummy signature, and attacker-chosen `state_diff_commitment` / `state_diff_length`.
2. State diff chunks whose total length equals `state_diff_length` but whose content contains wrong storage values, nonces, or class hashes.

The node stores the corrupted `ThinStateDiff`. The Patricia trie is updated with wrong leaves, producing a wrong global root. All subsequent RPC calls (`starknet_getStorageAt`, `starknet_getNonce`, `starknet_getClassHashAt`) return attacker-controlled values. If the node later participates in sequencing, it executes transactions against corrupted state, producing wrong receipts, wrong fee deductions, and wrong proof inputs.

---

### Likelihood Explanation

The P2P sync client connects to any peer discovered through the network. No IP/subnet rate-limiting or stake-weighted peer selection exists. A single adversarial peer that wins a sync session can inject the corrupted data. The missing check (`calculate_state_diff_hash` vs `state_diff_commitment`) is a one-line omission with no compensating control on the P2P path.

---

### Recommendation

After assembling the full `ThinStateDiff` in `StateDiffStreamBuilder::parse_data_for_block`, compute `calculate_state_diff_hash(&result)` and compare it to the `state_diff_commitment` retrieved from the stored block header. Return `ParseDataError::BadPeer` on mismatch. Additionally, verify the block signature against the block hash in `HeaderStreamBuilder::parse_data_for_block` and implement the deferred parent-hash chain check.

---

### Proof of Concept

```
Attacker peer → P2P sync client

1. Client sends Query { start_block: N, direction: Forward, limit: 1 }
   for Protocol::SignedBlockHeader.

2. Attacker replies with SignedBlockHeader {
       block_header: BlockHeader {
           block_number: N,
           block_hash: <any felt>,          // never verified
           state_diff_commitment: H(evil_diff),
           state_diff_length: len(evil_diff),
           ...
       },
       signatures: [BlockSignature::default()],  // count=1, bytes unchecked
   }
   → Stored verbatim in apollo_storage.

3. Client sends Query { start_block: N, ... } for Protocol::StateDiff.

4. Attacker replies with StateDiffChunk(s) encoding evil_diff
   (e.g., storage_diffs: { victim_contract: { key: attacker_value } })
   with total len == state_diff_length.

5. parse_data_for_block:
   - length check passes (current_state_diff_len == target_state_diff_len)
   - structural check passes (no duplicate keys)
   - NO hash check → evil_diff stored as block N's state diff.

6. starknet_getStorageAt(victim_contract, key, block=N)
   → returns attacker_value  (wrong authoritative RPC result)

7. If node enters sequencing mode, blockifier reads attacker_value
   from state, executes transactions against corrupted state,
   produces wrong receipts and wrong global root.
```

### Citations

**File:** crates/apollo_p2p_sync/src/client/header.rs (L102-120)
```rust
            // TODO(shahak): Check that parent_hash is the same as the previous block's hash
            // and handle reverts.
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L72-107)
```rust
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

**File:** crates/apollo_state_sync_types/src/state_sync_types.rs (L13-15)
```rust
///
/// Blocks that came from the state sync are trusted. Therefore, SyncBlock doesn't contain data
/// needed for verifying the block
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L838-867)
```rust
        // TODO(Asmaa): validate starknet_version and parent_hash when they are stored.
        let block_number = sync_block.block_header_without_hash.block_number;
        let timestamp = sync_block.block_header_without_hash.timestamp;
        let last_block_timestamp =
            self.previous_block_info.as_ref().map_or(0, |info| info.timestamp);
        let now: u64 = self.deps.clock.unix_now();
        if !(block_number == height
            && timestamp.0 >= last_block_timestamp
            && timestamp.0 <= now + self.config.static_config.block_timestamp_window_seconds)
        {
            warn!(
                "Invalid block info: expected block number {}, got {}, expected timestamp range \
                 [{}, {}], got {}",
                height,
                block_number,
                last_block_timestamp,
                now + self.config.static_config.block_timestamp_window_seconds,
                timestamp.0,
            );
            return false;
        }
        self.previous_block_info =
            Some(previous_block_info_from_block_header(&sync_block.block_header_without_hash));
        self.interrupt_active_proposal().await;

        info!(
            "Adding sync block to Batcher for height {}",
            sync_block.block_header_without_hash.block_number,
        );
        if let Err(e) = self.deps.batcher.add_sync_block(sync_block).await {
```

**File:** crates/apollo_batcher/src/batcher.rs (L686-700)
```rust
            match block_header_commitments {
                Some(header_commitments) => {
                    StorageCommitmentBlockHash::Partial(PartialBlockHashComponents {
                        header_commitments,
                        block_number,
                        l1_gas_price: block_header_without_hash.l1_gas_price,
                        l1_data_gas_price: block_header_without_hash.l1_data_gas_price,
                        l2_gas_price: block_header_without_hash.l2_gas_price,
                        sequencer: block_header_without_hash.sequencer,
                        timestamp: block_header_without_hash.timestamp,
                        starknet_version: block_header_without_hash.starknet_version,
                    })
                }
                None => return Err(BatcherError::MissingHeaderCommitments { block_number }),
            }
```
