### Title
Naive BEEFY verifier lacks MMR-leaf freshness check, letting a stale leaf/parachain-heads-root be bound to a newly advanced consensus height - (File: modules/consensus/beefy/verifier/src/lib.rs)

### Summary
The "naive" (non-SP1) BEEFY consensus path — `verify_mmr_update_proof` / `verify_mmr_leaf` in `modules/consensus/beefy/verifier/src/lib.rs`, mirrored by `EcdsaBeefy.sol::verifyMmrUpdateProof`/`verifyMmrLeaf` on EVM — never checks that the submitted MMR leaf is the *latest* leaf for the commitment's `block_number`. The relayer-supplied `leaf_indices`/`leafIndex` and `latest_mmr_leaf`/`latestMmrLeaf` are taken as-is and only checked for MMR-inclusion against the signed root. Because an MMR is append-only, any historical leaf is still provably included under a newer signed root, so an attacker can pair a *fresh, validly signed* commitment (high `block_number`) with an *old* `mmr_leaf` (stale `parent_number_and_hash`, `beefy_next_authority_set`, and `leaf_extra`/parachain-heads-root).

The sibling SP1 path (`modules/consensus/beefy/verifier/src/sp1.rs::verify_sp1_consensus` and `evm/src/consensus/SP1Beefy.sol::verifyConsensus`) explicitly guards against exactly this by requiring `parent_number + 1 == block_number` (the `Error::StaleMmrLeaf` / `StaleMmrLeaf()` check), and the code/doc comments in `error.rs` and `docs/content/protocol/consensus/beefy.mdx` describe this as a required invariant ("the leaf is pinned by requiring `parent_number + 1 == block_number`"). This pinning is missing from the naive/ECDSA code path.

### Finding Description
`verify_mmr_update_proof` (naive path): [1](#0-0) 
selects the authority set correctly and validates signatures/membership correctly, then calls `verify_mmr_leaf`: [2](#0-1) 
This function only checks MMR inclusion of `mmr.latest_mmr_leaf` at the relayer-supplied `leaf_index` against the signed `mmr_root`. It never compares `mmr.latest_mmr_leaf.parent_number_and_hash.0 + 1` to `commitment.block_number`. The rotation logic that follows: [3](#0-2) 
and the returned `heads_root` (`mmr.latest_mmr_leaf.leaf_extra`) used later by `verify_parachain_headers` are therefore driven entirely by whatever (possibly stale) leaf the attacker chose, while `trusted_state.latest_beefy_height` advances to the *new* `commitment.block_number`.

Contrast with the SP1 path, which explicitly documents and enforces the pin: [4](#0-3) 
and the equivalent Solidity check: [5](#0-4) 
No equivalent check exists in `EcdsaBeefy.sol::verifyMmrLeaf`: [6](#0-5) 
and the `leafIndex` helper simply derives a leaf-count from the *attacker-chosen* `relay.latestMmrLeaf.parentNumber`, with no comparison to `commitment.blockNumber`: [7](#0-6) 

The `Error::StaleMmrLeaf` variant and its documentation confirm this is a known, intentional invariant elsewhere in the codebase, making its absence here an inconsistency rather than a deliberate design choice: [8](#0-7) 

This is reachable from the unprivileged, unsigned `handle_unsigned` entrypoint via `BeefyConsensusClient::verify_consensus`, which dispatches to the naive `verify_consensus` for `PROOF_TYPE_NAIVE` proofs: [9](#0-8) 

### Impact Explanation
Because `latest_beefy_height` advances to the attacker's chosen (real, validly signed) block number while `heads_root`/`leaf_extra` and `beefy_next_authority_set` are pinned to an older leaf:
- Parachain header inclusion proofs (`verify_parachain_headers`) get checked against a stale `heads_root`, so state commitments/intermediate states finalized at an *older* block can be accepted and stored as though newly proven "latest" state — enabling replay of outdated parachain state roots into the trusted consensus/state-machine store used for cross-chain proof verification.
- The next-authority-set rotation (`if next_authority_set.id > trusted_state.next_authorities.id`) can be silently suppressed because the stale leaf's `next_authority_set.id` may be behind the real one, stranding the light client on an authority set the relay chain has already retired while height still advances — directly breaking the stated invariant "a consensus update must advance only when the exact current or next authority set for that block authenticated it."

Because downstream ISMP request/response/timeout verification and settlement rely on `IntermediateState`/state commitments produced by this path, false or stale state acceptance here can propagate into unauthorized execution or stale-state-based settlement, matching the Critical "false state acceptance" impact category.

### Likelihood Explanation
The attacker needs only a validly signed, current BEEFY commitment for a fresh block (obtainable from any legitimate relay-chain finality event since the signature/authority-membership checks are enforced correctly) paired with a genuinely-included, but older, MMR leaf and its still-valid inclusion proof against the new root (guaranteed by MMR append-only property). No signature forgery or authority compromise is required, and the entrypoint (`handle_unsigned`) is explicitly unprivileged/permissionless, so likelihood is high assuming the missing check is confirmed absent in the currently deployed logic.

### Recommendation
Add the same freshness pin used in the SP1 path to the naive path: require `mmr.latest_mmr_leaf.parent_number_and_hash.0.saturating_add(1) == commitment.block_number` inside `verify_mmr_leaf` (or `verify_mmr_update_proof`) in `modules/consensus/beefy/verifier/src/lib.rs`, returning `Error::StaleMmrLeaf` on mismatch, and add the equivalent `parentNumber + 1 == blockNumber` check to `EcdsaBeefy.sol::verifyMmrLeaf`.

### Proof of Concept
Conceptual (matches the "fast validation" methodology in the question):
1. Build a valid `ConsensusState` at height H with known `current_authorities`/`next_authorities`.
2. Obtain a genuine signed BEEFY commitment for a later block H' (H' > H) signed by the correct current/next authority set — passes all signature/membership checks.
3. Instead of supplying the MMR leaf for block H'-1 (the real latest leaf), supply an older, real leaf (e.g., for block H) together with its valid MMR inclusion proof against the new root (valid because MMR is append-only).
4. Submit via `handle_unsigned` → `BeefyConsensusClient::verify_consensus` → `verify_consensus::<SubstrateCrypto>` (naive path).
5. Observe: `verify_mmr_leaf` succeeds (no `parent_number+1==block_number` check exists), `trusted_state.latest_beefy_height` is set to H' while `heads_root`/`next_authority_set` reflect the state as of the older leaf — the state advances height without the invariant being honored.

Note: I could not execute this against a live/test build within this review; the analysis is based on static comparison of the naive path (`lib.rs`, `EcdsaBeefy.sol`) against the SP1 path (`sp1.rs`, `SP1Beefy.sol`, and the accompanying unit/foundry test `rejects_sp1_proof_carrying_a_stale_mmr_leaf` / `testRejectsStaleMmrLeaf`), which explicitly test for and guard against this exact scenario only in the SP1 path.

### Citations

**File:** modules/consensus/beefy/verifier/src/lib.rs (L105-130)
```rust
pub fn verify_mmr_update_proof<H: Keccak256 + EcdsaRecover + Send + Sync>(
	mut trusted_state: ConsensusState,
	mmr: MmrProof,
) -> Result<(ConsensusState, H256), Error> {
	let signatures_length = mmr.signed_commitment.signatures.len();
	let latest_height = mmr.signed_commitment.commitment.block_number;

	if trusted_state.latest_beefy_height >= latest_height {
		return Err(Error::StaleHeight {
			trusted_height: trusted_state.latest_beefy_height,
			current_height: latest_height,
		});
	}

	let commitment = mmr.signed_commitment.commitment.clone();

	// Pick the authority set the commitment claims to be signed under, then judge
	// participation against that set alone.
	let authority_set = if commitment.validator_set_id == trusted_state.current_authorities.id {
		&trusted_state.current_authorities
	} else if commitment.validator_set_id == trusted_state.next_authorities.id {
		&trusted_state.next_authorities
	} else {
		return Err(Error::UnknownAuthoritySet { id: commitment.validator_set_id });
	};

```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L179-186)
```rust
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

**File:** evm/src/consensus/SP1Beefy.sol (L119-126)
```text
        MiniCommitment memory commitment = proof.commitment;
        // Stale proofs are a no-op
        if (trustedState.latestHeight >= commitment.blockNumber) {
            return (trustedState, new IntermediateState[](0));
        }

        if (uint256(proof.mmrLeaf.parentNumber) + 1 != commitment.blockNumber) revert StaleMmrLeaf();

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

**File:** evm/src/consensus/EcdsaBeefy.sol (L231-238)
```text
    // @dev Calculates the mmr leaf index for a block whose parent number is given.
    function leafIndex(uint256 activationBlock, uint256 parentNumber) internal pure returns (uint256) {
        if (activationBlock == 0) {
            return parentNumber;
        } else {
            return parentNumber - activationBlock;
        }
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

**File:** modules/ismp/clients/beefy/src/consensus.rs (L89-94)
```rust
		let (new_state, verified_parachains) = match *proof_type {
			PROOF_TYPE_NAIVE => {
				let consensus_proof: ConsensusMessage = codec::Decode::decode(&mut &payload[..])
					.map_err(|e| BeefyError::DecodeNaiveProof(format!("{e:?}")))?;
				verify_consensus::<SubstrateCrypto>(consensus_state, consensus_proof)?
			},
```
