## Analysis

The external report's core broken invariant: **an array of participants used to satisfy a threshold/quorum requirement is never checked for uniqueness, so repeated entries can inflate participation counts beyond what the true number of distinct signers should allow.**

The direct Hyperbridge analog is in the BEEFY relay-chain consensus verifier, which underpins state-proof trust for the entire bridge.

### Title
BEEFY authority participation threshold counts raw signature array length instead of unique signer indices - (File: modules/consensus/beefy/verifier/src/lib.rs)

### Summary
`verify_mmr_update_proof` in the BEEFY verifier checks supermajority participation using `signatures_length = mmr.signed_commitment.signatures.len()` [1](#0-0)  — the raw count of entries in the attacker/relayer-supplied `signatures` vector — rather than the count of distinct authority indices that actually signed. There is no dedup/uniqueness check on `sig.index` before this count is used to satisfy `check_participation_threshold`.

### Finding Description
For each entry in `mmr.signed_commitment.signatures`, the verifier recovers an ECDSA address, hashes it, and records `(authority_leaves, authority_indices)` pairs, pushing `sig.index` unchecked [2](#0-1) . The threshold gate runs *before* any membership/merkle verification, using only the raw vector length:

```rust
if !check_participation_threshold(signatures_length as u32, authority_set.len) {
    return Err(Error::SuperMajorityRequired);
}
``` [3](#0-2) 

Nothing in this function (or in `EcdsaBeefy.sol`'s equivalent `verifyMmrUpdateProof`, which likewise uses `sigLen = relayProof.signedCommitment.votes.length` [4](#0-3) ) rejects a `signatures`/`votes` array containing multiple entries with the same `authorityIndex`/`index`. This is exactly the class of bug identified in the external report: the node (here, the consensus verifier) hands an unfiltered "players" array to the threshold-consensus logic without validating uniqueness, letting a repeated entry count multiple times toward the required quorum.

By contrast, other consensus verifiers in this same repo *do* explicitly guard against this: the Pharos BLS verifier rejects duplicate participant keys via a `BTreeSet` dedup check before counting stake [5](#0-4) , and the Tendermint verifier has `ensure_unique_addresses` for the same reason [6](#0-5) . The BEEFY path lacks this equivalent check on `authority_indices`/`sig.index`.

Whether this is actually exploitable end-to-end depends on whether the downstream `rs_merkle::MerkleProof::verify` call rejects duplicate indices in a multi-proof (which would make the merkle check fail even though the threshold gate already passed) — this needs to be confirmed against the `rs_merkle` crate's multiproof semantics, since the local codebase only calls into it and does not re-implement duplicate-index rejection itself.

### Impact Explanation
If the merkle multiproof verification does not itself enforce index/leaf uniqueness (a property of the external `rs_merkle` dependency, not verified locally), an authority (or someone in possession of one or more valid signatures over the commitment) could pad the `signatures` array with duplicate `(index, signature)` pairs to satisfy `check_participation_threshold` with fewer distinct real signers than the 2/3+1 supermajority requires. This would let a forged/insufficiently-attested BEEFY commitment be accepted as trusted consensus state, which downstream drives acceptance of parachain header proofs and therefore state commitments used across the entire ISMP messaging stack — i.e., false state acceptance.

### Likelihood Explanation
The threshold check is purely a length comparison with no uniqueness enforcement in this module, and no other code path in `verify_mmr_update_proof` closes the gap before the threshold gate runs. The likelihood that this is fully exploitable hinges on the external `rs_merkle` crate's behavior for duplicate leaf indices, which is outside this repo's index and should be confirmed with source-level testing.

### Recommendation
Before (or as part of) the `check_participation_threshold` call, deduplicate `sig.index` values (e.g., via a `BTreeSet`) and use the count of unique indices for the threshold comparison, mirroring the pattern already used in `modules/consensus/pharos/verifier/src/lib.rs::verify_validator_membership` and `modules/consensus/tendermint/verifier/src/verifier.rs::ensure_unique_addresses`. Apply the same fix to the mirrored Solidity path in `evm/src/consensus/EcdsaBeefy.sol`.

### Proof of Concept
Not fully confirmable from local code alone — this is a locally-provable *gap* (missing uniqueness check on `sig.index`/`authorityIndex` prior to threshold counting), but full exploitability requires characterizing `rs_merkle::MerkleProof::verify`'s handling of duplicate leaf indices, which lives outside this repository's indexed code. A conclusive PoC would need to: (1) construct a `signatures` vector with N unique-signer signatures plus M duplicate-index copies of one of those signatures such that `N + M >= threshold` while `N < threshold`, and (2) confirm the `rs_merkle` multiproof accepts the resulting `authority_indices`/`authority_leaves` pair set.

### Citations

**File:** modules/consensus/beefy/verifier/src/lib.rs (L109-109)
```rust
	let signatures_length = mmr.signed_commitment.signatures.len();
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L131-133)
```rust
	if !check_participation_threshold(signatures_length as u32, authority_set.len) {
		return Err(Error::SuperMajorityRequired);
	}
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L145-162)
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
```

**File:** evm/src/consensus/EcdsaBeefy.sol (L127-140)
```text
        uint256 sigLen = relayProof.signedCommitment.votes.length;
        uint256 latestHeight = relayProof.signedCommitment.commitment.blockNumber;
        Commitment memory commitment = relayProof.signedCommitment.commitment;
        if (
            commitment.validatorSetId != trustedState.currentAuthoritySet.id
                && commitment.validatorSetId != trustedState.nextAuthoritySet.id
        ) {
            revert UnknownAuthoritySet();
        }

        bool isCurrentAuthorities = commitment.validatorSetId == trustedState.currentAuthoritySet.id;
        AuthoritySetCommitment memory authoritySet =
            isCurrentAuthorities ? trustedState.currentAuthoritySet : trustedState.nextAuthoritySet;
        if (!checkParticipationThreshold(sigLen, authoritySet.len)) revert SuperMajorityRequired();
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
