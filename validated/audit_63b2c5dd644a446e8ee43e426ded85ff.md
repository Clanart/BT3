Based on my investigation, I found a real local analog: the Intent Gateway's cross-chain cancellation path (`_cancelFromDest`) dispatches a fund-releasing message without checking whether the order was already finalized on the same chain — mirroring the report's core pattern of a secondary "handler" path (here, the cancel/refund flow) that bypasses the authorization check the primary path relies on (here, `_filled[commitment]`).

### Title
Destination-Chain Cancellation Skips The `_filled` Check, Enabling a Refund Race Against An Already-Filled Order - (File: evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
In the Pear report, `NftHandler.transferNft` implemented its own transfer authorization instead of relying on `PositionNFT`'s standard `_isApprovedOrOwner`, so the two paths diverged and the "approved" invariant was silently broken. In Hyperbridge's intents system, `_cancelFromDest` is a second path (besides `_fillCrossChain`) that mutates `_filled[commitment]` and dispatches a fund-moving cross-chain message, but it never reads `_filled[commitment]` to check whether the order was already finalized by a fill before acting. This lets a stale/racing cancellation dispatch a `RefundEscrow` message even though the order was already filled and a `RedeemEscrow` message is in flight for the same commitment.

### Finding Description
`_fillCrossChain` (destination side) sets `_filled[commitment] = msg.sender` and dispatches a `RedeemEscrow` request back to the source chain to release escrow to the solver: [1](#0-0) 

`_cancelFromDest` (also destination side) is reachable by *anyone* once `order.deadline` has passed, and it unconditionally overwrites `_filled[commitment]` and dispatches a `RefundEscrow` request to the source chain — without ever checking the current value of `_filled[commitment]`: [2](#0-1) 

Both requests are processed identically on the source chain by `onAccept`, which routes `RedeemEscrow` and `RefundEscrow` to the same internal `_withdraw` function with no ordering guarantee and no check of `_filled[commitment]` before acting: [3](#0-2) 

`_withdraw` itself only guards against double-spend via the escrow balance counter (`_orders[commitment][token]`), not via `_filled[commitment]`: [4](#0-3) 

Because Hyperbridge post requests from the destination chain to the source chain are delivered asynchronously by independent relayers, there is no guarantee that a `RedeemEscrow` (from a legitimate fill) arrives before a racing `RefundEscrow` (from a same-block or later cancellation) for the same commitment. If the `RefundEscrow` for that commitment lands first, `_withdraw` will release the escrowed input tokens to the original user and zero out `_orders[commitment][token]`. When the legitimate `RedeemEscrow` later arrives for the solver who already delivered the output tokens on the destination chain, `_withdraw` finds `escrowed == 0` and reverts with `UnknownOrder`, permanently denying the solver their entitled input tokens — the solver has paid out on destination but the escrow was already drained to the user instead. This is exactly the report's underlying invariant break: a secondary state-mutating path (`_cancelFromDest`) does not respect the authorization/finality state (`_filled[commitment]`) that the primary path (`_fillCrossChain`) established, because the check was never wired to the shared state at the point of use.

### Impact Explanation
This produces a wrong-beneficiary fund loss: escrowed input tokens intended for the solver who correctly and timely filled the order can instead be redirected to the original user via a permissionless post-deadline cancellation, while the solver has already paid the destination-side output with no recourse to reclaim the input. This is unauthorized transaction manipulation of settlement outcome and loss of funds for an honest actor (the solver), falling squarely within the bounty's "logic attacks" / "false … settlement" / "loss of funds" categories, reachable without any privileged relayer, prover, or admin — any address can trigger `_cancelFromDest` once the deadline passes.

### Likelihood Explanation
The race requires only that a cancellation transaction (permissionless post-deadline) and a fill's follow-up `RedeemEscrow` message reach the source chain in an unfavorable order — plausible near-deadline where a fill is submitted right at/after the deadline boundary while a third party (or bot) opportunistically calls cancel. No compromised relayer or malicious peer is required; both messages are legitimate, correctly authenticated cross-chain requests from the registered gateway instance — the bug is purely in missing state-check logic, not in trust assumptions.

### Recommendation
Have `_cancelFromDest` check `_filled[commitment] == address(0)` before overwriting it and dispatching `RefundEscrow`, reverting with `Filled()` if the order was already finalized (filled) on the destination chain — mirroring the authorization check that should exist on any state-changing entrypoint that shares the `_filled` invariant, instead of relying only on the source-side escrow-balance check as a backstop.

### Proof of Concept
1. User places a cross-chain order with `deadline = D` on the source chain, escrowing input tokens.
2. At block `D+1`, a solver calls `fillOrder` on the destination chain right as the deadline elapses; `_fillCrossChain` sets `_filled[commitment] = solver` and dispatches `RedeemEscrow` to the source chain (`ExtrinsicIntents.sol:89-171`).
3. In the same or next block, any third party calls `cancelOrder` targeting the destination chain (`options.height` reflecting `D+1 > D`), which is now unauthenticated-by-user since the deadline has passed; `_cancelFromDest` overwrites `_filled[commitment] = user` and dispatches `RefundEscrow` to the source chain (`ExtrinsicIntents.sol:240-267`), with no check that `_filled[commitment]` was already set to `solver`.
4. If the relayer carrying `RefundEscrow` delivers to the source chain's `onAccept` before the relayer carrying `RedeemEscrow`, `_withdraw` releases the escrow to `user` and zeroes `_orders[commitment][token]` (`IntentsBase.sol:390-410`).
5. When `RedeemEscrow` later arrives for the solver, `_withdraw` reverts with `UnknownOrder` — the solver has already paid the output on the destination chain but receives nothing from escrow.

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
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
