## Analysis

The external report's core primitive — ERC20 `transfer`/`transferFrom` calls that can return `false` on failure without reverting, so code that only checks "did the low-level call revert" (rather than decoding the returned boolean) treats a *failed* transfer as *successful* — has a direct, locally provable analog in the Tron fork of the Intent Gateway.

The canonical EVM `IntentsBase.sol` uses OpenZeppelin's `SafeERC20.safeTransfer`, which correctly decodes and checks the returned boolean: [1](#0-0) 

But the Tron variant of the same escrow-release logic diverges from this and reimplements the transfer with a raw low-level `.call`, checking only that the *call itself* didn't revert — not the returned success boolean: [2](#0-1) 

The same broken pattern is repeated in the dust-sweep path of `onAccept`: [3](#0-2) 

### Title
Escrow settlement accepted on a non-reverting `false`-returning ERC20 transfer, permanently burning escrowed funds while marking the order as filled — (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` (reached via `onAccept`'s `RedeemEscrow`/`RefundEscrow` handling and via `onGetResponse`) releases escrowed tokens using a raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` and only checks `success` — whether the external call reverted — never decoding/validating the ABI-encoded boolean return value. For ERC20 implementations that return `false` on failure instead of reverting (a documented, common divergence, cited by the original report and by the AAVE V2 audit it references), this call reports `success = true` even though the beneficiary receives nothing.

### Finding Description
`withdraw()` decrements escrow accounting and finalizes order state unconditionally after the `.call` returns without reverting: [4](#0-3) 

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
_orders[body.commitment][token] -= amount;
```

If `token` is an ERC20 whose `transfer` returns `false` on failure (rather than reverting) — e.g. paused/blacklist-style tokens, or any token following the "return bool" pattern without strict revert semantics — the `success` flag from the low-level `.call` will still be `true` because the call executed without an EVM-level revert; the returned `false` payload is discarded and never inspected. The same pattern recurs for the fee-token payout at lines 707-714, and for the `SweepDust` handler at lines 652-674.

Because `withdraw()` also sets `_filled[body.commitment] = beneficiary` at line 684 *before* the loop, and because the escrow debit at line 701 always executes once `success` is true, the settlement is treated as final and one-time regardless of whether the token actually moved.

### Impact Explanation
This is a direct fund-loss / false-settlement-acceptance bug: escrowed input tokens (and accrued transaction fees) can be silently zeroed out of `_orders` and the order marked filled/refunded (`_filled[commitment]` set, `EscrowReleased`/`EscrowRefunded` emitted) while the beneficiary receives no tokens. Because `_filled` is now set and the escrow balance is already debited, there is no remaining code path to retry or reclaim the funds — they are permanently stuck in the `IntentGatewayV2` contract, unrecoverable by the user, solver, or protocol. This satisfies the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories, since a message that Hyperbridge/the destination proved as delivered is locally accepted as fully and correctly settled when it was not.

### Likelihood Explanation
No privileged actor, malicious relayer, or compromised prover is required — this is purely a property of which ERC20 token is configured as an order's input/fee token on this deployment. Any token registered for use with the Tron `IntentGatewayV2` that follows the "return false instead of revert" ERC20 pattern will trigger this on every redemption/refund/sweep involving that token, deterministically and without any adversarial timing. The existing guard (`if (!success) revert TransferFailed();`) does not stop this path because `success` reflects call-level reversion, not the token's own reported outcome — exactly the class of failure the seed report describes.

### Recommendation
Replace the raw `.call` + `success`-only checks in `withdraw()` and `onAccept`'s `SweepDust` branch with OpenZeppelin's `SafeERC20.safeTransfer` (as already used correctly in the sibling `IntentsBase.sol` `_withdraw` implementation), which decodes and enforces the ERC20 return value (or absence thereof) in addition to the call-level success, and reverts the whole settlement if the token transfer did not actually succeed.

### Proof of Concept
1. Deploy (or use) an ERC20 token `T` on the Tron chain whose `transfer(address,uint256)` returns `false` instead of reverting when, e.g., the caller/recipient is blacklisted, paused, or another failure condition is hit — a real-world pattern documented for several production tokens.
2. Place an order on the Tron `IntentGatewayV2` with `T` as an input token, escrowing `amount` of `T`.
3. Trigger settlement so that `onAccept` (or `onGetResponse`) calls `withdraw(body, ...)` with `beneficiary` in a state where `T.transfer` returns `false` (e.g., beneficiary blacklisted on `T`) rather than reverting.
4. Observe: `token.call(...)` returns `success = true` (call didn't revert) at `evm/tron/contracts/apps/IntentGatewayV2.sol:697`; the `if (!success) revert TransferFailed();` check passes; `_orders[body.commitment][token] -= amount;` executes; `_filled[body.commitment] = beneficiary` was already set; `EscrowReleased`/`EscrowRefunded` fires.
5. Verify the beneficiary's `T` balance is unchanged while the contract's internal escrow accounting shows the funds as released — the tokens remain locked in the contract with no code path to recover them, and the order can never be re-settled.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-674)
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
        }
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
