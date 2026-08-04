## Analysis

The external report's core issue — failing to check the boolean return value of `transfer`/`transferFrom` on non-compliant ERC20 tokens — has a direct, exploitable analog in the Tron variant of `IntentGatewayV2`.

### Title
Unchecked ERC20 `transfer` return value in escrow withdrawal permanently burns user funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron `IntentGatewayV2.withdraw()` function (and the `SweepDust` branch of `onAccept`) releases escrowed tokens using a raw low-level `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only checks that the *call itself* did not revert (`success`), never decoding/verifying the ERC20 boolean return value.

### Finding Description
In `withdraw()`, once a `RedeemEscrow`/`RefundEscrow` request or a GET-response timeout confirms non-fill, the beneficiary is finalized and escrow accounting is decremented unconditionally once `success` is true: [1](#0-0) 

The same unchecked pattern is used for the `TRANSACTION_FEES` payout in the same function: [2](#0-1) 

and for `SweepDust`: [3](#0-2) 

Per the EIP-20 spec, non-compliant tokens (this file targets the Tron/TRC20 environment, where non-standard token semantics are common) may return `false` from `transfer` instead of reverting when a transfer cannot be completed (e.g., blacklist, paused token, insufficient allowance edge cases in custom implementations, fee/limit logic). A low-level `.call` to such a token succeeds (`success == true`) as long as the token contract itself doesn't revert, even though no value moved. The code treats `success` as proof of a completed transfer.

Contrast this with the same file's inbound/escrow-funding path and with the mainline `evm/src/apps/IntentGatewayV2.sol`, which consistently uses OpenZeppelin's `SafeERC20.safeTransferFrom`/`safeTransfer` (which decodes and asserts the return value) for token movement: [4](#0-3) 
The withdrawal/payout path breaks this pattern by reverting to a manually-checked low-level `.call` instead of `safeTransfer`, even though `SafeERC20` is imported and used elsewhere in the same contract: [5](#0-4) 

### Impact Explanation
`withdraw()` unconditionally sets `_filled[body.commitment] = beneficiary` before attempting any transfers, and then decrements `_orders[body.commitment][token] -= amount` right after the unchecked `.call` — regardless of whether the token contract actually moved funds: [6](#0-5) 

If the escrowed token returns `false` on `transfer` (silent failure) rather than reverting, the gateway:
- marks the order as filled/settled (one-time; no retry path — `_filled` gates re-entry to this flow),
- decrements internal escrow accounting as if funds were paid out,
- but never actually delivers the tokens to the beneficiary.

The tokens remain stuck in the `IntentGatewayV2` contract, permanently unrecoverable by the intended beneficiary (user or solver), because the commitment is already marked filled and the escrow balance already zeroed. This is a direct, unauthorized loss of user/solver funds in a production bridge/intent-settlement contract, matching the bounty's "stealing or loss of funds" and "false state acceptance" categories — settlement state is falsely marked complete even though value transfer failed.

### Likelihood Explanation
This path executes on every legitimate escrow redemption/refund on Tron for any token whose `transfer` implementation can return `false` instead of reverting under some condition (paused, blacklisted, insufficient internal balance edge case, custom fee-token semantics, etc.) — no malicious relayer, prover, or governance actor is required. Any token registered for use with the Tron `IntentGatewayV2` that exhibits this (widely known, non-compliant-by-spec) behavior triggers permanent fund loss on ordinary use.

### Recommendation
Replace the manual `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `withdraw()` and the `SweepDust` branch of `onAccept` with `IERC20(token).safeTransfer(beneficiary, amount)` (already imported via `SafeERC20`), matching the pattern used elsewhere in this same contract for inbound transfers. This ensures the boolean return value (when present) is checked and reverts on failure, rather than silently finalizing state.

### Proof of Concept
1. Register/escrow a TRC20/ERC20-like token on Tron `IntentGatewayV2` whose `transfer` function returns `false` (without reverting) once some internal condition is hit (e.g., recipient blacklisted, or a custom guard).
2. User places an order, escrowing that token; a fill/refund path is triggered, dispatching `RedeemEscrow`/`RefundEscrow` to `onAccept` → `withdraw()`.
3. Inside `withdraw()`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `(true, <false-encoded-return>)` because the call did not revert — `success` is `true`.
4. `_filled[body.commitment]` is already set, `_orders[body.commitment][token] -= amount` executes, and `EscrowReleased`/`EscrowRefunded` is emitted — yet `beneficiary`'s token balance never increased.
5. The escrowed tokens remain locked in the `IntentGatewayV2` contract with no further code path to release them, since the commitment is marked filled and its escrow balance is zeroed.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L38-56)
```text
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";
import {ICallDispatcher, Call} from "../../../src/interfaces/ICallDispatcher.sol";


/**
 * @title IntentGatewayV2
 * @author Polytope Labs (hello@polytope.technology)
 *
 * Implements the IntentGatewayV2 contract for Tron
 *
 * @dev The IntentGateway allows for the creation and fulfillment of same-chain & cross-chain orders.
 */
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L391-400)
```text

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
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
