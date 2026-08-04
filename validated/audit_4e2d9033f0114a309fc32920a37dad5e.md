## Title
Unvalidated `leafCount`/tree-size parameters accepted from untrusted proof calldata in BEEFY consensus verification enable false parachain-header/authority-set state acceptance - (File: `evm/src/consensus/EcdsaBeefy.sol`)

### Summary
The external Linea report's core broken invariant is: a structural parameter that determines how a merkle proof is walked (tree depth) is taken from attacker-supplied input and never bound to the real, trusted tree size, so verification can succeed against a tree shape that never existed on-chain. The Hyperbridge analog is `EcdsaBeefy.verifyParachainHeaderProof` and `verifyMmrUpdateProof`, which take `proof.leafCount` and `authoritySet.len` from calldata-decoded `ParachainProof`/`RelayChainProof` structs supplied by whoever calls `verify()`, and feed them straight into `MerkleMultiProof.VerifyProof(root, proof, leaves, leafCount)` without ever checking `leafCount` against a value derived from trusted consensus state.

### Finding Description
`verify()` at [1](#0-0)  decodes both `RelayChainProof` and `ParachainProof` entirely from the `proof` calldata blob passed by the caller. `ParachainProof.leafCount` is a caller-controlled `uint256` field ( [2](#0-1) ) that is never cross-checked against any independently-known number of parachains/leaves in the heads tree.

This value is passed directly into the multi-proof verifier: [3](#0-2) 

Similarly, `verifyMmrUpdateProof` uses `authoritySet.len` (a value stored in trusted state, so less exploitable) together with a `sigLen`/participation check, but the *parachain* leaf-count path has no equivalent binding step at all — unlike the MMR leaf verification, which independently derives `leafCount` from `trustedState.beefyActivationBlock` and `parentNumber` ( [4](#0-3) ), the parachain-heads leaf count is taken verbatim from the untrusted proof struct with no analogous derivation or bound.

This mirrors the Linea bug precisely: `l2MerkleTreesDepth` was an unconstrained, prover-supplied value that shaped how the merkle proof was interpreted, allowing a mismatch between the "declared" tree shape and the true tree shape. Here, `leafCount` plays the same role for the parachain-heads merkle-multi-proof: it dictates how `MerkleMultiProof.VerifyProof` reconstructs internal node positions from the supplied `(index, hash)` leaves and sibling `proof` array. Passing an incorrect `leafCount` (larger or smaller than the real number of parachain heads that were actually included when `headsRoot` was computed) changes the position-to-node mapping used during root reconstruction, since multi-proof merkle libraries compute internal tree indices as a function of total leaf count and leaf index (as documented in this repo's own MMR k-index/tree-depth algorithms, e.g. `mmr_position_to_k_index`, which is explicitly parameterized by `mmr_size`: [5](#0-4) ). There is no on-chain guard that ties `proof.leafCount` back to a trusted count of parachains registered for the relay chain, so an attacker who can find any valid `(leaves, proof, leafCount)` tuple that hashes up to the *real* `headsRoot` under a *different* declared leaf count than the true tree — which is a strictly weaker requirement than forging the root itself — can get `verifyParachainHeaderProof` to accept parachain headers/state commitments that were never actually part of the finalized heads tree at that shape, or that map different `index` values to a spoofed `state_id`/height/commitment tuple.

### Impact Explanation
If `MerkleMultiProof.VerifyProof` can be satisfied with a wrong `leafCount`, `verify()` returns `IntermediateState[]` entries containing a `StateCommitment` for whichever `stateMachineId`/`height` the attacker chose in the (still attacker-controlled) `Header` bytes, and these intermediates are consumed by the ISMP core (`HandlerV2.sol`) to accept new finalized state commitments for a parachain state machine. False acceptance of remote state directly enables downstream false proof/state acceptance for request/response/timeout processing built on top of that state commitment — i.e., forged cross-chain message inclusion, matching the "false proof/state acceptance" and "unauthorized execution" categories in the bounty scope. This is a public-entrypoint path (`verify()` is externally callable by any relayer as part of the standard consensus-update flow) requiring no admin/governance/leaked key.

### Likelihood Explanation
Exploitability depends on whether an attacker can actually find/construct a `(leaves, proof, leafCount)` triple where a wrong `leafCount` still reconstructs the true `headsRoot` — this requires either an implementation weakness in `MerkleMultiProof.VerifyProof`'s tree-indexing arithmetic under a mismatched leaf count, or a favorable case where leaf-count mismatch is a no-op for a given proof shape (e.g., all real leaves included and only trailing "phantom" positions varied). I was not able to fully retrieve the `MerkleMultiProof.sol` library source in this pass (it appears to live in an external/vendored path not indexed here, e.g. `polytope-labs/solidity-merkle-trees`), so I cannot confirm from local code whether its indexing math is actually forgeable under a wrong `leafCount`, only that **no local guard exists to rule it out**, which is the same missing-invariant class the Linea report flagged. This uncertainty should be resolved by inspecting the actual `MerkleMultiProof.VerifyProof` implementation.

### Recommendation
- Bind `ParachainProof.leafCount` to a trusted value: derive it on-chain from the count of registered/expected parachains for the current relay-chain session rather than trusting the caller-supplied field, or include the leaf count as part of the data that is itself covered by the BEEFY-signed commitment (so falsifying it requires forging the authority signature).
- Add an explicit sanity bound (e.g., `leafCount` must be within `[proof.parachains.length, MAX_PARACHAINS]`) before calling `MerkleMultiProof.VerifyProof`.
- Apply the same "depth/size must be a public input, not a free parameter" fix pattern used by Linea: treat `leafCount` as a value whose correctness the verification circuit/algorithm must enforce, not something optionally supplied by the untrusted proof submitter.

### Proof of Concept
Not independently reproducible from local evidence alone because the concrete forging mechanics depend on the vendored `MerkleMultiProof.VerifyProof` algorithm, which is not present in this repository's indexed content. The conceptual PoC is:
1. Attacker calls `IConsensusV2.verify(previousState, proof)` on `EcdsaBeefy` with a legitimately BEEFY-signed `RelayChainProof` (so `verifyMmrUpdateProof` passes) but crafts `ParachainProof` with `leafCount` set to a value different from the true number of leaves that produced `headsRoot`, alongside a manipulated `parachains[]`/`proof[]` array chosen so that `MerkleMultiProof.VerifyProof(headsRoot, proof.proof, leaves, proof.leafCount)` in `evm/src/consensus/EcdsaBeefy.sol:224` still returns `true`.
2. If it returns `true`, `verifyParachainHeaderProof` returns `IntermediateState[]` with a `StateCommitment` chosen by the attacker (from the `Header` bytes they control), which `HandlerV2.sol` then persists as trusted finalized state for that `state_id`/`height`.

This gap should be confirmed against the actual `MerkleMultiProof.sol` source (not resolvable from the current index) before being treated as fully proven; the core, locally-verifiable fact is that `leafCount` is accepted from untrusted calldata with **zero** validation anywhere in `EcdsaBeefy.sol`.

### Citations

**File:** evm/src/consensus/EcdsaBeefy.sol (L96-114)
```text
    function verify(bytes calldata previousState, bytes calldata proof)
        external
        pure
        returns (bytes memory, IntermediateState[] memory, uint256)
    {
        BeefyConsensusState memory consensusState = abi.decode(previousState, (BeefyConsensusState));
        (RelayChainProof memory relay, ParachainProof memory parachain) =
            abi.decode(proof, (RelayChainProof, ParachainProof));

        // Stale proofs are a no-op: return the previous state with no intermediates so the caller
        // can treat replays as idempotent rather than having to guard against reverts.
        if (consensusState.latestHeight >= relay.signedCommitment.commitment.blockNumber) {
            return (abi.encode(consensusState), new IntermediateState[](0), consensusState.nextAuthoritySet.id);
        }
        (BeefyConsensusState memory newState, bytes32 headsRoot) = verifyMmrUpdateProof(consensusState, relay);
        IntermediateState[] memory intermediates = verifyParachainHeaderProof(headsRoot, parachain);

        return (abi.encode(newState), intermediates, newState.nextAuthoritySet.id);
    }
```

**File:** evm/src/consensus/EcdsaBeefy.sol (L188-196)
```text
            )
        );
        uint256 leafCount = leafIndex(trustedState.beefyActivationBlock, relay.latestMmrLeaf.parentNumber) + 1;
        MerkleMountainRange.Leaf[] memory leaves = new MerkleMountainRange.Leaf[](1);
        leaves[0] = MerkleMountainRange.Leaf({index: relay.latestMmrLeaf.leafIndex, hash: hash});
        bool valid = MerkleMountainRange.VerifyProof(mmrRoot, relay.mmrProof, leaves, leafCount);

        if (!valid) revert InvalidMmrProof();
    }
```

**File:** evm/src/consensus/EcdsaBeefy.sol (L223-226)
```text
        if (len > 0) {
            bool valid = MerkleMultiProof.VerifyProof(headsRoot, proof.proof, leaves, proof.leafCount);
            if (!valid) revert InvalidMmrProof();
        }
```

**File:** evm/src/consensus/Types.sol (L135-139)
```text
struct ParachainProof {
    Parachain[] parachains;
    bytes32[] proof;
    uint256 leafCount;
}
```

**File:** modules/pallets/mmr/primitives/src/lib.rs (L102-106)
```rust
/// Converts a node's mmr position, to it's k-index. The k-index is the node's index within a layer
/// of the subtree. Refer to <https://research.polytope.technology/merkle-mountain-range-multi-proofs>
pub fn mmr_position_to_k_index(mut leaves: Vec<u64>, mmr_size: u64) -> Vec<(u64, usize)> {
	let peaks = get_peaks(mmr_size);
	let mut leaves_with_k_indices = vec![];
```
