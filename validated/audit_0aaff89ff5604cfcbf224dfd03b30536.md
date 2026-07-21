### Title
Missing State Diff Commitment Verification Allows Malicious Peer to Corrupt `compiled_class_hash` in Stored `ThinStateDiff` — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

`StateDiffStreamBuilder::parse_data_for_block` validates only the **length** of the received state diff against the stored header's `state_diff_length`, but never computes `calculate_state_diff_hash` on the assembled `ThinStateDiff` and compares it against the header's `state_diff_commitment`. A malicious p2p peer can therefore send a `StateDiffChunk::DeclaredClass` with an arbitrary `compiled_class_hash` (e.g. `Felt::ZERO`) for any declared Sierra class. The chunk passes every guard in `parse_data_for_block` and `write_to_storage`, and the corrupted value is persisted verbatim into the node's `ThinStateDiff` storage.

---

### Finding Description

**Entrypoint:** `StateDiffStreamBuilder::parse_data_for_block` [1](#0-0) 

The function reads `target_state_diff_len` from the stored block header: [2](#0-1) 

It then loops, calling `unite_state_diffs` for each received chunk, until `current_state_diff_len == target_state_diff_len`: [3](#0-2) 

Inside `unite_state_diffs`, a `StateDiffChunk::DeclaredClass` is handled by a single unconditional insert — the only guard is a duplicate-key check: [4](#0-3) 

The post-loop validation only checks deprecated declared classes for duplicates; it does not touch `class_hash_to_compiled_class_hash` values at all: [5](#0-4) 

`write_to_storage` then persists the assembled `ThinStateDiff` directly, with no further validation: [6](#0-5) 

**The missing guard:** The header carries both `state_diff_length` (checked) and `state_diff_commitment` (never checked). `calculate_state_diff_hash` chains every `(class_hash, compiled_class_hash)` pair into the Poseidon commitment: [7](#0-6) [8](#0-7) 

Because `parse_data_for_block` never calls `calculate_state_diff_hash` and never compares the result against `header.state_diff_commitment`, a peer-supplied `compiled_class_hash` of any value — including `Felt::ZERO` — passes all checks and is stored.

---

### Impact Explanation

**Stored state corruption:** The persisted `ThinStateDiff.class_hash_to_compiled_class_hash` contains the attacker-chosen value instead of the real CASM hash. Any subsequent call to `calculate_state_diff_hash` on the stored diff produces a commitment that diverges from the header's `state_diff_commitment`, silently breaking the commitment invariant for that block.

**Execution state reader returns wrong hash:** `get_compiled_class_hash` in the RPC execution state reader reads `compiled_class_hash` directly from the stored state diff: [9](#0-8) 

The blockifier receives the attacker-controlled value (e.g. `ZERO`) instead of the real CASM hash. This corrupts every RPC execution, fee estimation, simulation, or trace call that touches the affected class on this node — the blockifier's class-hash verification step operates on a wrong authoritative value.

---

### Likelihood Explanation

Any peer in the p2p network that the syncing node connects to can trigger this. No operator, validator, or sequencer privilege is required. The attack requires only that the peer be selected as a sync source for at least one block containing a class declaration. The corrupted state diff is stored permanently; subsequent honest peers cannot overwrite it because the state marker advances past the block.

---

### Recommendation

After assembling the full `ThinStateDiff` (after the length check at line 99), compute `calculate_state_diff_hash(&result)` and compare it against `header.state_diff_commitment`. If they differ, return `ParseDataError::BadPeer`. This is the symmetric check to what `apollo_committer` already does (optionally, behind `verify_state_diff_hash`): [10](#0-9) 

The same pattern should be mandatory (not optional) in the p2p sync path, since the peer is untrusted.

---

### Proof of Concept

```
Preconditions:
  - Block N header is stored with state_diff_length=1 and
    state_diff_commitment=C (the real Poseidon hash of {H -> real_casm_hash}).

Attack:
  1. Malicious peer sends one StateDiffChunk::DeclaredClass {
         class_hash: H,
         compiled_class_hash: CompiledClassHash(Felt::ZERO)   // wrong
     }
  2. parse_data_for_block:
       current_state_diff_len = 1 == target_state_diff_len = 1  → loop exits
       validate_deprecated_declared_classes_non_conflicting → Ok (no deprecated classes)
       → returns Ok(Some((ThinStateDiff { class_hash_to_compiled_class_hash: {H→ZERO}, .. }, N)))
  3. write_to_storage calls append_state_diff(N, ThinStateDiff{H→ZERO}) → committed.

Verification:
  stored_diff = storage.get_state_diff(N)
  assert stored_diff.class_hash_to_compiled_class_hash[H] == ZERO   // ✓ corrupted

  recomputed = calculate_state_diff_hash(&stored_diff)
  assert recomputed != header.state_diff_commitment                  // ✓ mismatch

  // RPC execution path:
  compiled_hash = get_compiled_class_hash(H)   // returns ZERO
  assert compiled_hash != real_casm_hash        // ✓ wrong value fed to blockifier
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L51-110)
```rust
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
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L164-172)
```rust
        StateDiffChunk::DeclaredClass(declared_class) => {
            if state_diff
                .class_hash_to_compiled_class_hash
                .insert(declared_class.class_hash, declared_class.compiled_class_hash)
                .is_some()
            {
                return Err(BadPeerError::ConflictingStateDiffParts);
            }
        }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L184-195)
```rust
fn validate_deprecated_declared_classes_non_conflicting(
    state_diff: &ThinStateDiff,
) -> Result<(), BadPeerError> {
    // TODO(shahak): Check if sorting is more efficient.
    if state_diff.deprecated_declared_classes.len()
        == state_diff.deprecated_declared_classes.iter().cloned().collect::<HashSet<_>>().len()
    {
        Ok(())
    } else {
        Err(BadPeerError::ConflictingStateDiffParts)
    }
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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L59-68)
```rust
fn chain_declared_classes(
    declared_classes: &IndexMap<ClassHash, CompiledClassHash>,
    mut hash_chain: HashChain,
) -> HashChain {
    hash_chain = hash_chain.chain(&declared_classes.len().into());
    for (class_hash, compiled_class_hash) in sorted_index_map(declared_classes) {
        hash_chain = hash_chain.chain(&class_hash).chain(&compiled_class_hash.0)
    }
    hash_chain
}
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L189-207)
```rust
        let state_diff = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_diff(block_number)
            .map_err(storage_err_to_state_err)?
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing state diff at block {block_number}."
            )))?;

        let compiled_class_hash = state_diff
            .class_hash_to_compiled_class_hash
            .get(&class_hash)
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing class declaration at block {block_number}, class \
                 {class_hash}."
            )))?;

        Ok(*compiled_class_hash)
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
