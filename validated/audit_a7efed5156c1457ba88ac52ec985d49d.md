### Title
Stale, attacker-chosen proof height in `IntentGatewayV2` cross-chain `cancelOrder` allows refund after an order has already been filled - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`, `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`cancelOrder` → `_cancelFromSource` builds a Hyperbridge **GET** request to check the `_filled` storage slot on the destination `IntentGatewayV2` instance, using a caller-supplied `options.height` with no enforced lower/upper bound relative to the order's actual fill time. Because the order owner fully controls which historical height is queried, they can pick a height *before* a solver's legitimate `fillOrder` transaction landed on the destination chain, obtain a perfectly valid (non-malicious relayer/prover) storage proof that `_filled[commitment] == 0` at that height, and use it via `onGetResponse` to force a refund of the escrowed input tokens on the source chain — even though the solver already delivered the output tokens to the user on the destination chain. This is a stale-state acceptance bug, not a relayer/prover compromise: the proof is honest and correct for the height requested, but the protocol never validates that the queried height reflects the *current* fill status of the order.

### Finding Description
The escrow lifecycle is:

- `placeOrder` escrows `order.inputs` under `_orders[commitment][token]` on the **source** chain [1](#0-0) .
- A solver fills the order on the **destination** chain via `fillOrder`, which checks `_filled[commitment] != address(0)` locally and then dispatches a `RedeemEscrow` request back to the source to release the input escrow [2](#0-1) .
- Alternatively, the order owner can call `cancelOrder` from the **source** chain. For cross-chain orders this routes to `_cancelFromSource`, which dispatches a Hyperbridge `DispatchGet` request reading the `_filled` mapping slot on the destination instance at `options.height`, an option fully supplied by the caller: [3](#0-2) 
- Once the GET response returns, `onGetResponse` treats an empty value as "not filled" and unconditionally refunds the escrowed tokens to the user via `_withdraw(..., isRefund=true, finalize=true)`: [4](#0-3) 

The core issue is that `_cancelFromSource` never validates `options.height` against the order's deadline or against "now" on the destination chain — it is simply forwarded as the query height for the ISMP `Get` request. A user who wants to cancel-and-refund even after a solver has filled the order can:

1. Place an order.
2. Wait for/observe a solver's `fillOrder` transaction on the destination chain landing at destination block `H_fill`.
3. Call `cancelOrder` → `_cancelFromSource` with `options.height` set to any destination height `< H_fill` (before the fill), where `_filled[commitment]` was still `0`.
4. Hyperbridge/relayers deliver a completely valid state proof for that historical height (no relayer or prover misbehavior required — the proof is truthfully generated for the requested height).
5. `onGetResponse` sees `value.length == 0` (unfilled at that height) and refunds the user's escrowed inputs on the source chain via `_withdraw`.

Meanwhile the solver already transferred the output tokens to the user's beneficiary on the destination chain in step 2's `fillOrder` execution, and later dispatches its own `RedeemEscrow` request to claim the input escrow. Whichever of `RedeemEscrow` (solver) or the stale-proof `RefundEscrow` (user) lands first wins the `_orders[commitment][token]` balance (the second one reverts with `UnknownOrder` since the balance is already zero — see `_withdraw`) [5](#0-4) . If the attacker's stale-height cancel request is crafted and dispatched to race ahead of (or instead of) the solver's redeem, the user receives **both** the solver's output tokens on destination **and** a full refund of the input escrow on source — a clean double-payment at the solver's expense, achievable by an unprivileged user with no relayer or prover collusion.

### Impact Explanation
This breaks the "moves exactly once and only to the rightful beneficiary" invariant for bridged/escrowed order funds called out in the Hyperbridge impact gate. A malicious order-placer can extract double value from a solver: retain the solver's output payment while reclaiming their own escrowed input, i.e., direct theft/loss of solver funds through unauthorized settlement of an already-fulfilled order. This is a logic/proof-acceptance flaw in a production cross-chain intents contract, not a griefing or DoS issue.

### Likelihood Explanation
The attacker is the order's own user — fully unprivileged, requires no relayer, prover, or admin cooperation, and no front-running of anyone else's transaction is strictly necessary since the attacker only needs to choose a `options.height` from before their own order was filled (which they can observe on the public destination chain). The only requirement is that Hyperbridge accepts GET queries at arbitrary past finalized heights and that no destination-side re-check of current `_filled` status exists in the accept path — both of which appear to hold from the code reviewed.

### Recommendation
- In `_cancelFromSource` (and equivalent same-chain/dest cancel paths), enforce that `options.height` corresponds to a recent/latest finalized destination height (e.g., reject heights below the latest known/finalized height for that state machine, or force the pallet/host to always use the latest finalized height rather than caller-supplied height).
- Alternatively/additionally, re-validate on `onGetResponse` that no `RedeemEscrow` has already been processed for the commitment (i.e., check `_filled[commitment]` is still unset immediately before refunding) and reject if a fill/redeem already executed, closing the TOCTOU window entirely regardless of proof height.
- Consider requiring the GET query height to be bound to (or after) the order's expiry/deadline for cross-chain cancels, so cancellation cannot target a snapshot taken before a legitimate fill had a chance to occur.

### Proof of Concept
Conceptual sequence (needs to be implemented as a Forked/foundry test against `IntentGatewayV2`/`ExtrinsicIntents`/`IntrinsicIntents`):
1. User places a cross-chain order on chain A escrowing `100 TOKEN`.
2. Solver fills the order on chain B at destination block `H_fill`, sending output tokens to the user's beneficiary and later dispatching `RedeemEscrow` toward chain A.
3. User calls `cancelOrder` on chain A with `CancelOptions.height = H_fill - 1` (a height strictly before the fill, where `_filled[commitment] == 0` on chain B).
4. A relayer honestly delivers the (real, unmodified) storage proof for that height; `onGetResponse` sees `value.length == 0` and calls `_withdraw(body, true, true)`, refunding the full `100 TOKEN` escrow to the user on chain A.
5. Result: user holds both the solver's output tokens (from step 2) and the refunded input escrow (from step 4); the solver's `RedeemEscrow` (if it arrives after) reverts with `UnknownOrder` since `_orders[commitment][token]` is already zero — solver's fill was uncompensated.

Note: I was unable to view the full body of `_cancelFromSource` prior to line 541 (only the tail showing the `DispatchGet` construction was indexed), so I could not confirm whether there is an additional deadline/height check earlier in the function that might partially mitigate this. This should be verified directly against the full source before treating the finding as final — I recommend a Devin session inspect the complete `_cancelFromSource`/`_cancelFromDest` implementations in `evm/src/apps/intentsv2/ExtrinsicIntents.sol` to confirm the absence of a height-freshness or already-filled re-check.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-446)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }

        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L550-577)
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
            });

            // dispatch storage query request
            if (msg.value > 0) {
                // there's some native tokens left to pay for request dispatch
                IDispatcher(hostAddr).dispatch{value: msg.value}(request);
            } else {
                // try to pay for dispatch with fee token
                dispatchWithFeeToken(request);
            }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L311-325)
```text
    /**
     * @dev Handles the response to a Hyperbridge GET request dispatched during
     * `_cancelFromSource`. Verifies that the `_filled` storage slot on the destination
     * chain is empty (meaning the order was never filled), then refunds the escrowed
     * tokens to the original user. Reverts with `Filled` if the slot is non-empty.
     *
     * @param incoming The incoming GET response from Hyperbridge containing the storage proof.
     */
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
}
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
