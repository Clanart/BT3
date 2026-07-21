Let me check the header write path and whether block hash is verified anywhere in the p2p sync pipeline.

The full attack path is now clear. Let me trace every layer before rendering a verdict.

**Layer 1 — Header sync (no block hash verification)**

`parse_data_for_block` in `header.rs` only checks that the block number field matches and that `signatures.len() == 1`. It does not call `calculate_block_hash`, does not verify the signature cryptographically, and does not compare the received `block_hash` against any L1-anchored truth. [1](#0-0) 

`write_to_storage` for `SignedBlockHeader` stores the peer-supplied header verbatim, including `event_commitment`, with no recomputation. [2](#0-1) 

**Layer 2 — Transaction sync (no event-commitment cross-check)**

`parse_data_for_block` in `transaction.rs` pushes the peer-supplied `transaction_output` (which contains the events vector) directly into `block_body.transaction_outputs` at line 87, with no validation against the `event_commitment` already stored in the header. [3](#0-2) 

`write_to_storage` calls `append_body` → `write_transactions` → `write_events`, which stores the peer-supplied events in the events table. [4](#0-3) [5](#0-4) [6](#0-5) 

**Layer 3 — No post-storage commitment check**

`append_body` contains zero code that reads back the stored events, recomputes `calculate_event_commitment`, and compares it to the `event_commitment` field in the stored header. [7](#0-6) 

**Applying the fast-rejection criteria**

| Criterion | Applies? |
|---|---|
| Requires operator/admin/validator privileges | No — any p2p peer |
| Bad peer data is **rejected/ignored/retried** | **No** — data is accepted and committed to storage |
| Resource-only / DoS-only impact | No — events table is durably corrupted |
| No concrete corrupted committed value | No — the stored events are concrete and differ from the Merkle root of `event_commitment` |

The second fast-rejection criterion is the decisive one: the data is **not** rejected — it is committed to the LMDB events table and subsequently served by `starknet_getEvents`. The "malicious-peer-only" sub-clause in Review Step 5 applies when the impact is limited to resource waste or disconnection; here the impact is durable storage corruption and wrong RPC output, so it does not apply.

**Commitment binding analysis**

`event_commitment` is part of the block hash preimage (it is hashed into `calculate_block_hash` via `BlockHeaderCommitments`). [8](#0-7) 

Because the p2p sync path also does not verify the block hash against L1, a malicious peer controls **both** the `event_commitment` field in the header **and** the events in the body. The peer can make them consistent with each other (both fabricated) or inconsistent. Either way, the stored events do not reflect the canonical on-chain event set.

---

### Title
Missing event-commitment verification in p2p transaction sync allows malicious peer to store fabricated events — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary
The p2p sync client accepts `FullTransaction` objects from an untrusted peer and stores their `transaction_output` (including the events vector) without verifying that the resulting event Merkle root matches the `event_commitment` field in the already-stored block header. A malicious peer acting as the sync source can inject arbitrary events that are durably committed to the events table and served verbatim by `starknet_getEvents`.

### Finding Description
`parse_data_for_block` in `transaction.rs` collects peer-supplied `FullTransaction` values and pushes each `transaction_output` directly into `block_body.transaction_outputs` (line 87) with no validation. [9](#0-8) 

`write_to_storage` then calls `append_body`, which calls `write_transactions`, which calls `write_events` — storing the peer-supplied events in the LMDB events table. [10](#0-9) 

The header sync path stores the peer-supplied `event_commitment` verbatim without verifying the block hash or the signature cryptographically. [11](#0-10) 

There is no post-storage step that recomputes `calculate_event_commitment` over the stored events and compares it to the stored header field. [12](#0-11) 

### Impact Explanation
- `starknet_getEvents` returns attacker-chosen event data for any block synced via p2p, with no indication of tampering.
- L1Handler events (used for L1→L2 message verification) can be fabricated or suppressed, breaking cross-chain message integrity checks that rely on stored event content.
- The corruption is durable (committed to LMDB) and survives node restarts.

### Likelihood Explanation
Any node reachable on the p2p network can be selected as the sync source. No special privileges are required beyond participating in the p2p protocol. The attack is silent — the victim node logs no error and advances its body marker normally.

### Recommendation
After `append_body`, recompute the event commitment from the stored `transaction_outputs` using `calculate_event_commitment` and compare it to `header.event_commitment`. Reject and disconnect the peer if they differ. As a prerequisite, also verify the block hash against the header fields (which binds `event_commitment` to the block hash) and verify the block signature cryptographically before storing the header.

### Proof of Concept
1. Store a header with a known `event_commitment` (e.g., the commitment of an empty event set).
2. Feed a `FullTransaction` whose `transaction_output` contains one or more fabricated events through `parse_data_for_block`.
3. Call `write_to_storage`.
4. Read back events via `get_block_transaction_outputs`.
5. Call `calculate_event_commitment` on the returned events.
6. Assert the recomputed commitment does not equal the stored header's `event_commitment`.

The test will pass (demonstrating the mismatch) because no guard in the production path prevents it.

### Citations

**File:** crates/apollo_p2p_sync/src/client/header.rs (L34-50)
```rust
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
```

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

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L73-91)
```rust
                let Some(FullTransaction { transaction, transaction_output, transaction_hash }) =
                    maybe_transaction?.0
                else {
                    if current_transaction_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::NotEnoughTransactions {
                            expected: target_transaction_len,
                            actual: current_transaction_len,
                            block_number: block_number.0,
                        }));
                    }
                };
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
                current_transaction_len += 1;
            }
```

**File:** crates/apollo_storage/src/body/mod.rs (L506-533)
```rust
impl<T: StorageTransaction<Mode = RW> + BodyStorageReader> BodyStorageWriter for T {
    #[latency_histogram("storage_append_body_latency_seconds", false)]
    fn append_body(self, block_number: BlockNumber, block_body: BlockBody) -> StorageResult<Self> {
        let markers_table = self.open_table(&self.tables().markers)?;
        update_marker(self.txn(), &markers_table, block_number)?;

        if self.scope() != StorageScope::StateOnly {
            let events_table = self.open_table(&self.tables().events)?;
            let transaction_hash_to_idx_table =
                self.open_table(&self.tables().transaction_hash_to_idx)?;
            let transaction_metadata_table =
                self.open_table(&self.tables().transaction_metadata)?;
            let file_offset_table = self.txn().open_table(&self.tables().file_offsets)?;

            write_transactions(
                &block_body,
                self.txn(),
                self.file_handlers(),
                &file_offset_table,
                &transaction_hash_to_idx_table,
                &transaction_metadata_table,
                &events_table,
                block_number,
            )?;
        }

        Ok(self)
    }
```

**File:** crates/apollo_storage/src/body/mod.rs (L619-621)
```rust
        let tx_location = file_handlers.append_transaction(tx);
        let tx_output_location = file_handlers.append_transaction_output(tx_output);
        write_events(tx_output, txn, events_table, transaction_index)?;
```

**File:** crates/apollo_storage/src/body/mod.rs (L644-662)
```rust
fn write_events<'env>(
    tx_output: &TransactionOutput,
    txn: &DbTransaction<'env, RW>,
    events_table: &'env EventsTable<'env>,
    transaction_index: TransactionIndex,
) -> StorageResult<()> {
    let mut contract_addresses_set = HashSet::new();

    for event in tx_output.events().iter() {
        contract_addresses_set.insert(event.from_address);
    }

    for contract_address in contract_addresses_set {
        let key = (contract_address, transaction_index);
        // Here, we use the function assumption; the append will fail if an older transaction_index
        // is a table.
        events_table.append_greater_sub_key(txn, &key, &NoValue)?;
    }
    Ok(())
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-264)
```rust
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
```

**File:** crates/starknet_api/src/block_hash/event_commitment.rs (L21-26)
```rust
pub fn calculate_event_commitment<H: StarkHash>(
    event_leaf_elements: &[EventLeafElement],
) -> EventCommitment {
    let event_leaves = event_leaf_elements.iter().map(calculate_event_hash).collect();
    EventCommitment(calculate_root::<H>(event_leaves))
}
```
