### Title
Unverified `state_diff_length` in P2P-synced headers enables wrong state diff acceptance — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

`StateDiffStreamBuilder::parse_data_for_block` uses `header.state_diff_length` as the sole termination condition when accumulating state-diff chunks from a P2P peer. That field is stored verbatim from the peer's `SignedBlockHeader` without ever being cross-checked against the `state_diff_length` encoded inside `concatenated_counts` — the packed field that is committed into the block hash. A malicious peer can therefore advertise any `state_diff_length` value, causing the syncing node to accept a state diff of the wrong size, store it, and serve wrong storage/nonce/class-hash values through its RPC layer.

---

### Finding Description

**Analog to the external report.** The external bug distributes a cumulative uptime value evenly across *N* epochs (where *N* is derived from a checkpoint range) without verifying actual per-epoch activity. The sequencer analog is structurally identical: `parse_data_for_block` for state diffs derives its termination count *N* (`target_state_diff_len`) from the stored header field rather than from the block-hash commitment that encodes the authoritative count.

**Root cause — `parse_data_for_block` in `state_diff.rs`.**

```
target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .state_diff_length          // ← taken from peer-supplied header, never verified
    .ok_or(...)?;

while current_state_diff_len < target_state_diff_len {
    current_state_diff_len += state_diff_chunk.len();   // ← sum of chunk lengths
    unite_state_diffs(&mut result, state_diff_chunk)?;
}
if current_state_diff_len != target_state_diff_len { return Err(...); }
``` [1](#0-0) 

The authoritative `state_diff_length` lives inside `concatenated_counts`, a packed felt that is an input to `calculate_block_hash`:

```
concat_counts(
    transactions_data.len(),
    event_leaf_elements.len(),
    state_diff.len(),          // ← this is the committed value
    l1_da_mode,
)
``` [2](#0-1) 

`state_diff_length` is a *separate* field in `BlockHeader` / `StorageBlockHeader` that is transmitted over the wire as part of the protobuf `StateDiffCommitment` message. It is stored as-is by `append_header` with no check that it equals the value encoded in `concatenated_counts`. [3](#0-2) 

**Enabling condition — `parse_data_for_block` in `header.rs`.**

The P2P header parser only checks block number and signature *count*; it does not verify the block hash, does not call `verify_block_signature`, and does not compare `state_diff_length` against `concatenated_counts`. A developer TODO explicitly acknowledges the missing check:

```rust
// TODO(Shahak): Verify `n_transactions` and `state_diff_length` match values in
// concatenated_counts.
``` [4](#0-3) 

**No downstream commitment check.** After `parse_data_for_block` assembles the `ThinStateDiff`, it calls only `validate_deprecated_declared_classes_non_conflicting`; it never hashes the assembled diff and compares it against `header.state_diff_commitment`. So even if `state_diff_length` were correct, a peer could substitute arbitrary content. [5](#0-4) 

**Attack scenario.**

1. Malicious peer sends a `SignedBlockHeader` for block *B* with `state_diff_length = X` where X ≠ Y (the true length encoded in `concatenated_counts`).
2. The syncing node stores the header with `state_diff_length = X`.
3. The state-diff sync loop runs until `current_state_diff_len == X`, accepting a diff of size X.
4. If X < Y the diff is truncated (missing storage/nonce/class updates); if X > Y it contains fabricated entries.
5. The wrong `ThinStateDiff` is written to storage via `append_state_diff`. [6](#0-5) 

---

### Impact Explanation

The wrong `ThinStateDiff` is the authoritative source for `get_storage_at`, `get_nonce_at`, `get_class_hash_at`, and `get_compiled_class_hash_at` queries served by the RPC layer and the state-sync service. Any node that syncs via P2P and is fed a manipulated header will return wrong storage values, wrong nonces, and wrong class hashes for the affected block — matching the **High** impact criterion: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

---

### Likelihood Explanation

The P2P network is permissionless; any peer that can establish a connection can send headers. No cryptographic check (block-hash recomputation, signature verification against a known sequencer key) is performed on headers in the P2P sync path. The attack requires only the ability to act as a P2P peer and knowledge of the target block number.

---

### Recommendation

1. **Verify `state_diff_length` against `concatenated_counts`** immediately after receiving a `SignedBlockHeader`. Decode `state_diff_length` from `concatenated_counts` using the same bit-extraction logic used for `n_events` (`extract_event_count_from_concatenated_counts`) and assert equality with `header.state_diff_length` before storing the header.

2. **Verify the assembled state diff against `state_diff_commitment`** at the end of `parse_data_for_block` for state diffs: compute `calculate_state_diff_hash(&result)` and compare it against the `state_diff_commitment` stored in the header.

3. **Verify the block hash** in `parse_data_for_block` for headers: recompute `PartialBlockHash::from_partial_block_hash_components` from the received header fields and assert it matches `block_header.block_hash` (the state-root-independent partial hash is sufficient for this check without requiring the global root).

---

### Proof of Concept

```
// In parse_data_for_block (state_diff.rs), the invariant that is silently assumed
// but never checked:
//
//   header.state_diff_length
//       == state_diff_length extracted from header.concatenated_counts
//
// A peer can break this by sending:
//   SignedBlockHeader {
//       state_diff_length: Some(WRONG_LEN),   // e.g. 0 or 1000
//       header_commitments: BlockHeaderCommitments {
//           concatenated_counts: concat_counts(n_txs, n_events, TRUE_LEN, da_mode),
//           state_diff_commitment: hash_of_true_diff,
//           ...
//       },
//       ...
//   }
//
// The syncing node stores WRONG_LEN, then accepts a diff of size WRONG_LEN
// (which the peer fabricates), and stores it as the canonical state diff for
// block B.  All subsequent RPC calls for storage/nonce/class at block B return
// the fabricated values.
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-323)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L374-393)
```rust
pub fn concat_counts(
    transaction_count: usize,
    event_count: usize,
    state_diff_length: usize,
    l1_data_availability_mode: L1DataAvailabilityMode,
) -> Felt {
    let l1_data_availability_byte: u8 = match l1_data_availability_mode {
        L1DataAvailabilityMode::Calldata => 0,
        L1DataAvailabilityMode::Blob => 0b10000000,
    };
    let concat_bytes = [
        to_64_bits(transaction_count).as_slice(),
        to_64_bits(event_count).as_slice(),
        to_64_bits(state_diff_length).as_slice(),
        &[l1_data_availability_byte],
        &[0_u8; 7], // zero padding
    ]
    .concat();
    Felt::from_bytes_be_slice(concat_bytes.as_slice())
}
```

**File:** crates/apollo_storage/src/header.rs (L305-327)
```rust
        let storage_block_header = StorageBlockHeader {
            block_hash: block_header.block_hash,
            parent_hash: block_header.block_header_without_hash.parent_hash,
            block_number: block_header.block_header_without_hash.block_number,
            l1_gas_price: block_header.block_header_without_hash.l1_gas_price,
            l1_data_gas_price: block_header.block_header_without_hash.l1_data_gas_price,
            l2_gas_price: block_header.block_header_without_hash.l2_gas_price,
            l2_gas_consumed: block_header.block_header_without_hash.l2_gas_consumed,
            next_l2_gas_price: block_header.block_header_without_hash.next_l2_gas_price,
            state_root: block_header.block_header_without_hash.state_root,
            sequencer: block_header.block_header_without_hash.sequencer,
            timestamp: block_header.block_header_without_hash.timestamp,
            l1_da_mode: block_header.block_header_without_hash.l1_da_mode,
            state_diff_commitment: block_header.state_diff_commitment,
            transaction_commitment: block_header.transaction_commitment,
            event_commitment: block_header.event_commitment,
            receipt_commitment: block_header.receipt_commitment,
            state_diff_length: block_header.state_diff_length,
            n_transactions: block_header.n_transactions,
            n_events: block_header.n_events,
        };

        headers_table.append(&self.txn, &block_number, &storage_block_header)?;
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L104-121)
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
        }
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L130-157)
```rust
    fn convert_sync_block_to_block_data(
        block_number: BlockNumber,
        sync_block: SyncBlock,
    ) -> SignedBlockHeader {
        let header_commitments = sync_block.block_header_commitments.expect(
            "Block header commitments should be present from starknet version 0.13.2, and we're \
             creating a new block here.",
        );
        let n_events =
            extract_event_count_from_concatenated_counts(&header_commitments.concatenated_counts);
        // TODO(Shahak): Verify `n_transactions` and `state_diff_length` match values in
        // concatenated_counts.
        SignedBlockHeader {
            block_header: BlockHeader {
                block_hash: BlockHash(StarkHash::from(block_number.0)),
                block_header_without_hash: sync_block.block_header_without_hash,
                state_diff_commitment: Some(header_commitments.state_diff_commitment),
                state_diff_length: Some(sync_block.state_diff.len()),
                transaction_commitment: Some(header_commitments.transaction_commitment),
                event_commitment: Some(header_commitments.event_commitment),
                n_transactions: sync_block.account_transaction_hashes.len()
                    + sync_block.l1_transaction_hashes.len(),
                n_events,
                receipt_commitment: Some(header_commitments.receipt_commitment),
            },
            signatures: vec![BlockSignature::default()],
        }
    }
```
