## Finding Confirmed

The concern is valid. Tracing `verify_consensus` → `verify_mmr_update_proof` → `verify_mmr_leaf` in the naive/ECDSA BEEFY verifier shows that **no check binds the `latest_mmr_leaf` to the specific `block_number` of the signed commitment**. [1](#0-0) 

`verify_mmr_update_proof` validates: (1) the authority set / supermajority signatures over `commitment`, (2) extracts `mmr_root` from the *signed* payload, then calls `verify_mmr_leaf` which only proves that `mmr.latest_mmr_leaf` is *some* leaf included at `mmr.mmr_proof.leaf_indices[0]` in an MMR of the size derivable from that same attacker-chosen leaf index, against the signed `mmr_root`: [2](#0-1) 

Because an MMR is append-only, **any historical leaf remains provably included under any later root**. Nothing in this function (or its caller) checks that `latest_mmr_leaf.parent_number + 1 == commitment.block_number`, i.e. that the supplied leaf is actually the leaf appended *at* the signed block. The function then returns `mmr.latest_mmr_leaf.leaf_extra` as `heads_root` straight from this unvalidated leaf: [3](#0-2) 

and `verify_consensus` feeds that `heads_root` directly into `verify_parachain_headers` with no further cross-check: [4](#0-3) 

This is exactly the class of bug the codebase's own SP1 (ZK) BEEFY path defends against — it explicitly checks `mmrLeaf.parentNumber + 1 == commitment.blockNumber` before trusting the leaf's `extra` field (parachain heads root): [5](#0-4) 

and the Rust `Error` enum even documents this exact threat model for the SP1 flow (`StaleMmrLeaf`) — "An MMR is append-only, so historical leaves also prove against the commitment's root; accepting one would let a proof advance the height while replaying an old `beefy_next_authority_set`...": [6](#0-5) 

However, this exact `StaleMmrLeaf` guard is **never invoked in the naive `verify_mmr_update_proof`/`verify_mmr_leaf` path** — it only appears in `sp1.rs`'s SP1 proof verification. I searched the whole naive verifier module for `parent_number`/`StaleMmrLeaf` usage and found none in `lib.rs`. The equivalent Solidity ECDSA verifier (`EcdsaBeefy.sol`) has the identical gap — its `verifyMmrLeaf` also never compares `latestMmrLeaf.parentNumber` to `commitment.blockNumber`: [7](#0-6) 

This means a relayer/prover-supplied (unsigned/unprivileged, part of the `ConsensusMessage`) `latest_mmr_leaf` + `mmr_proof` pair for an **old, already-superseded block** can be paired with a validly supermajority-signed `signed_commitment`/`authority_proof` for a newer `block_number`, as long as the old leaf is still provable under the current `mmr_root` (which append-only MMRs guarantee). The resulting `heads_root` corresponds to a stale/mismatched relay-chain state, not the one committed at the reported `block_number`.

### Title
Naive BEEFY verifier accepts stale MMR leaves, decoupling `heads_root` from the signed `block_number` — (`modules/consensus/beefy/verifier/src/lib.rs`)

### Summary
`verify_mmr_update_proof`/`verify_mmr_leaf` in the naive (ECDSA) BEEFY consensus verifier never checks that the supplied `latest_mmr_leaf.parent_number` corresponds to `commitment.block_number - 1`. Since MMRs are append-only, an old, previously-valid leaf remains provable against any later `mmr_root`. This lets an attacker submit a validly supermajority-signed commitment for the latest block alongside a stale/unrelated `latest_mmr_leaf`, causing `verify_consensus` to bind `verify_parachain_headers` to a stale `heads_root` (`leaf_extra`) that does not correspond to the finalized state at the reported block.

### Finding Description
`verify_mmr_leaf` only proves MMR-inclusion of whatever leaf and leaf_index are supplied in the unsigned `ConsensusMessage`, using an `mmr_size` computed purely from that same attacker-chosen `leaf_index` [8](#0-7) . It never cross-references `mmr.latest_mmr_leaf`'s `parent_number`/epoch against `mmr.signed_commitment.commitment.block_number`, unlike the SP1 path which explicitly enforces `StaleMmrLeaf` [9](#0-8) . The returned `heads_root` (`leaf_extra`) is trusted as-is by `verify_consensus` and fed to `verify_parachain_headers`, with the only remaining check being a merkle multi-proof against that (potentially stale) root [10](#0-9) .

### Impact Explanation
An attacker-influenced `heads_root` allows `verify_parachain_headers` to accept parachain header state commitments that do not correspond to the actually-finalized state at the claimed `block_number`, enabling acceptance of stale/forged parachain state as authenticated. Downstream ISMP consumers treat these `IntermediateState`/state commitments as trusted, which can lead to false state-machine commitments being accepted — a direct violation of "false remote state must never become trusted."

### Likelihood Explanation
The `ConsensusMessage` (including `mmr.latest_mmr_leaf` and `mmr.mmr_proof`) is submitted by an unprivileged relayer/caller and is not itself signed — only the `signed_commitment`/`authority_proof` portion carries authority signatures. Constructing a valid signed commitment for a new block while reusing an old, still-provable leaf requires no privileged access, only a previously-observed valid MMR proof for an older block that remains valid under append-only MMR semantics.

### Recommendation
Add an explicit check in the naive BEEFY verifier (mirroring the SP1 `StaleMmrLeaf` guard) that `mmr.latest_mmr_leaf.parent_number + 1 == mmr.signed_commitment.commitment.block_number` (and/or that `leaf_index` corresponds to that block) before trusting `leaf_extra` as `heads_root`. Apply the same fix to `EcdsaBeefy.sol`'s `verifyMmrLeaf`.

### Proof of Concept
Not independently runnable within this review's tooling, but conceptually: capture a valid `(latest_mmr_leaf, mmr_proof)` pair for block N-k (still provable against the later `mmr_root` due to MMR append-only property), then submit a `ConsensusMessage` with a fresh, properly supermajority-signed `signed_commitment` for block N whose `mmr_root` payload still contains that older leaf provably, and observe `verify_mmr_update_proof` returns the stale `leaf_extra` as `heads_root`, which `verify_parachain_headers` then accepts against attacker-supplied `parachain_proof.parachains`. A unit test analogous to `rejects_sp1_proof_carrying_a_stale_mmr_leaf` [11](#0-10)  but targeting `verify_mmr_update_proof`/`verify_consensus` (the naive path) would currently pass where it should fail.

### Citations

**File:** modules/consensus/beefy/verifier/src/lib.rs (L91-98)
```rust
pub fn verify_consensus<H: Keccak256 + EcdsaRecover + Send + Sync>(
	trusted_state: ConsensusState,
	proof: ConsensusMessage,
) -> Result<(Vec<u8>, Vec<ParachainHeader>), Error> {
	let (state, heads_root) = verify_mmr_update_proof::<H>(trusted_state, proof.mmr)?;
	let verified_headers = verify_parachain_headers::<H>(heads_root, proof.parachain)?;
	Ok((state.encode(), verified_headers))
}
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L177-187)
```rust
	verify_mmr_leaf::<H>(&mmr, mmr_root)?;

	if mmr.latest_mmr_leaf.beefy_next_authority_set.id > trusted_state.next_authorities.id {
		trusted_state.current_authorities = trusted_state.next_authorities.clone();
		trusted_state.next_authorities = mmr.latest_mmr_leaf.beefy_next_authority_set.clone();
	}

	trusted_state.latest_beefy_height = latest_height;

	Ok((trusted_state, mmr.latest_mmr_leaf.leaf_extra))
}
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L189-223)
```rust
/// Verifies the inclusion of parachain headers in the parachain heads root via a merkle multi proof
pub fn verify_parachain_headers<H: Keccak256>(
	heads_root: H256,
	parachain_proof: ParachainProof,
) -> Result<Vec<ParachainHeader>, Error> {
	if parachain_proof.parachains.is_empty() {
		return Ok(vec![]);
	}

	let mut indexed_leaf_hashes = Vec::with_capacity(parachain_proof.parachains.len());

	for para_header in &parachain_proof.parachains {
		let leaf = (para_header.para_id, para_header.header.clone());
		let hash: [u8; 32] = H::keccak256(&leaf.encode()).into();
		indexed_leaf_hashes.push((para_header.index as usize, hash));
	}

	indexed_leaf_hashes.sort_by_key(|(index, _)| *index);

	let (leaf_indices, leaf_hashes): (Vec<usize>, Vec<[u8; 32]>) =
		indexed_leaf_hashes.into_iter().unzip();
	let merkle_proof = MerkleProof::<MerkleHasher<H>>::new(parachain_proof.proof.clone());
	let valid = merkle_proof.verify(
		heads_root.0,
		&leaf_indices,
		&leaf_hashes,
		parachain_proof.total_leaves as usize,
	);

	if !valid {
		Err(Error::InvalidParachainProof)?;
	}

	Ok(parachain_proof.parachains)
}
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L225-256)
```rust
fn verify_mmr_leaf<H: Keccak256 + Send + Sync>(
	mmr: &MmrProof,
	mmr_root: H256,
) -> Result<(), Error> {
	// `leaf_indices` is supplied by the relayer in the unsigned consensus message;
	// an empty vector previously panicked the runtime via the unchecked `[0]` index
	// after the BEEFY signature and authority membership checks had already succeeded.
	// This verifier checks a single MMR leaf, so reject any proof that does not carry
	// exactly one leaf index.
	if mmr.mmr_proof.leaf_indices.len() != 1 {
		Err(Error::InvalidMmrProof)?
	}
	let leaf_index = mmr.mmr_proof.leaf_indices[0];
	let leaf_hash = H::keccak256(&mmr.latest_mmr_leaf.encode());
	let mmr_size = leaf_index_to_mmr_size(leaf_index);

	let mmr_proof = MmrMerkleProof::<[u8; 32], KeccakMerge<H>>::new(
		mmr_size,
		mmr.mmr_proof.items.iter().map(|h| (*h).into()).collect(),
	);
	let leaf_pos = leaf_index_to_pos(leaf_index);
	let leaf = (leaf_pos, leaf_hash.into());
	let valid = mmr_proof
		.verify(mmr_root.into(), vec![leaf])
		.map_err(|e| Error::MmrVerificationFailed(e.to_string()))?;

	if !valid {
		Err(Error::InvalidMmrProof)?
	}

	Ok(())
}
```

**File:** evm/src/consensus/SP1Beefy.sol (L121-126)
```text
        if (trustedState.latestHeight >= commitment.blockNumber) {
            return (trustedState, new IntermediateState[](0));
        }

        if (uint256(proof.mmrLeaf.parentNumber) + 1 != commitment.blockNumber) revert StaleMmrLeaf();

```

**File:** modules/consensus/beefy/verifier/src/error.rs (L38-48)
```rust
	/// The proof carries an mmr leaf other than the one appended at the commitment's block.
	/// An MMR is append-only, so historical leaves also prove against the commitment's root;
	/// accepting one would let a proof advance the height while replaying an old
	/// `beefy_next_authority_set` and suppressing the authority set rotation.
	#[error("Stale mmr leaf: leaf parent number {parent_number} is not {block_number} - 1")]
	StaleMmrLeaf {
		/// Parent block number carried by the proof's mmr leaf.
		parent_number: u32,
		/// Block number reported by the commitment.
		block_number: u32,
	},
```

**File:** evm/src/consensus/EcdsaBeefy.sol (L174-196)
```text
    // @dev Stack too deep, sigh solidity
    function verifyMmrLeaf(BeefyConsensusState memory trustedState, RelayChainProof memory relay, bytes32 mmrRoot)
        internal
        pure
    {
        bytes32 hash = keccak256(
            Codec.Encode(
                PartialBeefyMmrLeaf({
                    version: relay.latestMmrLeaf.version,
                    parentNumber: relay.latestMmrLeaf.parentNumber,
                    parentHash: relay.latestMmrLeaf.parentHash,
                    nextAuthoritySet: relay.latestMmrLeaf.nextAuthoritySet,
                    extra: relay.latestMmrLeaf.extra
                })
            )
        );
        uint256 leafCount = leafIndex(trustedState.beefyActivationBlock, relay.latestMmrLeaf.parentNumber) + 1;
        MerkleMountainRange.Leaf[] memory leaves = new MerkleMountainRange.Leaf[](1);
        leaves[0] = MerkleMountainRange.Leaf({index: relay.latestMmrLeaf.leafIndex, hash: hash});
        bool valid = MerkleMountainRange.VerifyProof(mmrRoot, relay.mmrProof, leaves, leafCount);

        if (!valid) revert InvalidMmrProof();
    }
```

**File:** modules/consensus/beefy/verifier/src/sp1.rs (L1-1)
```rust
// Copyright (C) Polytope Labs Ltd.
```

**File:** modules/consensus/beefy/verifier/src/test.rs (L408-413)
```rust
// SP1 proves that the leaf is *in* the mmr, not that it is the latest leaf, and an mmr is
// append-only — so every historical leaf also proves against the commitment's root. Accepting
// one would advance `latest_beefy_height` while replaying an old `beefy_next_authority_set`,
// suppressing the rotation and stranding the client on a set the relay chain has retired.
#[test]
fn rejects_sp1_proof_carrying_a_stale_mmr_leaf() {
```
