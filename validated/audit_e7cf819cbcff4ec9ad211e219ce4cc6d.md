### Title
`From<CommitmentStateDiff> for ThinStateDiff` Silently Drops `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and `concatenated_counts` in Block Hash - (`File: crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The conversion `From<CommitmentStateDiff> for ThinStateDiff` unconditionally sets `deprecated_declared_classes: Vec::new()`. When a block contains a deprecated (Cairo 0) class declaration, the `ThinStateDiff` fed into `calculate_block_commitments` is missing that field. This causes two paired outputs — `state_diff_commitment` (Poseidon hash) and `concatenated_counts` (which encodes `state_diff_length`) — to be computed over an incomplete diff. Both values are embedded in `PartialBlockHashComponents` and propagated into the final block hash, the consensus `PartialBlockHash`, and the P2P-synced block header, all of which will be wrong for any block that declares a deprecated class.

---

### Finding Description

**Root cause — the silent drop:** [1](#0-0) 

```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff.class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),   // ← always empty
        }
    }
}
```

`CommitmentStateDiff` has no `deprecated_declared_classes` field because it is built from `StateMaps`, which tracks deprecated declarations in `declared_contracts` but that map is ignored during the conversion: [2](#0-1) 

The resulting `ThinStateDiff` is passed directly to `calculate_block_commitments` inside `BlockExecutionArtifacts::new`: [3](#0-2) 

**Two paired outputs are corrupted:**

1. **`state_diff_commitment`** — `calculate_state_diff_hash` chains `deprecated_declared_classes` into the Poseidon hash. With an empty list it chains only the count `0`, omitting all deprecated class hashes: [4](#0-3) 

2. **`concatenated_counts` / `state_diff_length`** — `ThinStateDiff::len()` counts `deprecated_declared_classes.len()`. With an empty list the length is under-counted, so `concat_counts` encodes a smaller-than-actual `state_diff_length` into the packed felt: [5](#0-4) [6](#0-5) 

Both corrupted values are stored in `BlockHeaderCommitments` and then in `PartialBlockHashComponents`: [7](#0-6) [8](#0-7) 

**Downstream propagation:**

- `PartialBlockHashComponents` is persisted to storage and used to compute the final block hash and the consensus `PartialBlockHash`: [9](#0-8) 

- The `state_diff_commitment` is passed to the committer. Because the committer also receives the same truncated `ThinStateDiff`, the internal hash-verification check passes — masking the corruption: [10](#0-9) 

- The P2P sync server serves `state_diff_length` from the stored header. Syncing peers use this value to know how many chunks to consume: [11](#0-10) 

  A header with an under-counted `state_diff_length` will cause the sync client to stop consuming chunks before all deprecated-class chunks are received, producing a truncated state diff on the peer.

---

### Impact Explanation

Any block that contains at least one `DeclareV0`/`DeclareV1` transaction (deprecated Cairo 0 class declaration) will have:

- A wrong `state_diff_commitment` in its header (Poseidon hash computed over an incomplete diff).
- A wrong `concatenated_counts` field (under-counted `state_diff_length`).
- A wrong block hash and wrong consensus `PartialBlockHash`.
- Syncing peers that reconstruct a truncated state diff, diverging from the sequencer's own storage.

This matches **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input** and **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**.

---

### Likelihood Explanation

Deprecated class declarations (`DeclareV0`/`DeclareV1`) are valid, unprivileged transactions that any user can submit. No special privilege is required. The bug is triggered by the ordinary execution of any such transaction. On networks that still accept Cairo 0 deployments the condition is routinely met.

---

### Recommendation

`CommitmentStateDiff` must be extended to carry `deprecated_declared_classes`, populated from `StateMaps.declared_contracts` (entries where the value is `false`/deprecated). The `From<CommitmentStateDiff> for ThinStateDiff` conversion must then propagate that field instead of hardcoding `Vec::new()`. The `TODO(AlonH)` comment acknowledges the structural debt; resolving it is the fix.

---

### Proof of Concept

1. Submit a `DeclareV0` transaction declaring a Cairo 0 class hash `C`.
2. The blockifier records `C` in `StateMaps.declared_contracts` with `is_deprecated = true`.
3. `CommitmentStateDiff::from(state_maps)` ignores `declared_contracts`; `CommitmentStateDiff` has no entry for `C`.
4. `ThinStateDiff::from(commitment_state_diff)` sets `deprecated_declared_classes: Vec::new()`.
5. `calculate_block_commitments` computes `state_diff_commitment = Poseidon(... | 0 deprecated classes | ...)` — missing `C`.
6. `concat_counts` encodes `state_diff_length` without counting `C` — one unit too small.
7. The block header is stored with these wrong values; the block hash is derived from them.
8. A syncing peer reads `state_diff_length = N` (correct value would be `N+1`), consumes exactly `N` chunks, and never receives the deprecated-class chunk for `C` — its state diff is missing `C`.
9. The peer's Patricia trie root diverges from the sequencer's root, and any proof or RPC call against that peer returns wrong state.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L723-731)
```rust
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
        }
    }
```

**File:** crates/blockifier/src/state/cached_state.rs (L756-768)
```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff
                .class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
}
```

**File:** crates/apollo_batcher/src/block_builder.rs (L170-176)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
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

**File:** crates/starknet_api/src/state.rs (L110-122)
```rust
    /// This has the same value as `state_diff_length` in the corresponding `BlockHeader`.
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
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
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L351-357)
```rust
    let commitments = BlockHeaderCommitments {
        transaction_commitment,
        event_commitment,
        receipt_commitment,
        state_diff_commitment,
        concatenated_counts,
    };
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L527-566)
```rust
    fn finalize_commitment_output<R: BatcherStorageReader + ?Sized>(
        storage_reader: Arc<R>,
        CommitmentTaskOutput {
            response: CommitBlockResponse { global_root },
            height,
            #[cfg(feature = "os_input")]
            state_commitment_infos,
        }: CommitmentTaskOutput,
        should_finalize_block_hash: bool,
    ) -> CommitmentManagerResult<FinalBlockCommitment> {
        let block_hash = match should_finalize_block_hash {
            false => {
                debug!("Finalized commitment for block {height} without calculating block hash.");
                None
            }
            true => {
                debug!("Finalizing commitment for block {height} with calculating block hash.");
                let (previous_block_hash, partial_block_hash_components) =
                    storage_reader.get_parent_hash_and_partial_block_hash_components(height)?;
                let previous_block_hash = previous_block_hash.ok_or_else(|| {
                    CommitmentManagerError::MissingBlockHash(height.prev().expect(
                        "For the genesis block, the block hash is constant and should not be \
                         fetched from storage.",
                    ))
                })?;
                let partial_block_hash_components = partial_block_hash_components
                    .ok_or(CommitmentManagerError::MissingPartialBlockHashComponents(height))?;
                debug!(
                    "Calculating block hash for block {height} with partial block hash \
                     components: {partial_block_hash_components:?}"
                );
                debug!(
                    "Global root: {global_root:?}, previous block hash: {previous_block_hash:?}"
                );
                Some(calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?)
            }
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
