## Title
Silent ERC20 transfer failures are treated as success in `withdraw()`/`SweepDust`, causing permanent loss of escrowed funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

## Summary

`IntentGatewayV2.sol` (Tron variant) settles escrow withdrawals and dust sweeps using raw low-level `.call()` to invoke `IERC20.transfer`, checking only that the call did not revert (`success`) while completely ignoring the ABI-encoded boolean return value. Any ERC20 whose `transfer()` returns `false` on failure instead of reverting will be treated as a fully successful payout: the contract decrements its internal escrow accounting, marks the order as filled/refunded, and emits the corresponding event — even though no tokens actually left the contract. Because `_filled[body.commitment]` is set unconditionally and the escrowed balance is destroyed, there is no remaining path to retry or reclaim the funds; they become permanently stuck in the contract while the on-chain state says the order was settled.

## Finding Description

The `withdraw()` function is the internal settlement routine invoked from `onAccept()` for `RedeemEscrow`/`RefundEscrow` requests, and from `onGetResponse()` for cancellation-verification callbacks: [1](#0-0) 

For every escrowed token it does:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
_orders[body.commitment][token] -= amount;
```
and before the loop it unconditionally does:
```solidity
_filled[body.commitment] = beneficiary;
``` [2](#0-1) 

`success` here only reflects whether the external call reverted — not whether the token's `transfer()` actually returned `true`. Solidity's low-level `.call` returns `success = true` any time the callee executes without reverting, regardless of the ABI-decoded return payload. Non-standard but real-world ERC20 tokens exist that return `false` on failed transfers instead of reverting (this exact class of token behavior is the seed report's "missing/non-standard return value" bug class, just the inverse direction: a token that *does* return a value, but a falsy one, on failure). For such a token, `withdraw()` will:
1. Mark the commitment as filled (`_filled[body.commitment] = beneficiary`) — permanently blocking any other settlement/refund attempt for that commitment.
2. Decrement `_orders[body.commitment][token]` as if the funds were paid out.
3. Emit `EscrowReleased`/`EscrowRefunded`.
4. Never actually move the tokens — they remain locked in the `IntentGatewayV2` contract with no accounting path left to recover them.

The same unchecked-boolean pattern is repeated for the `SweepDust` admin-triggered path: [3](#0-2) 

Notably, the file imports and declares `using SafeERC20 for IERC20;` (`evm/tron/contracts/apps/IntentGatewayV2.sol:39,56`), and the sibling logic in `IntentsBase.sol` (used by the standard EVM `IntentGatewayV2.sol`) correctly uses `IERC20(token).safeTransfer(beneficiary, amount);`, which properly decodes and checks the returned boolean via OpenZeppelin's `SafeERC20`: [4](#0-3) 

This confirms the Tron variant is a regression that reimplements settlement without the safety the rest of the codebase relies on, despite having `SafeERC20` available.

## Impact Explanation

This directly causes loss of funds for order fillers/solvers and cancelling users: escrowed input tokens or refund amounts are irreversibly locked in the `IntentGatewayV2` contract while the protocol's own bookkeeping (`_orders`, `_filled`) asserts the order was already settled. Because `_filled[body.commitment]` is set before the transfer outcome is verified, there is no fallback path — `onAccept`/`onGetResponse` cannot be re-triggered for the same commitment to retry the payout, and the escrowed balance has already been zeroed out. This matches the bounty's "stealing or loss of funds" and "false state acceptance" categories: the contract accepts a false "transfer succeeded" state and finalizes settlement on it.

## Likelihood Explanation

Triggering this does not require a malicious relayer, prover, or admin — it only requires that one of the tokens used as escrow input (chosen when the order is created on the source chain) be a token implementation that returns `false` rather than reverting on transfer failure (a documented, real-world ERC20 non-compliance pattern, the same class explicitly called out as in-scope in the seed report). Any condition that makes the transfer logically fail without reverting — e.g., a blacklist/pause mechanism common in stablecoins, or any non-standard token — silently locks funds while the protocol proceeds as if settlement succeeded.

## Recommendation

Use OpenZeppelin's `SafeERC20.safeTransfer` (already imported and used elsewhere in the file via `using SafeERC20 for IERC20`) instead of raw `.call` + `success`-only checks in `withdraw()` and the `SweepDust` branch of `onAccept()`. `SafeERC20` correctly decodes the return data when present and requires it to be `true`, while tolerating tokens that return no data at all — covering both the "missing return value" and "returns false silently" failure modes safely. At minimum, decode and require the returned boolean when return data is present:
```solidity
if (!success || (data.length > 0 && !abi.decode(data, (bool)))) revert TransferFailed();
```
Additionally, avoid writing `_filled[body.commitment] = beneficiary` before all transfers in the loop are confirmed successful, so a failed transfer cannot finalize an order's state.

## Proof of Concept

1. An order is created on the source chain with an input token `T` that implements `transfer()` to return `false` (not revert) when the transfer cannot be completed (e.g., recipient is blacklisted, contract is paused, or any non-standard failure semantics).
2. The order is filled/cancelled on the destination chain, and a `RedeemEscrow`/`RefundEscrow` request is relayed back to `onAccept()` on the source chain's `IntentGatewayV2`.
3. `withdraw()` executes: `_filled[body.commitment] = beneficiary;` is set immediately.
4. The loop calls `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))`. Token `T`'s `transfer()` runs to completion and returns `false` (no revert), so `success == true`.
5. `_orders[body.commitment][token] -= amount;` zeroes out the escrow record; `EscrowReleased`/`EscrowRefunded` is emitted.
6. `beneficiary`'s token balance never increased — the `amount` of token `T` remains stuck inside `IntentGatewayV2`, and because `_filled[body.commitment]` is already set and `_orders` accounting is already zeroed, there is no code path left to retry the payout or recover the funds. [1](#0-0)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-673)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
