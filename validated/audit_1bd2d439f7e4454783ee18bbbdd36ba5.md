## Analysis

The external report's core defect is **using raw `transferFrom` without decoding/verifying the boolean return value**, which lets non-standard ERC20 tokens (that return `false` on failure instead of reverting) pass silently while state assumes the transfer succeeded, causing stuck/lost funds.

Hyperbridge's `IntentGatewayV2` (Tron/EVM apps) has the exact same root defect on the **escrow payout path** in `withdraw()`, which is the intent-settlement custody logic responsible for releasing escrowed order funds to the beneficiary.

### Title
Unchecked ERC20 return value in `IntentGatewayV2.withdraw()` silently marks escrow as paid while tokens remain locked - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`withdraw()` releases escrowed order tokens to a beneficiary using a raw low-level `.call` with the `IERC20.transfer` selector, checking only that the *call itself* did not revert — never decoding/validating the returned boolean. [1](#0-0) 

### Finding Description
`withdraw()` is the internal settlement routine invoked both on successful fill redemption and on refund (via `onGetResponse`), and it permanently updates escrow accounting (`_orders[commitment][token] -= amount`) and marks the order filled (`_filled[body.commitment] = beneficiary`) unconditionally as long as the low-level call does not revert: [2](#0-1) 

For a non-standard ERC20 token (there are many such tokens deployed across EVM chains — tokens that return `false` on failed transfer instead of reverting, or tokens with no return value at all that the `success` check happens to interpret loosely), `success` will be `true` (the call executed without reverting) even though the actual balance transfer failed or returned `false`. Because the code never inspects `returndata` to confirm it decodes to `true`, the function proceeds to zero out `_orders[body.commitment][token]` and mark `_filled[body.commitment]`, exactly mirroring the reported bug class where `transferFrom()`'s boolean return is never checked/decoded via `safeTransferFrom`/`safeTransfer`.

The fee-token payout in the same function has the identical pattern: [3](#0-2) 

### Impact Explanation
Once `_orders[commitment][token]` is decremented to zero and `_filled[commitment]` is set, the escrow for that order is considered fully and correctly settled by the contract's own bookkeeping. If the actual token transfer silently failed:
- The tokens remain permanently locked in the `IntentGatewayV2` contract — there is no code path to re-attempt the transfer or reclaim them, because any retry would hit `UnknownOrder()` (`_orders[...] == 0`) and the refund path is blocked once `_filled` is set.
- The beneficiary/filler never receives the escrowed funds despite the contract emitting `EscrowReleased`/`EscrowRefunded`, i.e., false settlement state is recorded.

This is a direct loss-of-funds condition on bridge custody / intent settlement, matching the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories — the on-chain state falsely asserts the beneficiary was paid.

### Likelihood Explanation
This is triggerable with any single non-standard ERC20 token configured as the order/fee token — no relayer, prover, or admin compromise is needed. Any user creating or filling an order that involves such a token (or a fee token with imperfect ERC20 compliance) hits this path through the normal, permissionless intent fill/refund flow. Given hundreds of live ERC20 tokens exhibit this non-standard behavior (as the original report itself notes for USDT/BNB-class tokens), the likelihood on a live deployment accepting arbitrary/many tokens is non-trivial.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` (both the token payout loop and the fee-token payout) with OpenZeppelin's `SafeERC20.safeTransfer`, which decodes and enforces the boolean return value (or the absence of one) correctly, and reverts the entire settlement (leaving `_orders`/`_filled` untouched) if the transfer truly fails.

### Proof of Concept
1. Deploy an ERC20 token whose `transfer()` returns `false` on failure instead of reverting (a legal ERC20 implementation, and structurally similar to tokens like older USDT-style tokens on some chains).
2. Configure/escrow an order in `IntentGatewayV2` using that token as `body.tokens[i].token`.
3. Arrange for the transfer to fail post-approval-check (e.g., token-specific blacklist/pause flag causing `transfer` to return `false` rather than revert) at the moment `withdraw()` executes.
4. `token.call(...)` succeeds (`success == true`) because the call did not revert; the returned `false` is never inspected.
5. `_orders[body.commitment][token] -= amount` proceeds, `_filled[body.commitment] = beneficiary` is set, and `EscrowReleased`/`EscrowRefunded` fires — but the beneficiary's balance never increased.
6. The escrowed tokens remain stranded in `IntentGatewayV2` permanently, with no remaining code path (`UnknownOrder` blocks retry) to recover them. [2](#0-1)

### Citations

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
