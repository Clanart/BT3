## Title
Missing `_filled[commitment]` guard in `_cancelFromDest` lets a destination-side cancellation race a legitimate fill and steal the source-chain escrow from the solver - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
The `DIAWhitelistedStaking` bug is a "state not updated/checked before reuse" class: a value (`principal`) that should gate a one-time payout isn't zeroed/consulted, letting the same payout be claimed repeatedly. The same broken-invariant pattern exists in Hyperbridge's cross-chain intents flow: `_cancelFromDest` unconditionally overwrites `_filled[commitment]` and dispatches a `RefundEscrow` message without first checking whether the order was already filled (`_filled[commitment] != address(0)`), which is exactly the "commitment already settled" state that should gate this path.

### Finding Description
`_fillCrossChain` (solver fill path) sets `_filled[commitment] = msg.sender` and asynchronously dispatches a `RedeemEscrow` post request to the source chain to pay the solver from escrow: [1](#0-0) 

`_cancelFromDest` can be invoked by anyone after the order deadline (or by the user before it), and it **overwrites `_filled[commitment]` without checking its current value**, then dispatches a `RefundEscrow` message to the source chain requesting the escrowed inputs be refunded to `order.user`: [2](#0-1) 

On the source chain, `onAccept` routes both `RedeemEscrow` and `RefundEscrow` to the same `_withdraw` function, whose only safety check is `escrowed == 0` per token: [3](#0-2) 

Because `RedeemEscrow` and `RefundEscrow` are independent, asynchronously-delivered cross-chain messages, whichever arrives at the source chain first wins the escrow — the check only prevents *replaying the same* claim, it does nothing to enforce that only the *rightful* claim (matching what actually happened on the destination) is honored. Since `_cancelFromDest` never checks `_filled[commitment]` before running, a destination-side cancellation dispatched after a legitimate fill (or racing one) can still produce a `RefundEscrow` message.

### Impact Explanation
If a solver fills the order on the destination chain (delivering the buyer's output tokens out of pocket) but the corresponding `RedeemEscrow` message is slower to reach the source chain than an independently-triggered `RefundEscrow` from `_cancelFromDest`, the source chain will pay out the escrow to `order.user` instead of the solver. When the (legitimate) `RedeemEscrow` later arrives, `_withdraw` sees `escrowed == 0` and reverts with `UnknownOrder`. Net effect: the user receives both the destination-side output *and* their source-side escrow back, while the solver who fulfilled the order loses the value they fronted on the destination chain — an unauthorized fund loss / wrong-beneficiary outcome, not a compromised-relayer or malicious-peer scenario, since any ordinary user or (after deadline) any caller can trigger `_cancelFromDest` through the normal public entrypoint.

### Likelihood Explanation
This does not require a malicious relayer, prover, or admin — only two independent Hyperbridge message deliveries (`RedeemEscrow` from the destination-side fill, `RefundEscrow` from a destination-side cancel) racing to the source chain, which is a routine cross-chain timing condition, not an adversarial network assumption. Any account can call the destination-side cancel path once the deadline passes (per the code comment "anyone may call" after deadline), and delivery order of independent ISMP messages is not otherwise constrained by the protocol. The `_cancelFromSource` (GET-proof) path is safer because it verifies the actual `_filled` state on the destination chain before requesting a refund, but `_cancelFromDest` has no equivalent check.

### Recommendation
In `_cancelFromDest`, check `_filled[commitment] == address(0)` before overwriting it and dispatching `RefundEscrow`, mirroring the pattern already used for `_filled` checks elsewhere; reject with something like `AlreadyFilled()` if the order has already been marked filled. This closes the race by making the destination-side cancellation authoritative only when no fill has occurred yet, consistent with how `_cancelFromSource`'s GET-proof path already validates destination state before initiating a refund.

### Proof of Concept
1. Solver calls the destination-chain fill entrypoint before `order.deadline`, which invokes `_fillCrossChain`: sets `_filled[commitment] = solver`, transfers output tokens to the beneficiary, and dispatches `RedeemEscrow` to the source chain (`ExtrinsicIntents.sol:89-171`).
2. Relayer for the `RedeemEscrow` message is delayed (network conditions, no malicious actor required).
3. Once `order.deadline` passes on the destination chain, any account calls the destination-side cancel entrypoint, invoking `_cancelFromDest`, which unconditionally sets `_filled[commitment] = order.user` and dispatches `RefundEscrow` to the source chain (`ExtrinsicIntents.sol:240-267`).
4. The `RefundEscrow` message is delivered to the source chain first; `onAccept` → `_withdraw` finds `escrowed > 0`, pays the full escrow to `order.user`, and zeroes `_orders[commitment][token]` (`IntentsBase.sol:390-410`).
5. The delayed `RedeemEscrow` message later arrives at the source chain; `_withdraw` now sees `escrowed == 0` and reverts with `UnknownOrder` — the solver is never paid despite having already delivered the destination output tokens.

I was not able to trace the exact ISMP delivery-ordering guarantees inside `modules/pallets` (e.g., whether any global request-ordering or per-app sequencing exists that might prevent the two messages from racing); if such ordering exists it would need to be checked to fully confirm exploitability, but nothing in `ExtrinsicIntents.sol` or `IntentsBase.sol` itself enforces this ordering at the application layer.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-96)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
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
