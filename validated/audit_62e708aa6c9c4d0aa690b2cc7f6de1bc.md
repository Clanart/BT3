This is a valid, distinct finding — though not exactly the mechanism described in the submitted question, the same root cause exists and is exploitable through the base ECDSA path. The `StaleMmrLeaf` freshness check (`parent_number + 1 == block_number`) exists in `sp1.rs::verify_sp1_consensus` but is **missing** from `lib.rs::verify_mmr_update_proof`.

### Title
Missing MMR leaf freshness check in `verify_mmr_update_proof` allows height advancement with stale authority-set state - (File: modules/consensus/beefy/verifier/src/lib.rs)

### Summary
`verify_mmr_update_proof` validates that a submitted `mmr.latest_mmr_leaf` is included in the MMR root carried by a validly-signed commitment, but never checks that this leaf is the one actually appended *at* `commitment.block_number`. Since an MMR is append-only, any historical leaf remains provable against a later root. This lets an unprivileged relayer pair a fresh, validly-signed commitment (advancing `latest_beefy_height` to the real chain tip) with a stale, older `latest_mmr_leaf`/inclusion proof, so that the rotation check on line 179 sees an old `beefy_next_authority_set.id` and never fires.

### Finding Description [1](#0-0) 
`verify_mmr_leaf` only checks that `mmr.latest_mmr_leaf` is included at `mmr.mmr_proof.leaf_indices[0]` against the `mmr_root` extracted from the commitment payload [2](#0-1) . It never confirms `leaf_indices[0]`/the leaf's `parent_number_and_hash` corresponds to `commitment.block_number`. Because an MMR is append-only, a relayer can freely pick any earlier leaf index and still produce a valid Merkle inclusion proof against the current (later) root.

The equivalent SP1 path explicitly acknowledges and closes this exact hole: `verify_sp1_consensus` enforces `parent_number.saturating_add(1) == proof.block_number` via `Error::StaleMmrLeaf`, with a code comment describing precisely this attack — "An mmr is append-only, so a historical leaf also proves against the commitment's root. Accepting one would advance `latest_beefy_height` while replaying an old leaf, suppressing the rotation ... and stranding the client on a set the relay chain has retired" [3](#0-2) . The corresponding `StaleMmrLeaf` error/comment in `error.rs` confirms this was a recognized, deliberately-mitigated risk for SP1 [4](#0-3) , but no analogous check was added to the base ECDSA `verify_mmr_update_proof`.

As a result: `trusted_state.latest_beefy_height = latest_height` unconditionally advances to the real, current chain height (backed by a genuinely valid signed commitment), while `trusted_state.current_authorities`/`next_authorities` remain pinned to whatever old authority set the attacker-chosen stale leaf reports, because `mmr.latest_mmr_leaf.beefy_next_authority_set.id > trusted_state.next_authorities.id` is false for a stale leaf [5](#0-4) .

Regarding the submitted question's specific framing (replaying an old commitment signed by a retired authority set to skip its "mandate boundary"): that variant requires an actual retired-but-colluding validator set to produce signatures beyond their legitimate signing window, which is excluded ("malicious peers/validators/relayers" per scope). However, the underlying broken invariant it points at — height can advance without authority rotation keeping pace — is real and reachable by a purely unprivileged relayer via the stale-leaf substitution described above, without any validator collusion.

### Impact Explanation
This desynchronizes the light client's height from its authority-set state: `latest_beefy_height` reaches the real chain tip while `current_authorities`/`next_authorities` remain outdated. Any subsequent legitimate commitment actually signed by the real (rotated) validator set with `validator_set_id` not matching either stale `current_authorities.id` or `next_authorities.id` would be rejected as `UnknownAuthoritySet`, potentially stalling the bridge, or — more concerning — the client can be kept permanently on an outdated authority mapping, undermining the "false remote state must never become trusted" invariant central to the consensus-proof pivot.

### Likelihood Explanation
Requires only an unprivileged relayer submitting a proof: a genuinely valid, freshly signed commitment (freely available since BEEFY commitments are public gossip) combined with an old MMR leaf and its inclusion proof against the new root (trivially constructible from public MMR data). No validator collusion needed.

### Recommendation
Add the same freshness check used in `verify_sp1_consensus` to `verify_mmr_update_proof`/`verify_mmr_leaf`: require `mmr.latest_mmr_leaf.parent_number_and_hash.0 + 1 == commitment.block_number` (and/or that `leaf_indices[0]` corresponds to the leaf appended at that block), rejecting with an error analogous to `Error::StaleMmrLeaf` if it does not.

### Proof of Concept
1. Trusted state: `current_authorities.id = A`, `next_authorities.id = B`, `latest_beefy_height = H0`.
2. Wait for the real relay chain to produce a validly signed commitment at height `H1 >> H0` (post-rotation, signed by set `B` or later); this satisfies `validator_set_id == trusted_state.next_authorities.id == B` and passes the authority multi-proof check.
3. Instead of submitting the MMR leaf actually appended at `H1`, submit an old leaf (from height `H0+1`, still under set `A`/`B`) together with a Merkle inclusion proof of that old leaf against the `mmr_root` in the `H1` commitment (valid, since MMR is append-only).
4. `verify_mmr_leaf` passes (old leaf legitimately included in the newer root). `mmr.latest_mmr_leaf.beefy_next_authority_set.id <= trusted_state.next_authorities.id`, so no rotation occurs at line 179.
5. `trusted_state.latest_beefy_height` is set to `H1` — the real chain tip — while `current_authorities`/`next_authorities` remain stuck at `A`/`B`, even though the real chain has since rotated further.

### Citations

**File:** modules/consensus/beefy/verifier/src/lib.rs (L177-186)
```rust
	verify_mmr_leaf::<H>(&mmr, mmr_root)?;

	if mmr.latest_mmr_leaf.beefy_next_authority_set.id > trusted_state.next_authorities.id {
		trusted_state.current_authorities = trusted_state.next_authorities.clone();
		trusted_state.next_authorities = mmr.latest_mmr_leaf.beefy_next_authority_set.clone();
	}

	trusted_state.latest_beefy_height = latest_height;

	Ok((trusted_state, mmr.latest_mmr_leaf.leaf_extra))
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

**File:** modules/consensus/beefy/verifier/src/sp1.rs (L66-74)
```rust
	// An mmr is append-only, so a historical leaf also proves against the commitment's root.
	// Accepting one would advance `latest_beefy_height` while replaying an old leaf, suppressing
	// the rotation below and stranding the client on a set the relay chain has retired.
	// `parent_number` is part of the leaf preimage hashed into `leaf_hash`, so pinning it here
	// pins the leaf itself.
	let parent_number = proof.mmr_leaf.parent_number_and_hash.0;
	if parent_number.saturating_add(1) != proof.block_number {
		Err(Error::StaleMmrLeaf { parent_number, block_number: proof.block_number })?;
	}
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
