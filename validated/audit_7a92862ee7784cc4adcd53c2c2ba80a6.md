## Analysis

The external report's core broken invariant: **ERC20 transfer return value not checked → contract proceeds as if a token transfer succeeded even when it silently failed.**

Searching the Hyperbridge codebase for this exact pattern, the EVM `IntentsBase.sol`/`IntentGatewayV2.sol` intent-settlement contracts consistently use OpenZeppelin's `SafeERC20.safeTransfer`/`safeTransferFrom`, which reverts on failed or non-standard-return tokens. `evm/tron/contracts/apps/IntentGatewayV2.sol`, however, imports `SafeERC20` and declares `using SafeERC20 for IERC20;` [1](#0-0)  but never actually calls `safeTransfer` in its escrow-release paths. Instead, `withdraw()` and the `SweepDust` handler in `onAccept()` use a raw low-level `.call()` and only check that the call didn't revert, never decoding/validating the returned boolean: [2](#0-1) 

### Title
Unvalidated ERC20 `transfer` return value in `IntentGatewayV2.withdraw()`/`SweepDust` allows silent escrow release without moving funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` (called from settlement `onAccept` for `RedeemEscrow`/`RefundEscrow` and from `onGetResponse`) and the `SweepDust` branch of `onAccept()` release escrowed tokens using `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only check the outer call `success` boolean, never inspecting the ABI-decoded return value of `transfer`. Any ERC20/TRC20 token that returns `false` on failure instead of reverting (a common, standards-compliant behavior, and especially prevalent among Tron TRC20 tokens) will cause this check to pass even though no tokens moved.

### Finding Description
In `withdraw()`:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
_orders[body.commitment][token] -= amount;
```
`success` is true whenever the token contract itself doesn't revert — it says nothing about whether `transfer` returned `true`. The escrow accounting `_orders[body.commitment][token] -= amount` and `_filled[body.commitment] = beneficiary` are updated unconditionally once the call doesn't revert, and the same defect repeats for the fee-token payout and for `SweepDust` [3](#0-2) . This is the exact bug class from the external report ("initiates ERC20 token transfers without validating the return value of the transfer operation") reproduced in a production bridge-custody/settlement path rather than in an unrelated sale contract.

By contrast, the EVM (non-Tron) analog in `IntentsBase.sol` uses `IERC20(token).safeTransfer(beneficiary, amount)` which reverts on a `false` return [4](#0-3) , confirming the Tron variant is a deviation, not an intentional design choice.

### Impact Explanation
This sits directly in the bridge-custody/intent-settlement path: escrowed input tokens (placed via `placeOrder`) are meant to move exactly once to the rightful beneficiary (solver on fill, user on refund/cancel). If the underlying token returns `false` rather than reverting, `withdraw()` marks the order filled/refunded and decrements the escrow bookkeeping while the tokens remain stuck in the contract — a silent loss/lock of user or solver funds with no on-chain signal of failure (no revert, no compensating event). Because `_orders[commitment][token]` is decremented regardless, a subsequent legitimate retry of `withdraw` for the same commitment is also blocked (`UnknownOrder` once escrow hits zero), permanently locking the funds in the gateway.

### Likelihood Explanation
No privileged actor, malicious relayer, or governance action is required — the trigger is simply the behavior of a non-reverting-but-failing token used as an intent input on the Tron deployment (e.g., blacklist rejection, transfer-fee edge case, or any TRC20 implementation returning `false` instead of reverting on failure). Any user placing an order with such a token, or any solver relying on `withdraw()` payouts, is exposed on every settlement.

### Recommendation
Replace the manual `.call()` + `success`-only check in `withdraw()` and the `SweepDust` handler with `SafeERC20.safeTransfer`, consistent with the already-imported `SafeERC20` library and with the EVM `IntentsBase.sol` implementation, so that a `false` return value reverts the transaction instead of being silently treated as success.

### Proof of Concept
1. Deploy a TRC20/ERC20 token whose `transfer` returns `false` on failure instead of reverting (e.g., mimic a blacklist/insufficient-balance edge case that many TRC20 tokens implement this way).
2. Place a cross-chain order in `IntentGatewayV2` (Tron) using this token as an input; it gets escrowed via `safeTransferFrom` (pull side is safe).
3. Have the order filled/cancelled such that `onAccept`/`onGetResponse` calls `withdraw()`.
4. Configure/trigger the token to return `false` for the payout `transfer` call (e.g., beneficiary is blacklisted, or balance edge case is hit) without reverting.
5. Observe: `withdraw()` does not revert (`success` from the outer `.call()` is `true`), `_orders[commitment][token]` is decremented to zero, `_filled[commitment]` is set, and `EscrowReleased`/`EscrowRefunded` is emitted — yet the beneficiary's token balance never increased. The tokens remain permanently locked in the `IntentGatewayV2` contract with no valid path to reclaim them.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-721)
```text
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

    /**
     * @notice Withdraws the escrowed tokens for a request body.
     * @dev This function is marked as internal.
     * @param body The request body containing commitment, tokens, and beneficiary.
     * @param isRefund Whether this is a refund (true) or a successful fill (false).
     */
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
