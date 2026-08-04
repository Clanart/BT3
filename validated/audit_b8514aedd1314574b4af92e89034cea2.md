## Analysis

The external report's core broken invariant: **the caller assumes an external token transfer either succeeds or reverts, but the token can silently signal failure (return `false` / enter a halted state) without reverting, and the caller doesn't check for this, so protocol state is updated as if the transfer succeeded.**

The direct local analog is in the Tron variant of `IntentGatewayV2`, where escrow withdrawal uses a raw low-level `.call` to invoke `IERC20.transfer` and only checks that the **call itself didn't revert**, never decoding/validating the ERC20 boolean return value.

### Title
Escrow marked settled and decremented even when ERC20 `transfer` returns `false` — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` and `SweepDust` handling in the Tron `IntentGatewayV2` release escrowed tokens using a raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` and only checks `success` from the low-level call — not the ABI-decoded boolean return value of `transfer`. Many ERC20-like tokens (pausable, blacklistable, or non-reverting-on-failure tokens) return `false` on a failed transfer instead of reverting. In that case `success` is `true` (the call executed without reverting) even though no tokens moved, yet the code proceeds to decrement escrow accounting and mark the order filled/refunded.

### Finding Description [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    ...
    if (token == address(0)) {
        (bool sent,) = beneficiary.call{value: amount}("");
        if (!sent) revert InsufficientNativeToken();
    } else {
        (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
        if (!success) revert TransferFailed();
    }

    _orders[body.commitment][token] -= amount;
    ...
}
```
`success` here only reflects whether the low-level call reverted — it does **not** decode the returned `bytes` to confirm the token's `transfer` returned `true`. A token that returns `false` instead of reverting on failure (e.g., a paused/blacklisted/frozen state, insufficient allowance edge case, or any non-standard ERC20) makes `success == true`, so the guard `if (!success) revert TransferFailed();` never fires. Execution continues: `_filled[body.commitment]` is set (marking the order permanently filled/refunded) and `_orders[body.commitment][token] -= amount` decrements escrow — even though the beneficiary received nothing.

The same unchecked pattern repeats in `SweepDust` handling in the same file: [2](#0-1) 

This is the same class of bug as the stETH report: an external token contract's ability to signal a non-reverting failure state is not checked before the protocol commits irreversible bookkeeping. Notably, the same file *does* use `SafeERC20`/`safeTransferFrom` for inbound deposits (`placeOrder`), and the mainline (non-Tron) `IntentsBase._withdraw` correctly uses `IERC20(token).safeTransfer(...)`, which reverts on a `false` return — confirming that this raw-call path in the Tron contract is a real deviation from the protocol's own safe pattern, not an assumption problem inherent to all EVM contracts. [3](#0-2) 

### Impact Explanation
Once `_filled[commitment]` is set and `_orders[commitment][token]` is decremented, the order is considered settled by the contract's own accounting. The intended beneficiary (user being refunded, or solver being paid escrow) never receives the tokens, and there is no remaining code path to retry or reclaim the escrow — the on-chain state says the order is fulfilled. This is a direct loss of user/solver funds: tokens remain stuck in the contract (or are otherwise unaccounted for) while the rightful beneficiary gets nothing, and no legitimate retry/redemption path exists since the commitment is already marked filled.

### Likelihood Explanation
This does not require a malicious relayer, prover, or governance actor — it is purely a function of which ERC20 token is used as the intent's input/output asset. Any token that can return `false` on transfer failure without reverting (a common, standards-compliant behavior for many real-world tokens, including pausable/blacklistable stablecoins) triggers this path under ordinary operating conditions (e.g., the token being paused, or the beneficiary being blacklisted) — an unprivileged attacker or even a routine administrative pause on the token side is sufficient to trigger silent fund loss.

### Recommendation
Replace the raw `.call` + `success`-only check with `SafeERC20.safeTransfer`, matching the pattern already used in `IntentsBase.sol` and inbound transfers in this same file. `SafeERC20` decodes the return data and reverts on `false`, or reverts directly if the call itself fails — either way, ensuring `_orders` accounting and `_filled` state are never mutated unless the transfer actually succeeded.

### Proof of Concept
1. Deploy an `IntentInputToken` whose `transfer` returns `false` when the recipient is blacklisted/paused (rather than reverting) — a legal ERC20 pattern.
2. User places an order using this token as input; tokens are escrowed via `safeTransferFrom` (correctly reverts-on-failure, so escrow accounting is accurate at deposit).
3. Trigger `RefundEscrow`/`RedeemEscrow` via `onAccept` → `withdraw()`, with the beneficiary in a blacklisted/paused state on the token.
4. `token.call(...)` returns `success = true` with encoded `false` payload; the `if (!success)` check does not catch it.
5. `_filled[commitment]` is set and `_orders[commitment][token] -= amount` executes — the order is now permanently marked settled, but the beneficiary's balance never changed. Tokens are effectively lost/locked in the contract with no remaining code path to redeem them.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-720)
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-410)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
