## Finding

### Title
Incorrect ERC-20 success check in `withdraw`/`SweepDust` lets a false-return token silently swallow escrowed funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The external report's core defect is a "wrong success check": the code inspects the wrong condition to decide whether an external token operation succeeded, so the contract proceeds as if the transfer worked when it actually did not (or vice-versa). The exact same defect class exists in the Tron variant of `IntentGatewayV2`'s fund-release path: it checks only that the low-level `.call()` did not revert, never decoding the boolean the ERC-20 `transfer()` function returns.

### Finding Description
`withdraw()` releases escrowed order funds to a beneficiary using a raw low-level call instead of `SafeERC20`: [1](#0-0) 

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
`success` here only reflects whether the low-level call reverted — it never inspects/decodes the ABI-encoded `bool` that ERC-20's `transfer()` is supposed to return. Several real-world tokens (and any adversarial/non-standard token an order can be created for) return `false` on failure (insufficient balance, blacklist, paused state, etc.) instead of reverting. When that happens, `success` is `true` even though zero tokens moved, so the code proceeds exactly as if the transfer succeeded. `_filled[body.commitment]` is already written unconditionally at function entry, and `_orders[body.commitment][token] -= amount` still executes, permanently marking the order filled/finalized and the escrow consumed. The same unguarded pattern (`success` from `.call()`, no return-data check) is repeated in the `SweepDust` branch of `onAccept`: [2](#0-1) 

By contrast, the canonical (non-Tron) EVM implementation in `IntentsBase.sol` correctly uses OpenZeppelin's `SafeERC20.safeTransfer`, which decodes and validates the boolean return value and reverts on `false`: [3](#0-2) 

This confirms the Tron variant diverged from the safe pattern used elsewhere in the same codebase, reproducing the report's exact "checked the wrong value from the call, so failure is mistaken for success" bug class in a fund-custody path rather than a pure DoS path.

### Impact Explanation
This is a genuine loss-of-funds / false-state-acceptance bug within the required impact gate: escrowed order tokens can be permanently locked/lost while the protocol records the order as `Filled`/`Refunded`. Once `_filled[commitment]` is set and `_orders[commitment][token]` is decremented, there is no legitimate retry path — the beneficiary never receives funds, and the order cannot be re-withdrawn or refunded because on-chain state says it was already settled. This directly matches "stealing or loss of funds" and "false proof/state acceptance" under the Hyperbridge impact gate, reachable purely through an unprivileged token behavior (any ERC-20 selectable for an order's input/output list), with no malicious relayer, prover, or admin required.

### Likelihood Explanation
Likelihood is Medium: it requires an order that escrows/pays out a token which returns `false` on failed transfer instead of reverting (a known, non-trivial but real subset of ERC-20 tokens). No governance, relayer collusion, or proof manipulation is needed — any user creating or filling an order denominated in such a token triggers the flawed check on the standard `RedeemEscrow`/`RefundEscrow`/`SweepDust` code paths.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `withdraw()` and the `SweepDust` branch with OpenZeppelin's `SafeERC20.safeTransfer`, consistent with the pattern already used in `evm/src/apps/intentsv2/IntentsBase.sol`. This decodes and validates the return data (treating tokens with no return data as reverting-on-failure, and tokens returning `false` as failed), preventing state from being finalized/decremented when the underlying transfer did not actually succeed.

### Proof of Concept
1. Deploy/whitelist an ERC-20 token whose `transfer()` returns `false` instead of reverting when the sender's balance is insufficient (a legal, standards-compliant-but-non-reverting implementation).
2. A user places an order escrowing this token via `IntentGatewayV2` on the Tron deployment.
3. Drain the gateway's balance of that token to below the escrowed `amount` (e.g., via a prior `SweepDust`/partial withdrawal on a shared balance, or by the token contract itself artificially failing under some condition returning `false`).
4. Hyperbridge delivers a `RedeemEscrow`/`RefundEscrow` message; `onAccept` → `withdraw()` executes:
   - `token.call(...)` succeeds (no revert) but the encoded return data decodes to `false`.
   - `success` is `true`, so `revert TransferFailed()` is skipped.
   - `_filled[body.commitment]` was already set to `beneficiary`; `_orders[body.commitment][token] -= amount` executes.
5. The beneficiary's token balance is unchanged (transfer silently failed), yet the order is now permanently marked as settled with escrow consumed — the funds are unrecoverable.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L403-409)
```text
            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
