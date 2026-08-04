## Title
`IntentGatewayV2.withdraw` (Tron) marks escrow released on ERC20 `transfer` calls that return `false` without reverting, permanently losing beneficiary funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` releases escrowed order funds and sweeps dust by making a raw low-level `.call()` to the token's `transfer` function and only checking that the call itself did not revert (`success`). It never inspects the returned ABI-encoded boolean. Non-standard/legacy ERC20 tokens that signal failure by returning `false` (instead of reverting) will pass this check even though no tokens moved, while the function unconditionally decrements escrow accounting and marks the order as permanently filled/refunded.

### Finding Description
In `withdraw`, for each escrowed token the contract does: [1](#0-0) 

```solidity
_filled[body.commitment] = beneficiary;
...
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
_orders[body.commitment][token] -= amount;
```

The same unsafe pattern is used for the fee-token payout and in the `SweepDust` handler inside `onAccept`: [2](#0-1) 

`success` from a low-level `.call()` only reflects that the callee did not revert — it says nothing about the boolean return value that ERC20's `transfer` is specified to return. Tokens that implement `transfer` to return `false` on insufficient balance/blacklist/paused conditions (rather than reverting) will make this call succeed with `success == true` and returndata `abi.encode(false)`. The contract does not decode or check that returndata.

Note the same file already imports and declares `using SafeERC20 for IERC20;` and uses `safeTransferFrom` elsewhere (e.g. predispatch asset pulls), but the withdraw/sweep paths bypass `safeTransfer`/`safeCall` semantics entirely, which is the exact class of defect the external report describes (an ERC20 interaction that doesn't correctly account for non-reverting failure modes), just manifesting as a false-success acceptance instead of a false-revert.

### Impact Explanation
Because `_filled[body.commitment] = beneficiary` is set and `_orders[body.commitment][token] -= amount` executes unconditionally whenever the low-level call doesn't revert, a token transfer that silently fails (returns `false`) causes:
- The beneficiary never receives their entitled tokens.
- The order is irreversibly marked as filled/refunded (`_filled` is permanent, `onGetResponse`'s cancellation path also calls the same `withdraw`, and `RedeemEscrow`/`RefundEscrow` cannot be replayed since escrow accounting is already zeroed).
- The tokens remain custodied by the gateway contract with no accounting path left to reclaim them for the rightful beneficiary — a direct loss of bridged/escrowed user funds, matching the bounty's "stealing or loss of funds" / "false state acceptance" categories.

### Likelihood Explanation
This is reachable by any unprivileged relayer delivering a legitimate `RedeemEscrow`/`RefundEscrow`/`SweepDust` POST request for an order whose input/output token is a non-standard ERC20 (return-false-on-failure semantics, e.g. older tokens, some fee-on-transfer/paused tokens). No malicious peer, prover, or admin action is required — only that the destination-side escrowed token behaves per pre-EIP20-strict semantics, which is common enough among real-world tokens that the project already guards against this pattern elsewhere in the same contract via `SafeERC20`.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `withdraw` and in the `SweepDust` branch of `onAccept` with `IERC20(token).safeTransfer(beneficiary, amount)` (consistent with the `using SafeERC20 for IERC20;` declaration already present, and consistent with the non-Tron `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw` which correctly uses `safeTransfer`).

### Proof of Concept
1. Create an order whose input token is a mock ERC20 whose `transfer` returns `false` (does not revert) when, e.g., the gateway's balance is insufficient or a blacklist flag is set for the beneficiary.
2. Deposit/escrow the order normally so `_orders[commitment][token] = amount`.
3. Trigger `RedeemEscrow`/`RefundEscrow` via `onAccept`, which calls `withdraw(body, isRefund)`.
4. `token.call(...)` returns `(true, abi.encode(false))` — `success` is `true`, so `TransferFailed` is not raised.
5. `_filled[commitment]` is set and `_orders[commitment][token] -= amount` runs, finalizing the order as paid while the beneficiary's token balance is unchanged and the tokens remain stuck in the gateway contract.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-713)
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
        }
    }

    /**
     * @notice Withdraws the escrowed tokens for a request body.
     * @dev This function is marked as internal.
     * @param body The request body containing commitment, tokens, and beneficiary.
     * @param isRefund Whether this is a refund (true) or a successful fill (false).
     */
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
```
