### Title
Unsafe raw ERC20 transfer with unchecked return data allows escrow accounting to burn user/solver funds without delivering tokens - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron variant of `IntentGatewayV2` performs all outbound ERC20 payouts (escrow release, refunds, protocol dust sweeps, and fee redemption) using a raw low-level `.call()` to the token's `transfer()` selector, checking only that the external call did not revert (`success`) but never decoding/validating the returned boolean payload. This is the exact bug class described in the external report (TRST-M-4): tokens that signal failure by returning `false` instead of reverting will be silently treated as successful transfers.

### Finding Description
In `withdraw()`, escrowed input tokens, fee tokens, and (in `onAccept`'s `SweepDust` branch) protocol dust are all moved with the same pattern: [1](#0-0) [2](#0-1) [3](#0-2) 

Each of these calls `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only checks `success` — the boolean returned by the low-level `call` itself (i.e., "did the callee not revert") — never inspecting the ABI-decoded return data from the token's `transfer()` function. For any ERC20 that returns `false` on failure rather than reverting (a widely-used, historically common pattern — the same class of token cited in the original GMX/Lyra report for BNB/USDT), the `call` succeeds (`success == true`) even though no tokens were moved, because the low-level call only fails when the callee reverts or is not a contract.

Crucially, this broken check gates state that has already been irreversibly committed: in `withdraw()`, `_orders[body.commitment][token] -= amount;` executes unconditionally after the (bogus) success check, and `_filled[body.commitment] = beneficiary;` is set at the top of the function before any transfer occurs. So even when the token silently fails, the escrow accounting is finalized as if the payout succeeded — the commitment is marked filled/refunded and the escrow balance is decremented to zero, permanently and irreversibly.

This differs materially from the sibling EVM contract `evm/src/apps/intentsv2/IntentsBase.sol`, whose `_withdraw()` correctly uses OpenZeppelin's `safeTransfer`: [4](#0-3) 

The Tron contract does not use `SafeERC20` for any of these payout paths despite importing/using it in other places (`safeTransferFrom` for inbound escrow), showing the outbound side was left with the unsafe raw-call pattern.

### Impact Explanation
This falls squarely within "stealing or loss of funds" and "false state acceptance" in the bounty scope. When a non-reverting-on-failure ERC20 is used as an order's input or fee token:
- A solver's legitimate `RedeemEscrow` settlement (`withdraw(body, false)`) can finalize (`_filled` set, `_orders` zeroed, `EscrowReleased` emitted) while the solver never actually receives the escrowed tokens — permanent fund loss with no recovery path, since the commitment is now marked filled and cannot be retried.
- Equally, a user's refund (`RefundEscrow` / `onGetResponse` cancellation path) can be marked complete and the escrow deleted without the user ever receiving their tokens back.
- Protocol `SweepDust` operations can mark dust as swept (`DustSwept` emitted) while tokens remain stuck in the contract, permanently locking accounted funds.

The existing guards (`if (_orders[body.commitment][token] == 0) revert UnknownOrder();` and `if (!success) revert TransferFailed();`) do not stop this path because `success` is true for a `false`-returning token — there is no mechanism anywhere in this file checking the decoded return data or before/after token balances (unlike `IntentGatewayV2.sol`'s predispatch flow, which uses balance snapshots to measure actual transfer effects).

### Likelihood Explanation
This requires no privileged actor, malicious relayer, or governance action — it is triggered purely by the choice of input/fee token for an order combined with normal contract usage (placing, filling, cancelling, or the host relaying a legitimate `RedeemEscrow`/`RefundEscrow`/`SweepDust` message). Any token onboarded to this Tron deployment that returns `false` instead of reverting on failed transfer (e.g., due to insufficient contract balance, blacklist, paused state, or similar conditions supported by many real-world tokens) will trigger this path deterministically, not just as an edge case.

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` payout patterns in `evm/tron/contracts/apps/IntentGatewayV2.sol` (`withdraw()` for escrow tokens and transaction fees, and the `SweepDust` branch of `onAccept()`) with OpenZeppelin's `SafeERC20.safeTransfer`, matching the pattern already used in `evm/src/apps/intentsv2/IntentsBase.sol`. This ensures both call-revert and false-return failures cause the transaction to revert before escrow/`_filled` state is mutated.

### Proof of Concept
1. Deploy an ERC20 token whose `transfer()` returns `false` on failure instead of reverting (e.g., due to a paused/blacklist flag), and place an order on `IntentGatewayV2` (Tron) using this token as an input asset, escrowing funds via `placeOrder`.
2. Have the solver fill the order cross-chain; the source chain host delivers the `RedeemEscrow` message and calls `onAccept()` → `withdraw(body, false)`.
3. Trigger the failure condition on the token (e.g., pause transfers to the solver's address) so `transfer()` returns `false` without reverting.
4. Observe that `token.call(...)` returns `success == true` (since the call itself doesn't revert), so `if (!success) revert TransferFailed();` does not trigger.
5. `_orders[body.commitment][token] -= amount;` executes, `_filled[body.commitment] = beneficiary;` was already set, and `EscrowReleased` is emitted — yet the solver's token balance never increased. The escrowed funds are now unrecoverable: the commitment is marked filled and the escrow debited to zero, but the underlying token transfer silently failed.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-699)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-416)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
```
