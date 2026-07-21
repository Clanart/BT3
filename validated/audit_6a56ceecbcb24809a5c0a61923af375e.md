### Title
Missing `state_diff_commitment` Verification in P2P Sync Allows Malicious Peer to Corrupt `class_hash_to_compiled_class_hash` Storage — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

`StateDiffStreamBuilder::parse_data_for_block` validates only the **count** (`state_diff_length`) of incoming state diff chunks against the block header, but never verifies the **content** against the `state_diff_commitment` Poseidon hash also present in that header. A malicious p2p peer can supply a state diff with the correct total length but attacker-chosen `class_hash_to_compiled_class_hash` entries. That forged diff is written directly to storage. `ClassStreamBuilder::parse_data_for_block` then reads it back and uses it as the sole acceptance filter for incoming Sierra class bodies, so the attacker can install arbitrary class-hash mappings in the node's storage.

---

### Finding Description

**State diff sync — no commitment check**

`StateDiffStreamBuilder::parse_data_for_block` reads only `state_diff_length` from the stored block header: [1](#0-0) 

It accumulates chunks until the count matches, then calls `validate_deprecated_declared_classes_non_conflicting` and returns. There is no call to `calculate_state_diff_hash` and no comparison against `block_header.state_diff_commitment`. A `grep` for either symbol in `state_diff.rs` returns zero hits.

The assembled `ThinStateDiff` is then written unconditionally: [2](#0-1) 

**Header sync — block hash also unverified**

`HeaderStreamBuilder::parse_data_for_block` checks only block-number ordering and signature-vector length: [3](#0-2) 

The `block_hash` field is accepted as-is; the `state_diff_commitment` field inside the header is stored but never cross-checked against the actual state diff content. This means the `state_diff_commitment` value in storage is trusted but the state diff it is supposed to commit to is never bound to it.

**Class sync uses the forged state diff as its acceptance filter**

`ClassStreamBuilder::parse_data_for_block` reads the stored state diff and extracts `class_hash_to_compiled_class_hash` as the set of "allowed" class hashes: [4](#0-3) 

For every incoming `(ApiContractClass, ClassHash)` pair it checks only whether the class hash appears in that map: [5](#0-4) 

If the map was forged, the filter is forged. The class body is accepted and forwarded to the class manager.

**Class manager hash verification is absent (TODO)**

In `write_to_storage`, `class_hash` is bound but never passed to `add_class`; the class manager computes its own content-derived hash. The code itself acknowledges the missing check: [6](#0-5) 

The result is a permanent mismatch: the state diff in storage records attacker-chosen `class_hash → compiled_class_hash` pairs, while the class manager stores the class under its true content hash.

---

### Impact Explanation

The `class_hash_to_compiled_class_hash` map stored in the state diff is the authoritative source used by the blockifier to resolve which compiled class (CASM/native) to execute for a given Sierra class hash. If that map is corrupted:

- A `declare` transaction for a legitimate class hash will be mapped to an attacker-chosen `compiled_class_hash`, causing the blockifier to load and execute the wrong compiled artifact.
- Alternatively, the attacker can map a class hash to a `compiled_class_hash` that does not exist in the class manager, causing execution failures for any contract that deploys or calls that class.

This satisfies the Critical impact criterion: **Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution.**

---

### Likelihood Explanation

Any peer that can participate in the p2p network can serve state diff responses. No operator or validator privilege is required. The attack requires only that the attacker:

1. Serve a `SignedBlockHeader` with a plausible `state_diff_length` (the only field validated).
2. Serve `StateDiffChunk` messages whose total `len()` equals `state_diff_length` but whose `DeclaredClass` entries carry attacker-chosen `class_hash`/`compiled_class_hash` values.
3. Serve matching Sierra class bodies in the subsequent class-sync session.

All three steps are within the capability of any network participant.

---

### Recommendation

After assembling the full `ThinStateDiff` in `StateDiffStreamBuilder::parse_data_for_block`, compute its Poseidon commitment with `calculate_state_diff_hash` and compare it against `block_header.state_diff_commitment`:

```rust
let computed = calculate_state_diff_hash(&result);
if computed != header.state_diff_commitment.ok_or(...)? {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffCommitmentMismatch { ... }));
}
```

This is the same guard already present in `apollo_committer` (`committer.rs:167-176`): [7](#0-6) 

Additionally, in `ClassStreamBuilder::write_to_storage`, implement the already-noted TODO: verify that the class hash returned by the class manager matches the class hash recorded in the state diff before advancing the marker.

---

### Proof of Concept

```rust
// 1. Write a forged state diff for block 0 directly to storage.
let forged_diff = ThinStateDiff {
    class_hash_to_compiled_class_hash: indexmap! {
        ClassHash(felt!("0xDEAD")) => CompiledClassHash(felt!("0xBEEF"))
    },
    ..Default::default()
};
storage_writer.begin_rw_txn()?.append_state_diff(BlockNumber(0), forged_diff)?.commit()?;

// 2. Run parse_data_for_block for the class sync against a peer that sends
//    a ContractClass with class_hash = 0xDEAD.
let result = ClassStreamBuilder::parse_data_for_block(
    &mut mock_response_manager_with(class_hash_0xDEAD, some_sierra_class),
    BlockNumber(0),
    &storage_reader,
).await.unwrap();

// 3. Assert: the class was accepted (result is Some) even though
//    the real state_diff_commitment for block 0 does not include 0xDEAD.
assert!(result.is_some());
// The class manager now stores a class under the attacker-chosen hash,
// and the state diff maps 0xDEAD → 0xBEEF — neither matches the committed diff.
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

**File:** crates/apollo_p2p_sync/src/client/header.rs (L104-119)
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
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L39-41)
```rust
                // TODO(shahak): Verify class hash matches class manager response. report if not.
                // TODO(shahak): Try to avoid cloning. See if ClientError can contain the request.
                while let Err(err) = class_manager_client.add_class(class.clone()).await {
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L95-106)
```rust
            let (target_class_len, declared_classes, deprecated_declared_classes) = {
                let state_diff = storage_reader
                    .begin_ro_txn()?
                    .get_state_diff(block_number)?
                    .expect("A state diff with number lower than the state diff marker is missing");
                (
                    state_diff.class_hash_to_compiled_class_hash.len()
                        + state_diff.deprecated_declared_classes.len(),
                    state_diff.class_hash_to_compiled_class_hash,
                    state_diff.deprecated_declared_classes.iter().cloned().collect::<HashSet<_>>(),
                )
            };
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L131-148)
```rust
                let (is_declared, duplicate_class) = match api_contract_class {
                    ApiContractClass::ContractClass(contract_class) => (
                        declared_classes.get(&class_hash).is_some(),
                        declared_classes_result.insert(class_hash, contract_class).is_some(),
                    ),
                    ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
                        deprecated_declared_classes.contains(&class_hash),
                        deprecated_declared_classes_result
                            .insert(class_hash, deprecated_contract_class)
                            .is_some(),
                    ),
                };

                if !is_declared {
                    return Err(ParseDataError::BadPeer(BadPeerError::ClassNotInStateDiff {
                        class_hash,
                    }));
                }
```

**File:** crates/apollo_committer/src/committer.rs (L167-176)
```rust
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
```
