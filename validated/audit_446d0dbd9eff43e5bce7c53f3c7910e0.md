Audit Report

## Title
Missing proof-length validation in `BlockIdRepairType::FecSetRoot` allows Merkle leaf/internal-node reinterpretation - (File: `core/src/repair/serve_repair.rs`)

## Summary
`BlockIdRepairType::verify_response` for the `FecSetRoot` variant only checks that `fec_set_proof` is non-empty and never validates its length against the tree's real shape (`merkle_tree::get_proof_size()`), unlike the sibling `ParentAndFecSetCount` branch which explicitly performs this check. Combined with the double-Merkle tree's uniform, depth-agnostic `join_nodes` hashing (no leaf-vs-internal-node domain separation for the outer "block_id" tree), this permits a value that is really an internal node hash to be replayed as a claimed `fec_set_root` leaf together with a shortened, real proof suffix, and still verify against the genuine `block_id` root.

## Finding Description
`verify_merkle_proof`/`get_merkle_root` in `ledger/src/shred/merkle_tree.rs` fold the supplied proof entries against `node`/`index` with no binding to the real number of leaves in the tree: [1](#0-0)  Because the folding index is right-shifted each step and stays at `0` once it reaches `0` (a right shift of `0` is idempotent), a caller can supply an internal node `N = join_nodes(L0, L1)` (which sits at `index=0` one level up) together with only the proof suffix from that level to the root, and `index==0` will still hold at the end, causing the fold to reconstruct the true root — without ever needing `N` to be a genuine leaf. The internal-node hash function `join_nodes` applies the exact same prefix at every level with no distinction from a leaf domain: [2](#0-1) 

In `verify_response`, the `ParentAndFecSetCount` branch defends against exactly this kind of reinterpretation by requiring `parent_proof.len()` to equal the expected `get_proof_size(fec_set_count + 1)` before calling `verify_merkle_proof`, and by binding `fec_set_count` into the leaf preimage itself: [3](#0-2)  The accompanying regression test explicitly documents that this length/leaf-binding check was added to prevent a padded/mismatched-tree replay: [4](#0-3) 

The `FecSetRoot` branch, however, only checks `fec_set_proof.is_empty()` and never validates `fec_set_proof.len()` against `get_proof_size()` for the expected tree size, nor binds any tree-size/count value into the `fec_set_root` preimage: [5](#0-4)  This is a structurally real asymmetry between the two branches, and the general Merkle-proof mechanics described above make the internal-node/leaf reinterpretation cryptographically plausible for the `FecSetRoot` branch.

## Impact Explanation
I was unable to fully confirm the downstream impact within the available tool budget. `fec_set_root` and `fec_set_index` are consumed extensively in `core/src/repair/block_id_repair_service.rs` (49 matches) and `core/src/repair/repair_handler.rs` (25 matches), but I was not able to load and trace through this consuming logic before running out of iterations to determine:
- Whether the FEC-set-root repair path independently re-validates the returned `fec_set_root`/`fec_set_index` pairing against locally known shred data before trusting it,
- Whether `fec_set_index` values are attacker-constrained to only positions where the two-child internal-node reinterpretation described above is actually achievable (this requires the attacker to know or reconstruct real sibling leaves/internal nodes of the block's real double-Merkle tree, which — per the finding's own text — are "already-hashed values" derived from public, already-distributed shred data, making this plausible but not confirmed as exploitable in the running system), and
- What concrete bad state (false FEC-set-to-index mapping, corrupted shred/blockstore bookkeeping) would actually result from a verified-but-wrong `fec_set_root`.

Given this, while the code-level asymmetry between the two `verify_response` branches is real and reproducible as a difference in validation logic, I could not independently confirm a reachable, concrete state-corruption impact (e.g., false shred/FEC-set acceptance, blockstore corruption, or consensus-relevant harm) through the downstream repair-handling code within the available investigation. This matches the original claim's own stated uncertainty and does not meet the bar of a confirmed, reachable bad-state/bad-execution impact required by the target scopes without further tracing of `block_id_repair_service.rs`/`repair_handler.rs`.

## Likelihood Explanation
The missing length check is a genuine, reproducible code-level gap, and the underlying Merkle-fold mechanics (idempotent right-shift at `index=0`) do generically allow internal-node/leaf substitution in Merkle proofs lacking domain separation. However, likelihood of actual exploitation depends on whether the downstream consumers in `block_id_repair_service.rs`/`repair_handler.rs` treat a `FecSetRoot` response as authoritative for consensus-relevant decisions without additional cross-validation — this was not confirmed.

## Recommendation
Add a proof-length check to the `FecSetRoot` branch of `verify_response` in `core/src/repair/serve_repair.rs`, computing `get_proof_size()` for the real/claimed tree size (analogous to the `ParentAndFecSetCount` branch) and rejecting mismatched proof lengths, and/or introduce explicit leaf-domain separation in the double-Merkle tree used for `block_id` so that `fec_set_root`/`parent_info_leaf` leaves are cryptographically distinguishable from internal `join_nodes` outputs at every level.

## Proof of Concept
Not confirmed end-to-end. The concrete PoC would require: (1) constructing a real double-Merkle tree with known `fec_set_roots` and `parent_info_leaf`; (2) computing `N = join_nodes(L0, L1)`; (3) crafting `BlockIdRepairResponse::FecSetRoot { fec_set_root: N, fec_set_proof: <suffix> }` for an attacker-chosen `fec_set_index` mapping to `leaf_index = 0`; (4) showing `verify_response` returns `true`; and (5) tracing through `block_id_repair_service.rs`/`repair_handler.rs` to show this leads to a concrete bad state (e.g., wrong FEC-set/shred acceptance). Step (5) was not completed due to tool-call exhaustion, so this finding remains a **candidate** requiring further verification of downstream consumption before a confirmed impact can be assigned.

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

**File:** ledger/src/shred/merkle_tree.rs (L115-132)
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
