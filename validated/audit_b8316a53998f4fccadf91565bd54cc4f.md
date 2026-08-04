### Title
Unchecked ERC20 return value in escrow settlement lets malicious/non-standard input tokens cause silent escrow loss without reverting - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` diverges from the EVM version's `SafeERC20`-based transfers. In `withdraw()` and in the `SweepDust` handler inside the request-processing dispatcher, token payouts are performed with raw low-level `.call()` and only the call-success flag is checked — the actual ABI-encoded boolean return value of `transfer()` is never decoded or validated.

### Finding Description
In `withdraw()`, escrowed tokens and transaction fees are released like this: [1](#0-0) 

and the `SweepDust` request handler does the same: [2](#0-1) 

`(bool success,) = token.call(...)` only reports whether the call reverted; it does **not** decode the returned `bool`. Per the ERC20 standard, a `transfer`/`transferFrom` call can execute successfully (no revert) yet return `false` to indicate the operation failed (e.g., some ERC20 implementations return `false` on transfers to blacklisted/paused/zero-balance targets instead of reverting). Because `success` remains `true` in that case, the code proceeds as if the transfer succeeded.

Compare this to the canonical EVM `IntentGatewayV2.sol`, which consistently uses `safeTransferFrom`/`safeTransfer` from OpenZeppelin's `SafeERC20`, which decodes and enforces the boolean return value: [3](#0-2) 
`SafeERC20` is imported and used elsewhere in the same Tron file for the escrow *deposit* path (`safeTransferFrom`), but the *payout* path (`withdraw`, `SweepDust`) intentionally bypasses it and reverts to unchecked raw calls, creating an inconsistency between deposit-side and payout-side transfer safety.

Critically, `withdraw()` unconditionally advances protocol state regardless of whether the underlying token transfer actually moved value: [4](#0-3) 
`_filled[body.commitment]` is set and `_orders[body.commitment][token]` is decremented even when the wrapped `transfer` returned `false` without reverting. Since `order.inputs`/`order.outputs` tokens are arbitrary attacker/user-supplied ERC20 addresses (escrowed via `safeTransferFrom` during order creation), any non-standard token that returns `false` instead of reverting on failure will cause the gateway to mark the order as filled/refunded and clear escrow accounting while the tokens remain stuck in the contract — a silent settlement without actual fund movement.

### Impact Explanation
This breaks the invariant that "bridged assets/order escrow must move exactly once and only to the rightful beneficiary and amount." When payout fails silently:
- Funds become permanently locked in the `IntentGatewayV2` contract because `_orders[commitment][token]` is decremented to zero, so a retry via `UnknownOrder()` guard is no longer possible.
- The order is marked `Filled`/`Refunded` in state and events are emitted, misrepresenting that settlement occurred, even though the beneficiary received nothing — a false-settlement/state-acceptance condition analogous to the reported "unchecked transfer return value" bug class.

### Likelihood Explanation
The path is reachable purely by escrowing a legitimate-looking but non-standard ERC20 as an order input token (permitted by design, since `IntentGatewayV2` supports arbitrary ERC20 tokens) — no relayer, prover, or admin compromise is needed. The trigger condition (an ERC20 returning `false` instead of reverting) is a known, documented category of real-world token non-compliance, which is exactly the scenario the external report and OpenZeppelin's `SafeERC20` guidance are designed to guard against; the fact that the deposit side of this very file already uses `SafeERC20` for that reason shows the risk is acknowledged elsewhere in the codebase but not applied consistently to the payout side.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` handler with `SafeERC20.safeTransfer`, consistent with the rest of the file's `safeTransferFrom` usage and with the canonical EVM `IntentGatewayV2.sol`. If Tron TRC20 return-data quirks motivated the raw-call approach, at minimum decode and enforce the boolean return value when returndata is non-empty, matching `SafeERC20`'s tolerant-but-verifying behavior, before mutating `_orders`/`_filled` state.

### Proof of Concept
1. Deploy a "compliant-looking" ERC20 whose `transfer()` returns `false` (without reverting) when the recipient is on an internal blocklist or when an internal condition fails, and `true` otherwise (a legitimate non-malicious pattern seen in some real tokens).
2. Create and escrow an order in `IntentGatewayV2` (Tron) using this token as an input/output token via the normal order-creation flow (`safeTransferFrom` succeeds normally).
3. Trigger settlement so that `withdraw()` is invoked with the beneficiary address in a state where the token's `transfer()` returns `false` (e.g., beneficiary temporarily blocklisted in the token, or an internal cap reached) but the call itself does not revert.
4. Observe: `success` is `true`, so `TransferFailed()` is not raised; `_orders[commitment][token]` is decremented to 0 and `_filled[commitment]` is set, while `beneficiary`'s token balance is unchanged and the tokens remain stranded in `IntentGatewayV2`. `UnknownOrder()` now guards against any further retry, permanently locking the escrowed funds.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L38-39)
```text
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-713)
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
```
