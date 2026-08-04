### Title
Escrow withdrawal uses raw `.call` with `IERC20.transfer` selector and only checks call success, not the returned boolean — silent transfer failures cause fund loss for beneficiaries - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2` (Tron variant) escrows user funds in `placeOrder` using `safeTransferFrom`, but pays them back out in `withdraw()` and in the `SweepDust` branch of `onAccept()` using a raw low-level `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))`, checking only that the call itself did not revert (`success`). It never inspects/decodes the returned `bool`. This is the exact bug class from the external report (non-`safeTransfer` usage failing for tokens with non-standard/false-returning `transfer`), but here it manifests worse: instead of merely reverting for USDT-like tokens, the contract can treat a *logically failed* transfer (one that returns `false` without reverting) as successful, permanently decrementing/deleting the internal escrow accounting while the beneficiary never receives the funds.

### Finding Description
In `withdraw()`: [1](#0-0) 
tokens are paid out via:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
and fee token redemption uses the identical pattern: [2](#0-1) 

The same pattern is repeated in the dust-sweep path of `onAccept()`: [3](#0-2) 

Immediately after each of these calls, the contract mutates authoritative escrow state — decrementing `_orders[body.commitment][token]` — treating the payout as final and irreversible: [4](#0-3) 

Because only `success` (i.e., "the call did not revert") is checked, any ERC20 token whose `transfer` function returns `false` on failure instead of reverting (a legal, ERC20-spec-compliant behavior) will cause `success == true` even though the beneficiary's balance was not credited. Contrast this with the rest of the codebase, which imports and consistently uses `SafeERC20.safeTransfer`/`safeTransferFrom` — those revert on a `false` return, but this raw `.call` path does not decode `returndata` at all, so a `false` return is silently accepted as success.

### Impact Explanation
This directly maps to the bounty's "stealing or loss of funds" category. When `withdraw()` is invoked for a `RedeemEscrow`/`RefundEscrow` request or via `onGetResponse`, the escrowed amount is marked as paid (`_orders[...] -= amount`, `_filled[commitment] = beneficiary`) even if the token transfer to the beneficiary silently failed. The user's escrowed principal remains stuck in the contract with no accounting reference to reclaim it (the commitment is already marked filled/refunded), resulting in permanent fund loss for the legitimate order owner or solver. The same applies to protocol fee redemption and to dust sweeps dispatched from the hyperbridge governance path.

### Likelihood Explanation
Likelihood depends on whether escrowed tokens with false-returning-on-failure `transfer` semantics are configured for this deployment (e.g., certain older/non-standard ERC20 tokens, or tokens that can return `false` under specific conditions like paused/blacklisted states without reverting — some TRC20/ERC20 tokens on Tron behave this way). No privileged actor, relayer, or governance action is required to trigger the loss — it occurs automatically for any legitimate cross-chain fill/refund/dust-sweep once such a token is in the escrowed asset set, making this a directly reachable, unprivileged-triggered path once such a token is configured as an order input or fee token.

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` payout paths in `withdraw()`, the fee redemption block, and the `SweepDust` handling in `onAccept()` with `SafeERC20.safeTransfer`, consistent with the `safeTransferFrom` already used for escrow deposits in `placeOrder`. This ensures both revert-on-false and no-return-value tokens are handled correctly and that a failed transfer can never be recorded as a completed withdrawal.

### Proof of Concept
1. Configure (or have selected by an order) an ERC20/TRC20 token as an order input/output whose `transfer` implementation returns `false` on failure instead of reverting (e.g., due to an internal pause/blacklist check), while still returning `true` under normal conditions so it passes initial checks.
2. A user places an order with this token as input via `placeOrder`, escrowing funds successfully (`safeTransferFrom` succeeds).
3. Before the fill/redeem/refund flow, the token enters the failure condition for the beneficiary address (e.g., beneficiary gets blacklisted, or a transient state prevents transfer) so that `transfer` returns `false` without reverting.
4. `onAccept`/`onGetResponse` triggers `withdraw()`. The raw `.call` succeeds (`success == true`) despite `transfer` returning `false` and no tokens moving.
5. `_orders[commitment][token] -= amount` executes, `_filled[commitment] = beneficiary`, and `EscrowReleased`/`EscrowRefunded` is emitted — the order is now permanently marked settled, but the beneficiary never received the funds and has no further recourse to claim them.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L701-705)
```text
            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L708-713)
```text
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
```
