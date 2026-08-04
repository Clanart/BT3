## Analysis

The external report's core broken invariant is: **a threshold/supermajority check counts array *length* instead of *distinct signers*, so one signer's signature(s) can be replayed within the array to satisfy the threshold.**

I searched Hyperbridge's consensus-proof verifiers (BEEFY, Pharos, BSC, sync-committee, Tendermint) — this is exactly the class of code that turns a signature list into a trust decision, structurally identical to `bigclaim()`'s signer-count check. Comparing the verifiers side-by-side surfaces an inconsistency:

- **Pharos verifier** explicitly dedupes participant keys with a `BTreeSet` and rejects `Error::DuplicateParticipant` before computing stake/threshold: [1](#0-0) 
- **Tendermint verifier** has `ensure_unique_addresses` with dedicated tests (`rejects_duplicate_addresses`): [2](#0-1) 
- **BSC / sync-committee verifiers** use bitsets (`validators_bit_set`, `sync_committee_bits`), which structurally cannot contain duplicates.
- **BEEFY verifier (both Rust and Solidity)** has no equivalent check. `check_participation_threshold` / `checkParticipationThreshold` validate only the *count* of signatures supplied, not the number of *distinct* authority indices behind them: [3](#0-2) [4](#0-3) 

The loop that follows recovers a signer address per vote and builds a merkle leaf keyed by the vote's claimed `authority_index`/`authorityIndex`, then verifies those leaves against the authority-set root — but never asserts the indices are pairwise distinct: [5](#0-4) 

The Solidity `EcdsaBeefy` contract mirrors this exactly — `sigLen` (array length) gates `checkParticipationThreshold`, then each vote is independently recovered and pushed into a `MerkleMultiProof.Leaf[]` without an index-uniqueness check: [6](#0-5) 

### Title
Missing duplicate-signer check in BEEFY supermajority threshold - (File: `modules/consensus/beefy/verifier/src/lib.rs`, `evm/src/consensus/EcdsaBeefy.sol`)

### Summary
BEEFY consensus verification (both the Rust light-client verifier used by pallet-ismp and the Solidity `EcdsaBeefy` consensus client) computes its 2/3+1 supermajority requirement from the *length* of the supplied signature array, not from the count of *distinct* authority indices represented in it. This is the same broken invariant as the reported `bigclaim()` issue: a threshold meant to require independent signers can be satisfied by repeating one signer's contribution.

### Finding Description
`verify_mmr_update_proof` computes `signatures_length = mmr.signed_commitment.signatures.len()` and passes it straight to `check_participation_threshold`, which only compares a count against `((2*total)/3)+1` [3](#0-2) . It never verifies `authority_indices` (built later in the same function from `sig.index`) contains no repeats before treating the threshold as satisfied — contrast this with `verify_validator_membership` in the Pharos verifier, which explicitly builds a `BTreeSet` and rejects on `deduped.len() != participants.len()` [1](#0-0) , and with the Tendermint verifier's `ensure_unique_addresses` gate on the next-validator set [7](#0-6) .

The identical pattern exists on the EVM side: `checkParticipationThreshold(sigLen, authoritySet.len)` gates on `relayProof.signedCommitment.votes.length` alone [8](#0-7) , and the subsequent loop recovers an address per vote and constructs merkle leaves keyed by `vote.authorityIndex` with no dedup pass [9](#0-8) .

The corrupted value is `sigLen`/`signatures_length`: it is treated as a proxy for "number of distinct authorities who attested," but it is only a count of array entries. Nothing stops the same `(authorityIndex, signature)` pair — or several different votes that all recover to the same underlying address — from being repeated to pad the count past the threshold.

### Impact Explanation
BEEFY consensus proofs are the trust root for all state commitments Hyperbridge relays from Polkadot-style relay chains into destination chains (via `IConsensusV2`/`verify`). If the supermajority gate can be satisfied without genuine 2/3+1 distinct-authority participation, an attacker can get a forged/insufficiently-attested MMR root and parachain header set accepted as finalized state. This is a **false remote state acceptance** — the exact class the Hyperbridge pivot guidance flags as never-acceptable — with downstream effects including forged proofs for request/response delivery, reward claims, and asset movement that key off "trusted" state commitments.

### Likelihood Explanation
This significantly depends on library-level behavior I could not fully verify from the repository content available to me: both `rs_merkle::MerkleProof::verify` (Rust) and `MerkleMultiProof.VerifyProof` (`@polytope-labs/solidity-merkle-trees`, Solidity) are external crate/package dependencies, and their vendored source was not present in the indexed codebase, so I could not confirm whether their multi-proof reconstruction algorithm tolerates or implicitly rejects duplicate leaf indices in the input list. If the multiproof algorithm requires strictly sorted, distinct indices to correctly reconstruct sibling pairs (a common implementation constraint), duplicate entries may already cause proof reconstruction to fail incidentally, which would reduce or eliminate exploitability. I flag this as the key open uncertainty.

Independent of that, the *pattern* itself is a genuine inconsistency in this codebase: two sibling verifiers (Pharos, Tendermint) treat exactly this gap as security-relevant enough to add explicit dedup checks and regression tests, while BEEFY — arguably the most security-critical consensus client since it anchors the relay-chain trust root — has no equivalent guard in either its Rust or Solidity implementation.

### Recommendation
Add an explicit uniqueness check on `authority_indices` (Rust) and `vote.authorityIndex` values (Solidity) before or as part of the participation-threshold check, mirroring `verify_validator_membership`'s `BTreeSet` dedup in the Pharos verifier and `ensure_unique_addresses` in the Tendermint verifier. This removes any dependency on incidental behavior of the underlying merkle multi-proof library and makes the supermajority gate correct by construction.

### Proof of Concept
Conceptual (blocked on confirming external merkle-multiproof library behavior, so presented as the exploit *shape* rather than a runnable trace):
1. Attacker observes one legitimately gossiped BEEFY vote `(authorityIndex = k, signature = sig_k)` for a target commitment (BEEFY votes are broadcast, not secret).
2. Attacker constructs a `SignedCommitment`/`RelayChainProof` whose `signatures`/`votes` array repeats `(k, sig_k)` enough times that `signatures_length`/`sigLen` reaches `((2*total)/3)+1`.
3. `check_participation_threshold`/`checkParticipationThreshold` passes on length alone.
4. The verifier proceeds to merkle-proof verification and (pending confirmation of the multiproof library's duplicate-index handling) may accept the commitment as finalized despite only one real authority having attested, producing a false trusted-state update. [5](#0-4) [9](#0-8)

### Citations

**File:** modules/consensus/pharos/verifier/src/lib.rs (L144-157)
```rust
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

**File:** modules/consensus/tendermint/verifier/src/verifier.rs (L231-258)
```rust
fn create_updated_trusted_state(
	old_trusted_state: &TrustedState,
	consensus_proof: &ConsensusProof,
) -> Result<UpdatedTrustedState, VerificationError> {
	let header = &consensus_proof.signed_header.header;

	// Promote next_validators to validators
	let validators = old_trusted_state.next_validators.clone();

	// Only a signalled rotation may replace the stored next set, and only with a list
	// that hashes to the new signed next_validators_hash. When the interval is unchanged
	// we keep the trusted set and ignore any list the proof happened to carry.
	let old_next_hash = Hash::Sha256(old_trusted_state.next_validators_hash);
	let rotates =
		!header.next_validators_hash.is_empty() && header.next_validators_hash != old_next_hash;
	let next_validators = if rotates {
		let provided = consensus_proof.next_validators.as_ref().ok_or_else(|| {
			VerificationError::ValidatorSetError(
				"next validator set rotated but the proof carried no next validators".to_string(),
			)
		})?;
		ensure_unique_addresses(provided)?;
		validate_validator_set_hash(
			&ValidatorSet::new(provided.clone(), None),
			header.next_validators_hash,
			true,
		)
		.map_err(|e| VerificationError::ValidatorSetError(e.to_string()))?;
```

**File:** modules/consensus/tendermint/verifier/src/verifier.rs (L296-332)
```rust
#[cfg(test)]
mod tests {
	use super::ensure_unique_addresses;
	use cometbft::{
		validator::{Info, ProposerPriority},
		vote::Power,
		PublicKey,
	};
	use tendermint_primitives::account_id_from_public_key;

	// A valid ed25519 public key (RFC 8032 test vector 1).
	const ED25519_PUBKEY: [u8; 32] = [
		0xd7, 0x5a, 0x98, 0x01, 0x82, 0xb1, 0x0a, 0xb7, 0xd5, 0x4b, 0xfe, 0xd3, 0xc9, 0x64, 0x07,
		0x3a, 0x0e, 0xe1, 0x72, 0xf3, 0xda, 0xa6, 0x23, 0x25, 0xaf, 0x02, 0x1a, 0x68, 0xf7, 0x07,
		0x51, 0x1a,
	];

	fn validator() -> Info {
		let pub_key = PublicKey::from_raw_ed25519(&ED25519_PUBKEY).unwrap();
		Info {
			address: account_id_from_public_key(&pub_key).unwrap(),
			pub_key,
			power: Power::try_from(10u64).unwrap(),
			name: None,
			proposer_priority: ProposerPriority::from(0i64),
		}
	}

	#[test]
	fn accepts_unique_addresses() {
		assert!(ensure_unique_addresses(&[validator()]).is_ok());
	}

	#[test]
	fn rejects_duplicate_addresses() {
		assert!(ensure_unique_addresses(&[validator(), validator()]).is_err());
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

**File:** modules/consensus/beefy/verifier/src/lib.rs (L145-176)
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

**File:** modules/consensus/beefy/verifier/src/lib.rs (L258-262)
```rust
/// Checks for supermajority participation
fn check_participation_threshold(len: u32, total: u32) -> bool {
	len >= ((2 * total) / 3) + 1
}

```

**File:** evm/src/consensus/EcdsaBeefy.sol (L122-162)
```text
    function verifyMmrUpdateProof(BeefyConsensusState memory trustedState, RelayChainProof memory relayProof)
        internal
        pure
        returns (BeefyConsensusState memory, bytes32)
    {
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

        uint256 payloadLength = commitment.payload.length;
        bytes32 mmrRoot;
        for (uint256 i = 0; i < payloadLength; i++) {
            if (commitment.payload[i].id == MMR_ROOT_PAYLOAD_ID && commitment.payload[i].data.length == 32) {
                mmrRoot = Bytes.toBytes32(commitment.payload[i].data);
            }
        }
        if (mmrRoot == bytes32(0)) revert MmrRootHashMissing();

        // verify the commitment
        bytes32 commitmentHash = keccak256(Codec.Encode(commitment));
        MerkleMultiProof.Leaf[] memory authorities = new MerkleMultiProof.Leaf[](sigLen);
        for (uint256 i = 0; i < sigLen; i++) {
            Vote memory vote = relayProof.signedCommitment.votes[i];
            address authority = ECDSA.recover(commitmentHash, vote.signature);
            authorities[i] =
                MerkleMultiProof.Leaf({index: vote.authorityIndex, hash: keccak256(abi.encodePacked(authority))});
        }

        bool valid = MerkleMultiProof.VerifyProof(authoritySet.root, relayProof.proof, authorities, authoritySet.len);
        if (!valid) revert InvalidAuthoritiesProof();
```
