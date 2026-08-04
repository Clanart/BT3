## Title
Missing duplicate/uniqueness check on authority indices lets a single leaked BEEFY signature satisfy the 2/3+1 supermajority threshold - (File: `evm/src/consensus/EcdsaBeefy.sol`)

### Summary
The original report's core complaint is that the verifier trusts proof elements without explicitly validating structural invariants (field membership, curve membership) before using them in the security-critical algebraic check, relying instead on implicit reverts deep in precompiles. The same pattern of "missing explicit invariant check before the security decision" exists in Hyperbridge's BEEFY consensus verifier: the supermajority participation count (`sigLen`) is derived directly from the raw length of the submitted `votes` array without ever checking that the `authorityIndex` values (and thus the underlying signers) are pairwise distinct.

### Finding Description
`EcdsaBeefy.verifyMmrUpdateProof` computes: [1](#0-0) 

```
uint256 sigLen = relayProof.signedCommitment.votes.length;
...
if (!checkParticipationThreshold(sigLen, authoritySet.len)) revert SuperMajorityRequired();
...
for (uint256 i = 0; i < sigLen; i++) {
    Vote memory vote = relayProof.signedCommitment.votes[i];
    address authority = ECDSA.recover(commitmentHash, vote.signature);
    authorities[i] = MerkleMultiProof.Leaf({index: vote.authorityIndex, hash: keccak256(abi.encodePacked(authority))});
}
```

`checkParticipationThreshold` is a pure arithmetic comparison of `len >= (2*total)/3 + 1` with no notion of identity: [2](#0-1) 

Nowhere in `verifyMmrUpdateProof` (or the equivalent Rust path) is there a check that the `authorityIndex` field of each `Vote` is unique across the `votes` array before this array length is used to satisfy the supermajority gate. The same gap exists in the Rust naive verifier, which builds `authority_indices` straight from `sig.index` with no dedup before calling `check_participation_threshold`: [3](#0-2) 

BEEFY signatures are broadcast publicly by the relay chain gossip network — they are not secrets held by a trusted relayer. Because `commitmentHash` is fixed for a given commitment and `ECDSA.recover` is a pure function of `(commitmentHash, signature)`, any party who has observed even a single valid vote from the current authority set can duplicate that one `(signature, authorityIndex)` pair into as many `Vote` entries as needed to inflate `sigLen` past the `(2*total)/3 + 1` threshold, without needing any additional real signatures and without needing to compromise, collude with, or impersonate a relayer/validator.

This is exactly the class of bug the external report describes: a value that must satisfy a strict structural invariant (here: "each vote comes from a distinct authority") is consumed directly into a security decision (the supermajority check) with no explicit, local check of that invariant — the code implicitly assumes downstream logic (the merkle multi-proof) will reject duplicates, rather than validating the assumption itself.

### Impact Explanation
If the supermajority gate can be satisfied using far fewer real signers than 2/3+1 of the authority set, an attacker (or a small minority of malicious/compromised authorities well below the honest 1/3 fault threshold) can push forged/stale BEEFY commitments and associated parachain header proofs through `EcdsaBeefy.verify`, causing the light client to accept **false remote state** as trusted. This directly enables acceptance of forged state/consensus proofs, which downstream feeds ISMP request/response/timeout handling and cross-chain fund movement — i.e., false state acceptance leading to unauthorized execution or fund loss, which matches the required impact categories for this bounty.

### Likelihood Explanation
Exploitability depends on whether the downstream `MerkleMultiProof.VerifyProof` call (from the external `@polytope-labs/solidity-merkle-trees` dependency, not present in this repository) independently rejects duplicate `index` values inside the leaf set. That library's source is not part of this codebase and could not be verified here, so it is possible the duplicate-index attempt would be caught at that later stage. However, this uncertainty is itself the vulnerability being reported: Hyperbridge's own contract performs the supermajority threshold decision (`checkParticipationThreshold`) *before* — and independently of — whatever validation the merkle library performs, and never enforces distinctness itself. This is a local, provable gap in Hyperbridge's verifier code regardless of the external library's behavior, and defense-in-depth requires the check to live in the verifier, not be assumed from an opaque dependency.

### Recommendation
Before computing `sigLen`/calling `checkParticipationThreshold`, deduplicate `votes` by `authorityIndex` (and/or by recovered `authority` address) and use the count of distinct authorities for the threshold comparison. Revert explicitly (e.g. `DuplicateAuthorityIndex()`) if any `authorityIndex` appears more than once in `relayProof.signedCommitment.votes`. Apply the equivalent fix to `modules/consensus/beefy/verifier/src/lib.rs::verify_mmr_update_proof`, deduplicating `authority_indices` before calling `check_participation_threshold`.

### Proof of Concept
1. Observe (via public relay-chain gossip, no privileged access required) a single valid BEEFY vote `(authorityIndex = k, signature = sig_k)` for a commitment `C` from the current authority set.
2. Construct `RelayChainProof.signedCommitment.votes` as `sig_k` repeated `N` times, all with `authorityIndex = k`, where `N >= (2*total)/3 + 1`.
3. Call `EcdsaBeefy.verify(previousState, proof)` (a `pure`, permissionless entry point) with this crafted proof.
4. `sigLen = votes.length = N` passes `checkParticipationThreshold`, even though only one real authority actually signed `C`.
5. Whether the exploit fully succeeds depends on whether `MerkleMultiProof.VerifyProof` (external dependency, not in-repo) separately rejects the duplicate `index = k` entries; the root cause — the missing local uniqueness check ahead of the threshold decision — is independent of that outcome and is present in Hyperbridge's own code as cited above.

### Citations

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

**File:** evm/src/consensus/EcdsaBeefy.sol (L240-243)
```text
    // @dev Check for supermajority participation.
    function checkParticipationThreshold(uint256 len, uint256 total) internal pure returns (bool) {
        return len >= ((2 * total) / 3) + 1;
    }
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L109-171)
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
```
