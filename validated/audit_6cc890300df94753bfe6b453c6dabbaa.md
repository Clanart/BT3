## Title
BEEFY ECDSA consensus verifier counts duplicate authority votes toward the 2/3+1 threshold, allowing false state acceptance - (File: `evm/src/consensus/EcdsaBeefy.sol`)

### Summary
`EcdsaBeefy.verifyMmrUpdateProof` (and its Rust counterpart `verify_mmr_update_proof` in `modules/consensus/beefy/verifier/src/lib.rs`) determine whether a BEEFY commitment has reached supermajority by counting the raw number of submitted `votes`/`signatures`, without ever checking that the recovered signer/`authorityIndex` values are unique. This mirrors the reported `Consensus.checkSignatures` bug: `signatures.length < threshold` is checked, but duplicated signers are never excluded, so an attacker can inflate the apparent participation count by repeating a single valid vote.

### Finding Description
In `evm/src/consensus/EcdsaBeefy.sol`: [1](#0-0) 

`sigLen` is simply `relayProof.signedCommitment.votes.length`, and `checkParticipationThreshold(sigLen, authoritySet.len)` only compares this count to `(2*total)/3+1`: [2](#0-1) 

The subsequent loop recovers a signer address per vote and builds one `MerkleMultiProof.Leaf` per vote, keyed by `vote.authorityIndex`: [3](#0-2) 

Nothing in this code deduplicates `vote.authorityIndex` (or the recovered `authority` address) across the `votes` array before or after the threshold check. An attacker only needs one real, validly-signed vote from a member of the authority set for a given commitment — BEEFY votes are gossiped publicly off-chain, so obtaining one is not privileged access — and can repeat that same `Vote{authorityIndex, signature}` entry as many times as needed in the submitted `votes` array to satisfy `checkParticipationThreshold`, while each duplicated `Leaf` still correctly proves membership of that one real authority in the merkle tree.

The identical structural gap exists in the Substrate verifier: [4](#0-3) 

`signatures_length` (line 109) is compared to `check_participation_threshold` (line 131) before any per-signature uniqueness check, and the loop at lines 149-162 pushes one `authority_leaves`/`authority_indices` entry per submitted signature with no dedup.

This directly contrasts with sibling consensus verifiers in the same repo that were hardened against exactly this class of bug (BSC and Pharos explicitly reject duplicate/out-of-range participants): [5](#0-4) 

`EcdsaBeefy`/its Rust twin lack this guard entirely.

### Impact Explanation
`handleConsensus()` is a permissionless entrypoint that any address can call to submit a BEEFY consensus proof, which is routed to `EcdsaBeefy.verify` → `verifyMmrUpdateProof`. If the threshold can be satisfied via duplicated votes instead of genuine independent supermajority participation, an attacker holding (or having observed) far fewer than 2/3+1 real authority signatures can get an arbitrary MMR root / commitment accepted as the new trusted BEEFY state. Because the MMR root gates parachain header inclusion proofs (`verifyParachainHeaderProof`), and thus the state commitments trusted by ISMP for cross-chain request/response processing, this is a false-state-acceptance vulnerability that can be leveraged to forge state proofs for requests, responses, or timeouts, enabling unauthorized execution or fund movement on the receiving chain.

### Likelihood Explanation
Exploitability depends on whether the specific `MerkleMultiProof.VerifyProof` implementation used (`@polytope-labs/solidity-merkle-trees`) tolerates duplicate `(index, hash)` leaves in its multi-proof verification — this dependency is not vendored in this repository so it cannot be independently confirmed here, but nothing in `EcdsaBeefy.sol` itself rejects duplicates before or after that call, so the described bypass primitive is undeniably present in the local verification logic regardless of the library's exact tolerance. This is the same class of unguarded-duplication flaw called out in the seed report, applied to the on-chain BEEFY quorum check that gates trusted cross-chain state.

### Recommendation
In both `EcdsaBeefy.verifyMmrUpdateProof` and `beefy_verifier::verify_mmr_update_proof`, before checking the participation threshold, deduplicate votes by `authorityIndex` (or recovered signer address) — e.g., collect into a set/BTreeSet and require its length equal `sigLen`/`signatures_length` and satisfy the supermajority threshold on the deduplicated count, mirroring the guard already implemented in `pharos::verify_validator_membership`.

### Proof of Concept
1. Attacker observes (via public BEEFY gossip) one valid `Vote{authorityIndex: i, signature: sig}` for a target `Commitment` from real authority `i`, where the true supermajority for this commitment was never reached.
2. Attacker crafts `relayProof.signedCommitment.votes` as `[Vote(i, sig), Vote(i, sig), ..., Vote(i, sig)]` (repeated `k = ceil((2*total)/3)+1` times).
3. Attacker calls `handleConsensus(host, proof)` → `EcdsaBeefy.verify` → `verifyMmrUpdateProof`.
4. `sigLen == k` passes `checkParticipationThreshold`; each duplicated `Leaf{index: i, hash: keccak256(authority_i)}` is a real, provable leaf in `authoritySet.root`, so `MerkleMultiProof.VerifyProof` succeeds unless the library itself rejects repeated indices.
5. The attacker's chosen (potentially forged) MMR root/commitment becomes the new trusted BEEFY state, which downstream is used to accept forged parachain state commitments.

### Citations

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

**File:** evm/src/consensus/EcdsaBeefy.sol (L152-162)
```text
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

**File:** modules/consensus/beefy/verifier/src/lib.rs (L105-162)
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
```

**File:** modules/consensus/pharos/verifier/src/lib.rs (L143-152)
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
```
