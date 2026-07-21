### Title
Missing State Diff Commitment Verification in P2P Sync Allows Malicious Peer to Corrupt Nonces Table — (`crates/apollo_p2p_sync/src/client/state_diff.rs`, `crates/apollo_storage/src/state/mod.rs`)

---

### Summary

The p2p sync path assembles a `ThinStateDiff` from peer-delivered chunks and writes it directly to storage via `append_state_diff` → `write_nonces` without ever verifying the assembled content against the `state_diff_commitment` stored in the block header. A malicious p2p peer can send state diff chunks with the correct total entry count (satisfying the only structural guard) but with a wrong nonce `N'` for contract `C`. The wrong nonce is written unconditionally to `nonces_table`, and `get_nonce_at` subsequently returns `N'` instead of the committed `N`.

---

### Finding Description

**Entrypoint — p2p sync state diff assembly**

`StateDiffStreamBuilder::parse_data_for_block` reads the `state_diff_length` field from the stored header and accumulates peer-delivered `StateDiffChunk` messages until the running count equals that target: [1](#0-0) 

The only guards applied are:
1. Total entry count must equal `header.state_diff_length` (a plain integer, not a hash).
2. No duplicate contract addresses within a single chunk type.
3. No duplicate deprecated class hashes across chunks.

There is **no check against `state_diff_commitment`** (the cryptographic Pedersen/Poseidon hash committed in the header). A malicious peer can send the correct number of nonce entries with arbitrary nonce values and pass every guard.

**Write path — no commitment re-check**

`BlockData::write_to_storage` for `(ThinStateDiff, BlockNumber)` calls `append_state_diff` directly: [2](#0-1) 

`append_state_diff` calls `write_nonces` unconditionally: [3](#0-2) 

`write_nonces` iterates the `nonces` `IndexMap` and upserts each entry into `nonces_table` with no validation: [4](#0-3) 

**Read path — returns whatever was stored**

`StateReader::get_nonce_at` performs a cursor lower-bound lookup on `nonces_table` and returns the stored value verbatim: [5](#0-4) 

**Central sync — same gap, explicitly acknowledged**

The central sync `store_state_diff` contains a TODO that explicitly admits the missing verification: [6](#0-5) 

**Deserialization — no commitment check either**

`ThinStateDiff::deserialize_from` decompresses and deserializes the nonces field at position 5 of the payload with no commitment binding: [7](#0-6) 

---

### Impact Explanation

`StateSync::get_nonce_at` is the authoritative nonce source for the gateway's `SyncStateReader`. If `nonces_table` holds `N'` for contract `C` after block `B`, the gateway rejects any transaction from `C` with nonce `N` (the correct committed value) and accepts one with nonce `N'`. This enables:

- **Replay attacks**: if `N' < N`, a previously executed transaction with nonce `N'` can be re-submitted.
- **Griefing / DoS**: if `N' > N`, all valid pending transactions from `C` are permanently blocked.

Impact classification: **Critical — invalid or unauthorized transaction accepted / valid transaction rejected through account nonce validation**. [8](#0-7) 

---

### Likelihood Explanation

Any node that can establish a p2p connection (permissionless in a public network) can act as the malicious peer. The attacker only needs to:
1. Serve a header stream that the victim accepts (headers are verified by block hash, but the state diff commitment inside the header is never cross-checked against the state diff body).
2. Serve state diff chunks with the correct total `state_diff_length` count but a substituted nonce value.

No operator, validator, or sequencer privileges are required.

---

### Recommendation

Before calling `append_state_diff`, recompute the `state_diff_commitment` from the assembled `ThinStateDiff` (using the same Pedersen/Poseidon hash used during block production) and compare it against the value stored in the block header. Reject the peer and discard the data if the hashes do not match. The commitment hash function already exists in `starknet_api::block_hash::state_diff_hash`. [9](#0-8) 

---

### Proof of Concept

```rust
// Pseudocode unit test (production storage path, no mocks)
let (reader, mut writer) = open_storage(config)?;

// Correct committed nonce for contract C after block 0 is N = Felt::from(5u64)
let correct_nonce = Nonce(Felt::from(5u64));
// Attacker substitutes N' = Felt::from(99u64)
let tampered_nonce = Nonce(Felt::from(99u64));

let mut state_diff = ThinStateDiff::default();
state_diff.nonces.insert(contract_address_C, tampered_nonce); // attacker-controlled

writer.begin_rw_txn()?
    .append_state_diff(BlockNumber(0), state_diff)?
    .commit()?;

let txn = reader.begin_ro_txn()?;
let state_reader = txn.get_state_reader()?;
let returned = state_reader
    .get_nonce_at(StateNumber::unchecked_right_after_block(BlockNumber(0)), &contract_address_C)?
    .unwrap();

assert_eq!(returned, tampered_nonce);   // passes — N' is stored and returned
assert_ne!(returned, correct_nonce);    // N is never stored

// A transaction with nonce=correct_nonce would now be rejected by the gateway;
// a transaction with nonce=tampered_nonce would be accepted.
```

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

**File:** crates/apollo_storage/src/state/mod.rs (L302-310)
```rust
    pub fn get_nonce_at(
        &self,
        state_number: StateNumber,
        address: &ContractAddress,
    ) -> StorageResult<Option<Nonce>> {
        // State diff updates are indexed by the block_number at which they occurred.
        let block_number: BlockNumber = state_number.block_after();
        get_nonce_at(block_number, address, self.txn, &self.nonces_table)
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L547-547)
```rust
        write_nonces(&thin_state_diff.nonces, &self.txn, block_number, &nonces_table)?;
```

**File:** crates/apollo_storage/src/state/mod.rs (L788-797)
```rust
fn write_nonces<'env>(
    nonces: &IndexMap<ContractAddress, Nonce>,
    txn: &DbTransaction<'env, RW>,
    block_number: BlockNumber,
    contracts_table: &'env NoncesTable<'env>,
) -> StorageResult<()> {
    for (contract_address, nonce) in nonces {
        contracts_table.upsert(txn, &(*contract_address, block_number), nonce)?;
    }
    Ok(())
```

**File:** crates/apollo_central_sync/src/lib.rs (L442-443)
```rust
        // TODO(dan): verifications - verify state diff against stored header.
        debug!("Storing state diff.");
```

**File:** crates/apollo_storage/src/serialization/serializers.rs (L1165-1177)
```rust
    fn deserialize_from(bytes: &mut impl std::io::Read) -> Option<Self> {
        let compressed_data = Vec::<u8>::deserialize_from(bytes)?;
        let data = decompress(compressed_data.as_slice())
            .expect("destination buffer should be large enough");
        let data = &mut data.as_slice();
        Some(Self {
            deployed_contracts: IndexMap::deserialize_from(data)?,
            storage_diffs: IndexMap::deserialize_from(data)?,
            class_hash_to_compiled_class_hash: IndexMap::deserialize_from(data)?,
            deprecated_declared_classes: Vec::deserialize_from(data)?,
            nonces: IndexMap::deserialize_from(data)?,
        })
    }
```

**File:** crates/apollo_state_sync/src/lib.rs (L239-258)
```rust
    async fn get_nonce_at(
        &self,
        block_number: BlockNumber,
        contract_address: ContractAddress,
    ) -> StateSyncResult<Nonce> {
        let storage_reader = self.storage_reader.clone();

        let txn = storage_reader.begin_ro_txn()?;
        verify_synced_up_to(&txn, block_number)?;

        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let state_reader = txn.get_state_reader()?;

        verify_contract_deployed(&state_reader, state_number, contract_address)?;

        let res = state_reader
            .get_nonce_at(state_number, &contract_address)?
            .ok_or(StateSyncError::ContractNotFound(contract_address))?;

        Ok(res)
```
