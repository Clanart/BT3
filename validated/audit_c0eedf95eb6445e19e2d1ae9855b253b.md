I found a real analog, though it requires a caveat I'll state explicitly.

### Title
Duplicate authority votes are not deduplicated before the merkle multi-proof check, allowing supermajority to be forged from a minority of real signatures - (File: evm/src/consensus/EcdsaBeefy.sol)

### Summary
`EcdsaBeefy.verifyMmrUpdateProof` counts `relayProof.signedCommitment.votes.length` directly against the supermajority threshold via `checkParticipationThreshold`, but never checks that the `authorityIndex` values inside `votes` are unique before doing so.

### Finding Description
`checkParticipationThreshold(sigLen, authoritySet.len)` is evaluated purely on `sigLen = relayProof.signedCommitment.votes.length`, i.e. the raw count of submitted `Vote` entries. [1](#0-0) 
Each vote independently produces an `ECDSA.recover` result keyed by `vote.authorityIndex` and is fed as a leaf into `MerkleMultiProof.VerifyProof`: [2](#0-1) 
`checkParticipationThreshold` itself does nothing but a length comparison: [3](#0-2) 

Because the same authority's `(authorityIndex, signature)` pair can be supplied multiple times in `votes`, `sigLen` can be inflated arbitrarily above the real number of distinct signers while the merkle multi-proof (over `authorityIndex → address` leaves, deduplicated implicitly by index) may still validate if the underlying multi-proof library tolerates repeated indices/leaves. If `MerkleMultiProof.VerifyProof` does not itself reject duplicate leaf indices, an attacker holding signatures from well under 2/3+1 of the real authority set (e.g. a handful of colluding or leaked-but-still-legitimate authority keys) could pad `votes` with repeated copies of the same authority's vote to pass `checkParticipationThreshold`, then rely on the (potentially duplicate-tolerant) merkle proof to validate authority membership — advancing `trustedState.latestHeight` and `nextAuthoritySet` and committing new, attacker-influenced `IntermediateState[]` (fake parachain state roots) as finalized.

### Impact Explanation
If exploitable, this is a false state acceptance vulnerability at the root of the bridge's trust model: `EcdsaBeefy.verify` output feeds directly into intermediate state commitments that downstream ISMP request/response verification and asset movement rely on. A forged supermajority would let an attacker without true consensus quorum push arbitrary state roots, enabling downstream double-spend / unauthorized settlement of bridged messages and assets.

### Likelihood Explanation
This is **uncertain and not independently confirmed** because verifying whether `MerkleMultiProof.VerifyProof` (imported from `@polytope-labs/solidity-merkle-trees`) rejects duplicate leaf indices requires reading that external library's source, which was not available in this index. If that library already deduplicates or requires strictly increasing/sorted indices (a common design for multi-proofs), this path is not exploitable and the `EcdsaBeefy` contract is safe as-is. I could not confirm the library's internal behavior with the tools available, so I cannot assert this as a proven, exploitable bug — only as a locally-supported analog to the "point-arithmetic edge case bypasses a validity check" bug class from the ECDSA384 report (there, degenerate scalar-mul inputs slipped past validation; here, degenerate/duplicate authority-index inputs could slip past a supermajority-count validation if the multi-proof primitive doesn't independently enforce uniqueness).

### Recommendation
Add an explicit on-chain uniqueness check on `authorityIndex` across `relayProof.signedCommitment.votes` before computing `sigLen` / calling `checkParticipationThreshold` — e.g. require indices to be strictly increasing, or track a bitmap of seen indices — regardless of what the merkle multi-proof library does internally. This makes the supermajority guarantee self-contained in `EcdsaBeefy.sol` rather than dependent on an external library's undocumented duplicate-handling behavior. It would also be worth confirming (by reading the vendored `MerkleMultiProof.VerifyProof` implementation, which this analysis could not access) whether duplicate indices are currently rejected; if they already are, this finding should be downgraded/closed.

### Proof of Concept
Not executable — this finding is conditional on unverified behavior of the external `MerkleMultiProof.VerifyProof` library. A concrete PoC would require: (1) confirming that supplying two `Vote` entries with identical `authorityIndex` but the same valid signature is accepted by `MerkleMultiProof.VerifyProof`, then (2) constructing a `RelayChainProof` where `votes.length >= (2*total)/3+1` is satisfied only via duplicated entries from authorities numbering well under the real threshold, and calling `EcdsaBeefy.verify` to observe whether `latestHeight` advances and new `IntermediateState[]` are accepted.

### Citations

**File:** evm/src/consensus/EcdsaBeefy.sol (L126-141)
```text
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
