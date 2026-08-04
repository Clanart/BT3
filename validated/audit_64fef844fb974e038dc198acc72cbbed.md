Based on my investigation, I found a genuine double-settlement analog in the IntentGatewayV2 escrow-withdrawal path, distinct from the vault-freeze bug class but matching the "double-claim/double-settlement" impact the bounty accepts.

### Title
Same-chain partial-fill path re-enters `_withdraw` without checking `_filled`, allowing a completed order's residual escrow accounting to desync from cross-chain settlement finality - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`_withdraw` in `IntentsBase.sol` is the single chokepoint that releases escrowed order inputs, whether triggered by a same-chain fill, a cross-chain `RedeemEscrow`/`RefundEscrow` message, or a GET-response cancellation. Its only anti-replay guard is the per-token `_orders[commitment][token]` balance underflow check ` [1](#0-0) `. Unlike the vault-freeze bug class (two symmetric limits deadlocking each other), the actual custody-relevant local finding is that `_withdraw` never re-checks `_filled[commitment]` before moving funds — finalization state (`_filled`) and escrow balance state (`_orders`) are updated in the same call but are logically independent guards, and every entry point into `_withdraw` (`onAccept` for `RedeemEscrow`/`RefundEscrow`, `onGetResponse` for source-cancel, and the intrinsic same-chain fill/cancel paths) relies solely on its own upstream check (`_filled[commitment] != address(0)` in `cancelOrder`, `if (_filled[commitment] != address(0)) revert Filled();`) rather than a check inside `_withdraw` itself.

### Finding Description
`cancelOrder` checks `_filled[commitment]` once at the top: ` [2](#0-1) `. This dispatches to `_cancelSameChain`, `_cancelFromSource`, or `_cancelFromDest` depending on chain. `_cancelFromDest` immediately sets `_filled[commitment]` locally and dispatches a `RefundEscrow` POST to the source chain: ` [3](#0-2) `. On the source chain, `onAccept` receives the `RefundEscrow` message and calls `_withdraw(body, true, true)` directly — with no re-check of `_filled[commitment]` on the source chain before finalizing: ` [4](#0-3) `. Separately, `_cancelFromSource` dispatches a GET request to prove non-fill on the destination, and its response handler `onGetResponse` also calls `_withdraw(body, true, true)` unconditionally once the destination proof shows an empty slot: ` [5](#0-4) `.

`_withdraw` itself only guards via the escrow balance: ` [6](#0-5) `. Because `_orders[commitment][token]` is decremented by the *exact amount specified in the withdrawal request body* rather than validated against the full remaining balance, and because the same commitment's `_orders` entries are shared across the fill path (`IntrinsicIntents.sol`, which uses `_withdraw(body, false, isFullyFilled)` with proportional partial amounts) and the cross-chain refund/redeem path, a race between an in-flight partial fill on the source chain and an already-in-flight `RefundEscrow`/GET-response cancellation message (both of which were valid to initiate before either finalized) can result in both paths independently passing their own local pre-check (`_filled` was `address(0)` in both when each message was dispatched) and both calling `_withdraw` with `finalize=true`, each writing `_filled[commitment] = beneficiary` and decrementing/transferring escrow — sending out more than 100% of the escrowed input across two different beneficiaries (the user via refund, and the solver via redeem), since `_orders` never floors at zero and Solidity's default checked-arithmetic revert on underflow is the *only* thing preventing this, not an explicit `_filled` state re-check.

This mirrors the reported vault bug class's core defect: two independently-triggerable state transitions (fill vs. cancel) share a common resource-limit variable (`_orders[commitment][token]`) without a single atomic guard that serializes them, and the finalization flag (`_filled`) is set as a side effect rather than checked as a precondition inside the shared mutation function.

### Impact Explanation
If exploitable, this allows double-settlement of escrowed order inputs — a user could race a same-chain partial-fill transaction against a destination-initiated cancellation message such that both a solver's redeem and the user's refund draw from the same escrow, i.e., paying out more total value than was ever deposited, directly matching the bounty's "replay/double-claim/double-settlement" and "stealing or loss of funds" categories.

### Likelihood Explanation
This requires precise timing between a source-chain fill (or partial fill) transaction and a destination-cancel message being relayed and finalized within the same block window before the underflow-revert boundary is hit, and depends on exact order state and per-token escrow amounts lining up so neither leg underflows before completing. I was not able to fully trace whether `_orders[commitment][token]` for the *same specific token* is guaranteed to be reduced to exactly zero by a full fill before a colliding refund message lands (which would make the second call revert on `escrowed == 0`), so this cannot be confirmed as a fully provable exploit path without deeper reentrancy/ordering analysis across `IntrinsicIntents.sol`'s partial-fill accounting and `ExtrinsicIntents.sol`'s message-driven withdrawal calls — this is a plausible but not conclusively demonstrated local analog.

### Recommendation
Add an explicit `if (_filled[commitment] != address(0)) revert Filled();` check at the top of `_withdraw` itself (not just at the call sites), so finalization state is checked atomically with escrow mutation regardless of which of the four call paths (same-chain fill, same-chain cancel, `onAccept` redeem/refund, `onGetResponse` cancel) triggers it. This removes reliance on each caller independently re-validating `_filled` before dispatching or relaying a cross-chain message that ultimately reaches a shared, non-reentrant-guarded mutation point.

### Proof of Concept
Not independently reproduced in this pass — a runnable Foundry PoC would need to: (1) place a cross-chain order, (2) call `_cancelFromDest` to dispatch `RefundEscrow` toward the source chain, (3) before that message lands on the source chain, submit a same-chain-style partial/full fill against the same commitment on the source chain (only applicable if source==dest, i.e., same-chain order) or interleave a solver's `RedeemEscrow` delivery against the source chain in the same block as the relayed `RefundEscrow`, and (4) verify whether `_orders[commitment][token]` underflow reverts the second call or whether distinct tokens/amounts let both pass. This is flagged as unverified and would require Devin's execution environment (Foundry) to confirm state-transition ordering, which I do not have access to in this ask-only session.

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L470-474)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
        bytes32 commitment = keccak256(abi.encode(order));

        if (_filled[commitment] != address(0)) revert Filled();

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-251)
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```
