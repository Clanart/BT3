## Analog Found: Unchecked ERC-20 `transfer()` return value lets `IntentGatewayV2.withdraw` finalize escrow settlement without the beneficiary ever receiving funds

### Title
Escrow settlement finalizes and marks orders filled/refunded even when the underlying ERC-20 `transfer()` call returns `false` - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Sherlock report's core broken invariant is: a fund-moving function trusts its own bookkeeping ("the transfer succeeded") instead of verifying that the actual value transferred equals the expected amount, then lets dependent contracts treat a `true`/success result as proof that funds moved. In Hyperbridge's Tron build of the Intent Gateway, `withdraw()` (and its `onAccept`/`SweepDust` sibling paths) reproduce this exact pattern for ERC-20 tokens: it uses a raw low-level `.call` to `transfer()` and only checks that the *call itself* did not revert, never checking the ERC-20 boolean return value.

### Finding Description
`IntentGatewayV2.withdraw` in the Tron contract releases escrowed tokens to a beneficiary during order fill settlement, cross-chain cancellation, and refund flows: [1](#0-0) 

For every token in the withdrawal request it does:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
and then unconditionally decrements the escrow accounting (`_orders[body.commitment][token] -= amount`), marks the order filled (`_filled[body.commitment] = beneficiary`), and emits `EscrowReleased`/`EscrowRefunded`.

The bug: a low-level `.call` only reports `success = false` when the callee *reverts*. Many ERC-20 tokens (including several widely deployed on Tron/TRC-20-style networks, and any token that returns `false` instead of reverting on failure — paused state, blacklist, insufficient allowance/balance edge cases, non-standard implementations) will return `success = true` with an encoded `false` return value. This code never decodes/verifies that boolean. The same unchecked pattern also appears in the fee-sweep and dust-sweep branches: [2](#0-1) [3](#0-2) 

This is a real regression relative to the mainline EVM contract's escrow-release logic in `IntentsBase.sol`, which correctly uses OpenZeppelin's `SafeERC20.safeTransfer` (which decodes and enforces the boolean return value, or requires no-return-data to be interpreted correctly): [4](#0-3) 

The Tron file even imports and declares `using SafeERC20 for IERC20;` at the top, but does not use it in the settlement path, instead hand-rolling the unsafe raw-call check: [5](#0-4) 

### Impact Explanation
Escrowed user/solver funds are the exact custody value at risk (the corrupted value is the escrow ledger entry `_orders[commitment][token]` combined with the `_filled[commitment]` settlement flag). When `transfer()` returns `false` without reverting:
- `withdraw()` still zeroes out the escrow balance and marks the order `Filled`/`Refunded` and emits the success event, even though the beneficiary received zero tokens.
- The tokens remain stuck in the `IntentGatewayV2` contract, permanently unrecoverable through the normal withdrawal path because the escrow accounting that gated the transfer has already been deleted and the order is marked settled — a second attempt reverts with `UnknownOrder`.
- This applies to solver payouts on fill (`onAccept` → `withdraw`), user refunds on cancellation, and relayer/fee-token payouts — i.e., loss of funds for users, solvers, and fee recipients, and false settlement-state acceptance (an order is recorded as filled/refunded when it functionally is not).

### Likelihood Explanation
No privileged actor, relayer collusion, or malformed proof is required — this triggers purely from token behavior on ordinary, unprivileged calls (`fillOrder`, `cancelOrder`, `onAccept`, `onIsmpTimeout`/`SweepDust`) whenever the escrowed/fee/dust token is one that returns `false` on failure instead of reverting. Given Tron's ecosystem includes several TRC-20/ERC-20 style tokens with non-standard failure semantics, and given the contract already whitelists arbitrary tokens via `Order.inputs`/`output.assets` (no allowlist enforced in the shown code), this is a realistic, attacker/token-independent trigger, not merely a theoretical edge case.

### Recommendation
Replace every raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `evm/tron/contracts/apps/IntentGatewayV2.sol` (in `withdraw`, the `SweepDust` handler, and the fee payout branch) with `SafeERC20.safeTransfer`/`safeTransferFrom`, consistent with the mainline `IntentsBase.sol` implementation. This ensures a `false` boolean return value reverts the transaction instead of allowing escrow state to be finalized without the underlying transfer actually succeeding.

### Proof of Concept
1. Deploy (or use) an ERC-20/TRC-20 token whose `transfer()` implementation returns `false` on failure instead of reverting (e.g., insufficient balance check implemented as `if (balance < amount) return false;`).
2. Place and fill (or cancel) an `IntentGatewayV2` order using that token as an escrowed input, arranging the token's `transfer()` to hit its failure branch (e.g., the gateway's balance of the token is drained/misaligned by a prior fee-on-transfer or rounding case, or the token pauses transfers to `beneficiary`).
3. Call `withdraw()` (via `onAccept`, `fillOrder`, or `cancelOrder` completion). The low-level `.call` succeeds (`success = true`) because the token did not revert, even though it internally returned `false` and moved no tokens.
4. Observe: `_orders[commitment][token]` is decremented to 0, `_filled[commitment]` is set, `EscrowReleased`/`EscrowRefunded` is emitted — yet `beneficiary`'s token balance is unchanged. The tokens are now unrecoverable through the contract's normal paths.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-672)
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
