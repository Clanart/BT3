## Analysis

The Curve report's core issue: code was copied from one chain deployment to another without adapting it to that chain's actual token/transfer semantics, silently breaking the intended cash-flow guarantee. The direct local analog is in the Tron port of the Intent Gateway.

The canonical EVM `IntentsBase._withdraw()` uses `SafeERC20.safeTransfer` for every escrow release/refund/dust-sweep, which reverts the whole transaction if the underlying token's `transfer` call fails to return `true` (or returns no data in a spec-incompatible way): [1](#0-0) 

But `evm/tron/contracts/apps/IntentGatewayV2.sol` — the Tron fork of the same contract — replaced `safeTransfer` with a raw low-level `.call` that only checks whether the call *reverted*, never the ABI-decoded boolean return value: [2](#0-1) 

The same unchecked pattern is repeated for the transaction-fee payout inside the same function, and for the `SweepDust` governance action in `onAccept`: [3](#0-2) [4](#0-3) 

### Title
Unchecked TRC20 return value in `withdraw()` lets escrow accounting finalize while funds never reach the beneficiary - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`withdraw()` (invoked from `onAccept` for both `RedeemEscrow` and `RefundEscrow`) transfers escrowed tokens via `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only checks that the call did not revert, not that the token's `transfer` actually returned `true`. On Tron, TRC20 tokens frequently deviate from strict ERC20 return-value semantics (returning `false` or no data instead of reverting on failure, e.g. blacklisted/frozen recipients or insufficient-balance edge cases in non-standard token implementations). This mirrors the Curve report's root cause: code was ported to a different chain without adapting it to that chain's real execution semantics, breaking the intended contract behavior.

### Finding Description
`_orders[body.commitment][token] -= amount` and `_filled[body.commitment] = beneficiary` are both mutated unconditionally as soon as `token.call(...)` does not revert: [5](#0-4) 

If the token contract's `transfer` returns `false` instead of reverting (a documented, common non-standard pattern that `SafeERC20` exists specifically to guard against, and which the sibling mainline `IntentsBase._withdraw` correctly guards against with `safeTransfer`), the low-level `.call` still reports `success == true` because the EVM/TVM call itself completed normally. The code therefore:
1. Decrements the internal escrow ledger (`_orders`) as if tokens left the contract,
2. Marks the order `_filled[commitment] = beneficiary` (a one-time, non-retriable settlement flag),
3. Emits `EscrowReleased`/`EscrowRefunded`,

while the tokens remain in the contract and the beneficiary receives nothing.

### Impact Explanation
Because `_filled` is a one-time settlement marker checked by `fillOrder`/`cancelOrder` (`if (_filled[commitment] != address(0)) revert Filled();`), there is no retry path once this state is set. The escrowed tokens are permanently stranded in the contract with no accounting path back to any user or solver — a direct, deterministic loss/lock of bridged funds triggered purely by ordinary token behavior on the Tron deployment, with no malicious relayer, prover, or governance actor required.

### Likelihood Explanation
This path is reached on every `RedeemEscrow`/`RefundEscrow` settlement and every `SweepDust` governance dispatch that involves a token whose `transfer` can return `false` without reverting. Since Tron's TRC20 ecosystem includes tokens with exactly this behavior (freeze/blacklist mechanics, non-standard implementations), and the contract explicitly imports `SafeERC20` yet does not use it for these transfers, unlike its EVM sibling, this is a straightforward and easily-triggered logic gap in a production contract, not a testnet-only or dependency issue.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust` branch of `onAccept` with `SafeERC20.safeTransfer`, matching the mainline `IntentsBase._withdraw` implementation, so that any non-`true`/non-standard return value reverts the whole settlement instead of silently finalizing it.

### Proof of Concept
1. Deploy/interact with a TRC20 token on Tron whose `transfer` implementation returns `false` (rather than reverting) when the recipient is frozen/blacklisted or under some other failure condition (a documented pattern for several Tron stablecoin-style tokens).
2. Place a cross-chain order whose input/output token is this token; have a solver fill it, or let it expire and get cancelled from the destination chain.
3. When Hyperbridge delivers the `RedeemEscrow`/`RefundEscrow` message, `onAccept` → `withdraw()` calls `token.call(...)`. The call does not revert but returns `false`.
4. `_orders[commitment][token]` is decremented and `_filled[commitment]` is set; `EscrowReleased`/`EscrowRefunded` is emitted — yet the beneficiary's token balance is unchanged.
5. Any subsequent attempt to fill or cancel the order reverts with `Filled()`; the escrowed tokens are now permanently locked in the contract with no beneficiary ever receiving them.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
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
