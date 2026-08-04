### Title
Unchecked ERC20 return value in escrow `withdraw()`/`SweepDust` lets escrow accounting mark orders settled while token transfer silently fails - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron variant of `IntentGatewayV2` deviates from the rest of the codebase (which consistently uses OpenZeppelin's `SafeERC20.safeTransfer`/`safeTransferFrom`) by performing outbound escrow payouts with a raw low-level `.call()` and checking only that the external call did not revert — never validating the ABI-decoded boolean return value of `transfer()`. This is the exact bug class from the external report (unchecked ERC20 transfer return value), reproduced locally in the escrow settlement path rather than in HTLC commit/lock/redeem.

### Finding Description
In `withdraw()`, which is invoked from `onAccept()` for both `RedeemEscrow` and `RefundEscrow` message kinds (i.e. the path that finalizes cross-chain intent settlement and refunds), the contract does: [1](#0-0) 

and for transaction fees: [2](#0-1) 

Both use `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only check the low-level `success` flag from `.call`, never decoding/asserting the returned `bool` from the ERC20's `transfer()` function. The same unchecked pattern appears in the `SweepDust` handling inside `onAccept`: [3](#0-2) 

For any ERC20-like token that returns `false` on failure instead of reverting (a well-known non-standard-but-common behavior, distinct from tokens that omit a return value entirely like historic USDT, which this pattern does correctly tolerate), `success` will be `true` even though no tokens actually moved. Immediately after the unchecked transfer, the code irreversibly updates escrow state: [4](#0-3) [5](#0-4) 

`_filled[body.commitment]` is set to the beneficiary and `_orders[body.commitment][token]` is decremented/deleted regardless of whether the beneficiary actually received funds. This directly contradicts the rest of the same contract, which correctly uses `SafeERC20.safeTransferFrom` for inbound token collection in `placeOrder` (e.g. line 399, 453, 478), showing the outbound payout path was deliberately/mistakenly implemented with a weaker check.

### Impact Explanation
This breaks the "moves exactly once and only to the rightful beneficiary and amount" bridge-custody invariant. If a token used in escrow silently returns `false` on transfer failure (e.g., due to internal token policy, temporary pause, or non-standard implementation), the `IntentGatewayV2` contract will still mark the order `_filled` and decrement the escrow ledger — permanently orphaning the escrowed tokens inside the contract while the on-chain record falsely states the order has been redeemed or refunded. Because `_filled[commitment]` becomes non-zero, any subsequent legitimate resend of the withdrawal request (retry) will revert due to the `Filled()` check elsewhere in the contract, permanently locking the funds with no recovery path — a direct fund-loss condition reachable purely through normal cross-chain message processing (`onAccept` from `RedeemEscrow`/`RefundEscrow`), not requiring a malicious relayer, prover, or admin.

### Likelihood Explanation
Likelihood is moderate: it requires the deployed escrow token to be one that returns `false` rather than reverting on transfer failure (a real category of tokens, though not the most common one on EVM/Tron). No attacker action beyond normal cross-chain message flow is needed — the settlement path executes automatically once a valid `RedeemEscrow`/`RefundEscrow` message is accepted by the host, so this can be triggered unintentionally by any token whose balance/allowance conditions cause a `false` return (e.g. a token with a max-transfer-amount cap, denylist, or other soft-fail behavior).

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` branch of `onAccept()` with `SafeERC20.safeTransfer()`, consistent with the rest of the contract's inbound transfer handling (`safeTransferFrom`). `SafeERC20` correctly reverts both when the external call fails and when the call succeeds but returns `false`, ensuring `_filled`/`_orders` state is only updated after a provably successful transfer.

### Proof of Concept
1. Deploy (or use) an ERC20 token whose `transfer()` implementation returns `false` on failure instead of reverting (e.g., a token enforcing a denylist or transfer cap that a user/admin unintentionally trips for the escrow contract's beneficiary address).
2. A user places a cross-chain intent order via `placeOrder()`, escrowing this token; `_orders[commitment][token]` is credited correctly via `safeTransferFrom`.
3. The order later times out/is cancelled, or is filled and redeemed on the destination chain, causing a legitimate `RedeemEscrow`/`RefundEscrow` message to arrive and `onAccept()` to call `withdraw()`.
4. Inside `withdraw()`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `success = true` (external call itself doesn't revert) but the token internally returns `false` and does not move any balance.
5. `withdraw()` proceeds to set `_filled[commitment] = beneficiary` and decrement `_orders[commitment][token] -= amount` as if the payout succeeded.
6. The beneficiary never receives the tokens, and because `_filled[commitment]` is now set, any resubmission of the withdrawal request will revert with `Filled()`, permanently locking the escrowed tokens inside `IntentGatewayV2` with no path to recovery. [6](#0-5)

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
