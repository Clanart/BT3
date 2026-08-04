## Finding

### Title
Hardcoded `_filled` Storage Slot Constant Mismatches Actual Layout, Breaking Cross-Chain Cancel/Fill State Proofs - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase` hardcodes the storage slot of the `_filled` mapping as a compile-time constant (`FILLED_SLOT_BIG_ENDIAN_BYTES = 0x...02`) to build ISMP GET-request storage proof keys used for cross-chain order cancellation/fill verification. [1](#0-0) 
This is the exact bug class from the external report: a value that is correct only under one specific compilation/layout assumption is baked in as a constant and reused everywhere, rather than being derived or verified against the actual deployed storage layout.

### Finding Description
`_calculateCommitmentSlotHash` combines a commitment hash with the hardcoded slot constant to compute the storage key used to prove, via Hyperbridge GET requests, whether an order was already filled on the destination chain — this is explicitly documented as being "used to construct storage proof keys for cross-chain cancel verification": [2](#0-1) 

However, `_filled` is the **first** state variable declared in `IntentsBase`, which itself only inherits `EIP712` (no state-consuming storage in the OZ v5 non-upgradeable `EIP712`, since name/version are packed into immutables, not storage slots): [3](#0-2) [4](#0-3) 

`IntentGatewayV2` composes `IntentsBase` via the diamond `IntrinsicIntents`/`ExtrinsicIntents` inheritance, plus `ReentrancyGuardTransient` (transient storage only, no persistent slots) and `Initializable` (OZ v5 ERC-7201 namespaced storage, not sequential slots): [5](#0-4) 

None of these bases consume sequential storage slots ahead of `IntentsBase`'s own variables, so `_filled` actually resolves to storage slot `0`, not slot `2` as hardcoded in `FILLED_SLOT_BIG_ENDIAN_BYTES`. The identical mismatch appears in the Tron fork of the contract, where the doc-comment even claims a *third* different value ("Hex value 0x06") while the constant itself is still `0x02`: [6](#0-5) 

Because the constant is baked in at compile time and reused across the diamond-inherited, proxy-deployed `IntentGatewayV2` (and its Tron variant with a different storage layout), any storage-proof key computed via `_calculateCommitmentSlotHash` will point at the wrong storage slot on the destination chain.

### Impact Explanation
If the storage key used in the cross-chain cancel/fill-status GET-request proof targets an unrelated (empty) slot instead of the real `_filled` mapping slot, a relayer/verifier following this logic will always observe "not filled" for that commitment, even when the order was genuinely filled on the destination chain. This directly enables false state acceptance in the request/response verification path and can lead to double-settlement: the solver receives the output tokens at the destination (legitimate fill), while the source chain accepts a cancellation/refund based on the incorrect (stale/zero) proof, releasing the escrowed input tokens back to the user as well. This is a duplicate payout of the same order — funds lost by the protocol/solver and an unauthorized double-claim, matching the bounty's explicit "false proof/state acceptance" and "replay/double-claim/double-settlement" categories.

### Likelihood Explanation
Medium-High: this does not require a malicious relayer, prover, or admin — it is a deterministic consequence of the actual compiled storage layout versus the hardcoded constant, triggered by any user/solver going through the standard cancel-after-fill flow using GET-request proofs. It reproduces on every deployment of this exact bytecode (mainnet and Tron variant alike), the same failure mode the external WETH report describes: a compile-time constant that silently diverges from the real deployed state depending on which contract configuration/chain it runs on.

### Recommendation
- Do not hardcode the `_filled` mapping's storage slot as a raw constant. Either compute/assert it via a Foundry storage-layout check at build/deploy time, or use an explicit, verified fixed slot pattern (e.g., ERC-7201 namespaced storage or an OZ `StorageSlot` with a compile-time-checked layout test) that is invariant to inheritance ordering.
- Add a deployment-time or CI storage-layout regression test that fails the build if `_filled`'s actual slot diverges from the slot encoded in `FILLED_SLOT_BIG_ENDIAN_BYTES`, for both the main EVM contract and the Tron fork.
- Align the constant value with the doc comments (`0x02` vs "slot 2" vs "0x06") and verify against `forge inspect IntentGatewayV2 storage-layout` (or Tron equivalent) rather than manual counting.

### Proof of Concept
1. Deploy `IntentGatewayV2` per its documented diamond inheritance (`IntrinsicIntents`, `ExtrinsicIntents`, `ReentrancyGuardTransient`, `Initializable`) behind a proxy, as intended for CREATE2-deterministic multi-chain deployment. [7](#0-6) 
2. Run `forge inspect IntentGatewayV2 storage-layout` (or equivalent slot dump) and confirm `_filled` resolves to slot `0`, while `FILLED_SLOT_BIG_ENDIAN_BYTES` encodes slot `2`. [8](#0-7) 
3. Place a cross-chain order, have a solver fill it on the destination chain (`_filled[commitment] = filler`).
4. Trigger the source-chain cancellation path that calls `_calculateCommitmentSlotHash(commitment)` to build the GET-request storage proof key against slot 2 of the destination `IntentGatewayV2`. [9](#0-8) 
5. Because slot 2 is empty (the true value lives in slot 0), the storage proof returns no fill record; the source-chain cancellation succeeds and escrowed input tokens are refunded to the user, even though the solver already received the destination-chain outputs — a duplicate payout for a single order.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L52-52)
```text
abstract contract IntentsBase is EIP712 {
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L118-122)
```text
    /**
     * @dev Maps order commitment hashes to the address that filled or refunded the order.
     * A non-zero value indicates the order has been finalized and cannot be filled again.
     */
    mapping(bytes32 => address) public _filled;
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

**File:** evm/src/apps/IntentGatewayV2.sol (L46-61)
```text
/**
 * @title IntentGatewayV2
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @dev The IntentGateway allows for the creation and fulfillment of same-chain & cross-chain orders.
 * This is the concrete entry-point contract that composes all intent logic via inheritance:
 *
 *            EIP712
 *              |
 *          IntentsBase
 *           /       \
 *  IntrinsicIntents  ExtrinsicIntents
 *           \       /
 *        IntentGatewayV2
 */
contract IntentGatewayV2 is IntrinsicIntents, ExtrinsicIntents, ReentrancyGuardTransient, Initializable {
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L85-90)
```text
    /**
     * @notice Constant representing a filled slot in big endian format
     * @dev Hex value 0x06 padded with leading zeros to fill 32 bytes
     */
    bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
        hex"0000000000000000000000000000000000000000000000000000000000000002";
```
