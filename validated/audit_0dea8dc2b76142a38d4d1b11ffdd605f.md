## Title
Escrow withdrawal accepts failed ERC20 transfers as successful due to unchecked return-data in `IntentGatewayV2.withdraw` (Tron) - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron deployment of `IntentGatewayV2` releases escrowed intent funds using a raw low-level `.call()` to the ERC20 `transfer` selector and only checks that the call itself did not revert (`success`), never inspecting the returned `bool` payload. Tokens that signal failure by returning `false` instead of reverting will pass this check while moving zero tokens, yet the contract still permanently marks the order as filled/refunded and decrements escrow accounting — this is the exact "missing/ignored ERC20 return value" bug class from the external report, but here it strikes the fund-custody/settlement path instead of a simple swap.

### Finding Description
In `withdraw()`, escrowed input tokens (and transaction fees) are released with:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [1](#0-0) 

The same unchecked pattern is used for treasury fee release and for the `SweepDust` handler in `onAccept`: [2](#0-1) [3](#0-2) 

`success` only reflects whether the low-level call reverted — it says nothing about the ABI-decoded return value. Per EIP-20, a compliant `transfer` should return `bool`, but numerous real tokens (deliberately or due to bugs) return `false` on failure rather than reverting (e.g., the class documented in the seed report — EURS-style tokens). With this pattern, such a failed transfer still reports `success == true`.

Immediately after the transfer call, `withdraw()` unconditionally finalizes state:
```solidity
_orders[body.commitment][token] -= amount;
...
_filled[body.commitment] = beneficiary;
...
emit EscrowReleased({commitment: body.commitment});
``` [4](#0-3) 

This directly contrasts with the input side of the same contract, which correctly uses OpenZeppelin's `safeTransferFrom` (checking the returned bool): [5](#0-4) 

So escrow is safely *collected* via `SafeERC20`, but unsafely *released* via a raw, unchecked-return-data `.call`.

### Impact Explanation
This corrupts the escrow accounting invariant: `_orders[commitment][token]` and `_filled[commitment]` are updated to reflect a completed settlement even though the actual token balance never moved to the beneficiary. Because the escrow map is decremented and the order is marked filled/refunded, there is no retry path — the tokens are irrecoverably stuck in the contract while the solver/user who was supposed to receive them gets nothing. This is a direct loss/lock of bridged funds in the escrow-release path, which is one of the explicitly in-scope impacts (fund loss, false settlement state acceptance).

### Likelihood Explanation
Likelihood is Medium: it requires the escrowed/swept token to be one that returns `false` on failed transfer rather than reverting. This is a known but non-universal ERC20 behavior class (explicitly called out in the seed report), and any user or solver can trigger it simply by using such a token as an order's input asset — no privileged actor, relayer, or malicious peer is needed. The call itself is reachable through the normal cross-chain settlement flow (`onAccept` → `withdraw`) and the `SweepDust` governance-triggered path, both of which execute unconditionally once the transfer call doesn't revert.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` (input/fee release) and in the `SweepDust` branch of `onAccept()` with OpenZeppelin's `SafeERC20.safeTransfer`, consistent with the input-side `safeTransferFrom` usage already in the same file. This ensures both a reverted call and a `false` return value cause the transaction to revert, so escrow state is only finalized when the transfer actually succeeded.

### Proof of Concept
1. Deploy a mock ERC20 whose `transfer()` returns `false` on failure (e.g., insufficient allowance/balance condition or a deliberately "weird" token) instead of reverting.
2. Place an order using that token as an input on the source chain via `placeOrder` (succeeds — uses `safeTransferFrom`).
3. Solver fills the order on the destination chain; cross-chain `RedeemEscrow` message is delivered to `onAccept` → `withdraw()` on the source chain.
4. Force the mock token's `transfer()` to return `false` for this specific release (e.g., pause transfers to that recipient while leaving the call itself successful).
5. Observe: `withdraw()` proceeds because `success == true`, decrements `_orders[commitment][token]`, sets `_filled[commitment] = beneficiary`, and emits `EscrowReleased`, even though `beneficiary`'s token balance is unchanged — the escrowed tokens are now permanently stuck in the contract with no way to retry or reclaim them.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-713)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
```
