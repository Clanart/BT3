### Title
Silent ERC-20 `transfer` Failure in Escrow Withdrawal Permanently Locks Funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.withdraw()` (Tron variant) and the `SweepDust` branch of `onAccept()` pay out escrowed tokens using a raw low-level `.call` with the `IERC20.transfer` selector and only check that the call did not revert — they never decode/verify the returned boolean. This is the exact bug class from the external USDT report: a token that returns `false` on failure instead of reverting will be treated as a successful transfer.

### Finding Description
The contract imports and aliases `SafeERC20` (`using SafeERC20 for IERC20;`) and uses it correctly elsewhere (e.g. `safeTransferFrom` when escrowing inputs), but the payout path in `withdraw()` bypasses it entirely: [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    ...
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();

    _orders[body.commitment][token] -= amount;
    ...
}
```

For an ERC-20 that returns `false` instead of reverting on failure (the same non-compliant behavior described for USDT), `success` is `true` because the low-level call itself does not revert — only the ABI-encoded return value is `false`, and that value is never inspected. The function then unconditionally:
1. Marks the order as filled/refunded via `_filled[body.commitment] = beneficiary` (line 684), before the transfer even executes.
2. Decrements the escrow accounting `_orders[body.commitment][token] -= amount` (line 701) as if the beneficiary was paid.

The same pattern recurs in the `SweepDust` handler in `onAccept`: [2](#0-1) 

### Impact Explanation
Because the escrow-release path (`withdraw`) is invoked once per `RedeemEscrow`/`RefundEscrow` cross-chain message via `onAccept`, and `_filled`/`_orders` are updated regardless of whether the ERC-20 transfer actually succeeded, a silently-failing token transfer causes the escrowed tokens to remain locked in the `IntentGatewayV2` contract while the protocol's bookkeeping records the order as settled. There is no retry path once `_filled[body.commitment]` is set (see `UnknownOrder` guard at line 691, `_orders[...] == 0` check gates re-entry into `withdraw`). This is a direct loss/lock of bridged funds — the rightful beneficiary never receives their tokens, and the escrow cannot be re-claimed, matching the "stealing or loss of funds" / "bridged assets ... must move exactly once and only to the rightful beneficiary" impact categories.

### Likelihood Explanation
This requires only that one of the escrowed input tokens used with `IntentGatewayV2` be a non-fully-ERC20-compliant token (returns `false` instead of reverting on failure — a documented, common real-world pattern, not a hypothetical). No malicious relayer, prover, or admin is needed; the message triggering `withdraw` (RedeemEscrow/RefundEscrow) is a normal, expected part of the intent-fulfillment/cancellation flow. Any transient failure condition on the token (e.g., a paused/blacklist-style token, or a token with quirky transfer semantics) that causes `transfer` to return `false` rather than revert will trip this bug during ordinary use.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust` branch of `onAccept()` with `IERC20(token).safeTransfer(beneficiary, amount)` (the `SafeERC20` library is already imported and used elsewhere in this same contract), ensuring both call-revert and return-value semantics are enforced consistently with the rest of the codebase.

### Proof of Concept
1. Configure an order whose escrowed input/output token is a non-standard ERC-20 that, under some internal condition (e.g., destination address blacklisted, contract paused), returns `false` from `transfer` instead of reverting.
2. Complete the normal fill/cancel flow so that Hyperbridge dispatches a `RedeemEscrow` or `RefundEscrow` POST request to `IntentGatewayV2.onAccept`.
3. `onAccept` → `withdraw(body, isRefund)` executes: `token.call(...)` succeeds (returns `false` payload) so `success == true`; `_filled[body.commitment]` is set and `_orders[body.commitment][token] -= amount` runs.
4. The beneficiary's tokens never leave the contract, yet the order is now flagged as filled/refunded (`UnknownOrder` check at line 691 blocks any future retry), permanently locking the beneficiary's funds inside the gateway.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-671)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-706)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }

```
