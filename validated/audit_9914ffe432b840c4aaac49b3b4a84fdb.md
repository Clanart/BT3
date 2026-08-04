Based on the investigation, I found a concrete analog in the Tron variant of the intent settlement contract. The GMIZap bug's core pattern — a check that syntactically exists but doesn't actually validate what it claims to validate — maps directly onto an unsafe ERC20 return-value check in the escrow settlement path.

### Title
Unchecked ERC20 boolean return value in escrow settlement permanently burns user/solver funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`withdraw()` in the Tron `IntentGatewayV2` settles cross-chain escrow (redeem to solver or refund to user) by making a raw low-level `.call` to the token's `transfer` function and only checking that the *call itself* did not revert (`success`), never decoding and checking the ERC20 boolean return value.

### Finding Description
In `withdraw()`: [1](#0-0) 

the token transfer is performed as:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [2](#0-1) 

This only guards against the call reverting; it does not decode `returndata` to verify the ERC20 `transfer` actually returned `true`. Many TRC20/ERC20 tokens (including several deployed on Tron, which is exactly the chain this contract targets) return `false` on failure instead of reverting. With such a token, `success` is `true` even though no tokens moved, yet the function unconditionally proceeds to:
```solidity
_orders[body.commitment][token] -= amount;
```
and sets `_filled[body.commitment] = beneficiary;` [3](#0-2) 

This is the same class of defect as the GMIZap report: a guard clause (`require`/`if (!success)`) that appears to validate a critical outcome but is structurally incapable of catching the actual failure mode it was meant to catch (the seed bug used a malformed type conversion that made the check meaningless; here the check inspects the wrong signal — call success instead of the ERC20 return value).

### Impact Explanation
Once this silent-failure path executes, the commitment's escrow accounting (`_orders[commitment][token]`) is decremented to zero and `_filled[commitment]` is set as if settlement succeeded, permanently closing out the order. The intended beneficiary (solver on redeem, or user on refund/cancel) never receives the tokens, and there is no retry path because the contract's own bookkeeping now reports the order as settled. This is a direct, unrecoverable loss of escrowed funds — squarely inside the required impact category ("stealing or loss of funds"). This same unguarded `.call` pattern is repeated for the fee-token release later in the same function.

### Likelihood Explanation
No privileged actor, malicious relayer, or compromised prover is needed — this triggers purely from ordinary order flow whenever the escrowed input token is a non-reverting-on-failure ERC20 (a known, common token behavior class, and specifically relevant since this file targets Tron, whose dominant TRC20 tokens are notorious for non-standard `transfer` semantics). Any order using such a token as an input asset is affected on every redeem/refund/cancel settlement.

### Recommendation
Replace the raw `.call` + `success`-only check with OpenZeppelin's `SafeERC20.safeTransfer`, which both checks call success and decodes/validates the boolean return value (treating tokens with no return data as success only when explicitly allowed). Apply this to every token transfer in `withdraw()`, including the fee-token release, and mirror the fix in `SweepDust` handling in the same contract, which has the identical pattern.

### Proof of Concept
1. Deploy (or use) an ERC20/TRC20 token whose `transfer()` returns `false` on failure instead of reverting (e.g., insufficient balance in the gateway due to a prior accounting drift, or a deliberately crafted token used as the order's input asset).
2. Place a cross-chain order with that token as `inputs[0].token`, escrowing `amount` in the gateway.
3. Trigger settlement (fill on destination → `RedeemEscrow` dispatched, or cancel → `RefundEscrow` dispatched) so that `onAccept` → `withdraw()` executes on the source chain.
4. Force the token's `transfer(beneficiary, amount)` to return `false` (e.g., balance is insufficient at the moment of call due to a race, or the token intentionally signals failure this way).
5. Observe: `success == true` (call didn't revert), `if (!success)` does not trigger, `_orders[commitment][token]` is decremented to 0, `_filled[commitment]` is set — yet `beneficiary`'s token balance is unchanged. The escrowed funds are permanently lost with no path to retry or reclaim.

### Citations

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
