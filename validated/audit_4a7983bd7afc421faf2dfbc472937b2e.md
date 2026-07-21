Looking at the full code path carefully:

The code path is now fully traced. Here is the verdict:

---

### Title
Missing `state_diff_commitment` Validation in P2P Sync State Diff Assembly — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

`StateDiffStreamBuilder::parse_data_for_block` assembles a `ThinStateDiff` from peer-supplied `StateDiffChunk` messages and writes it directly to storage. It validates only the **count** of state diff entries against `header.state_diff_length`, but never computes or compares `calculate_state_diff_hash(assembled_diff)` against `header.state_diff_commitment`. A malicious peer can supply structurally valid chunks (no duplicate keys, correct total length) whose storage key-value pairs differ from the committed values, causing the syncing node to persist and serve wrong storage state.

### Finding Description

**Entrypoint:** `parse_data_for_block` in `crates/apollo_p2p_sync/src/client/state_diff.rs`

The function reads `target_state_diff_len` from the stored header: [1](#0-0) 

It loops, accumulating chunks, until the count matches: [2](#0-1) 

The only post-assembly check is for duplicate deprecated class hashes: [3](#0-2) 

`unite_state_diffs` rejects duplicate **keys** within the same contract, but freely merges any key-value pairs across multiple `ContractDiff` messages for the same address: [4](#0-3) 

`calculate_state_diff_hash` is **never called** anywhere in the `apollo_p2p_sync` crate:

```
grep "calculate_state_diff_hash" crates/apollo_p2p_sync/**/*.rs
→ No matches found.
```

The assembled `ThinStateDiff` is written directly to storage with no commitment check: [5](#0-4) 

`append_state_diff` writes storage key-value pairs verbatim into the `contract_storage` table: [6](#0-5) 

**Attack construction:** Suppose the committed state diff for block N has `state_diff_length = 2`, representing two storage writes `(addr A, key K1 → V1)` and `(addr A, key K2 → V2)`. A malicious peer sends:
- `ContractDiff { contract_address: A, storage_diffs: { K1 → EVIL1 } }` (len = 1)
- `ContractDiff { contract_address: A, storage_diffs: { K2 → EVIL2 } }` (len = 1)

Total length = 2 = `target_state_diff_len`. No duplicate keys. `unite_state_diffs` merges them. The assembled diff has `{ K1 → EVIL1, K2 → EVIL2 }`. `calculate_state_diff_hash` of this diff ≠ `header.state_diff_commitment`, but this is never checked. The diff is stored.

**Header commitment is also unvalidated:** The header sync stores peer-supplied headers without verifying the block signature against the block hash (which commits `state_diff_commitment`): [7](#0-6) 

Only block number ordering and signature count are checked. So `state_diff_commitment` in the stored header is itself peer-controlled, making the missing check doubly exploitable.

**Contrast with the committer path:** The sequencer's committer does enforce this invariant when `verify_state_diff_hash` is enabled: [8](#0-7) 

No equivalent guard exists in the p2p sync path.

### Impact Explanation

A syncing node that accepts state diff data from a malicious peer stores wrong storage key-value pairs in its `contract_storage` table. Every subsequent `get_storage_at` call for the affected contract and keys returns the attacker-chosen value. RPC endpoints (`starknet_getStorageAt`, `starknet_call`, fee estimation, simulation) serve these wrong values as authoritative chain state. If the node is also used as a proof provider or SNOS input source, it supplies wrong storage leaves to the Patricia trie, producing wrong global roots and wrong storage proofs.

### Likelihood Explanation

Any peer on the p2p network can respond to a state diff query. The attack requires knowing the `state_diff_length` for a target block (publicly available from the header) and sending chunks with the correct total count but wrong values. No cryptographic material or privileged access is needed.

### Recommendation

After assembling the full `ThinStateDiff` and before returning `Ok(Some(...))`, compute and compare the commitment:

```rust
use starknet_api::block_hash::state_diff_hash::calculate_state_diff_hash;

let computed = calculate_state_diff_hash(&result);
let header_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("...")
    .state_diff_commitment
    .ok_or(...)?;
if computed != header_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffCommitmentMismatch { ... }));
}
```

This mirrors the guard already present in `apollo_committer`. [9](#0-8) 

### Proof of Concept

```rust
// Pseudocode property test
let valid_diff = ThinStateDiff {
    storage_diffs: indexmap! {
        addr_A => indexmap! { key_K1 => felt!(1), key_K2 => felt!(2) }
    },
    ..Default::default()
};
let commitment = calculate_state_diff_hash(&valid_diff); // C
// Store header with state_diff_length=2, state_diff_commitment=C

// Malicious peer sends:
let chunks = vec![
    StateDiffChunk::ContractDiff(ContractDiff {
        contract_address: addr_A,
        storage_diffs: indexmap! { key_K1 => felt!(0xDEAD) },
        ..Default::default()
    }),
    StateDiffChunk::ContractDiff(ContractDiff {
        contract_address: addr_A,
        storage_diffs: indexmap! { key_K2 => felt!(0xBEEF) },
        ..Default::default()
    }),
];
// parse_data_for_block accepts both (total len=2, no duplicate keys)
// assembled diff has wrong values
let assembled = parse_data_for_block(chunks, block_number, storage_reader).await.unwrap();
assert_ne!(calculate_state_diff_hash(&assembled.0), commitment); // PASSES — mismatch undetected
```

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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L72-97)
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
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L106-107)
```rust
            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
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

**File:** crates/apollo_storage/src/state/mod.rs (L629-634)
```rust
        write_storage_diffs(
            &thin_state_diff.storage_diffs,
            inner_txn,
            block_number,
            &storage_table,
        )?;
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L104-120)
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
