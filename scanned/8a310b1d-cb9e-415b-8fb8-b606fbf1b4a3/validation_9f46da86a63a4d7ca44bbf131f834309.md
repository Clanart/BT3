## Title
Missing proof-length validation in `BlockIdRepairType::FecSetRoot` allows Merkle leaf/internal-node reinterpretation (double-Merkle second-preimage) - (File: `core/src/repair/serve_repair.rs`)

### Summary
The footium bug class is: a Merkle-proof verifier that hashes leaves and internal nodes with the *same* domain (no leaf/node prefix separation) and does not bind the proof length/index to the tree's real shape, allowing an attacker-supplied value that is actually an *internal node* pre-image to be reinterpreted and verified as a *leaf* at an attacker-chosen position, reaching a legitimate root. Agave's shred/`merkle_tree.rs` primitives generally defend against this with explicit `MERKLE_HASH_PREFIX_LEAF` / `MERKLE_HASH_PREFIX_NODE` domain separation [1](#0-0) , but the "double Merkle tree" used for block-id repair inserts already-hashed values (`fec_set_root`, `parent_info_leaf`) directly as tree leaves without any leaf-domain-separation step [2](#0-1) , and `join_nodes` uses the exact same prefix/format for every internal node regardless of depth [3](#0-2) . Because `verify_merkle_proof`/`get_merkle_root` fold blindly from whatever `(node, index, proof)` triple is supplied, without any binding to the real number of leaves [4](#0-3) , correctness depends entirely on callers validating that `proof.len()` matches the expected `get_proof_size()` for the claimed tree size/index.

### Finding Description
`BlockIdRepairType::verify_response` in `core/src/repair/serve_repair.rs` has two branches that call `merkle_tree::verify_merkle_proof` against the double-Merkle `block_id`:

- The `ParentAndFecSetCount` branch explicitly checks that `parent_proof.len()` equals `get_proof_size(fec_set_count + 1) * SIZE_OF_MERKLE_PROOF_ENTRY` before verifying, and binds `fec_set_count` into the leaf pre-image itself [5](#0-4) . This is precisely the defense the code's own regression test documents was needed: without binding `fec_set_count`/proof length to the leaf, a padded tree lets the same leaf value verify at two different positions [6](#0-5) .
- The `FecSetRoot` branch, by contrast, only checks that `fec_set_proof` is non-empty and never checks its length against the expected `get_proof_size()` for `leaf_index`/tree size before calling `verify_merkle_proof` [7](#0-6) .

Because `join_nodes`/`get_merkle_root` apply the identical hash formula (`MERKLE_HASH_PREFIX_NODE` + truncated 20-byte halves) at every level of the double-Merkle tree, and no leaf-domain prefix distinguishes a true leaf (`fec_set_root`) from an internal node computed by `join_nodes(L0, L1)` [3](#0-2) , an internal node value `N = join_nodes(L0, L1)` is structurally indistinguishable from a genuine `fec_set_root` leaf. Since the fold in `get_merkle_root` is depth-agnostic — it simply repeats `join_nodes(node, proof_entry)` and shifts `index >>= 1` for however many proof entries are supplied [8](#0-7)  — supplying `node = N` together with the *real* proof suffix that would normally continue from `N`'s actual position one level up (i.e., omitting the first real proof entry that combines `L0`/`L1`) reproduces exactly the same fold sequence used to build the genuine root. The `FecSetRoot` branch does not require the caller to prove that the supplied `fec_set_root` is a genuine tree leaf at the expected depth for `leaf_index`, because it never validates `fec_set_proof.len()` against `get_proof_size()` for the real tree size.

### Impact Explanation
A response to `BlockIdRepairType::FecSetRoot` is accepted from any network peer without a trust assumption — that is the entire purpose of the Merkle-proof verification, to allow an untrusted responder's data to be validated cryptographically. By omitting the proof-length/tree-shape check present in the sibling `ParentAndFecSetCount` branch, `verify_response` for `FecSetRoot` can be satisfied with a `fec_set_root` that is actually an internal node of the real double-Merkle tree (a value that is entirely public/derivable from the already-distributed block), paired with a shortened, real proof suffix, at an attacker-chosen `fec_set_index`. If `repair_handler.rs`/`block_id_repair_service.rs` trust a verified `fec_set_root` as the genuine per-FEC-set root for the claimed `fec_set_index` (e.g., to fetch/validate/accept shreds for that FEC set), this reinterpretation could let a malicious repair responder cause the requester to accept an incorrect FEC-set-to-index mapping, corrupting shred/FEC-set bookkeeping used for block reconstruction — a false-acceptance primitive in the repair path.

### Likelihood Explanation
The exploit path relies on a real structural gap (asymmetric leaf/internal-node handling between the two `verify_response` branches, plus the double-Merkle tree's leaves being un-prefixed already-hashed values), not on breaking SHA-256. The `ParentAndFecSetCount` branch's explicit length check and accompanying `test_verify_fec_set_count_non_malleable` regression test show the Agave developers were aware of exactly this reinterpretation risk for that branch and fixed it there, but the same fix was not visibly applied to the `FecSetRoot` branch in the code retrieved. I was not able to fully trace, within the available tool budget, how `fec_set_root`/`fec_set_index` are subsequently consumed in `block_id_repair_service.rs`/`repair_handler.rs` (I could not load those files' contents before running out of iterations), so I cannot confirm with certainty whether downstream code independently re-validates the leaf's depth/position or otherwise neutralizes this gap. This uncertainty should be resolved by inspecting `core/src/repair/block_id_repair_service.rs` and `core/src/repair/repair_handler.rs` in full.

### Recommendation
In `core/src/repair/serve_repair.rs`, add a proof-length check to the `FecSetRoot` branch of `verify_response`, mirroring the `ParentAndFecSetCount` branch: compute the expected `get_proof_size()` for the real/claimed number of FEC sets (or otherwise the maximum valid tree depth) and reject any `fec_set_proof` whose length does not match, in addition to (or instead of) the current `is_empty()` check. More robustly, apply an explicit leaf-domain separation (analogous to `MERKLE_HASH_PREFIX_LEAF`) when computing/verifying leaves of the double-Merkle tree in `ledger/src/shred/merkle_tree.rs`, so that leaf and internal-node hash spaces are cryptographically distinguishable at every layer, not just within a single FEC-set-level tree.

### Proof of Concept
Not independently confirmed against downstream consumer code (`block_id_repair_service.rs`, `repair_handler.rs`) due to tool-call exhaustion; the concrete PoC would need to:
1. Build a real double-Merkle tree for a slot with `fec_set_roots = [L0, L1, L2]` and `parent_info_leaf`.
2. Compute `N = join_nodes(L0, L1)` (a legitimate, publicly derivable internal-node hash).
3. Craft a `Response::FecSetRoot { fec_set_root: N, fec_set_proof: <proof suffix from N's real position up to root> }` for an attacker-chosen `fec_set_index` that maps to `leaf_index = 0` (or whatever index the depth-agnostic fold resolves to).
4. Show `BlockIdRepairType::FecSetRoot { block_id, fec_set_index }.verify_response(...)` returns `true` even though `N` is not a genuine `fec_set_root` leaf for that index.

Given the incomplete confirmation of downstream impact, this should be treated as a **candidate** finding requiring verification of `block_id_repair_service.rs`/`repair_handler.rs` consumption logic before final severity assignment.

### Citations

**File:** ledger/src/shred/merkle_tree.rs (L13-18)
```rust
// Defense against second preimage attack:
// https://en.wikipedia.org/wiki/Merkle_tree#Second_preimage_attack
// Following Certificate Transparency, 0x00 and 0x01 bytes are prepended to
// hash data when computing leaf and internal node hashes respectively.
pub(crate) const MERKLE_HASH_PREFIX_LEAF: &[u8] = b"\x00SOLANA_MERKLE_SHREDS_LEAF";
pub(crate) const MERKLE_HASH_PREFIX_NODE: &[u8] = b"\x01SOLANA_MERKLE_SHREDS_NODE";
```

**File:** ledger/src/shred/merkle_tree.rs (L47-68)
```rust
    pub fn try_new_with_len(
        shreds: impl Iterator<Item = Result<Hash, Error>>,
        len: usize,
    ) -> Result<MerkleTree, Error> {
        let capacity = get_merkle_tree_size(len);
        let mut nodes = Vec::with_capacity(capacity);
        for shred in shreds {
            nodes.push(shred?);
        }
        let init = (len > 1).then_some(len);
        for size in successors(init, |&k| (k > 2).then_some((k + 1) >> 1)) {
            let offset = nodes.len() - size;
            for index in (offset..offset + size).step_by(2) {
                let node = &nodes[index];
                let other = &nodes[(index + 1).min(offset + size - 1)];
                let parent = join_nodes(node, other);
                nodes.push(parent);
            }
        }
        debug_assert_eq!(nodes.len(), capacity);
        Ok(MerkleTree { nodes })
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

**File:** core/src/repair/serve_repair.rs (L300-324)
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
