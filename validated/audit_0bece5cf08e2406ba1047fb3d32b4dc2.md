## Analysis

The external report's core defect: a value that controls a token transfer (`fee_destination`) is never validated to actually behave like a real token account, so the transfer can silently fail/lock funds, and the corresponding cancel/refund path breaks because it depends on the same unchecked transfer.

The closest verifiable local analog is in the **Tron deployment of `IntentGatewayV2`**, which handles escrow settlement (redeem/refund) for cross-chain intents.

### Title
Unchecked ERC20 return value in `IntentGatewayV2.withdraw()` / `SweepDust` can silently mark escrow as settled while beneficiary receives nothing - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron `IntentGatewayV2` contract imports and uses `SafeERC20` for every escrow **deposit** (`placeOrder`'s `safeTransferFrom` calls), but its escrow **payout** path (`withdraw`) and the governance `SweepDust` branch of `onAccept` bypass `SafeERC20` and instead perform a raw low-level `.call` to the token, checking only that the call itself did not revert - not the ERC20 return value. [1](#0-0) [2](#0-1) 

### Finding Description
`placeOrder` escrows funds safely with `IERC20(token).safeTransferFrom(...)`, which reverts on a token returning `false`. [3](#0-2) 

But `withdraw(WithdrawalRequest memory body, bool isRefund)` - invoked from `onAccept` for both `RedeemEscrow` (solver fill payout) and `RefundEscrow` (cancel) - releases the same escrow using:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [4](#0-3) 

A raw `.call` only reverts on `success == false` when the callee itself reverts. Any ERC20 implementation that returns `false` instead of reverting on a failed transfer (blacklist rejection, paused state, insufficient allowance edge case, or any non-fully-compliant TRC20/ERC20 token - a realistic condition on Tron where token compliance is inconsistent) makes `success == true` even though zero tokens moved. The code never decodes/checks the returned boolean, unlike `SafeERC20.safeTransfer`, which does exactly that check.

Because this check is bypassed, execution proceeds to unconditionally mark the order settled and decrement escrow:
```solidity
_filled[body.commitment] = beneficiary;
...
_orders[body.commitment][token] -= amount;
``` [5](#0-4) 

The same pattern (`token.call(...)` + `success`-only check) is used for the `TRANSACTION_FEES` payout and in the `SweepDust` admin branch of `onAccept`. [6](#0-5) [7](#0-6) 

This mirrors the report's core flaw precisely: a value governing a token transfer (there, `fee_destination`; here, the transfer's actual success/failure) is not properly validated, so the state machine advances (order marked filled/refunded, escrow accounting zeroed, `EscrowRefunded`/`EscrowReleased` emitted) as if funds moved, while they did not - and once `_orders[commitment][token]` is decremented, there is no retry path, so the funds are permanently stuck in the contract with no beneficiary credit ("locked funds", same as the airlock case).

### Impact Explanation
This falls squarely under "stealing or loss of funds" for bridge custody/intent settlement: escrowed user or solver funds can become permanently unrecoverable while the protocol's own bookkeeping (`_orders`, `_filled`) reports the order as successfully settled. This affects both the `RedeemEscrow` path (solver's payout on a filled order) and `RefundEscrow` (user's refund on a cancelled order) - i.e., both legitimate parties in an intent can lose funds through no fault of their own, triggered by ordinary use of a non-fully-compliant token.

### Likelihood Explanation
This requires no malicious relayer, prover, or admin - it is triggered purely by the ERC20 semantics of the token specified as an order input by the order creator at `placeOrder` time, combined with the withdraw path being reached through the normal, authenticated ISMP `onAccept` flow (`authenticate()` is already passed). Any token in `order.inputs` whose `transfer` can return `false` without reverting (common enough among non-standard tokens, especially in the Tron/TRC20 ecosystem this file specifically targets) hits the bug on every redeem/refund of that order.

### Recommendation
Replace the raw `.call` + `success`-only checks in `withdraw()`, the `TRANSACTION_FEES` payout, and the `SweepDust` branch with `SafeERC20.safeTransfer`, consistent with how `safeTransferFrom` is already used for deposits in `placeOrder`. This ensures a token returning `false` reverts the whole settlement instead of silently marking escrow as released while sending nothing.

### Proof of Concept
1. Deploy (or use) an ERC20/TRC20 token whose `transfer` returns `false` on failure instead of reverting (e.g., insufficient internal balance guard, blacklist check) rather than reverting.
2. User calls `placeOrder` with this token as an `order.inputs[0].token`; `safeTransferFrom` succeeds and funds are escrowed under `_orders[commitment][token]`.
3. Order is later refunded/redeemed: `onAccept` is invoked with a valid `RefundEscrow`/`RedeemEscrow` body, calling `withdraw(body, isRefund)`.
4. Arrange for the token's `transfer(beneficiary, amount)` to return `false` (e.g., beneficiary transiently blacklisted, or a race causing the token's internal check to fail) without reverting.
5. `token.call(...)` returns `success = true` (the call didn't revert), so `if (!success) revert TransferFailed();` does not trigger.
6. `_orders[body.commitment][token] -= amount;` executes, `_filled[body.commitment] = beneficiary;` is set, and `EscrowReleased`/`EscrowRefunded` is emitted - all as if the payout succeeded, even though the beneficiary's token balance never increased and the tokens remain trapped in the `IntentGatewayV2` contract with no way to reclaim them.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L55-56)
```text
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L399-399)
```text
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-672)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

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
