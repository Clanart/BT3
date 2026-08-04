## Finding Summary

The exact function cited in the question (`modules/consensus/bsc/verifier/src/error.rs::from`) is just an auto-generated `thiserror` implementation for `Error` — it has no logic that could amplify votes. However, tracing the actual "duplicate proof item" pattern the question describes across all of Hyperbridge's consensus verifiers surfaces a real asymmetry worth flagging:

- **BSC verifier** (`modules/consensus/bsc/verifier/src/lib.rs:63-90`) represents participation as a fixed-width `Bitvector`. Each validator maps to exactly one bit, so a "duplicate vote" is structurally impossible — `participant_count` is `count_ones()` over unique bit positions, not a count of raw signature entries. [1](#0-0) 

- **Pharos verifier** explicitly guards against this exact class of bug: `verify_validator_membership` builds a `BTreeSet` of participant keys and rejects the batch with `Error::DuplicateParticipant` if `deduped.len() != participants.len()`, *before* the stake/threshold check runs. [2](#0-1) 

- **BEEFY verifier** (`modules/consensus/beefy/verifier/src/lib.rs::verify_mmr_update_proof`) has no equivalent guard. It computes `signatures_length = mmr.signed_commitment.signatures.len()` directly from the attacker-supplied `Vec<SignatureWithAuthorityIndex>` and feeds that raw count into `check_participation_threshold`, without deduplicating on `sig.index`: [3](#0-2) 
It then builds `authority_leaves`/`authority_indices` by iterating the same (possibly duplicated) signature list and passes them straight to `merkle_proof.verify(...)` for authority-set membership: [4](#0-3) 

Because BEEFY commitments and votes are gossiped publicly (any relayer/observer can see a legitimate authority's signature over a given commitment), an attacker does not need to forge a new signature — they only need to **replay one authentic, publicly-observed signature multiple times** (same or repeated `authority_index`) in the `signatures` vector. Each repeated entry is a genuine leaf of the authority-set Merkle tree, so `merkle_proof.verify` proves membership of that (real) leaf without objection to it appearing more than once, while `signatures_length` (used for the 2/3 threshold check) counts it as if it were an independent signer.

## Impact

This lets an attacker pad `signatures_length` past the 2/3 threshold using far fewer *distinct* real authority signatures than intended, causing `check_participation_threshold` to accept a BEEFY MMR-root update that was not actually finalized by supermajority. Since the BEEFY consensus client underlies parachain-header verification and ultimately Hyperbridge state-machine commitments, false acceptance here can cascade into false state acceptance for downstream request/response/timeout proofs — matching the "Critical: false state acceptance" impact class in the question.

## Caveat / What I could not fully verify

I could not inspect the concrete implementation of `MerkleProof::<MerkleHasher<H>>::verify` (from the `merkle_mountain_range`/binary-merkle-tree crate used here) to confirm with certainty that it tolerates duplicate `(index, leaf)` pairs in its input arrays without rejecting the proof outright or panicking. If that library internally deduplicates or requires strictly-increasing unique indices and errors on duplicates, this specific amplification path would be blocked at the membership-proof stage even though the threshold-counting code itself has no dedup guard. I was not able to pull that crate's source in the remaining budget to confirm either way — this is the single open question that determines whether this is exploitable end-to-end or merely a defense-in-depth gap (the missing dedup check is real either way, given the contrast with Pharos's explicit `DuplicateParticipant` guard).

### Recommendation
Add an explicit uniqueness check on `sig.index` in `verify_mmr_update_proof` (mirroring Pharos's `verify_validator_membership`) before computing `signatures_length` and before invoking `merkle_proof.verify`, e.g. collecting indices into a `BTreeSet` and rejecting the message if `set.len() != signatures.len()`.

### Citations

**File:** modules/consensus/bsc/verifier/src/lib.rs (L81-90)
```rust
	// We have to use the same threshold specified in the bsc parlia consensus which is 2/3
	// https://github.com/bnb-chain/bsc/blob/da35ee13e2fe38efaeab2d6fb27f112332459b50/consensus/parlia/parlia.go#L557
	let participant_count = validators_bit_set
		.iter()
		.take(current_validators.len())
		.filter(|bit| **bit)
		.count();
	if participant_count < ((2 * current_validators.len()) / 3) {
		Err(Error::NotEnoughParticipants)?
	}
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

**File:** modules/consensus/beefy/verifier/src/lib.rs (L109-133)
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
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L145-175)
```rust
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
