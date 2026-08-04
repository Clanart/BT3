## Finding

### Title
Unsafe low-level `transfer` return-value handling in intent escrow withdrawal causes silent fund loss on Tron IntentGatewayV2 - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` escrows ERC20/TRC20 tokens for cross-chain intents and later releases them via the internal `withdraw()` function, which is reached from `onAccept()` for `RedeemEscrow`/`RefundEscrow` requests [1](#0-0) . Unlike the escrow-side code in the same contract, which correctly uses `IERC20.safeTransferFrom` [2](#0-1) , the withdrawal path performs a raw low-level `call` with `IERC20.transfer.selector` and only checks that the *call itself* did not revert — it never decodes/validates the boolean return value of `transfer()` [3](#0-2) . This is exactly the bug class from the external report (non-safe `transfer` usage that silently fails for tokens that return `false` instead of reverting), but here it corrupts settlement accounting in the bridge's own escrow rather than a third-party stargate call.

### Finding Description
`withdraw()` is the function that finalizes escrowed order funds — either releasing them to the solver/fill beneficiary (`RedeemEscrow`) or refunding the order owner (`RefundEscrow`) — after an authenticated cross-chain message is accepted by `onAccept()` [4](#0-3) . Inside `withdraw()`, for every ERC20 token in `body.tokens`, the code does:

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [5](#0-4) 

`success` here only reflects whether the external call reverted, not whether `transfer()` returned `true`. Many ERC20/TRC20 tokens (the same broad class the original report flags — tokens that don't strictly follow the "revert-on-failure" convention) return `false` on failure instead of reverting (e.g. insufficient balance in the gateway due to a fee-on-transfer, blacklist, or partial-failure token; TRC20 tokens with similarly-lenient implementations). When that happens, `success` is still `true`, so the `revert TransferFailed()` guard never fires, even though no tokens were actually delivered to `beneficiary`.

Immediately after, the code unconditionally finalizes the order state regardless of the actual transfer outcome:
```solidity
_orders[body.commitment][token] -= amount;
```
and at function entry:
```solidity
_filled[body.commitment] = beneficiary;
``` [6](#0-5) 

The same unchecked pattern is repeated for `TRANSACTION_FEES` redemption [7](#0-6)  and for the `SweepDust` handler in `onAccept()` [8](#0-7) .

Notably, this contract already imports and enables `SafeERC20` (`using SafeERC20 for IERC20;`) and uses `safeTransferFrom` correctly on the escrow-in side [9](#0-8) , and the parallel EVM-mainline implementation (`IntentsBase.sol`) uses `safeTransfer` for the exact same withdrawal step [10](#0-9) . The Tron variant's withdrawal path is the outlier that reintroduces the unsafe-transfer pattern the external report describes.

### Impact Explanation
Because the escrow accounting (`_orders[...] -= amount`) and the "filled" marker (`_filled[commitment] = beneficiary`) are updated regardless of whether the token actually moved, a failed-but-non-reverting `transfer()` call causes:
- The escrowed tokens to remain physically locked inside the `IntentGatewayV2` contract.
- The order to be permanently marked filled/refunded, so it can never be retried or re-withdrawn through the normal flow.
- The rightful beneficiary (solver or order owner) to receive zero tokens for an order the protocol believes is settled.

This is a direct loss/lock of bridged funds in the intent settlement path, matching the bounty's "stealing or loss of funds" and "false state acceptance" impact categories, without requiring any malicious relayer, prover, or admin — it is purely a token-return-value handling defect in production settlement code.

### Likelihood Explanation
The path is triggered through the normal, authenticated protocol flow (`onAccept` → `authenticate()` → `withdraw()`) whenever a `RedeemEscrow` or `RefundEscrow` message arrives for an order escrowing a token that returns `false` on failure instead of reverting [1](#0-0) . No attacker action is needed beyond having created/filled an order in a token with this property, or a token entering a state where `transfer` would fail (e.g., paused, blacklisted recipient, insufficient balance from prior dust sweep miscount) — all realistic operational conditions for an intent-settlement bridge holding many token addresses across many chains.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` (both the token loop and the fee-token branch) and in the `SweepDust` handler with `IERC20(token).safeTransfer(...)`, consistent with the rest of the contract's `SafeERC20` usage and with the mainline `IntentsBase.sol` implementation. This ensures a `false` return value reverts the transaction instead of silently corrupting escrow accounting.

### Proof of Concept
1. An order is placed and escrowed with an ERC20/TRC20 token `T` whose `transfer()` implementation returns `false` (rather than reverting) on failure — e.g., due to the gateway's balance of `T` being reduced below `amount` by a prior dust-sweep rounding, or `T` implementing a lenient/legacy ERC20 pattern.
2. The order is filled/cancelled normally; Hyperbridge relays an authenticated `RedeemEscrow`/`RefundEscrow` request to the destination `IntentGatewayV2` on Tron.
3. `onAccept()` calls `authenticate()` (passes, since the message is legitimately from the paired instance) then `withdraw(body, isRefund)` [11](#0-10) .
4. Inside `withdraw()`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `success = true` (external call didn't revert) even though `T.transfer` internally returned `false` and moved no tokens.
5. `_orders[body.commitment][token] -= amount` executes, and `_filled[body.commitment] = beneficiary` is set — the order is now permanently considered settled.
6. Result: `amount` of token `T` remains stuck in the `IntentGatewayV2` contract, unreachable through any accounting path, while the beneficiary received nothing — a permanent loss of the escrowed funds.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L398-399)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L614-626)
```text
    /**
     * @notice Executes an incoming post request.
     * @dev This function is called when an incoming post request is accepted.
     * It is only accessible by the host.
     * @param incoming The incoming post request data.
     */
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L664-667)
```text
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-701)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
