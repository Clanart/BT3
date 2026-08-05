### Title
`BlockIdRepairType::FecSetRoot` response verification lacks exact Merkle-proof-length checking, letting an intermediate double-Merkle-tree node be accepted as a leaf (`fec_set_root`) — ([File: core/src/repair/serve_repair.rs])

### Summary
The reported zkSync bug is a generic Merkle-proof class: because the proof length is not tied to the tree's fixed depth, a shorter proof lets an attacker present a known *intermediate* node hash as if it were a *leaf*, and still recompute the correct root. Agave's shred double-Merkle-tree implementation reproduces the exact structural precondition for this bug (no leaf/intermediate domain separation), and one of its two consumers — the `BlockIdRepairType::FecSetRoot` response handler — omits the length check that the sibling code path (`ParentAndFecSetCount`) does perform, allowing the invariant to actually be violated over the network.

### Finding Description
`ledger/src/shred/merkle_tree.rs::join_nodes` hashes every parent node the same way regardless of level: [1](#0-0) 
There is no leaf-vs-intermediate prefix, unlike the separate `merkle-tree/src/merkle_tree.rs` implementation, whose comment explicitly documents why this distinction is required "to prevent trivial second pre-image attacks": [2](#0-1) 

`get_merkle_root`/`verify_merkle_proof` in the shred tree fold the proof against `node`/`index` with no dependency on an expected tree depth — verification only checks that the final shifted `index` reaches `0`: [3](#0-2) 
This means a caller supplying a real intermediate hash of the tree as `node`, together with only the *upper* portion of the real proof (matching the number of remaining bits of a coarser index), reproduces the same root — exactly the "shorter path resolves to the same root" bug from the report.

Whether this is exploitable depends entirely on the caller enforcing that `proof.len()` matches the tree's real depth. `serve_repair.rs`'s `BlockIdRepairType` verification does this correctly for the `ParentAndFecSetCount` case (`parent_proof.len() != proof_size * SIZE_OF_MERKLE_PROOF_ENTRY`): [4](#0-3) 
but for `FecSetRoot` it only checks that the proof is non-empty, never that its length equals `get_proof_size(actual_tree_size)`: [5](#0-4) 
This asymmetry exists because, unlike `ParentAndFecSetCount`, a fresh `FecSetRoot` request carries no `fec_set_count`/tree-size field the requester can use to compute the expected proof length: [6](#0-5) 
so the code cannot reject a proof that is shorter than the real double-Merkle-tree depth. A peer answering a `FecSetRoot` request can therefore return a genuine *intermediate* node of the double-Merkle tree (a value it legitimately knows, having observed the block via Turbine — no preimage-finding needed) as `fec_set_root`, paired with only the corresponding upper slice of the real proof, and `verify_merkle_proof(...).is_ok()` will return true even though this value is not the leaf that the requester asked for at `fec_set_index`.

The accepted (but wrong) `fec_set_root` is not discarded — it is immediately used to derive the trust anchor for subsequent per-shred verification: [7](#0-6) 
and is compared directly against each fetched shred's own computed Merkle root in `ShredRepairType::ShredForBlockId`'s `verify_response`: [8](#0-7) 

### Impact Explanation
This breaks the core repair-protocol invariant that "the verified `fec_set_root` corresponds exactly to the FEC-set leaf at `fec_set_index` of the block identified by `block_id`." Once broken, the repair client's outstanding-request/response bookkeeping accepts a forged metadata response as validated, and the resulting bogus `fec_set_merkle_root` is fanned out into `ShredForBlockId` shred requests as the trust anchor peers must match. This falls squarely in the "repair"/"false acceptance" category of in-scope impact: the whole purpose of `verify_response` is to prevent any responding peer (not necessarily a trusted validator) from injecting unverified data into the repair pipeline, and this check silently fails to enforce the depth invariant it is supposed to enforce.

Full escalation to accepting forged shred *content* additionally requires that some shred's independently leader-signed per-FEC-set Merkle root collide with the forged (but genuine, non-preimaged) intermediate hash used as `fec_set_root`; I was not able to fully verify within this session whether a further signature check downstream would block that final step, so I flag this residual step as unconfirmed. Independent of that, the verification-bypass itself (accepting a non-leaf value as a validated leaf) is a demonstrable protocol-invariant violation reachable from any unprivileged peer that answers a `BlockIdRepairType::FecSetRoot` request.

### Likelihood Explanation
No malicious-validator or privileged-role assumption is needed — any node that answers a repair socket request (which by design comes from arbitrary cluster peers, not just trusted validators) can trigger this by returning a shorter, self-consistent proof plus an intermediate hash it legitimately observed via Turbine. The `FecSetRoot` request format structurally cannot carry the tree-size information needed to close this gap (unlike the sibling `ParentAndFecSetCount` path, which does close it), so the bug is a design gap present on every `FecSetRoot` exchange, not a rare edge case.

### Recommendation
- Include the FEC-set-count/tree size in the `FecSetRoot` request (or otherwise let the requester learn/bound it, e.g. from the already-received `ParentAndFecSetCount` response) so `verify_response` can strictly check `fec_set_proof.len() == get_proof_size(tree_size) * SIZE_OF_MERKLE_PROOF_ENTRY`, mirroring the check already done for `ParentAndFecSetCount`.
- Add domain separation between leaf and intermediate hashing in `ledger/src/shred/merkle_tree.rs::join_nodes` / the leaf-node hashing path, analogous to `merkle-tree/src/merkle_tree.rs`'s `LEAF_PREFIX`/`INTERMEDIATE_PREFIX`, so that an intermediate node can never be mistaken for (or substituted as) a leaf regardless of proof length.

### Proof of Concept
1. A validator sends `BlockIdRepairType::FecSetRoot { slot, block_id, fec_set_index }` to peer P, expecting the leaf FEC-set root at `fec_set_index` of `block_id`'s double-Merkle tree.
2. P (any peer that has observed the block via Turbine, not necessarily the block's leader) knows several intermediate hashes of the same double-Merkle tree from its own reconstruction of the tree.
3. P replies with `BlockIdRepairResponse::FecSetRoot { fec_set_root: <intermediate_node_hash>, fec_set_proof: <only the higher-level entries of the real proof> }`.
4. `verify_response` (`core/src/repair/serve_repair.rs:327-353`) only checks `fec_set_proof.is_empty()`, not its length against the real tree depth; `merkle_tree::verify_merkle_proof` folds the shortened proof and returns `Ok(())` because the coarser `leaf_index` also reduces to `0` after the fewer shifts.
5. The requester accepts `<intermediate_node_hash>` as the validated `fec_set_merkle_root` and issues `ShredForBlockId` requests using it as the expected per-shred Merkle root (`core/src/repair/block_id_repair_service.rs:634-660`), a value that does not actually correspond to any single FEC set at `fec_set_index`.

### Citations

**File:** ledger/src/shred/merkle_tree.rs (L106-111)
```rust
// Obtains parent's hash by joining two sibling nodes in merkle tree.
fn join_nodes<S: AsRef<[u8]>, T: AsRef<[u8]>>(node: S, other: T) -> Hash {
    let node = &node.as_ref()[..SIZE_OF_MERKLE_PROOF_ENTRY];
    let other = &other.as_ref()[..SIZE_OF_MERKLE_PROOF_ENTRY];
    hashv(&[MERKLE_HASH_PREFIX_NODE, node, other])
}
```

**File:** ledger/src/shred/merkle_tree.rs (L115-152)
```rust
pub fn get_merkle_root<'a, I>(index: usize, node: Hash, proof: I) -> Result<Hash, Error>
where
    I: IntoIterator<Item = &'a MerkleProofEntry>,
{
    let (index, root) = proof
        .into_iter()
        .fold((index, node), |(index, node), other| {
            let parent = if index % 2 == 0 {
                join_nodes(node, other)
            } else {
                join_nodes(other, node)
            };
            (index >> 1, parent)
        });
    (index == 0)
        .then_some(root)
        .ok_or(Error::InvalidMerkleProof)
}

/// Given a flattened merkle `proof` for `node` at `index`,
/// verify the proof against merkle root `root`
pub fn verify_merkle_proof(
    node: Hash,
    index: usize,
    proof: &[u8],
    expected_root: Hash,
) -> Result<(), Error> {
    let proof = proof
        .chunks(SIZE_OF_MERKLE_PROOF_ENTRY)
        .map(<&MerkleProofEntry>::try_from)
        .map(|entry| entry.map_err(|_| Error::InvalidMerkleProof))
        .collect::<Result<Vec<_>, Error>>()?;
    let merkle_root = get_merkle_root(index, node, proof)?;

    (merkle_root == expected_root)
        .then_some(())
        .ok_or(Error::InvalidMerkleProof)
}
```

**File:** merkle-tree/src/merkle_tree.rs (L1-19)
```rust
use {solana_hash::Hash, solana_sha256_hasher::hashv};

// We need to discern between leaf and intermediate nodes to prevent trivial second
// pre-image attacks.
// https://flawed.net.nz/2018/02/21/attacking-merkle-trees-with-a-second-preimage-attack
const LEAF_PREFIX: &[u8] = &[0];
const INTERMEDIATE_PREFIX: &[u8] = &[1];

macro_rules! hash_leaf {
    {$d:ident} => {
        hashv(&[LEAF_PREFIX, $d])
    }
}

macro_rules! hash_intermediate {
    {$l:ident, $r:ident} => {
        hashv(&[INTERMEDIATE_PREFIX, $l.as_ref(), $r.as_ref()])
    }
}
```

**File:** core/src/repair/serve_repair.rs (L172-182)
```rust
            ShredRepairType::ShredForBlockId {
                slot,
                index,
                fec_set_merkle_root,
                ..
            } => {
                shred_slot == *slot
                    && matches!(shred::layout::get_shred_type(shred), Ok(ShredType::Data))
                    && shred::layout::get_index(shred) == Some(*index)
                    && get_merkle_root(shred) == Some(*fec_set_merkle_root)
            }
```

**File:** core/src/repair/serve_repair.rs (L230-235)
```rust
    FecSetRoot {
        slot: Slot,
        block_id: Hash,
        fec_set_index: u32,
    },
}
```

**File:** core/src/repair/serve_repair.rs (L300-325)
```rust
            ) => {
                if *fec_set_count > MAX_FEC_SETS_PER_SLOT {
                    return false;
                }

                // + 1 here to account for the parent info which is the final leaf of the tree
                let proof_size = merkle_tree::get_proof_size(*fec_set_count as usize + 1);
                if parent_proof.len()
                    != proof_size as usize * merkle_tree::SIZE_OF_MERKLE_PROOF_ENTRY
                {
                    return false;
                }

                let parent_info_leaf = hashv(&[
                    &parent_slot.to_le_bytes(),
                    parent_block_id.as_ref(),
                    &fec_set_count.to_le_bytes(),
                ]);
                merkle_tree::verify_merkle_proof(
                    parent_info_leaf,
                    *fec_set_count as usize,
                    parent_proof,
                    *block_id,
                )
                .is_ok()
            }
```

**File:** core/src/repair/serve_repair.rs (L327-353)
```rust
            (
                Self::FecSetRoot {
                    slot: _slot,
                    block_id,
                    fec_set_index,
                },
                Self::Response::FecSetRoot {
                    fec_set_root,
                    fec_set_proof,
                },
            ) => {
                // The double-Merkle tree contains at least one FEC-set root and
                // the parent-info leaf, so a valid proof cannot be empty.
                if fec_set_proof.is_empty() {
                    return false;
                }
                debug_assert_eq!(*fec_set_index as usize % DATA_SHREDS_PER_FEC_BLOCK, 0);
                // Convert from shred-space to leaf-index
                let leaf_index = *fec_set_index as usize / DATA_SHREDS_PER_FEC_BLOCK;
                merkle_tree::verify_merkle_proof(
                    *fec_set_root,
                    leaf_index,
                    fec_set_proof,
                    *block_id,
                )
                .is_ok()
            }
```

**File:** core/src/repair/block_id_repair_service.rs (L634-660)
```rust
            BlockIdRepairResponse::FecSetRoot {
                fec_set_root: fec_set_merkle_root,
                ..
            } => {
                let BlockIdRepairType::FecSetRoot { fec_set_index, .. } = request else {
                    panic!(
                        "{my_pubkey}: Programmer error, *verified* response was FecSetRoot but \
                         request was not"
                    );
                };
                let start_index = fec_set_index;
                let end_index = fec_set_index + DATA_SHREDS_PER_FEC_BLOCK as u32;

                // Queue ShredForBlockId requests
                state
                    .pending_repair_requests
                    .extend((start_index..end_index).map(|index| {
                        OutgoingMessage::Shred(ShredRepairType::ShredForBlockId {
                            slot,
                            index,
                            fec_set_merkle_root,
                            block_id,
                        })
                    }));

                state.response_stats.fec_set_root_responses += 1;
            }
```
