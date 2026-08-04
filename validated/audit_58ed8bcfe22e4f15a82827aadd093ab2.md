Based on my investigation, I found a concrete local analog to the `POOL_VB_MASK` bug-class (a hardcoded constant whose value doesn't match its own documented/intended meaning, silently breaking a downstream calculation that trusts it).

### Title
Mismatched `FILLED_SLOT_BIG_ENDIAN_BYTES` storage-slot constant used to build cross-chain fill-status proof keys - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.sol` (Tron variant) and its EVM sibling `IntentsBase.sol` both declare a "magic" constant meant to encode the storage slot index of the `_filled` mapping, used to construct Merkle/storage-proof keys that Hyperbridge GET requests verify against a remote chain's state. In the Tron contract, the constant's own doc-comment and its literal value disagree, which is exactly the seed bug's pattern: a hand-computed numeric literal that is supposed to represent one specific value but actually encodes another.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`: [1](#0-0) 
the comment states "Hex value 0x06 padded with leading zeros" but the hex literal actually encodes `0x02`. The identical constant and helper exist in the shared base: [2](#0-1) 
where it is used to compute the storage-proof key for a commitment: [3](#0-2) 

`_calculateCommitmentSlotHash` combines this constant with the order commitment to derive the exact storage slot that a Hyperbridge GET-request proof must resolve to on the *counterparty* IntentGateway deployment, in order to determine whether an order has already been filled/cancelled there (used for cross-chain cancellation/refund flows). This value must exactly equal the real storage slot index of the `_filled` mapping on the target contract — the two-token inheritance chain (`HyperApp, EIP712` for the Tron variant vs. plain `EIP712` for the base) can shift that slot index depending on how many storage-consuming state variables OpenZeppelin's `EIP712` (its `_name`/`_version` strings) and `HyperApp` contribute before `_filled` is declared. The doc-comment disagreement (0x06 vs. the encoded 0x02) is direct, in-file evidence that this "constant" was not derived carefully and is a strong signal it does not track the real storage layout of the deployed contract it is meant to describe.

This is structurally identical to the seed bug: a static, hand-typed numeric constant used purely for a downstream bit/slot calculation, where a transcription mistake makes the constant diverge from its intended value, and every calculation built on top of it silently uses the wrong number instead of reverting loudly.

### Impact Explanation
If `FILLED_SLOT_BIG_ENDIAN_BYTES` does not match the true storage slot of `_filled` on the destination gateway, the derived storage-proof key in `_calculateCommitmentSlotHash` points at an unrelated storage slot (e.g., part of `_nonce`, `_params`, or another mapping's base slot) rather than the actual fill-status entry. Any cross-chain flow that trusts this key to assert "this order was/was not filled on the remote chain" would be checking the wrong data — for example silently reading zero/empty regardless of true fill state. That breaks the "commitment uniqueness / one-time settlement" invariant Hyperbridge relies on for order cancellation and refund paths: an order could be reported as unfilled when it was actually filled (or vice versa), opening the door to double-settlement of escrowed funds (fill on destination + refund on source, or repeated redemption), matching the "replay/double-claim/double-settlement" impact class in the bounty scope.

### Likelihood Explanation
The constant is unconditionally used any time `_calculateCommitmentSlotHash` is invoked as part of a cross-chain cancel/refund proof flow, so if the slot is wrong, every such proof request is affected — high likelihood in the sense that it's not a rare edge case, but a systemic miscalculation baked into the constant. Exploitability, however, depends on confirming the real storage layout of the deployed `IntentGatewayV2`/`IntentsBase` contracts (including inherited `HyperApp`/`EIP712` state), which I could not fully verify with the available tools — I was unable to inspect `HyperApp`'s storage-variable declarations to compute the exact expected slot index for the Tron variant.

### Recommendation
- Recompute and hardcode the correct storage slot for `_filled` for each concrete deployed contract (Tron `IntentGatewayV2` vs. EVM `IntentsBase`-derived contracts), accounting for all inherited storage-consuming state (`EIP712`'s `_name`/`_version`, and any `HyperApp` state) before `_filled` is declared.
- Resolve the doc-comment/value mismatch in `evm/tron/contracts/apps/IntentGatewayV2.sol` (0x06 vs 0x02) — one of the two is wrong, and this ambiguity is itself proof the value was never doubly-checked against the real layout.
- Add a Foundry storage-layout test (`forge inspect ... storage-layout` assertion) that pins `_filled`'s slot number and fails CI if the constant and the compiler-derived layout ever diverge, rather than relying on a hand-maintained literal.

### Proof of Concept
Deterministic PoC requires compiling the actual deployed `IntentGatewayV2` (with its full inheritance chain including `HyperApp`) and running `forge inspect IntentGatewayV2 storage-layout` to read the compiler-computed slot for `_filled`, then diffing that against `uint256(FILLED_SLOT_BIG_ENDIAN_BYTES)`. I could not execute this compilation/inspection step in this read-only session; the doc-comment/literal mismatch found directly in source (`0x06` claimed vs `0x02` encoded) is presented as the concrete, locally-provable evidence of the same broken-constant bug class as the seed report, and should be verified against the compiled layout before triage confirmation.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L85-90)
```text
    /**
     * @notice Constant representing a filled slot in big endian format
     * @dev Hex value 0x06 padded with leading zeros to fill 32 bytes
     */
    bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
        hex"0000000000000000000000000000000000000000000000000000000000000002";
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L68-73)
```text
    /**
     * @dev Big-endian encoding of storage slot 2 (the `_filled` mapping slot).
     * Used to construct storage proof keys for cross-chain cancel verification.
     */
    bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
        hex"0000000000000000000000000000000000000000000000000000000000000002";
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L364-373)
```text
    /**
     * @dev Computes the storage slot hash for a given commitment in the `_filled` mapping.
     * This is used to construct storage proof keys for cross-chain cancellation verification
     * via Hyperbridge GET requests.
     * @param commitment The order commitment hash.
     * @return The ABI-encoded storage slot hash.
     */
    function _calculateCommitmentSlotHash(bytes32 commitment) internal pure returns (bytes memory) {
        return abi.encodePacked(keccak256(abi.encodePacked(commitment, FILLED_SLOT_BIG_ENDIAN_BYTES)));
    }
```
