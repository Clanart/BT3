Based on my research, I found a concrete local analog in the BEEFY ECDSA consensus verifier, not merely a "signature overflow" issue but the same broken invariant class: **the code accepts a redundant/duplicated cryptographic contribution as if it were an independent one**, inflating a security threshold without a matching real-world guarantee — analogous to libsecp256k1 accepting a signature it shouldn't.

### Title
Duplicate authority votes inflate BEEFY supermajority participation count, enabling false consensus state acceptance - (File: evm/src/consensus/EcdsaBeefy.sol)

### Summary
`EcdsaBeefy.verifyMmrUpdateProof` derives the number of participating signers directly from `relayProof.signedCommitment.votes.length` and checks it against the supermajority threshold before validating that each vote comes from a *distinct* authority. Because `vote.authorityIndex` is never checked for uniqueness within the submitted `votes` array, an attacker can duplicate one or more valid, already-public authority signatures under different array slots to pad `sigLen`, satisfying `checkParticipationThreshold` with fewer real unique signers than required.

### Finding Description
`sigLen` is set from the raw votes array length and used to gate the entire proof at [1](#0-0) . The subsequent loop that builds `authorities[]` for the merkle multi-proof check pulls `vote.authorityIndex` and `vote.signature` per entry with no deduplication against previously seen indices in the same array: [2](#0-1) . The only remaining guard against duplicate indices is the external `MerkleMultiProof.VerifyProof` call from the `@polytope-labs/solidity-merkle-trees` dependency [3](#0-2) , which is not part of this repository and whose duplicate-index handling is not enforced by any local code path. Since BEEFY authority signatures for a given commitment are broadcast publicly (any relayer/observer sees them), an attacker submitting a proof to `verify()` does not need to compromise any authority key — they only need to repeat one or more already-valid signatures in the `votes` array to inflate `sigLen` past the `((2*total)/3)+1` threshold in `checkParticipationThreshold` [4](#0-3)  while the actual number of distinct signing authorities remains below supermajority. The Rust verifier mirrors the same pattern: `signatures_length` is taken directly from `mmr.signed_commitment.signatures.len()` and checked against `check_participation_threshold` before any per-authority-index deduplication [5](#0-4) .

### Impact Explanation
If the underlying merkle multi-proof library does not itself reject duplicate leaf indices (this repo neither vendors nor tests that guarantee), an attacker could get a state commitment accepted as finalized by BEEFY consensus with fewer than 2/3+1 genuinely independent authority signatures — i.e., false remote state acceptance. Since `IConsensusV2.verify()` feeds directly into `IntermediateState` used for ISMP request/response/timeout proof verification across the bridge, this would let an attacker manipulate which state commitments are trusted, undermining the "false proof/state acceptance" invariant that the whole ISMP proof-verification pipeline depends on.

### Likelihood Explanation
Exploitability is entirely contingent on the external `solidity-merkle-trees` library's multi-proof algorithm accepting duplicate leaf indices/leaves for a fixed `authoritySet.len` tree size — a property this repository does not itself enforce or test for locally. This repo's own code offers zero independent defense against replayed votes, so the security of the entire supermajority check rests on an unverified assumption about a dependency rather than on a local invariant.

### Recommendation
Add an explicit local uniqueness check on `vote.authorityIndex` (e.g., using a bitmap or sorted-and-deduplicated check) before counting `sigLen` toward `checkParticipationThreshold`, in both `EcdsaBeefy.sol::verifyMmrUpdateProof` and `beefy/verifier/src/lib.rs::verify_mmr_update_proof`, so that the participation threshold can never be satisfied by resubmitting the same authority's signature multiple times, regardless of downstream library behavior.

### Proof of Concept
1. Observe a legitimately signed BEEFY commitment where the real signer count is below `((2*total)/3)+1` (e.g., due to network conditions where only a plurality of authorities have signed so far).
2. Take one or more of the publicly broadcast valid `(authorityIndex, signature)` pairs and duplicate them as additional entries in `relayProof.signedCommitment.votes`, using distinct array slots but the same `authorityIndex`/signature values.
3. Call `EcdsaBeefy.verify(previousState, proof)` with the padded `votes` array; `sigLen` now exceeds the supermajority threshold at line 140 even though the number of unique authorities that actually signed is below the required 2/3+1.
4. If the downstream `MerkleMultiProof.VerifyProof` call does not independently reject the duplicate `index` entries, the call proceeds to accept the new state, promoting `trustedState.latestHeight` and authority sets based on insufficient genuine participation. [6](#0-5)

### Citations

**File:** evm/src/consensus/EcdsaBeefy.sol (L127-171)
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

        verifyMmrLeaf(trustedState, relayProof, mmrRoot);
        if (relayProof.latestMmrLeaf.nextAuthoritySet.id > trustedState.nextAuthoritySet.id) {
            trustedState.currentAuthoritySet = trustedState.nextAuthoritySet;
            trustedState.nextAuthoritySet = relayProof.latestMmrLeaf.nextAuthoritySet;
        }
        trustedState.latestHeight = latestHeight;

        return (trustedState, relayProof.latestMmrLeaf.extra);
```

**File:** evm/src/consensus/EcdsaBeefy.sol (L240-243)
```text
    // @dev Check for supermajority participation.
    function checkParticipationThreshold(uint256 len, uint256 total) internal pure returns (bool) {
        return len >= ((2 * total) / 3) + 1;
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
