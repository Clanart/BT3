### Title
Unhandled ERC20 `transfer` return value in Tron `IntentGatewayV2.withdraw` / `onAccept` (SweepDust) allows escrow to be marked settled while tokens never move - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` imports and declares `using SafeERC20 for IERC20` at the top of the file, but its escrow-redemption path (`withdraw`) and dust-sweep path (`onAccept`, `RequestKind.SweepDust`) bypass `SafeERC20.safeTransfer` and instead issue a raw low-level `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))`, checking only that the call did not revert (`success`) and never decoding/validating the returned boolean. This is the exact "unhandled return value of a token operation" bug class from the external report (there for `approve`, here for `transfer`), applied to the fund-custody exit path of the intent-settlement system.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, the `withdraw` function — invoked from both the escrow-redeem/refund flow (`onAccept` with `RequestKind.RedeemEscrow`/`RefundEscrow`) and the GET-response refund path (`onGetResponse`) — performs token payout like this: [1](#0-0) 

and the `SweepDust` handler in `onAccept` does the same: [2](#0-1) 

as does the fee redemption at the end of `withdraw`: [3](#0-2) 

In every one of these calls, only the outer `success` boolean of the low-level `.call` is checked — i.e., whether the call reverted. The ABI-encoded return data of `transfer` (the `bool` success flag defined by ERC20) is discarded (`(bool success,) = ...`). Any ERC20/TRC20 implementation that returns `false` on a failed transfer instead of reverting (a pattern explicitly permitted by the ERC20 standard and common on Tron TRC20 tokens) will make `success == true` at the low level while the actual balance transfer silently fails.

Despite the contract importing `SafeERC20` and declaring `using SafeERC20 for IERC20;` at the top of the file, `withdraw` and the `SweepDust` branch of `onAccept` do not use `safeTransfer`, unlike the equivalent EVM (non-Tron) `IntentsBase.sol`, which correctly uses `IERC20(token).safeTransfer(beneficiary, amount)` for the same operation (see `evm/src/apps/intentsv2/IntentsBase.sol:404-408`). This is precisely the pattern flagged in the external report: `approve`/`transfer` calls whose non-reverting `false` return is never inspected, even though `SafeERC20` is already present in the codebase and used elsewhere.

### Impact Explanation
Because `_orders[body.commitment][token]` is decremented and `EscrowReleased`/`EscrowRefunded`/`DustSwept` events are emitted unconditionally right after the unchecked `.call`, a failing (return-false) token transfer permanently corrupts the escrow accounting: the protocol's bookkeeping records the beneficiary as paid, but the beneficiary never receives the tokens. The escrowed input tokens remain trapped in the `IntentGatewayV2` contract with no remaining accounting entry to reclaim them (the `_orders` slot has already been zeroed/decremented and `_filled` marks the commitment as settled), resulting in a permanent loss of funds for solvers/users on cross-chain intent settlement and orphaned dust for the protocol.

### Likelihood Explanation
This path is reachable by the ordinary, permissionless cross-chain settlement flow: any RedeemEscrow/RefundEscrow message accepted by the host (or a GET-response refund) drives `withdraw` for whatever token was specified as an order's input/output asset. No malicious relayer, prover, or admin action is required — an intent creator or integrator simply needs to use (or the system needs to be deployed on Tron with) a TRC20 token whose `transfer` implementation returns `false` on failure rather than reverting, which is standard-compliant behavior and common among TRC20/ERC20 tokens circulating on Tron.

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` occurrences in `withdraw` and the `SweepDust` branch of `onAccept` with `IERC20(token).safeTransfer(beneficiary, amount)` (and `safeTransfer` for the fee-token payout), consistent with `SafeERC20`'s usage elsewhere in the codebase (e.g. `IntentsBase.sol`). This ensures a `false` return value from `transfer` reverts the whole settlement, preventing escrow state from being finalized without the corresponding token movement actually succeeding.

### Proof of Concept
1. Deploy `IntentGatewayV2` (Tron variant) with an input/output token `T` whose `transfer` implementation returns `false` on failure instead of reverting (e.g., insufficient balance triggers a `return false;` rather than a revert) — standard-permitted ERC20/TRC20 behavior.
2. Create and fill/settle an order whose escrowed asset is `T`, then trigger `RedeemEscrow` via `onAccept` so `withdraw` is called.
3. Arrange for `T.transfer(beneficiary, amount)` to internally fail and return `false` (e.g., the gateway's balance of `T` is depleted through a race/reentrant path, or the token has transfer restrictions on the beneficiary) while the low-level `.call` itself does not revert.
4. Observe: `success` from `token.call(...)` is `true` (no revert), so execution proceeds past `if (!success) revert TransferFailed();`. `_orders[body.commitment][token]` is decremented to zero and `EscrowReleased`/`EscrowRefunded` is emitted, but `beneficiary`'s `T` balance never increased — the tokens remain stuck in `IntentGatewayV2` with no remaining accounting path to recover them.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-701)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
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
