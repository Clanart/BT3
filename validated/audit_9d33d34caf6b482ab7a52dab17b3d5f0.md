### Title
Missing State-Diff Hash Verification in P2P Sync Allows Malicious Peer to Inject Arbitrary State — (`File: crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

The P2P sync client reassembles `ThinStateDiff` from chunked peer messages and validates only the total entry count (`state_diff_length`) against the stored block header. It never verifies the assembled diff's Poseidon hash against the `state_diff_commitment` that is cryptographically bound into the block hash. A malicious peer can therefore send chunks whose entry count matches but whose content is entirely different, causing the syncing node to persist a wrong state diff, compute a wrong global state root, and serve wrong storage values, nonces, and class hashes over RPC.

---

### Finding Description

`StateDiffStreamBuilder::parse_data_for_block` in `crates/apollo_p2p_sync/src/client/state_diff.rs` drives the reassembly loop:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    ...
    .state_diff_length          // ← only the count is read
    .ok_or(...)?;

while current_state_diff_len < target_state_diff_len {
    ...
    current_state_diff_len += state_diff_chunk.len();
    unite_state_diffs(&mut result, state_diff_chunk)?;
}

if current_state_diff_len != target_state_diff_len {
    return Err(...WrongStateDiffLength...);
}
// ← no hash check here
validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))
``` [1](#0-0) 

The assembled `result` is then written directly to storage:

```rust
storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
``` [2](#0-1) 

The block header that was synced earlier carries a `state_diff_commitment` (a Poseidon hash over the full diff content) that is part of the signed `PartialBlockHashComponents` and feeds into the block hash. `calculate_state_diff_hash` hashes every deployed contract, storage entry, declared class, nonce, etc. in a canonical order: [3](#0-2) 

`state_diff_length` is only the total entry count (`state_diff.len()`), computed inside `calculate_block_commitments`: [4](#0-3) 

Because `parse_data_for_block` reads only `state_diff_length` and never reads or checks `state_diff_commitment`, a peer can send chunks whose entry count equals `target_state_diff_len` but whose storage values, nonces, or class hashes are completely different. The length guard passes, `unite_state_diffs` merges the fake chunks without complaint, and the corrupted diff is committed to storage.

The committer subsequently reads this diff from storage and applies it to the Patricia trie, producing a wrong global root: [5](#0-4) 

The `verify_state_diff_hash` guard in the committer is only active when the batcher itself produced the block (the `state_diff_commitment` is passed as `Some(...)` only in the `decision_reached` path). For blocks arriving via central/P2P sync the commitment is `None`, so the committer falls back to recomputing the hash from whatever diff it received — it does not compare against the header's authoritative commitment: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A syncing node that accepts a peer-injected state diff will:

1. Store wrong storage values, nonces, and class hashes in `apollo_storage` (via `append_state_diff`).
2. Compute and persist a wrong global state root in the Patricia trie.
3. Return wrong values from every RPC call that reads state: `starknet_getStorageAt`, `starknet_getNonce`, `starknet_getClassHashAt`, `starknet_getStateUpdate`, fee estimation, simulation, and tracing.
4. Use the wrong state as the base for subsequent block execution, compounding the divergence.

This matches the **High** impact: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value*, and potentially **Critical**: *Wrong state, receipt, event, class hash, or storage value from blockifier/syscall/execution logic*.

---

### Likelihood Explanation

Any node that participates in the P2P network can act as a peer to a syncing node. No special privilege is required. The attacker only needs to relay authentic signed headers (which they cannot forge but can relay verbatim) and substitute the state diff chunks. The length of the real diff is observable from the header's `state_diff_length` field, making it straightforward to craft a replacement diff of the same length with different values.

---

### Recommendation

After the reassembly loop completes and before writing to storage, compute the Poseidon hash of the assembled diff and compare it against the `state_diff_commitment` stored in the block header:

```rust
let header = storage_reader.begin_ro_txn()?.get_block_header(block_number)?...;
let expected_commitment = header.state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage { ... })?;
let actual_commitment = calculate_state_diff_hash(&result);
if actual_commitment != expected_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffHashMismatch { ... }));
}
```

This mirrors the fix applied to `Rv32HintStoreChip`: just as the zkVM fix forces `is_buffer_start` to be set so the execution bridge constrains the write, this fix forces the assembled diff to match the commitment that is already bound into the signed block header, closing the gap between the length check and the content check.

---

### Proof of Concept

1. Victim node starts P2P sync from block 0.
2. Attacker peer sends the authentic `SignedBlockHeader` for block N (relayed from the real chain). The header contains `state_diff_length = K` and `state_diff_commitment = H_real`.
3. Attacker constructs a fake `ThinStateDiff` of length K (same entry count) but with different storage values — e.g., setting the STRK balance of every account to zero.
4. Attacker splits the fake diff into `StateDiffChunk`s via the same `split_thin_state_diff` logic and sends them to the victim.
5. `parse_data_for_block` counts `current_state_diff_len == K == target_state_diff_len`, passes all checks, and returns the fake diff.
6. `write_to_storage` calls `append_state_diff` with the fake diff.
7. The committer applies the fake diff to the Patricia trie, producing a wrong root.
8. `starknet_getStorageAt` on any account now returns the attacker-chosen value. [8](#0-7) [9](#0-8)

### Citations

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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L50-121)
```rust
    #[latency_histogram("p2p_sync_state_diff_parse_data_for_block_latency_seconds", true)]
    fn parse_data_for_block<'a>(
        state_diff_chunks_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<StateDiffChunk>,
        >,
        block_number: BlockNumber,
        storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
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

    fn get_start_block_number(storage_reader: &StorageReader) -> Result<BlockNumber, StorageError> {
        storage_reader.begin_ro_txn()?.get_state_marker()
    }

    fn convert_sync_block_to_block_data(
        block_number: BlockNumber,
        sync_block: SyncBlock,
    ) -> (ThinStateDiff, BlockNumber) {
        (sync_block.state_diff, block_number)
    }
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L29-42)
```rust
/// ).
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-327)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );

    let n_txs = transactions_data.len();
    let n_events = event_leaf_elements.len();
    let state_diff_length = state_diff.len();
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

**File:** crates/apollo_committer/src/committer.rs (L207-238)
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
        ]);
        info!(
            "For block number {height}, writing filled forest to storage with metadata: \
             {metadata:?}"
        );
        block_measurements.start_measurement(Action::Write);
        let n_write_entries = self
            .forest_storage
            .write_with_metadata(&filled_forest, metadata)
            .await
            .map_err(|err| self.map_internal_error(err))?;
        block_measurements.attempt_to_stop_measurement(Action::Write, n_write_entries).ok();
        block_measurements.attempt_to_stop_measurement(Action::EndToEnd, 0).ok();
        update_metrics(height, &block_measurements.block_measurement);
        self.update_offset(next_offset);
        Ok(CommitBlockResponse { global_root })
```

**File:** crates/apollo_committer_config/src/config.rs (L17-55)
```rust
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Validate)]
pub struct CommitterConfig<C: StorageConfigTrait> {
    pub reader_config: ReaderConfig,
    pub db_path: PathBuf,
    pub storage_config: C,
    pub verify_state_diff_hash: bool,
}

impl<C: StorageConfigTrait> SerializeConfig for CommitterConfig<C> {
    fn dump(&self) -> BTreeMap<ParamPath, SerializedParam> {
        let mut dump = BTreeMap::from_iter([
            ser_param(
                "verify_state_diff_hash",
                &self.verify_state_diff_hash,
                "If true, the committer will verify the state diff hash.",
                ParamPrivacyInput::Public,
            ),
            ser_param(
                "db_path",
                &self.db_path,
                "Path to the committer storage directory.",
                ParamPrivacyInput::Public,
            ),
        ]);
        dump.extend(prepend_sub_config_name(self.reader_config.dump(), "reader_config"));
        dump.extend(prepend_sub_config_name(self.storage_config.dump(), "storage_config"));
        dump
    }
}

impl<C: StorageConfigTrait> Default for CommitterConfig<C> {
    fn default() -> Self {
        Self {
            reader_config: ReaderConfig::default(),
            db_path: "/data/committer".into(),
            storage_config: C::default(),
            verify_state_diff_hash: true,
        }
    }
```

**File:** crates/apollo_p2p_sync/src/server/mod.rs (L372-408)
```rust
pub fn split_thin_state_diff(thin_state_diff: ThinStateDiff) -> Vec<StateDiffChunk> {
    let mut state_diff_chunks = Vec::new();
    #[cfg(not(test))]
    let mut contract_addresses = std::collections::HashSet::new();
    #[cfg(test)]
    let mut contract_addresses = std::collections::BTreeSet::new();

    contract_addresses.extend(
        thin_state_diff
            .deployed_contracts
            .keys()
            .chain(thin_state_diff.nonces.keys())
            .chain(thin_state_diff.storage_diffs.keys()),
    );
    for contract_address in contract_addresses {
        let class_hash = thin_state_diff.deployed_contracts.get(&contract_address).cloned();
        let storage_diffs =
            thin_state_diff.storage_diffs.get(&contract_address).cloned().unwrap_or_default();
        let nonce = thin_state_diff.nonces.get(&contract_address).cloned();
        state_diff_chunks.push(StateDiffChunk::ContractDiff(ContractDiff {
            contract_address,
            class_hash,
            nonce,
            storage_diffs,
        }));
    }

    for (class_hash, compiled_class_hash) in thin_state_diff.class_hash_to_compiled_class_hash {
        state_diff_chunks
            .push(StateDiffChunk::DeclaredClass(DeclaredClass { class_hash, compiled_class_hash }));
    }

    for class_hash in thin_state_diff.deprecated_declared_classes {
        state_diff_chunks
            .push(StateDiffChunk::DeprecatedDeclaredClass(DeprecatedDeclaredClass { class_hash }));
    }
    state_diff_chunks
```
