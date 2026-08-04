### Title
Escrow payout accepted as successful without checking ERC20 return value in Tron `IntentGatewayV2.withdraw`/`onAccept` (SweepDust) - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2` correctly uses OpenZeppelin's `SafeERC20.safeTransferFrom` when pulling funds into escrow, but the payout paths (`withdraw` and the `SweepDust` branch of `onAccept`) release escrowed tokens using a raw low-level `.call()` to `IERC20.transfer` and only check that the call did not revert — the returned `bool` success value is discarded. Any ERC20 token that signals failure by returning `false` (rather than reverting), a common pattern for non-standard-compliant TRC20/ERC20 tokens, will cause the contract to treat a failed transfer as successful, permanently zero out the escrow accounting, and mark the order as filled/refunded — while the beneficiary receives nothing.

### Finding Description
`withdraw()` iterates over `body.tokens` and pays the beneficiary with: [1](#0-0) 
and the accumulated transaction fees with: [2](#0-1) 
Both checks use `(bool success,) = token.call(...)` and only revert `if (!success)`. This pattern only detects reverts; it does not decode/verify the returned boolean, so a token whose `transfer` function returns `false` on failure (instead of reverting) will make `success == true` even though no tokens moved. Immediately after this false-positive check, the code does `_orders[body.commitment][token] -= amount;` and `_filled[body.commitment] = beneficiary;`, permanently finalizing the order and destroying the escrow record.

The same unchecked pattern is used in the `SweepDust` admin-message branch: [3](#0-2) 

This is the direct Tron analog of the reported bug class: instead of the "transfer always reverts because it doesn't return a bool" failure mode (handled fine by raw `.call`), this is the complementary/inverse failure mode — "transfer signals failure via a `false` return without reverting" — which raw `.call` combined with only checking `success` fails to catch. Notably, the same file already imports and uses `SafeERC20` for the escrow-funding path (`safeTransferFrom` at lines 453 and 478 in the same contract), showing the payout path is inconsistent and unguarded by comparison.

### Impact Explanation
Once `withdraw()` is invoked via the authenticated `RedeemEscrow`/`RefundEscrow` cross-chain message flow, the escrow slot is decremented and the order is marked `_filled` regardless of whether the beneficiary actually received funds. If the underlying token silently returns `false` on that particular transfer (e.g. due to an internal pause, blacklist, or non-standard failure semantics), the beneficiary's tokens are permanently locked in the contract with no remaining accounting path to reclaim them — the order is already marked filled/refunded, and the escrow amount it referenced has been zeroed. This is a direct, unauthorized fund loss for the legitimate order beneficiary.

### Likelihood Explanation
This does not require a malicious peer, relayer, or governance actor — it is triggered purely by the on-chain behavior of whichever ERC20/TRC20 token was specified as `order.inputs[i].token` when the order was placed, combined with the ordinary settlement flow (fill or cancel) that any user or solver can drive to completion. Any token in use on Tron/EVM deployments that can return `false` instead of reverting (a well-documented category of non-compliant tokens) triggers this silently on the very first failed transfer.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `withdraw()` and the `SweepDust` branch of `onAccept` with `IERC20(token).safeTransfer(beneficiary, amount)` (already imported via `using SafeERC20 for IERC20`), consistent with the escrow-funding path, so that both revert-on-failure and false-return-on-failure tokens are handled correctly and escrow state is never finalized against tokens that were not actually delivered.

### Proof of Concept
1. Deploy a token whose `transfer(to, amount)` returns `false` instead of reverting when `to` is on some internal blacklist/pause list (or simply when balance conditions aren't met, per some legacy token implementations).
2. Place a same-chain or cross-chain order using this token as an input asset; it escrows via `safeTransferFrom` successfully.
3. Trigger a fill/refund path that calls `withdraw()`, with the beneficiary being blacklisted/paused on the token at time of payout.
4. Observe: `token.call(...)` returns `(true, false-encoding)`, `success == true` is accepted, `_orders[commitment][token] -= amount` executes, `_filled[commitment] = beneficiary` is set, and `EscrowReleased`/`EscrowRefunded` is emitted — yet `beneficiary`'s token balance never increased. The escrowed tokens remain stuck in the contract with no further code path referencing them for that commitment.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-701)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
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
