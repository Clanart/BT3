### Title
P2P Sync State Diff Accepts Wrong-Content Chunks Satisfying Only Global Length Count, Skipping Commitment Verification — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

`parse_data_for_block` in the P2P sync client terminates chunk collection using only a **global total-count check** (`current_state_diff_len == target_state_diff_len`) and never verifies the assembled `ThinStateDiff` against the `state_diff_commitment` stored in the trusted block header. A malicious peer can serve chunks whose individual lengths sum to the correct `state_diff_length` but whose content (storage values, class hashes, nonces) is entirely fabricated, causing the syncing node to persist a wrong state diff that diverges from the committed Poseidon hash.

---

### Finding Description

**H01 analog mapping:**

| Audius H01 | Sequencer analog |
|---|---|
| `maxDelegators` caps delegator slots per SP | `target_state_diff_len` caps chunk collection per block |
| `minDelegationAmount` enforced globally (total over all SPs), not per-SP | `state_diff_length` enforced globally (total entry count), not per-entry-content |
| Attacker fills slots with dust delegations that satisfy the global minimum | Attacker fills the length quota with wrong-content chunks that satisfy the global count |
| Result: honest delegators blocked | Result: wrong state diff written to storage |

**Root cause — `parse_data_for_block`:** [1](#0-0) 

The function reads `target_state_diff_len` from the stored header's `state_diff_length` field (trusted, part of the block hash). [2](#0-1) 

It then accumulates `current_state_diff_len += state_diff_chunk.len()` and stops when the total equals `target_state_diff_len`. The only post-assembly check is `validate_deprecated_declared_classes_non_conflicting`, which only detects duplicate deprecated class hashes. **The assembled `result` is never verified against `header.state_diff_commitment`.**

**`StateDiffChunk::len()` counts entries globally, not by content:** [3](#0-2) 

A `ContractDiff` with `class_hash: Some(X)`, `nonce: Some(Y)`, and `storage_diffs: {k: v}` contributes `len() = 3`. A different `ContractDiff` with `class_hash: Some(X')`, `nonce: Some(Y')`, and `storage_diffs: {k: v'}` also contributes `len() = 3`. Both satisfy the same global count quota, but produce completely different state.

**`ThinStateDiff::len()` is the authoritative length used in the block hash:** [4](#0-3) 

This is the value embedded in `concatenated_counts` inside the block hash: [5](#0-4) 

The `state_diff_commitment` (Poseidon hash of the state diff) is also embedded in the block hash: [6](#0-5) 

Both fields are trusted once the header is accepted. But `parse_data_for_block` only uses `state_diff_length` to gate termination; it never calls `calculate_state_diff_hash` on the assembled result and compares it to `header.state_diff_commitment`.

**The committer path has this check (gated by config), but the P2P sync path does not:** [7](#0-6) 

The `verify_state_diff_hash` flag exists for the committer but is absent from the P2P sync client's `parse_data_for_block`.

---

### Impact Explanation

A malicious P2P peer that serves a syncing node can inject an arbitrary `ThinStateDiff` for any block, provided the total entry count matches `state_diff_length`. The corrupted diff is written directly to storage via `append_state_diff`: [8](#0-7) 

Downstream consequences:
- **Wrong storage values, class hashes, nonces** stored for every affected block.
- **Wrong global state root** computed by the Patricia trie committer, diverging from the L1-verified root.
- **Wrong proof inputs** fed to SNOS and the transaction prover, producing invalid or unverifiable proofs.
- **Wrong RPC responses** for `starknet_getStorageAt`, `starknet_getClassAt`, `starknet_getNonce`, etc.

This matches the Critical impact: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result.*

---

### Likelihood Explanation

Any node that syncs via P2P (rather than central sync) is exposed. The attacker needs only to be a reachable P2P peer — no validator key, no privileged position. The attack is cheap: the attacker crafts chunks whose lengths sum to the correct `state_diff_length` but whose field values are arbitrary. No cryptographic forgery is required because the commitment check is simply absent.

---

### Recommendation

After the `while` loop in `parse_data_for_block`, before returning `Ok(Some((result, block_number)))`, read `state_diff_commitment` from the stored header and verify:

```rust
let header = storage_reader.begin_ro_txn()?.get_block_header(block_number)?.unwrap();
if let Some(expected_commitment) = header.state_diff_commitment {
    let actual_commitment = calculate_state_diff_hash(&result);
    if actual_commitment != expected_commitment {
        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffCommitment {
            block_number,
            expected: expected_commitment,
            actual: actual_commitment,
        }));
    }
}
```

This mirrors the existing `verify_state_diff_hash` logic already present in the committer path. [9](#0-8) 

---

### Proof of Concept

1. A syncing node receives a valid signed header for block N with `state_diff_length = 3` and `state_diff_commitment = H_real` (the Poseidon hash of the real state diff).
2. A malicious peer responds to the state diff query with a single `StateDiffChunk::ContractDiff` containing `class_hash: Some(EVIL_CLASS)`, `nonce: Some(EVIL_NONCE)`, `storage_diffs: {EVIL_KEY: EVIL_VALUE}` — `len() = 3`.
3. `parse_data_for_block` computes `current_state_diff_len = 3 == target_state_diff_len = 3` → passes.
4. `validate_deprecated_declared_classes_non_conflicting` passes (no deprecated classes).
5. `Ok(Some((result, block_number)))` is returned; `write_to_storage` calls `append_state_diff(N, evil_diff)`.
6. The node's storage now contains `EVIL_CLASS`, `EVIL_NONCE`, `EVIL_VALUE` for block N, while the header still records `state_diff_commitment = H_real`. The invariant `calculate_state_diff_hash(stored_diff) == header.state_diff_commitment` is broken. [10](#0-9)

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L33-35)
```rust
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-70)
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

**File:** crates/apollo_protobuf/src/sync.rs (L146-167)
```rust
impl StateDiffChunk {
    pub fn len(&self) -> usize {
        match self {
            StateDiffChunk::ContractDiff(contract_diff) => {
                let mut result = contract_diff.storage_diffs.len();
                if contract_diff.class_hash.is_some() {
                    result += 1;
                }
                if contract_diff.nonce.is_some() {
                    result += 1;
                }
                result
            }
            StateDiffChunk::DeclaredClass(_) => 1,
            StateDiffChunk::DeprecatedDeclaredClass(_) => 1,
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}
```

**File:** crates/starknet_api/src/state.rs (L109-121)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-262)
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

**File:** crates/apollo_committer/src/committer_test.rs (L315-326)
```rust
async fn verify_state_diff_hash_succeeds() {
    let mut committer = new_test_committer().await;
    committer.config.verify_state_diff_hash = true;
    let state_diff = get_state_diff(1);
    let state_diff_commitment = Some(calculate_state_diff_hash(&state_diff));
    let height = BlockNumber(0);
    committer
        .commit_block(CommitBlockRequest { state_diff, state_diff_commitment, height })
        .await
        .unwrap();
    assert_eq!(committer.offset, BlockNumber(height.0 + 1));
}
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L22-30)
```rust
static STARKNET_STATE_DIFF0: LazyLock<Felt> = LazyLock::new(|| {
    ascii_as_felt("STARKNET_STATE_DIFF0").expect("ascii_as_felt failed for 'STARKNET_STATE_DIFF0'")
});

/// Poseidon(
///     "STARKNET_STATE_DIFF0", deployed_contracts, declared_classes, deprecated_declared_classes,
///     1, 0, storage_diffs, nonces
/// ).
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
```
