Found the analog. It's in `EcdsaBeefy.sol::verifyMmrUpdateProof` — the `sigLen` (vote count used for the supermajority threshold check) is computed directly from `relayProof.signedCommitment.votes.length` without deduplicating on `vote.authorityIndex`, before those same votes are individually recovered and checked for merkle-membership by index.

### Title
Duplicate `authorityIndex` votes let a single BEEFY authority satisfy the supermajority threshold, enabling false consensus/state-commitment acceptance - (File: evm/src/consensus/EcdsaBeefy.sol)

### Summary
`verifyMmrUpdateProof` counts `sigLen = relayProof.signedCommitment.votes.length` and requires `checkParticipationThreshold(sigLen, authoritySet.len)` — i.e. `sigLen >= 2/3 * total + 1` — before it ever checks whether the `votes` array contains distinct authorities. Each vote is then verified independently via `ECDSA.recover` and slotted into a `MerkleMultiProof.Leaf` keyed by `vote.authorityIndex`. Nothing rejects two (or many) entries in `votes` that reuse the same `authorityIndex`/signature.

### Finding Description
The threshold gate at [1](#0-0)  only counts array length, not unique signers. The subsequent loop at [2](#0-1)  builds one `MerkleMultiProof.Leaf` per vote entry, keyed by the attacker-supplied `vote.authorityIndex`, and the merkle-multiproof verification at [3](#0-2)  is checked against `authoritySet.root`.

If the underlying merkle-multiproof implementation (or its leaf-index handling) tolerates repeated indices in the leaf set — which is plausible since `MerkleMultiProof.VerifyProof` is driven purely by the `(index, hash)` pairs supplied by the caller and the total `authoritySet.len`, not by an on-chain uniqueness constraint over `authorityIndex` — a caller can pass `votes` containing the same authority's valid signature/index repeated `N` times to reach `sigLen >= 2/3 * total + 1` while the actual number of distinct signing authorities is far below supermajority. There is no `authorityIndex` dedup check anywhere in this function (unlike the analogous Rust `verify_validator_membership` dedup guard used in the Pharos consensus client, which explicitly rejects duplicate participant keys via a `BTreeSet` — that same defensive pattern is conspicuously absent here). This directly parallels the external report's core defect: a value (`sigLen`, standing in for "distinct signer count") is trusted at face value for a gating decision without being canonicalized/deduplicated first, letting a minority (or in the worst case a single colluding/compromised authority key) forge the appearance of quorum.

### Impact Explanation
If exploitable, this allows acceptance of a BEEFY consensus update (and the parachain header state commitments it carries) without genuine 2/3+1 authority participation — i.e. false remote state acceptance directly gating cross-chain message and state-proof verification for every ISMP request/response routed through this consensus client. This is exactly the "false proof/state acceptance" class the bounty targets, since a downstream forged state root would let fabricated request/response commitments or timeouts be accepted, leading to fund loss/unauthorized execution across all apps trusting this consensus client.

### Likelihood Explanation
This can only be triggered by whoever controls at least one valid signature under the authority set root (or by any signature index reachable via a valid vote/leaf construction) submitting a maliciously crafted `RelayChainProof` to the public `verify` entrypoint — no relayer, prover, or admin trust assumption beyond "the merkle-multiproof library does not itself enforce leaf-index uniqueness" is required, since `verify` is a stateless `pure` function callable by anyone (indirectly via the consensus router / light client update flow). The exploitability hinges entirely on whether `MerkleMultiProof.VerifyProof` rejects repeated indices; this repo does not vendor that library's source for direct inspection, so **I could not conclusively confirm from local code whether duplicate indices are rejected downstream** — this is the one open uncertainty in this analysis.

### Recommendation
Deduplicate `relayProof.signedCommitment.votes` by `authorityIndex` (or by recovered `authority` address) before computing `sigLen` and before threshold comparison, mirroring the `BTreeSet`-based duplicate rejection already used in `modules/consensus/pharos/verifier/src/lib.rs::verify_validator_membership`. Explicitly verify no two entries share an `authorityIndex` or recovered address prior to accepting the array length as the participation count.

### Proof of Concept
Conceptual (not executable without the vendored `MerkleMultiProof` source to confirm the duplicate-index behavior):
1. Attacker obtains one valid BEEFY vote signature+index pair (e.g. their own authority slot, or a leaked/observed valid signature for the commitment).
2. Attacker constructs `RelayChainProof.signedCommitment.votes` as `N` copies of that same `(signature, authorityIndex)` pair, where `N >= (2/3 * authoritySet.len) + 1`.
3. Calls `EcdsaBeefy.verify(previousState, proof)`. `sigLen = N` passes `checkParticipationThreshold`. Each of the `N` identical votes recovers to the same authority and produces `N` identical `MerkleMultiProof.Leaf` entries at the same `index`.
4. If `MerkleMultiProof.VerifyProof` accepts this leaf set against `authoritySet.root` (unverified locally), the state update proceeds and `trustedState.latestHeight`/authority set rotation is accepted despite only one real signer.

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

**File:** evm/src/consensus/EcdsaBeefy.sol (L153-159)
```text
        MerkleMultiProof.Leaf[] memory authorities = new MerkleMultiProof.Leaf[](sigLen);
        for (uint256 i = 0; i < sigLen; i++) {
            Vote memory vote = relayProof.signedCommitment.votes[i];
            address authority = ECDSA.recover(commitmentHash, vote.signature);
            authorities[i] =
                MerkleMultiProof.Leaf({index: vote.authorityIndex, hash: keccak256(abi.encodePacked(authority))});
        }
```

**File:** evm/src/consensus/EcdsaBeefy.sol (L161-162)
```text
        bool valid = MerkleMultiProof.VerifyProof(authoritySet.root, relayProof.proof, authorities, authoritySet.len);
        if (!valid) revert InvalidAuthoritiesProof();
```
