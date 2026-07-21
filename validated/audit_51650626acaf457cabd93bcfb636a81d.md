### Title
Missing Cross-Category Exclusivity Check in P2P State Diff Assembly Allows a Class Hash to Appear in Both `class_hash_to_compiled_class_hash` and `deprecated_declared_classes`, Corrupting Patricia Trie State and Class Selection - (File: `crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

The `unite_state_diffs` function in the P2P sync state diff stream builder checks for duplicate insertions within `class_hash_to_compiled_class_hash` (for `DeclaredClass` chunks) but performs no cross-category check when a `DeprecatedDeclaredClass` chunk arrives. A malicious peer can therefore send the same class hash as both a `DeclaredClass` and a `DeprecatedDeclaredClass` chunk, producing a `ThinStateDiff` that violates the documented invariant "Class hashes of declared_classes and deprecated_declared_classes are exclusive." The corrupted diff is written directly to storage, registering the class hash in both `declared_classes_block_table` and `deprecated_declared_classes_block_table`, corrupting the Patricia trie and causing the wrong compiled class to be selected for execution.

---

### Finding Description

**Invariant stated but not enforced:**

`StateDiff` carries a documented invariant with an explicit TODO:

```
// Invariant: Class hashes of declared_classes and deprecated_declared_classes are exclusive.
// TODO(yair): Enforce this invariant.
``` [1](#0-0) 

**Path 1 — `DeclaredClass` chunk (check present):**

When a `DeclaredClass` chunk arrives, `unite_state_diffs` inserts into `class_hash_to_compiled_class_hash` and returns `ConflictingStateDiffParts` if the key already exists:

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
``` [2](#0-1) 

**Path 2 — `DeprecatedDeclaredClass` chunk (check absent):**

When a `DeprecatedDeclaredClass` chunk arrives, the class hash is unconditionally pushed to `deprecated_declared_classes` with no check against `class_hash_to_compiled_class_hash`:

```rust
StateDiffChunk::DeprecatedDeclaredClass(deprecated_declared_class) => {
    state_diff.deprecated_declared_classes.push(deprecated_declared_class.class_hash);
}
``` [3](#0-2) 

**Post-assembly validation is also insufficient:**

`validate_deprecated_declared_classes_non_conflicting` only checks for duplicates *within* `deprecated_declared_classes` itself; it never cross-checks against `class_hash_to_compiled_class_hash`:

```rust
fn validate_deprecated_declared_classes_non_conflicting(
    state_diff: &ThinStateDiff,
) -> Result<(), BadPeerError> {
    if state_diff.deprecated_declared_classes.len()
        == state_diff.deprecated_declared_classes.iter().cloned().collect::<HashSet<_>>().len()
    {
        Ok(())
    } else {
        Err(BadPeerError::ConflictingStateDiffParts)
    }
}
``` [4](#0-3) 

**Corrupted diff is written to storage without further validation:**

The assembled `ThinStateDiff` is passed directly to `append_state_diff`:

```rust
storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
``` [5](#0-4) 

`append_state_diff` writes class hash X to `declared_classes_block_table` (Sierra path) and also to `deprecated_declared_classes_block_table` (deprecated path), with no exclusivity guard: [6](#0-5) 

---

### Impact Explanation

A class hash X registered in both `declared_classes_block_table` and `deprecated_declared_classes_block_table` corrupts the Patricia trie. When the blockifier later resolves class X for execution, it may retrieve the wrong compiled class (Cairo 0 deprecated bytecode instead of Sierra/CASM, or vice versa), directly matching the critical impact: **"Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution."**

Additionally, `calculate_state_diff_hash` chains both `class_hash_to_compiled_class_hash` (containing X) and `deprecated_declared_classes` (also containing X) into the Poseidon hash, producing a `StateDiffCommitment` that double-counts X. The `ThinStateDiff::len()` likewise double-counts X, corrupting the `state_diff_length` field embedded in `packed_lengths` of the block hash: [7](#0-6) [8](#0-7) 

---

### Likelihood Explanation

The attack requires a malicious P2P peer. The syncing node connects to peers and accepts state diff chunks up to the `target_state_diff_len` recorded in the already-stored block header. A peer that controls one slot for a `DeclaredClass` and one slot for a `DeprecatedDeclaredClass` (both summing to the expected length) can craft this payload with no special privilege. The length check is the only gate, and it is satisfied by the crafted pair. [9](#0-8) 

---

### Recommendation

In `unite_state_diffs`, when processing a `DeprecatedDeclaredClass` chunk, add a cross-category check:

```rust
StateDiffChunk::DeprecatedDeclaredClass(deprecated_declared_class) => {
    if state_diff
        .class_hash_to_compiled_class_hash
        .contains_key(&deprecated_declared_class.class_hash)
    {
        return Err(BadPeerError::ConflictingStateDiffParts);
    }
    state_diff.deprecated_declared_classes.push(deprecated_declared_class.class_hash);
}
```

Symmetrically, when processing a `DeclaredClass` chunk, also check that the class hash is not already in `deprecated_declared_classes`. Additionally, enforce the invariant at the `StateDiff` / `ThinStateDiff` construction level (resolving the existing `TODO(yair)`) so that all code paths — P2P sync, central sync, and native blockifier — are protected. [1](#0-0) 

---

### Proof of Concept

1. A syncing node stores a block header with `state_diff_length = 2` (e.g., one declared Sierra class and one deprecated class with different hashes, as produced by the honest proposer).
2. A malicious peer responds to the state diff request with exactly two chunks:
   - `StateDiffChunk::DeclaredClass { class_hash: X, compiled_class_hash: C }` (len = 1)
   - `StateDiffChunk::DeprecatedDeclaredClass { class_hash: X }` (len = 1)
3. `current_state_diff_len` reaches `target_state_diff_len = 2`; the length check passes.
4. `validate_deprecated_declared_classes_non_conflicting` passes because `deprecated_declared_classes = [X]` has no internal duplicates.
5. The assembled `ThinStateDiff` has `class_hash_to_compiled_class_hash = {X: C}` and `deprecated_declared_classes = [X]`.
6. `append_state_diff` writes X to both `declared_classes_block_table` and `deprecated_declared_classes_block_table`.
7. A subsequent transaction invoking class X causes the blockifier to resolve the wrong compiled class (Cairo 0 deprecated bytecode vs. Sierra/CASM), producing wrong execution output.
8. `calculate_state_diff_hash` chains X in both the declared and deprecated sections, producing a `StateDiffCommitment` that diverges from the honest block's commitment. [10](#0-9) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_api/src/state.rs (L51-53)
```rust
// Invariant: Addresses are strictly increasing.
// Invariant: Class hashes of declared_classes and deprecated_declared_classes are exclusive.
// TODO(yair): Enforce this invariant.
```

**File:** crates/starknet_api/src/state.rs (L111-122)
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L34-34)
```rust
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L51-122)
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

    fn get_start_block_number(storage_reader: &StorageReader) -> Result<BlockNumber, StorageError> {
        storage_reader.begin_ro_txn()?.get_state_marker()
    }

    fn convert_sync_block_to_block_data(
        block_number: BlockNumber,
        sync_block: SyncBlock,
    ) -> (ThinStateDiff, BlockNumber) {
        (sync_block.state_diff, block_number)
    }
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L173-175)
```rust
        StateDiffChunk::DeprecatedDeclaredClass(deprecated_declared_class) => {
            state_diff.deprecated_declared_classes.push(deprecated_declared_class.class_hash);
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

**File:** crates/apollo_storage/src/state/mod.rs (L638-662)
```rust
        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(inner_txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(inner_txn, class_hash, &block_number)?;
            }
        }

        write_compiled_class_hashes(
            &thin_state_diff.class_hash_to_compiled_class_hash,
            inner_txn,
            block_number,
            &compiled_class_hash_table,
        )?;

        for class_hash in thin_state_diff.deprecated_declared_classes.iter() {
            // Cairo0 classes can be declared in different blocks. The first block to declare the
            // class is recorded here.
            if deprecated_declared_classes_block_table.get(inner_txn, class_hash)?.is_none() {
                deprecated_declared_classes_block_table.insert(
                    inner_txn,
                    class_hash,
                    &block_number,
                )?;
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
