## Analysis

The `Reject.sol` bug pattern — counting an array of attacker-supplied entries toward a quorum/consensus threshold without verifying the entries are distinct signers — has a real analog in `verify_mmr_update_proof` in this repository's BEEFY consensus verifier.

### Title
Duplicate-signature inflation of BEEFY supermajority threshold - (`modules/consensus/beefy/verifier/src/lib.rs`)

### Summary
`verify_mmr_update_proof` computes `signatures_length = mmr.signed_commitment.signatures.len()` and checks it against a 2/3+1 supermajority threshold via `check_participation_threshold` [1](#0-0) , but it never verifies that the entries in `mmr.signed_commitment.signatures` come from distinct authorities before counting them toward that threshold.

### Finding Description
For each entry in `mmr.signed_commitment.signatures`, the loop recovers the signer's address and pushes `(sig.index, authority_address_hash)` pairs into `authority_leaves`/`authority_indices` [2](#0-1) . There is no `BTreeSet`/dedup check on `sig.index` or on the recovered address analogous to the ones present in the sibling verifiers in this same codebase — e.g. Pharos's `verify_validator_membership` explicitly deduplicates participant keys with a `BTreeSet` and returns `Error::DuplicateParticipant` on collision [3](#0-2) , and Tendermint's `ensure_unique_addresses` does the same for validator sets [4](#0-3) . The BEEFY verifier lacks this guard entirely.

The threshold check `check_participation_threshold(signatures_length as u32, authority_set.len)` only compares the raw count of submitted signature entries to `2/3 * total + 1` [5](#0-4) , exactly mirroring the flawed `submissionCount`/`maximumMissingSubmissions` computation in the original `Reject.sol` bug, where inflating an array's length with repeated entries from the same signer fakes broader participation.

### Impact Explanation
If a single relayer (or colluding minority of authorities smaller than the real 2/3+1 threshold) can submit multiple copies of the same valid ECDSA signature (same `sig.index`, same `sig.signature`, or the same authority producing valid signatures placed at different claimed `index` values that still validate against the merkle multi-proof for that index), `signatures_length` inflates without genuine distinct-authority backing. Because BEEFY consensus updates directly advance `trusted_state.latest_beefy_height`, roll `current_authorities`/`next_authorities` forward, and produce the `heads_root` that is trusted for parachain header inclusion proofs, forging apparent supermajority participation here means false state commitments (fabricated MMR roots / parachain headers) become "finalized" and trusted — directly matching the Hyperbridge Impact Gate's "false proof/state acceptance" category, which underlies all downstream request/response processing, fund releases, and message delivery that depend on this consensus client.

### Likelihood Explanation
This requires closer confirmation of exact exploitability, which I could not fully verify from static reading alone: the actual protection depends on (a) whether `rs_merkle`'s `MerkleProof::verify` rejects a set of `(index, leaf)` pairs containing duplicate indices as part of multi-proof validation, and (b) whether the relay chain's off-chain proof-collection process could ever hand the on-chain verifier a `signed_commitment.signatures` vector with repeated `index`/signature pairs reaching this function (in the intended flow, signatures come from real relay-chain gossip, which is expected to be distinct per validator, but this code path does not itself enforce that invariant — it trusts the caller-supplied vector's raw length as the participation count). Because I cannot access the `rs_merkle` crate implementation to confirm whether the underlying multi-proof verification silently tolerates duplicate `(index, leaf)` entries, I cannot say with certainty whether this is fully exploitable end-to-end versus only a defense-in-depth gap already caught by the merkle multi-proof's uniqueness-of-index handling.

### Recommendation
Add an explicit dedup check on `sig.index` (and/or the recovered authority address) before computing `signatures_length` / before running the threshold check, mirroring the pattern already used in `pharos::verify_validator_membership` and `tendermint::ensure_unique_addresses`: collect indices into a `BTreeSet` and reject with a new `Error::DuplicateAuthorityIndex` if the set's length differs from `mmr.signed_commitment.signatures.len()`, before computing `signatures_length` for the participation-threshold check.

### Proof of Concept
Not independently confirmed against the `rs_merkle` crate's exact multi-proof semantics for duplicate indices; a concrete PoC would require constructing a `MmrProof` where `signed_commitment.signatures` contains repeated `(index, signature)` pairs recovering to the same authority address at insufficient real distinct-signer count, and confirming `merkle_proof.verify` still returns `true` for the duplicated `authority_indices`/`authority_leaves` — this step needs to be validated against the actual `rs_merkle` implementation, which was not available in the indexed codebase content I could retrieve. [6](#0-5)

### Citations

**File:** modules/consensus/beefy/verifier/src/lib.rs (L109-175)
```rust
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

	if !check_participation_threshold(signatures_length as u32, authority_set.len) {
		return Err(Error::SuperMajorityRequired);
	}

	let mmr_root_data = commitment
		.payload
		.get_raw(&MMR_ROOT_PAYLOAD_ID)
		.ok_or(Error::MmrRootHashMissing)?;

	if mmr_root_data.len() != 32 {
		return Err(Error::InvalidMmrRootHashLength { len: mmr_root_data.len() });
	}
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

**File:** modules/consensus/beefy/verifier/src/lib.rs (L258-260)
```rust
/// Checks for supermajority participation
fn check_participation_threshold(len: u32, total: u32) -> bool {
	len >= ((2 * total) / 3) + 1
```

**File:** modules/consensus/pharos/verifier/src/lib.rs (L143-157)
```rust
/// Verify that all participating validators are members of the trusted validator set.
fn verify_validator_membership(
	validator_set: &ValidatorSet,
	participants: &[BlsPublicKey],
) -> Result<(), Error> {
	let deduped: alloc::collections::BTreeSet<&[u8]> =
		participants.iter().map(|k| k.as_ref()).collect();
	if deduped.len() != participants.len() {
		return Err(Error::DuplicateParticipant);
	}
	if let Some(key) = participants.iter().find(|key| !validator_set.contains(key)) {
		return Err(Error::UnknownValidator { key: key.clone() });
	}
	Ok(())
}
```

**File:** modules/consensus/tendermint/verifier/src/verifier.rs (L217-229)
```rust
/// Rejects a validator set that reuses the same address twice. Each address is a
/// hash of its public key, so a duplicate signals a set that was not built honestly.
fn ensure_unique_addresses(
	validators: &[cometbft::validator::Info],
) -> Result<(), VerificationError> {
	let unique = validators.iter().map(|v| v.address).collect::<BTreeSet<_>>();
	if unique.len() != validators.len() {
		return Err(VerificationError::ValidatorSetError(
			"duplicate validator address in set".to_string(),
		));
	}
	Ok(())
}
```
