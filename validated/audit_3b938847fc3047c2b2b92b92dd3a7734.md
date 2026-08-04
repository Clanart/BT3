## Title
Unchecked ERC20 return-value bytes in `IntentGatewayV2::withdraw()` allows escrow finalization without actual token delivery, causing permanent user fund loss - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2` finalizes escrow withdrawals and fee redemptions using a raw low-level `.call()` to the ERC20 `transfer` selector, checking only that the *call itself* did not revert (`success`), but never decoding/verifying the boolean return value the ERC20 standard requires. Non-standard tokens that return `false` on failed transfer instead of reverting will pass this check silently, while the contract nonetheless marks the order as filled/refunded and decrements escrow accounting — burning the record of the obligation without ever delivering the funds.

### Finding Description
In `withdraw()`, escrowed tokens are released to the beneficiary via: [1](#0-0) 

and accumulated protocol/relayer fees via: [2](#0-1) 

Both blocks use `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only check the boolean `success` returned by `.call` (i.e., that the callee did not revert / call did not fail at the EVM level). They never decode the returned bytes to confirm the ERC20 `transfer` actually returned `true`. Per EIP-20, compliant contracts may legitimately return `false` on a failed transfer without reverting; several deployed tokens (and non-standard TRC20 tokens on Tron, which is the exact target of this contract file) follow this pattern.

Immediately after the unguarded transfer, `withdraw()` unconditionally:
- Sets `_filled[body.commitment] = beneficiary` (line 684), permanently marking the order as completed.
- Decrements `_orders[body.commitment][token] -= amount` (line 701), destroying the escrow record.
- Deletes the fee escrow record (line 713).

If the underlying token silently returns `false` instead of reverting, the whole flow completes as if funds were delivered: the beneficiary receives nothing, the order can never be retried or refunded (it's already `_filled`), and the escrowed tokens remain permanently stuck/unaccounted-for in the contract. This is the exact "silent transfer failure continues the flow" pattern described in the source report, now instantiated in Hyperbridge's own cross-chain intent-settlement (escrow release) path rather than a payment SDK.

Notably, other flows in the *same* file use `SafeERC20.safeTransferFrom` (which reverts on non-compliant/false-returning tokens), e.g.: [3](#0-2) 
showing the codebase is aware of and elsewhere defends against this exact class of token, but the withdrawal/fee-redemption and dust-sweep paths were left using raw, unchecked `.call()`: [4](#0-3) 

### Impact Explanation
This directly causes loss of funds for the rightful beneficiary (solver or user) with no path to recovery, satisfying the "stealing or loss of funds" / "wrong beneficiary or amount" impact bar. Because `_filled` is set unconditionally regardless of whether the token transfer actually succeeded semantically, the escrowed tokens become permanently locked/lost inside the `IntentGatewayV2` contract while the protocol's own bookkeeping shows the order as settled — a double-loss: the beneficiary never gets paid, and the escrow can never be re-withdrawn since `_orders[commitment][token]` has already been decremented and `_filled` prevents retry logic.

### Likelihood Explanation
This requires no malicious actor, relayer, or governance compromise — it triggers automatically whenever an order's escrowed input/fee token is a non-standard ERC20/TRC20 implementation that returns `false` rather than reverting on failure (e.g., due to insufficient allowance/balance edge cases, blacklisting, or paused-token states common on Tron TRC20 tokens). Given this file specifically targets the Tron deployment where non-standard token semantics are common, likelihood is realistic in production rather than purely theoretical.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` handling with `SafeERC20.safeTransfer`, consistent with the rest of the contract (as already done for `safeTransferFrom` in the escrow-in path). This ensures both call-level failures and false-boolean-return failures cause a revert, preventing `_filled`/escrow state from being finalized without a genuine transfer.

### Proof of Concept
1. Deploy/select an ERC20/TRC20 token as an order's input/fee token whose `transfer()` returns `false` on failure instead of reverting (e.g., insufficient balance due to a rounding/dust edge case, or a blacklist-style token).
2. Create and fill an order through the normal `ExtrinsicIntents`/`IntentGatewayV2` flow so that escrow accounting records `_orders[commitment][token] = amount`.
3. Trigger `withdraw()` (via the normal `RedeemEscrow` request path or `onGetResponse` cancellation path) at a moment where the token's `transfer` call returns `false` (but does not revert).
4. Observe: `token.call(...)` returns `success = true` (the call itself succeeded), so `TransferFailed()` is never raised; `_filled[commitment]` is set and `_orders[commitment][token]` is decremented — yet the beneficiary's token balance is unchanged. The funds are now unrecoverable through the normal withdrawal path.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L452-454)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-667)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-705)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```
