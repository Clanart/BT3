### Title
Missing `state_diff_commitment` Verification in P2P Sync Allows Malicious Peer to Corrupt Contract Storage — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

`parse_data_for_block` validates received `StateDiffChunk` data only against `state_diff_length` from the stored block header. It never verifies the assembled `ThinStateDiff` against the `state_diff_commitment` also present in that header. A malicious p2p peer can therefore supply chunks whose total `len()` matches the committed length but whose content is entirely different — including `storage_diffs` for never-deployed contract addresses — causing `append_state_diff` to write fabricated key/value pairs into the `contract_storage` table. `starknet_getStorageAt` then returns those fabricated values as authoritative.

---

### Finding Description

**Step 1 — The only guard is `state_diff_length`.**

`parse_data_for_block` reads `target_state_diff_len` from the stored header and loops until `current_state_diff_len == target_state_diff_len`: [1](#0-0) 

`ThinStateDiff::len()` counts every storage entry the same as every deployed-contract entry: [2](#0-1) 

So a real diff of `{deployed_contracts: {A: class_X}}` (len = 1) and a malicious diff of `{storage_diffs: {B: {key: 0x1234}}}` (len = 1) are indistinguishable by the length check alone.

**Step 2 — `state_diff_commitment` is present in the header but never read by the state-diff sync.**

The stored header carries both fields: [3](#0-2) 

`parse_data_for_block` reads only `state_diff_length` (line 66) and never calls `calculate_state_diff_hash` to compare against `state_diff_commitment`. There is no such check anywhere in the state-diff sync path.

**Step 3 — `unite_state_diffs` accepts storage_diffs for any address without a deployment check.** [4](#0-3) 

**Step 4 — `append_state_diff` writes the fabricated storage entries unconditionally.** [5](#0-4) 

No deployment check exists before `write_storage_diffs`.

**Step 5 — `starknet_getStorageAt` returns the fabricated value.**

The RPC handler only falls back to `CONTRACT_NOT_FOUND` when the returned value is `Felt::default()` (zero): [6](#0-5) 

If the injected value is non-zero, the check is skipped entirely and the fabricated value is returned as the authoritative storage value.

---

### Impact Explanation

A syncing node that accepts a malicious peer's state diff will:
- Store fabricated `(contract_address, storage_key) → value` entries in the `contract_storage` table.
- Serve those values via `starknet_getStorageAt` with no indication of corruption.
- Have a stored `ThinStateDiff` that does not match the `state_diff_commitment` recorded in the block header — the commitment is never cross-checked after storage.

This satisfies: **High — RPC returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

Any p2p peer the victim connects to can mount this attack. No operator or validator privilege is required. The attacker only needs to:
1. Know the `state_diff_length` for a target block (available from the synced header).
2. Craft chunks whose total `len()` equals that value but whose content differs.

The header signature (`verify_block_signature`) is not called in the p2p header sync path either: [7](#0-6) 

So even the `state_diff_commitment` value in the header is unverified against the sequencer's key, making the attack reachable from any peer.

---

### Recommendation

In `parse_data_for_block`, after assembling the full `ThinStateDiff`, compute `calculate_state_diff_hash(&result)` and compare it against `header.state_diff_commitment`. Reject the peer (return `BadPeerError`) if they do not match. This is the same pattern already used in `apollo_committer`: [8](#0-7) 

Additionally, verify the block signature against the sequencer's public key in the header sync path to prevent a malicious peer from also forging the `state_diff_commitment` field in the header.

---

### Proof of Concept

Concrete substitution attack for a block whose real state diff is `{deployed_contracts: {addr_A: class_X}}` (len = 1, commitment = H):

1. Malicious peer sends one `ContractDiff` chunk: `{contract_address: addr_B, class_hash: None, storage_diffs: {key_K: 0xDEAD}}` — this also has `len() == 1`.
2. `parse_data_for_block` accepts it: `current_state_diff_len (1) == target_state_diff_len (1)`.
3. `unite_state_diffs` inserts `storage_diffs[addr_B][key_K] = 0xDEAD` with no deployment check.
4. `append_state_diff` writes `contract_storage[(addr_B, key_K, block_N)] = 0xDEAD`.
5. `starknet_getStorageAt(addr_B, key_K, block_N)` returns `0xDEAD` (non-zero → no `CONTRACT_NOT_FOUND` guard fires).
6. The header still records `state_diff_commitment = H` (the commitment of the real diff), which now disagrees with the stored state diff — but this is never detected.

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-104)
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
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L147-162)
```rust
            if !contract_diff.storage_diffs.is_empty() {
                match state_diff.storage_diffs.get_mut(&contract_diff.contract_address) {
                    Some(storage_diffs) => {
                        for (k, v) in contract_diff.storage_diffs {
                            if storage_diffs.insert(k, v).is_some() {
                                return Err(BadPeerError::ConflictingStateDiffParts);
                            }
                        }
                    }
                    None => {
                        state_diff
                            .storage_diffs
                            .insert(contract_diff.contract_address, contract_diff.storage_diffs);
                    }
                }
            }
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

**File:** crates/apollo_storage/src/header.rs (L99-107)
```rust
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

**File:** crates/apollo_storage/src/state/mod.rs (L540-545)
```rust
        write_storage_diffs(
            &thin_state_diff.storage_diffs,
            &self.txn,
            block_number,
            &storage_table,
        )?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L375-382)
```rust
        if res == Felt::default() && contract_address != BLOCK_HASH_TABLE_ADDRESS {
            // check if the contract exists
            txn.get_state_reader()
                .map_err(internal_server_error)?
                .get_class_hash_at(state_number, &contract_address)
                .map_err(internal_server_error)?
                .ok_or_else(|| ErrorObjectOwned::from(CONTRACT_NOT_FOUND))?;
        }
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L82-121)
```rust
    fn parse_data_for_block<'a>(
        signed_headers_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<SignedBlockHeader>,
        >,
        block_number: BlockNumber,
        _storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            // TODO(noamsp): investigate and remove this timeout.
            let maybe_signed_header =
                timeout(Duration::from_secs(15), signed_headers_response_manager.next())
                    .await
                    .ok()
                    .flatten()
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
            let Some(signed_block_header) = maybe_signed_header?.0 else {
                return Ok(None);
            };
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
        }
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
