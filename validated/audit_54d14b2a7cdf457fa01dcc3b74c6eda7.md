### Title
P2P State Diff Sync Accepts Peer-Supplied `ThinStateDiff` Without Verifying Against Stored `state_diff_commitment` — (File: `crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

The P2P sync client assembles a `ThinStateDiff` from peer-supplied `StateDiffChunk` messages and validates it only by total element count against `state_diff_length` from the stored block header. It never calls `calculate_state_diff_hash` on the assembled result and compares it to the `state_diff_commitment` already stored in the same header. A single malicious peer can supply a correctly-sized but content-corrupted state diff that passes all existing checks and is written directly to MDBX, causing the sequencer to store wrong storage values, nonces, and class hashes, and subsequently compute a wrong state root and block hash.

---

### Finding Description

**Root cause — `parse_data_for_block` in `StateDiffStreamBuilder`**

`parse_data_for_block` reads `state_diff_length` from the stored header and accumulates chunks until `current_state_diff_len == target_state_diff_len`. The only content checks are structural (no duplicate keys, no empty chunks). After the loop the assembled `ThinStateDiff` is written to storage without any cryptographic check. [1](#0-0) 

The `state_diff_commitment` field is stored in the same block header that supplies `state_diff_length`, and `calculate_state_diff_hash` already exists and is used elsewhere: [2](#0-1) 

The function is never called inside `parse_data_for_block`. The assembled diff is passed directly to `write_to_storage`: [3](#0-2) 

**Compounding issue — header signature not verified**

`parse_data_for_block` for `HeaderStreamBuilder` checks only that exactly one signature is present; it never calls `verify_block_signature` (which verifies `Poseidon(block_hash, state_diff_commitment)` against the sequencer's public key): [4](#0-3) 

The verification function exists but is unused in the sync path: [5](#0-4) 

**No downstream rescue**

The committer's optional `verify_state_diff_hash` flag compares the state diff against the `state_diff_commitment` passed in the `CommitBlockRequest`. That commitment is read from the stored block header — the same header that was accepted without signature verification. If both the header and the state diff are attacker-controlled, the commitment and the diff are consistent with each other, so the committer check passes even when enabled. [6](#0-5) 

The batcher reads the `state_diff_commitment` for the committer request from the stored header: [7](#0-6) 

---

### Impact Explanation

A malicious P2P peer executes the following two-step attack:

1. **Fake header.** Send a `SignedBlockHeader` with the correct block number, any one-element signature (passes the length check), a chosen `state_diff_commitment` value `C_evil`, and a matching `state_diff_length` `L_evil`. The header is stored verbatim.

2. **Fake state diff.** Send `L_evil` state diff entries whose `calculate_state_diff_hash` equals `C_evil` but whose storage values, nonces, or class hashes differ from the canonical chain. The length check passes; no commitment check is performed. The corrupted `ThinStateDiff` is written to MDBX.

Consequences:
- `get_storage_at`, `get_nonce_at`, `get_class_hash_at` RPC calls return attacker-chosen values — **wrong state/storage value from RPC**.
- The committer computes a wrong Patricia trie root from the corrupted state diff — **wrong state root**.
- The block hash derived from that root is wrong — **wrong block hash / commitment**.
- Proof inputs built from the corrupted state (SNOS commitment infos, storage proofs) are wrong — **wrong proof facts**.

This matches: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result* (Critical) and *RPC returns an authoritative-looking wrong value* (High).

---

### Likelihood Explanation

Any node that the sequencer connects to via `apollo_network` / libp2p can act as the malicious peer. No privileged access, no stake, and no cryptographic key material is required. The attack is fully passive from the network's perspective — the attacker simply responds to the sequencer's own sync queries with crafted data. The sequencer has no mechanism to prefer honest peers over malicious ones for this data type.

---

### Recommendation

**1. Verify the state diff commitment inside `parse_data_for_block` (primary fix)**

After the assembly loop in `StateDiffStreamBuilder::parse_data_for_block`, read `state_diff_commitment` from the stored header and compare it against `calculate_state_diff_hash(&result)`. If the header does not carry a commitment (pre-0.13.2 blocks), skip the check. Return `BadPeerError::ConflictingStateDiffParts` (or a new dedicated variant) on mismatch. [8](#0-7) 

**2. Verify the block header signature inside `parse_data_for_block` for headers**

Call `verify_block_signature` with the sequencer's known public key before accepting a `SignedBlockHeader`. This closes the path where an attacker plants a fake `state_diff_commitment` in the header to make step 1 above pass. [9](#0-8) 

---

### Proof of Concept

The following sketch demonstrates the attack without modifying any sequencer code — only the peer's response is crafted.

```
// Attacker controls a libp2p peer that the sequencer connects to.

// Step 1 – respond to the sequencer's HeaderQuery for block N with a fake header:
let evil_state_diff = ThinStateDiff {
    nonces: indexmap! { victim_contract => Nonce(Felt::from(999u64)) },
    ..Default::default()
};
let evil_commitment = calculate_state_diff_hash(&evil_state_diff); // C_evil
let evil_length    = evil_state_diff.len();                        // L_evil = 1

let fake_header = SignedBlockHeader {
    block_header: BlockHeader {
        block_header_without_hash: BlockHeaderWithoutHash {
            block_number: BlockNumber(N),
            ..real_header.block_header_without_hash  // copy gas prices etc.
        },
        state_diff_commitment: Some(evil_commitment),
        state_diff_length:     Some(evil_length),
        block_hash:            BlockHash(Felt::from(0xdeadbeefu64)), // arbitrary
        ..Default::default()
    },
    signatures: vec![BlockSignature::default()], // passes the length==1 check
};
// Sequencer stores fake_header verbatim (no signature check).

// Step 2 – respond to the sequencer's StateDiffQuery for block N:
// Send one ContractDiff chunk that sets victim_contract's nonce to 999.
// current_state_diff_len (1) == target_state_diff_len (1) → accepted.
// calculate_state_diff_hash is never called → no mismatch detected.
// ThinStateDiff with nonce=999 is written to MDBX.

// Result: starknet_getNonce(victim_contract, block N) now returns 999
// instead of the canonical value, and the committer builds a wrong
// Patricia root from this corrupted state diff.
```

The sequencer's `parse_data_for_block` for state diffs accepts the chunk because `current_state_diff_len == target_state_diff_len` and there are no duplicate keys. [10](#0-9)  The `state_diff_commitment` stored in the header is never read during state diff parsing. [11](#0-10)

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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L59-107)
```rust
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

**File:** crates/starknet_api/src/block.rs (L716-730)
```rust
/// Verifies that the the block header was signed by the expected sequencer.
pub fn verify_block_signature(
    sequencer_pub_key: &SequencerPublicKey,
    signature: &BlockSignature,
    state_diff_commitment: &GlobalRoot,
    block_hash: &BlockHash,
) -> Result<bool, BlockVerificationError> {
    let message_hash = Poseidon::hash_array(&[block_hash.0, state_diff_commitment.0]);
    verify_message_hash_signature(&message_hash, &signature.0, &sequencer_pub_key.0).map_err(
        |err| BlockVerificationError::BlockSignatureVerificationFailed {
            block_hash: *block_hash,
            error: err,
        },
    )
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

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L392-403)
```rust
        let state_diff_commitment = if no_state_diff_commitment {
            None
        } else {
            // TODO(Amos): Add method to fetch only hash commitment and use it here.
            match batcher_storage_reader.get_parent_hash_and_partial_block_hash_components(height) {
                Ok((_, Some(PartialBlockHashComponents { header_commitments, .. }))) => {
                    Some(header_commitments.state_diff_commitment)
                }
                Ok((_, None)) => panic!("Missing hash commitment for height {height}."),
                Err(err) => panic!("Failed to read hash commitment for height {height}: {err}"),
            }
        };
```
