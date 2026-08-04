## Title
IntentGatewayV2 (Tron) settlement path ignores ERC20 `transfer` return value, allowing escrow to be marked settled without funds moving - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` diverges from the canonical EVM implementation by replacing `SafeERC20.safeTransfer` with a raw low-level `.call()` using a manually-encoded `IERC20.transfer.selector`, and only checks that the **call itself did not revert** — it never decodes/validates the ABI-returned `bool` from the token's `transfer` function. This is the exact interface/return-value assumption gap described in the external report (Aave/Geist `withdraw()`/`repay()` return values being ignored): a non-reverting-but-failing ERC20 `transfer()` call is treated as a successful payout.

### Finding Description
In `withdraw()`, escrowed funds are released and the order is marked filled based solely on the low-level call succeeding: [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    ...
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
    ...
    _orders[body.commitment][token] -= amount;
```

Only the `success` boolean of the raw `call` is checked (i.e., whether the callee reverted). It never inspects the returned ABI-encoded `bool` that a standard `transfer()` is supposed to return. Any token that returns `false` instead of reverting on a failed transfer (the same non-standard behavior flagged in the external Aave/Geist report, and historically common among ERC20/TRC20 implementations) will make the low-level call return `success = true` while zero tokens actually move. The same pattern is reused in the dust-sweep path: [2](#0-1) 

Both `_filled[body.commitment] = beneficiary` and `_orders[body.commitment][token] -= amount` execute unconditionally once the low-level call "succeeds," permanently recording the order/escrow as settled. `withdraw()` is reachable from the public, unprivileged `onAccept` handler for `RedeemEscrow`/`RefundEscrow` requests, and from `cancelOrder()` for same-chain cancellations: [3](#0-2) [4](#0-3) 

By contrast, the canonical EVM `IntentGatewayV2`/`IntentsBase` settlement path uses `SafeERC20.safeTransfer`, which does decode and enforce the boolean return value: [5](#0-4) . The Tron file imports `SafeERC20` and uses `safeTransferFrom` elsewhere for deposits, but deliberately switched to the unchecked raw `.call()` pattern specifically in the fund-disbursing `withdraw()` and `SweepDust` paths — the exact "interface declaration doesn't match actual return semantics, and the return value is never checked" bug class from the external report, now placed on the fund-release side rather than the deposit side.

### Impact Explanation
Once `_filled`/`_orders` are updated, the commitment is permanently considered settled (`Filled()` guards block any retry/cancel), so the beneficiary has no path to reclaim the escrowed tokens even though they never received them — the funds become stuck in the `IntentGatewayV2` contract while the protocol's internal state falsely records the intent as fulfilled/refunded. This is a direct loss-of-funds / false-settlement-acceptance condition reachable through the intended public settlement flow, not through a malicious relayer or admin.

### Likelihood Explanation
This requires only that one of the escrowed input tokens used in an order return `false` on a failed `transfer` instead of reverting — a well-known non-standard but real-world ERC20/TRC20 behavior (and the same category of token behavior called out in the seed report). No privileged actor, malicious prover, or governance action is needed; any user who creates/fills/cancels an order denominated in such a token triggers the vulnerable code path during normal operation.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust` handler with `SafeERC20.safeTransfer`/`safeTransferFrom`, which is already imported and used elsewhere in this same file, so both call success and the ABI-decoded return value are enforced before finalizing escrow accounting and marking the order `_filled`.

### Proof of Concept
1. An order is created with an input token `T` whose `transfer()` implementation returns `false` (does not revert) when, e.g., the contract is paused or a blacklist check fails, rather than reverting.
2. A legitimate `RedeemEscrow`/`RefundEscrow` message (or a same-chain `cancelOrder`) invokes `withdraw()`.
3. `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `success = true` (the call didn't revert) even though `T.transfer` internally returned `false` and moved no tokens.
4. `_filled[body.commitment] = beneficiary` and `_orders[body.commitment][token] -= amount` execute, permanently marking the order as settled.
5. The beneficiary never receives token `T`; the tokens remain stuck in `IntentGatewayV2`, and no further redemption/cancellation is possible because `Filled()` now blocks retries.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-672)
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
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
