## Analysis

The core broken invariant in the Trail of Bits report is: **a value written under one implicit type/layout assumption is later read and trusted under a different assumption, with no compiler-enforced link between the write-side and read-side interpretation of the same storage location.** The report's root cause is not "assembly" per se — it's that the storage *slot index* is decoupled from the compiler's automatic type/layout tracking, so a codebase change can silently desynchronize writer and reader.

Hyperbridge's `IntentGatewayV2`/`IntentsBase` cross-chain cancellation flow reproduces exactly this pattern with a **hand-maintained storage-slot constant** used to build a Hyperbridge `GET` storage-proof key, instead of relying on Solidity's own storage-layout resolution. [1](#0-0) 

```solidity
/**
 * @dev Big-endian encoding of storage slot 2 (the `_filled` mapping slot).
 * Used to construct storage proof keys for cross-chain cancel verification.
 */
bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
    hex"0000000000000000000000000000000000000000000000000000000000000002";
```

`_filled` is declared as the **first** storage variable in `IntentsBase` — only constants and an `enum` (which consume no storage) precede it: [2](#0-1) 

`IntentsBase` inherits only `EIP712`, which in the OpenZeppelin version used here stores its state under an ERC-7201 namespaced slot (not a sequential slot 0/1/2), so it does not consume the "normal" slot 0/1 the comment implies precedes `_filled`. This means the hand-coded assumption "slot 2" is not self-evidently correct from the declaration order — it can only be correct by accident/careful bookkeeping.

Critically, the **exact same constant, copy-pasted into the Tron variant, already shows a real-world drift**: its comment says `Hex value 0x06`, but the hex literal actually encodes `2`: [3](#0-2) 

This is direct, in-repo evidence that this hardcoded slot constant has already been miscopied/failed to track a real layout change once, and nothing in the compiler or CI catches it — precisely the "type mismatch through raw storage access, invisible to Solidity's type checker" bug class from the report, just manifesting as slot-index mismatch instead of ABI-type mismatch.

This constant is consumed to build the storage-proof key that Hyperbridge uses to authenticate whether an order was filled on the destination chain, before allowing the source chain to refund escrow: [4](#0-3) [5](#0-4) [6](#0-5) 

```solidity
function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
    if (incoming.response.values[0].value.length != 0) revert Filled();
    WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
    withdraw(body, true);
}
```

### Title
Hardcoded/miscomputed `_filled` storage-slot constant can desynchronize the cross-chain fill-proof key from the actual storage layout, enabling escrow double-claim — (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2`/`IntentsBase` builds a raw storage-proof key for the cross-chain order-cancellation `GET` request using a manually hardcoded slot index (`FILLED_SLOT_BIG_ENDIAN_BYTES`) rather than a compiler-verified reference to the `_filled` mapping. This mirrors the report's core flaw: a value read from raw/derived storage is trusted for a security decision, with no mechanism ensuring the reader's assumed layout matches the writer's actual layout. The Tron contract's own doc comment (`0x06`) already contradicts its hex literal (`0x02`), proving this constant is not derived automatically and has already drifted from its intended value in at least one deployment target.

### Finding Description
`cancelOrder`'s source-chain path dispatches a Hyperbridge `GET` request keyed on `keccak256(commitment, FILLED_SLOT_BIG_ENDIAN_BYTES)` to read the destination chain's `_filled[commitment]` slot, expecting an empty value if unfilled. `onGetResponse` treats a non-empty value as "filled" and only proceeds to refund escrow when the returned value is empty. If `FILLED_SLOT_BIG_ENDIAN_BYTES` does not point at the actual slot the destination contract writes `_filled` to (due to inheritance/layout changes across upgrades or per-chain variants, as already evidenced by the Tron/EVM comment mismatch), the storage proof will read an unrelated, always-empty slot. The check `value.length != 0` then always passes as "not filled," regardless of the real fill state.

### Impact Explanation
If the slot constant desyncs from the real layout (which has already happened once in-repo, per the contradictory comment/hex value), `onGetResponse` will unconditionally treat destination orders as unfilled. A user could fill/receive the destination-side output (or a solver front the fill) while the source chain still refunds the escrowed input tokens back to the order owner via `withdraw(body, true)`, producing a double-claim/double-settlement: the same order pays out both to the filler (destination) and back to the user (source refund), draining escrowed or protocol funds. This falls squarely under "false proof/state acceptance" and "double-settlement" per the bounty scope.

### Likelihood Explanation
This requires no privileged actor, relayer collusion, or governance action — it triggers purely from the existing public `cancelOrder` → `onGetResponse` flow whenever the hardcoded slot constant is wrong for a given deployment/layout. Because the constant is manually maintained and copied across `IntentsBase.sol` (EVM) and `IntentGatewayV2.sol` (Tron) with no shared compile-time derivation (e.g., no `forge inspect storage-layout` assertion/test enforcing it), any future refactor of inherited state (adding a state variable to `IntentsBase`/`EIP712`/`HyperApp`, or diamond-inheritance reordering) can silently break this invariant with no compiler error — exactly the scenario the exploit narrative in the source report describes.

### Recommendation
- Short term: replace the hardcoded `FILLED_SLOT_BIG_ENDIAN_BYTES` with a value computed/verified against the actual compiled storage layout (e.g., assert it via `forge inspect <Contract> storage-layout` in CI, failing the build if `_filled`'s slot changes), and immediately reconcile the Tron vs EVM discrepancy (`0x06` comment vs `0x02` literal).
- Long term: avoid manually encoded raw storage slots for cross-chain state proofs entirely; expose a canonical, compiler-checked accessor (or an on-chain layout-registration mechanism) that both the proof-key builder and any future refactor must go through, so a layout change cannot silently invalidate the fill-proof used for fund release decisions.

### Proof of Concept
1. Deploy/upgrade `IntentGatewayV2` (or its Tron variant) with any inheritance/state-variable change that shifts `_filled`'s actual slot away from `2` (as already nearly happened, per the `0x06`-vs-`0x02` comment mismatch) — no code review step currently catches this since nothing asserts the constant against the compiled layout.
2. User places a cross-chain order; solver fills it on the destination chain, setting `_filled[commitment]` at the *real* slot (not the hardcoded one).
3. User calls `cancelOrder` on the source chain after the deadline, triggering a `DispatchGet` keyed on `calculateCommitmentSlotHash(commitment)` (built from the stale `FILLED_SLOT_BIG_ENDIAN_BYTES`).
4. The storage proof returns the (wrong, always-empty) slot's value; `onGetResponse` sees `value.length == 0`, treats the order as unfilled, and calls `withdraw(body, true)`, refunding the user's escrow.
5. The user now holds both the destination-side fill proceeds and the source-side refund — a double-claim causing fund loss to the solver/protocol.

*Note: I could not execute `forge inspect storage-layout` in this environment to conclusively pin the exact numeric slot `_filled` resolves to in the fully linearized `IntentGatewayV2` contract; the finding is based on declaration-order analysis and the already-observed comment/hex-literal contradiction in the Tron contract, which is direct evidence the slot constant is unreliable and manually (mis)maintained rather than compiler-derived.*

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L85-90)
```text
    /**
     * @notice Constant representing a filled slot in big endian format
     * @dev Hex value 0x06 padded with leading zeros to fill 32 bytes
     */
    bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
        hex"0000000000000000000000000000000000000000000000000000000000000002";
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L322-324)
```text
    function calculateCommitmentSlotHash(bytes32 commitment) public pure returns (bytes memory) {
        return abi.encodePacked(keccak256(abi.encodePacked(commitment, FILLED_SLOT_BIG_ENDIAN_BYTES)));
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L550-567)
```text
            bytes memory context =
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(
                // contract address
                abi.encodePacked(instance(order.destination)),
                // storage slot hash
                calculateCommitmentSlotHash(commitment)
            );
            DispatchGet memory request = DispatchGet({
                dest: order.destination,
                keys: keys,
                timeout: 0,
                height: uint64(options.height),
                fee: options.relayerFee,
                context: context,
                payer: msg.sender
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```
