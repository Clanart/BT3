## Title
Unchecked ERC20 return-data in `IntentGatewayV2.withdraw()` allows silent-fail tokens to permanently drain/lock escrowed funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

## Summary
The Tron variant of `IntentGatewayV2` settles escrow redemptions and refunds using a raw low-level `.call` with the `IERC20.transfer` selector and only checks that the **call itself did not revert**, never decoding the returned boolean. This is the exact bug class from the external report ("not all tokens return boolean") but manifesting in the opposite, more dangerous direction: a token whose `transfer()` returns `false` without reverting is treated as a **successful** payout. Because the escrow accounting (`_orders[...] -= amount`) and the one-time settlement flag (`_filled[commitment] = beneficiary`) are updated unconditionally before/alongside this unchecked transfer, a silently-failing token transfer causes the order to be marked permanently redeemed while the beneficiary receives nothing — an irreversible loss of the escrowed funds.

## Finding Description
In `withdraw()`, which is invoked from `onAccept()` for both `RedeemEscrow` and `RefundEscrow` request kinds (reachable by any user who creates/cancels/fills a cross-chain intent order), token payouts are performed like this: [1](#0-0) 

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

`success` here only reflects whether the low-level call reverted — it does **not** decode/validate the ABI-encoded boolean return value. Any ERC20 implementation that returns `false` (rather than reverting) on a failed transfer — a well-known real-world pattern for tokens with blacklists, pausable transfers, or custom failure semantics — will make this code treat the failed transfer as a success. The same unchecked pattern is repeated in the `SweepDust` handler in the same file: [2](#0-1) 

By contrast, the canonical/audited EVM implementation of the same logic in `IntentsBase.sol` correctly uses OpenZeppelin's `safeTransfer`, which does decode and validate returndata: [3](#0-2) 

This shows the "Fixed" remediation referenced in the external M-04 report was applied to the primary `IntentsBase.sol`/`IntentGatewayV2.sol` (main EVM) contracts using `SafeERC20`, but the Tron fork of `IntentGatewayV2.sol` still contains the raw, unchecked `.call` pattern for the actual withdrawal/settlement path — the highest-value operation in the whole contract, since it is where escrowed user/solver funds finally leave custody.

Because `_filled[body.commitment]` is set and `_orders[body.commitment][token]` is decremented regardless of whether the token really moved, there is no retry path: once `withdraw()` "succeeds" (call doesn't revert), the order is permanently marked filled/refunded and the escrow slot is zeroed out, even if the beneficiary received zero tokens.

## Impact Explanation
Any order creator can choose an arbitrary ERC20 as an input token when constructing their own order (`order.inputs[i].token`). A malicious or non-standard token contract that returns `false` from `transfer()` under attacker-controlled conditions (rather than reverting) causes:
- The solver/filler (the rightful escrow beneficiary on `RedeemEscrow`) or the order owner (on `RefundEscrow`) to receive **no tokens**.
- `_filled[commitment]` to be permanently set and `_orders[commitment][token]` to be permanently decremented, so the funds can never be claimed again.

This is a direct, irreversible loss of escrowed funds for the rightful beneficiary — matching the bounty's "stealing or loss of funds" and "logic attack" categories — reachable purely through the normal `onAccept`/`withdraw` flow without any malicious relayer, prover, or admin involvement.

## Likelihood Explanation
The attack requires only that the attacker (order creator) supply a custom ERC20 token as one of `order.inputs`, which is fully attacker-controlled input with no allowlist enforced in this contract. Silent-failure-on-`false` token semantics are a well-documented real-world pattern (blacklists, pausability, compliance holds), making this a realistic and low-effort trigger, not a purely theoretical one.

## Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` handler in `evm/tron/contracts/apps/IntentGatewayV2.sol` with OpenZeppelin's `SafeERC20.safeTransfer`, consistent with `evm/src/apps/intentsv2/IntentsBase.sol`, so that both "no return data" and "returns false" failure modes are properly handled and cause a revert instead of a silent, unrecoverable settlement.

## Proof of Concept
1. Attacker deploys a custom ERC20 token `EvilToken` whose `transfer()` function contains logic such as: if `msg.sender == IntentGatewayV2Address && to == <specific beneficiary>`, skip the balance update and `return false;` (no revert).
2. Attacker creates a cross-chain order with `EvilToken` as an input asset, escrowing it in `IntentGatewayV2` (Tron).
3. A solver fills the order on the destination chain, delivering the real requested output assets to the user, expecting reimbursement via `RedeemEscrow`.
4. The `RedeemEscrow` POST request arrives at the source chain and triggers `withdraw()`; `EvilToken.transfer(solver, amount)` returns `false` without reverting.
5. `withdraw()` treats this as success: `_filled[commitment]` is set, `_orders[commitment][token]` is decremented, `EscrowReleased` is emitted — but the solver's balance of `EvilToken` never increased.
6. The solver has permanently lost the escrowed reimbursement with no path to reclaim it, since the order is now marked filled and the escrow entry is deleted.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
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
