## Title
Unchecked ERC-20 return value in `IntentGatewayV2.withdraw()` permanently locks escrowed funds on false-returning token transfers - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

## Summary
The Tron variant of `IntentGatewayV2` imports and enables `SafeERC20` (`using SafeERC20 for IERC20;`) but does not use it in the escrow-settlement path. Instead, `withdraw()`, the `SweepDust` handler, and the fee-payout branch use a raw low-level `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only check that the *call itself* did not revert — they never inspect the returned ABI-encoded boolean. This is the same class of defect the external report flags (a payout primitive that silently fails to move value while the contract's bookkeeping assumes success), except here the silent-failure vector is a non-reverting `transfer()` that returns `false` rather than insufficient forwarded gas.

## Finding Description
`withdraw()` and the `SweepDust`/fee-forwarding paths in `evm/tron/contracts/apps/IntentGatewayV2.sol` do this for every ERC-20 leg: [1](#0-0) [2](#0-1) [3](#0-2) 

`if (!success) revert TransferFailed();` only guards against a revert. Any ERC-20/TRC-20 implementation that follows the EIP-20 spec literally — returning `false` on a failed transfer (e.g., insufficient contract balance, a paused/blocklisted token, a token with transfer hooks that no-op) instead of reverting — passes this check. Once past it:

- `_orders[body.commitment][token] -= amount;` decrements escrow accounting as if the funds left the contract.
- `_filled[body.commitment] = beneficiary;` marks the order as finalized (redeemed or refunded), emitting `EscrowReleased`/`EscrowRefunded`.
- The tokens never actually reached `beneficiary` — they remain in the gateway contract, but the accounting no longer reflects any claim on them (a second withdrawal attempt for the same commitment/token hits `UnknownOrder` because `_orders[...] == 0`).

Contrast this with the same logic in the standard EVM contract, `IntentsBase._withdraw()`, which correctly uses `SafeERC20.safeTransfer`, reverting on both call failure and a `false` return: [4](#0-3) 

The Tron file even imports and declares `using SafeERC20 for IERC20;` but never calls `safeTransfer`/`safeTransferFrom` in the withdrawal/sweep/fee paths, indicating the raw `.call` pattern was substituted without preserving return-value verification.

## Impact Explanation
This is a direct, unprivileged loss-of-funds path: a user's or solver's escrowed input tokens (or accumulated transaction fees, or swept protocol dust) can be permanently stranded in the gateway with no recovery mechanism, because the on-chain state (`_orders`, `_filled`) is updated as though settlement succeeded. This matches the bounty's "stealing or loss of funds" and "false state acceptance" categories — the contract accepts a failed transfer as a successful settlement, and the escrow entry that would have permitted recovery is deleted/decremented.

## Likelihood Explanation
Triggering this does not require a malicious relayer, prover, or admin — it only requires that one of the escrowed input/output tokens is (or becomes) a standard-conforming ERC-20/TRC-20 that returns `false` rather than reverting on failure (common among older token implementations, some stablecoins' edge cases, or blocklisting/pausable tokens). Since token selection for orders is attacker/user-controlled (`order.inputs[i].token`), an order can be deliberately crafted around such a token, or an existing order can be caught by a token later transitioning into a failure state (e.g., temporary pause) at settlement time.

## Recommendation
Replace the manual `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()`, the `SweepDust` branch, and the fee-payout branch with `SafeERC20.safeTransfer` (already imported/enabled via `using SafeERC20 for IERC20;`), which reverts on both call failure and a `false` return value, consistent with `IntentsBase._withdraw()` in the standard EVM contract.

## Proof of Concept
1. Deploy (or point) the `IntentGatewayV2` (Tron) contract at a token whose `transfer()` returns `false` on failure instead of reverting (e.g., simulate contract balance insufficiency, or use a mock token like the ones used in the SDK/foundry test suite that mirror this behavior).
2. User places an order with that token as an input, escrowing `amount` (`_orders[commitment][token] = amount`).
3. Trigger the fill/refund path so `onAccept` calls `withdraw(body, isRefund)`.
4. Make the token's `transfer(beneficiary, amount)` return `false` without reverting (drain the gateway's spendable balance for that token via a prior interaction, or use a mock).
5. Observe: `withdraw()` does not revert (`success == true`, but decoded return data is `false`, which is never checked), `_orders[commitment][token]` is decremented to `0`, `_filled[commitment]` is set, and `EscrowReleased`/`EscrowRefunded` is emitted — yet `beneficiary`'s token balance is unchanged and the escrowed `amount` remains stuck in the gateway with no further code path able to move it out.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
