This confirms the vulnerability exactly as described in the claim. There's an existing test `test_verify_fec_set_root_rejects_empty_proof` that only checks the empty-proof edge case, with no test covering proof-length/depth binding, matching the "existing guards reviewed and shown insufficient" requirement.

Audit Report

## Title
Missing proof-length binding lets a malicious repair peer forge a `FecSetRoot` block-id-repair response by substituting an internal double-Merkle node for a leaf - (File: `core/src/repair/serve_repair.rs`)

## Summary
The `FecSetRoot` branch of `BlockIdRepairType::verify_response` in `core/src/repair/serve_repair.rs` only rejects an empty proof but never validates that `fec_set_proof.len()` matches the depth expected for the claimed `leaf_index`, unlike the sibling `ParentAndFecSetCount` branch which explicitly derives `proof_size` from `fec_set_count`. Because FEC-set-root "leaves" of the double-Merkle tree are hashed with the same `join_nodes`/`MERKLE_HASH_PREFIX_NODE` construction used for internal nodes (no leaf-specific domain separation is applied before insertion into `MerkleTree::try_new_with_len`), an internal double-Merkle node is byte-identical in structure to a leaf, allowing a malicious peer to substitute a real internal node plus a truncated proof and still have `verify_merkle_proof` fold up to the correct, publicly known `block_id`.

## Finding Description
`verify_response` for `FecSetRoot` is:

```rust
if fec_set_proof.is_empty() {
    return false;
}
debug_assert_eq!(*fec_set_index as usize % DATA_SHREDS_PER_FEC_BLOCK, 0);
let leaf_index = *fec_set_index as usize / DATA_SHREDS_PER_FEC_BLOCK;
merkle_tree::verify_merkle_proof(*fec_set_root, leaf_index, fec_set_proof, *block_id).is_ok()
``` [1](#0-0) 

This contrasts with the `ParentAndFecSetCount` branch, which computes the exact expected proof length from `fec_set_count` and rejects any mismatch: [2](#0-1) 

`verify_merkle_proof`/`get_merkle_root` only check that the folded index reaches `0` after processing the supplied proof entries; they never compare the proof length to the tree's actual depth: [3](#0-2) 

Because FEC-set roots are inserted as "leaves" of the double-Merkle tree without any leaf-specific domain prefix (`join_nodes` always uses `MERKLE_HASH_PREFIX_NODE`, both for combining leaves and combining internal nodes), an internal node of that tree has the exact same byte format as a leaf: [4](#0-3) 

For `fec_set_index = 0` (`leaf_index = 0`), `index % 2 == 0` holds at every fold step regardless of how many entries are supplied, so a malicious peer can supply `fec_set_root = N01` (the real, publicly-derivable internal node combining FEC-set roots 0 and 1) together with a proof that is one entry shorter than the correct proof — omitting the sibling FEC-set root at index 1, since `N01` already encodes it. The fold `join(N01, N23)=M`, then `join(M, P')=root` reaches the correct `block_id` with `index == 0`, so `verify_merkle_proof` returns `Ok`, even though `N01` is not the true FEC-set-0 Merkle root. The existing test suite only covers the empty-proof case for `FecSetRoot`: [5](#0-4) 

and a distinct regression test proves the `fec_set_count`-binding mitigation exists only for `ParentAndFecSetCount`: [6](#0-5) 

confirming that no equivalent length/depth check protects `FecSetRoot`.

## Impact Explanation
A victim node that accepts the forged `fec_set_root` will use it to issue `ShredRepairType::ShredForBlockId` requests, per the flow in `block_id_repair_service.rs`: [7](#0-6) 

Since the accepted `fec_set_root` is not the actual per-shred Merkle root for that FEC set, every subsequent real per-shred proof verification against it will fail, causing the victim to falsely accept corrupted block-id repair metadata and stall repair progress for that FEC set/slot — a false-acceptance/repair-degradation outcome from a single forged, low-cost packet sent by an unprivileged repair-serving peer.

## Likelihood Explanation
The attack requires only that the attacker be selected as a repair-serving peer for a `BlockIdRepairType::FecSetRoot` request (any node can be selected under Agave's decentralized/untrusted repair model) and that it craft one UDP response packet using publicly derivable hash values already present in the broadcast shreds for the slot. No hash collision search is needed, and the `debug_assert_eq!` on `fec_set_index` alignment provides no protection in release builds.

## Recommendation
In the `FecSetRoot` branch of `verify_response`, bind the expected proof length to the tree depth the same way `ParentAndFecSetCount` does — e.g., require the caller to know `fec_set_count` (cached from a prior `ParentAndFecSetCount` exchange keyed by `block_id`) and enforce `fec_set_proof.len() == get_proof_size(fec_set_count + 1) * SIZE_OF_MERKLE_PROOF_ENTRY`; alternatively, introduce a leaf-domain hash prefix distinct from `MERKLE_HASH_PREFIX_NODE` for FEC-set-root leaves so an internal node can never validate as a leaf regardless of supplied proof length.

## Proof of Concept
1. Construct a double-Merkle tree with `fec_set_count = 4`: leaves `L0..L3` (FEC-set roots) plus the parent-info leaf `PInfo`; internal nodes `N01=join(L0,L1)`, `N23=join(L2,L3)`, `M=join(N01,N23)`, `P'=join(PInfo,PInfo)`, `root=join(M,P')`, using `MerkleTree::try_new_with_len` as in `blockstore.rs`.
2. Send `BlockIdRepairType::FecSetRoot { fec_set_index: 0, block_id: root }`.
3. Respond with `BlockIdRepairResponse::FecSetRoot { fec_set_root: N01, fec_set_proof: [N23, P'] }` (2 entries instead of the correct 3-entry proof `[L1, N23, P']`).
4. Call `request.verify_response(&response)`; observe it returns `true` via `verify_merkle_proof(N01, 0, [N23, P'], root)` folding correctly to `root`, despite `N01 != L0`.
5. This can be added as a unit test alongside `test_verify_fec_set_root_rejects_empty_proof` and `test_verify_fec_set_count_non_malleable` in `core/src/repair/serve_repair.rs` to demonstrate the missing length check.

### Citations

**File:** core/src/repair/serve_repair.rs (L300-311)
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
```

**File:** core/src/repair/serve_repair.rs (L337-353)
```rust
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

**File:** core/src/repair/serve_repair.rs (L3258-3304)
```rust
    #[test]
    fn test_verify_fec_set_count_non_malleable() {
        let parent_slot = 99u64;
        let parent_block_id = Hash::new_unique();
        let fec_set_count: u32 = 2; // even => total leaves = 3, last leaf duplicated
        let fec_set_roots: Vec<Hash> = (0..fec_set_count).map(|_| Hash::new_unique()).collect();
        let real_parent_leaf = hashv(&[
            &parent_slot.to_le_bytes(),
            parent_block_id.as_ref(),
            &fec_set_count.to_le_bytes(),
        ]);
        let mut leaves: Vec<Hash> = fec_set_roots;
        leaves.push(real_parent_leaf);
        let tree =
            merkle_tree::MerkleTree::try_new_with_len(leaves.iter().copied().map(Ok), leaves.len())
                .unwrap();
        let block_id = *tree.root();
        let real_parent_proof: Vec<u8> = tree
            .make_merkle_proof(fec_set_count as usize, leaves.len())
            .flat_map(|entry| entry.unwrap().iter().copied())
            .collect();

        let request = BlockIdRepairType::ParentAndFecSetCount {
            slot: 100,
            block_id,
        };

        // honest response verifies
        assert!(
            request.verify_response(&BlockIdRepairResponse::ParentFecSetCount {
                fec_set_count,
                parent_info: (parent_slot, parent_block_id),
                parent_proof: real_parent_proof.clone(),
            })
        );

        // Attack: claim N+1 and reuse the honest proof. The padded tree puts
        // `real_parent_leaf` at both positions N and N+1, so without binding
        // `fec_set_count` into the leaf this proof would verify.
        assert!(
            !request.verify_response(&BlockIdRepairResponse::ParentFecSetCount {
                fec_set_count: fec_set_count + 1,
                parent_info: (parent_slot, parent_block_id),
                parent_proof: real_parent_proof.clone(),
            })
        );
    }
```

**File:** core/src/repair/serve_repair.rs (L3306-3320)
```rust
    #[test]
    fn test_verify_fec_set_root_rejects_empty_proof() {
        let block_id = Hash::new_unique();
        let request = BlockIdRepairType::FecSetRoot {
            slot: 100,
            block_id,
            fec_set_index: 0,
        };
        let response = BlockIdRepairResponse::FecSetRoot {
            fec_set_root: block_id,
            fec_set_proof: vec![],
        };

        assert!(!request.verify_response(&response));
    }
```

**File:** ledger/src/shred/merkle_tree.rs (L106-111)
```rust
// Obtains parent's hash by joining two sibling nodes in merkle tree.
fn join_nodes<S: AsRef<[u8]>, T: AsRef<[u8]>>(node: S, other: T) -> Hash {
    let node = &node.as_ref()[..SIZE_OF_MERKLE_PROOF_ENTRY];
    let other = &other.as_ref()[..SIZE_OF_MERKLE_PROOF_ENTRY];
    hashv(&[MERKLE_HASH_PREFIX_NODE, node, other])
}
```

**File:** ledger/src/shred/merkle_tree.rs (L113-152)
```rust
// Recovers root of the merkle tree from a leaf node
// at the given index and the respective proof.
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

**File:** core/src/repair/block_id_repair_service.rs (L634-659)
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
```
