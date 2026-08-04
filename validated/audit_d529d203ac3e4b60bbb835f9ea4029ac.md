## Finding

The literal mechanism asked about — a genuine leaf hash claimed at **two different** index positions in `authority_set.keyset_commitment` — would require breaking the collision resistance of `rs_merkle::MerkleProof::verify` (the leaf hash at position A would need to equal the leaf hash at unrelated position B, which is a hash-collision requirement on an imported, third-party merkle library, not a bug in Hyperbridge's own code). That specific path is not practically exploitable and, even if it were, would be an imported-dependency defect, which is explicitly out of scope for this bounty.

However, there is a related, in-repo issue worth flagging: neither `verify_mmr_update_proof` in `modules/consensus/beefy/verifier/src/lib.rs` nor its Solidity counterpart `verifyMmrUpdateProof` in `evm/src/consensus/EcdsaBeefy.sol` deduplicates `sig.index` / `vote.authorityIndex` before computing the participation count.

`authority_leaves`/`authority_indices` are built with one entry per element of `mmr.signed_commitment.signatures`, with no uniqueness check on `sig.index`: [1](#0-0) 

The supermajority check uses the raw count of submitted signatures, not the count of distinct authority indices: [2](#0-1) 

The same pattern exists on the EVM side — `sigLen` is `votes.length` with no uniqueness enforcement on `vote.authorityIndex`: [3](#0-2) 

If the same `(index, signature)` pair is submitted multiple times, the `MerkleProof::verify`/`MerkleMultiProof.VerifyProof` call would trivially succeed (it is proving the same true leaf at the same true position, repeated), and `check_participation_threshold`/`checkParticipationThreshold` would count each duplicate toward the 2/3 supermajority, since it operates on `signatures_length`/`sigLen` rather than the count of unique `authority_indices`. This means an unprivileged submitter who has observed even a single legitimate authority signature over a commitment (BEEFY justifications are broadcast publicly) could replicate that one signature enough times to satisfy the supermajority gate, defeating the BFT assumption that state transitions require signatures from >2/3 of *distinct* authorities.

That said, this does not let an attacker forge an arbitrary/false MMR root — the commitment and its signature(s) still must be genuine and self-consistent (the recovered address must match a real authority in `authority_set.keyset_commitment`), so this does not directly enable stealing funds by itself. It weakens (but does not fully break) the intended BFT security margin of `verify_consensus`, since the check that is supposed to require multiple independent signers can be satisfied by copies of one.

I could not fully verify, within the available tool budget, whether `pallet-beefy-consensus-proofs` (the pallet that calls into this verifier before mutating on-chain consensus state) performs any additional index-uniqueness validation on the incoming `MmrProof` prior to calling `verify_mmr_update_proof`; I found no such check in the verifier or prover crates. Given the note about index size limits, I recommend confirming this in `modules/pallets/beefy-consensus-proofs/src/lib.rs` directly (via a Devin session with full file access) before treating this as conclusively unmitigated.

### Title
Missing authority-index uniqueness check lets duplicate signatures satisfy the BEEFY supermajority threshold - (File: modules/consensus/beefy/verifier/src/lib.rs)

### Summary
`verify_mmr_update_proof` and its Solidity equivalent `verifyMmrUpdateProof` count raw submitted signatures (`signatures_length`/`sigLen`) toward the 2/3 supermajority participation threshold without verifying that each `sig.index`/`vote.authorityIndex` is unique. A submitter can duplicate one legitimate, publicly-observable authority signature multiple times to pass the threshold while collecting far fewer than 2/3 of distinct authority signatures.

### Finding Description
`authority_indices`/`authority_leaves` are built one-per-signature with no dedup, and `check_participation_threshold` uses the raw signature array length, not unique-index count. [4](#0-3) 
The equivalent EVM path has the same shape. [5](#0-4) 

### Impact Explanation
The 2/3 supermajority requirement is intended to enforce Byzantine fault tolerance on rotating `current_authorities`/`next_authorities`. Bypassing it with duplicated signatures from a single (or minority) real signer weakens the trust assumption underpinning all downstream state/consensus commitments accepted by the light client, without requiring a full attacker-controlled forged signature set.

### Likelihood Explanation
Requires only a single publicly-broadcast valid BEEFY signature (obtainable by any observer of relay chain justifications) and no privileged access — but it still requires the underlying commitment (MMR root) and that one signature to be genuinely valid, limiting what state can actually be finalized this way to real, honestly-produced roots signed by at least one true authority.

### Recommendation
Deduplicate `sig.index` (and correspondingly `vote.authorityIndex`) before computing the participation count in both `verify_mmr_update_proof` (Rust) and `verifyMmrUpdateProof` (Solidity), rejecting proofs containing repeated authority indices.

### Proof of Concept
Not independently executed; based on static code review of the cited functions showing absence of an index-uniqueness check ahead of the `signatures_length`/`sigLen` threshold computation.

### Citations

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

**File:** modules/consensus/beefy/verifier/src/lib.rs (L145-171)
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
```

**File:** evm/src/consensus/EcdsaBeefy.sol (L127-162)
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
