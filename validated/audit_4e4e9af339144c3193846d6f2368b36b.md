### Title
Missing `state_diff_commitment` Hash Verification in P2P Sync State Diff Assembly Allows Malicious Peer to Inject Wrong State — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

`parse_data_for_block` in the P2P sync client uses `state_diff_length` from the stored block header as the sole termination and correctness condition when assembling state diff chunks from a peer. The `state_diff_commitment` Poseidon hash, which is present in the same header and cryptographically binds the correct state diff, is never checked against the assembled result. A malicious peer can send chunks whose lengths sum to the expected value but whose contents (storage values, class hashes, nonces) are entirely fabricated, causing the syncing node to permanently store wrong state.

---

### Finding Description

**Root cause — `parse_data_for_block`** [1](#0-0) 

The function reads `target_state_diff_len` from the stored header:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    ...
    .state_diff_length
    .ok_or(...)?;
```

It then accumulates chunks:

```rust
current_state_diff_len += state_diff_chunk.len();
unite_state_diffs(&mut result, state_diff_chunk)?;
```

and exits when `current_state_diff_len == target_state_diff_len`, followed only by a duplicate-deprecated-class check. The assembled `result` is written to storage with no comparison against `state_diff_commitment`.

**`StateDiffChunk::len()` definition** [2](#0-1) 

For a `ContractDiff`, `len()` counts `storage_diffs.len() + (class_hash.is_some() as usize) + (nonce.is_some() as usize)`. A peer can craft any combination of addresses, keys, and values that sums to the target length.

**`ThinStateDiff::len()` definition** [3](#0-2) 

The docstring explicitly states: *"This has the same value as `state_diff_length` in the corresponding `BlockHeader`."* The length is a structural count, not a cryptographic commitment.

**`state_diff_commitment` is present but unused** [4](#0-3) 

The same `get_block_header` call that returns `state_diff_length` also returns `state_diff_commitment: Option<StateDiffCommitment>`. The function reads only the former.

**Write path has no hash check** [5](#0-4) 

`write_to_storage` calls `append_state_diff` directly with no verification.

**Contrast: the sequencer's own committer does verify** [6](#0-5) 

When the sequencer commits its own blocks, `commit_block_inner` optionally calls `calculate_state_diff_hash` and compares it against the provided commitment. The P2P sync path has no equivalent guard.

---

### Impact Explanation

A syncing node that accepts a fabricated state diff will store wrong values in every state table (`contract_storage`, `deployed_contracts`, `nonces`, `compiled_class_hash`). Downstream effects:

- `starknet_getStorageAt`, `starknet_getClassHashAt`, `starknet_getNonce` return wrong authoritative values — **High: RPC returns wrong value**.
- Fee estimation and simulation execute against wrong state — **High: wrong fee/simulation result**.
- If the wrong state diff is applied to the Patricia trie, the computed global root diverges from the header's `state_root`, producing a wrong block hash — **Critical: wrong state root / block hash**.

The corrupted state persists in storage until the node is resynced.

---

### Likelihood Explanation

Medium. The attacker must be a P2P peer from which the syncing node accepts state diff responses. In a permissionless P2P network any node can occupy this role. The attack requires no special privilege: the peer simply sends well-formed `StateDiffChunk` messages whose lengths sum to `target_state_diff_len` but whose field values are attacker-controlled. No signature or proof is required for the chunk stream itself — only the block header is signed, and the header is not re-checked against the assembled diff.

---

### Recommendation

After the assembly loop, read `state_diff_commitment` from the same header and verify:

```rust
let header = storage_reader.begin_ro_txn()?.get_block_header(block_number)?...;
let target_state_diff_len = header.state_diff_length.ok_or(...)?;
// ... assembly loop ...
if let Some(expected_commitment) = header.state_diff_commitment {
    let calculated = calculate_state_diff_hash(&result);
    if calculated != expected_commitment {
        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffCommitment {
            expected: expected_commitment,
            calculated,
        }));
    }
}
```

`calculate_state_diff_hash` is already used in the committer path and in the `state_diff_hash_calculator` binary, so no new dependency is needed. [6](#0-5) 

---

### Proof of Concept

1. Honest sequencer produces block N with state diff `D` (e.g., storage slot `(addr, key) = value_correct`). The signed header carries `state_diff_length = L` and `state_diff_commitment = H(D)`.

2. Syncing node receives and stores the signed header. `state_diff_length = L` and `state_diff_commitment = H(D)` are now in local storage.

3. Malicious peer constructs a fabricated diff `D'` where `(addr, key) = value_wrong` but `D'.len() == L` (trivially achievable by choosing the same structural shape with different values).

4. Malicious peer sends `D'` as a stream of `StateDiffChunk` messages. `parse_data_for_block` accumulates them: `current_state_diff_len` reaches `L`, no conflicts are detected, `validate_deprecated_declared_classes_non_conflicting` passes.

5. `write_to_storage` calls `append_state_diff(block_number, D')`. The wrong diff is committed.

6. Any subsequent `starknet_getStorageAt(addr, key, block_number)` returns `value_wrong`. Fee estimation for contracts reading `(addr, key)` uses `value_wrong`. The global root computed from `D'` diverges from the header's `state_root`, breaking block hash consistency for all downstream blocks. [7](#0-6)

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

**File:** crates/apollo_storage/src/header.rs (L98-107)
```rust
    /// The state diff commitment, if available.
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
