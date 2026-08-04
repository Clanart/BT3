### Title
Missing MMR-leaf freshness check in the naive BEEFY consensus path allows stale-leaf replay that decouples signed height from authority-set rotation - (File: modules/consensus/beefy/verifier/src/lib.rs)

### Summary
`verify_mmr_update_proof` / `verify_mmr_leaf` in the naive (`PROOF_TYPE_NAIVE`) BEEFY verifier accept **any** MMR leaf that merkle-proves against the signed `mmr_root`, without checking that the leaf is the *latest* one (i.e. `leaf.parent_number + 1 == commitment.block_number`). Because an MMR is append-only, an old leaf still verifies against any newer root, so an attacker-supplied relayer message can pair a validly-signed, fresh commitment/`mmr_root` with an arbitrarily old, attacker-chosen leaf (`mmr.latest_mmr_leaf`) — mixing metadata from two different heights/contexts under one accepted proof.

### Finding Description
`verify_consensus` calls `verify_mmr_update_proof`, which:
1. Verifies BEEFY signatures authenticate `commitment` (and thus `mmr_root`) — this binding is correct. [1](#0-0) 
2. Calls `verify_mmr_leaf(&mmr, mmr_root)` to prove `mmr.latest_mmr_leaf` is included in that root, then unconditionally uses fields from that leaf (`beefy_next_authority_set`, `leaf_extra`) and sets `trusted_state.latest_beefy_height = latest_height` (the *commitment's* height, not the leaf's height). [2](#0-1) 

`verify_mmr_leaf` only checks that exactly one `leaf_index` was supplied and that the corresponding leaf hash merkle-proves against `mmr_root` — it never checks `parent_number + 1 == block_number` (freshness): [3](#0-2) 

The `Error::StaleMmrLeaf` variant exists in the shared error enum specifically to prevent this, with a comment explaining the exact attack: *"An MMR is append-only, so historical leaves also prove against the commitment's root; accepting one would let a proof advance the height while replaying an old `beefy_next_authority_set` and suppressing the authority set rotation."* [4](#0-3) 

This exact check **is implemented** in the SP1 proof path (`verify_sp1_consensus`), with a comment noting it must be kept in step with the naive/Solidity equivalent: [5](#0-4) 

But the naive path in `lib.rs` never performs this check — `leaf_index` and the leaf's own `parent_number` are entirely attacker-controlled in the unsigned `ConsensusMessage`, only bounded by "must exist and merkle-prove under the current `mmr_root`," which any historical leaf satisfies.

### Impact Explanation
An attacker (any unsigned relayer message via `handle_unsigned`) can submit:
- a validly signed BEEFY commitment for a new, higher `block_number` (fresh signatures/authority set proof — advancing `trusted_state.latest_beefy_height`), together with
- an old MMR leaf (low `leaf_index`, stale `parent_number`) that still merkle-proves under the new root.

This lets the verifier accept `trusted_state.latest_beefy_height` advancing to the new height while `current_authorities`/`next_authorities` rotation logic only sees the stale leaf's `beefy_next_authority_set` (which will not exceed the already-known `next_authorities.id`, so no rotation occurs even though the real chain has since rotated). The consensus client can be driven into a state where `latest_beefy_height` has advanced past the point where the real chain's authority set actually rotated, while the client's `current_authorities`/`next_authorities` are stuck on the old set. Future genuinely-signed commitments from the real (rotated) authority set will then be rejected by `UnknownAuthoritySet`, since neither `current_authorities.id` nor `next_authorities.id` will match — permanently bricking further consensus updates for that state machine unless a privileged operator intervenes.

This does not directly let fabricated parachain state be accepted (the `heads_root`/parachain headers used are still authentically proven under whichever root was used), but it breaks the append-only "latest leaf" invariant the codebase itself documents as security-critical, and can permanently stall consensus updates for a state machine, which downstream can block or delay legitimate withdrawal/settlement proofs that depend on the bridge's liveness. It does not appear to allow immediate acceptance of a *false* state root, so it falls short of a direct "Critical: false state acceptance" per the question's exact framing, but it is a real correctness/liveness bug matching the described "mixed context" pattern (signature context bound to new height, leaf context stale) that the developers themselves flagged and fixed in the parallel SP1 path but not here.

### Likelihood Explanation
High for triggering via unprivileged input: `leaf_index`, `parent_number`, and the full `latest_mmr_leaf` structure are attacker-supplied fields in the unsigned `ConsensusMessage` passed through `pallet_ismp::handle_unsigned`, and no code path enforces leaf freshness for the naive proof type. The attacker only needs one genuinely-signed, fresh BEEFY commitment (obtainable by observing real relay-chain finality) plus a legitimate historical MMR leaf/proof (also publicly obtainable), which they can freely recombine.

### Recommendation
Add the same freshness check used in `sp1.rs` to `verify_mmr_leaf`/`verify_mmr_update_proof` in `lib.rs`: reject unless `mmr.latest_mmr_leaf.parent_number.saturating_add(1) == commitment.block_number` (equivalently, derive/validate `leaf_index` against the expected leaf count for `block_number`), returning `Error::StaleMmrLeaf` as already defined, mirroring the Solidity/`EcdsaBeefy.sol` leaf-count derivation (`leafIndex`) that ties leaf position to the claimed block height rather than trusting an attacker-chosen `leaf_index`.

### Proof of Concept
1. Trusted state at height `H0`, `current_authorities = A0`.
2. Attacker submits `ConsensusMessage` #1 with a genuine signed commitment at height `H1 > H0` (signed by `A0`/`A_next`), but sets `mmr.mmr_proof.leaf_indices = [old_index]` and `mmr.latest_mmr_leaf` = leaf from an old block `H_old << H1`, with a valid merkle proof of that old leaf against the new `mmr_root`.
3. `verify_mmr_update_proof` passes: signature/authority checks succeed (bound to `H1`'s commitment), `verify_mmr_leaf` succeeds (old leaf genuinely included in new root) — no freshness check exists.
4. `trusted_state.latest_beefy_height` is set to `H1`; `next_authorities` rotation is skipped or applied incorrectly because it's driven by the stale leaf's `beefy_next_authority_set` rather than the actual authority set current at `H1`.
5. If, in reality, the authority set had already rotated to `A2` by `H1` (i.e., a legitimate future commitment would be signed by `A2`), any subsequent genuine update signed by `A2` is rejected with `UnknownAuthoritySet`, since `trusted_state` never learned about `A2` — the client is stuck below the current real height/authority set while believing it is caught up to `H1`. [2](#0-1)  and [5](#0-4)  together confirm the check is a known necessary invariant that is present in one path and missing in the other.

### Citations

**File:** modules/consensus/beefy/verifier/src/lib.rs (L143-176)
```rust
	let mmr_root = H256::from_slice(mmr_root_data);

	let commitment_hash = H::keccak256(&commitment.encode());
	let mut authority_leaves: Vec<[u8; 32]> = Vec::new();
	let mut authority_indices = Vec::new();

	for sig in mmr.signed_commitment.signatures.iter() {
		let uncompressed = H::secp256k1_recover(&commitment_hash.0, &sig.signature)
			.map_err(|_| Error::FailedToRecoverPublicKey)?;

		let hashed_uncompressed = H::keccak256(&uncompressed);

		let mut eth_address = [0u8; 20];
		eth_address.copy_from_slice(&hashed_uncompressed.as_ref()[12..]);

		let authority_address_hash = H::keccak256(&eth_address);

		authority_leaves.push(authority_address_hash.into());
		authority_indices.push(sig.index as usize);
	}

	let merkle_proof = MerkleProof::<MerkleHasher<H>>::new(mmr.authority_proof.clone());

	let valid = merkle_proof.verify(
		authority_set.keyset_commitment.into(),
		&authority_indices,
		&authority_leaves,
		authority_set.len as usize,
	);

	if !valid {
		Err(Error::InvalidAuthoritiesProof)?;
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
