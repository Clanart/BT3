### Title
Escrow withdrawal on Tron IntentGatewayV2 accepts a non-reverting ERC20 `transfer` that returns `false` as success, permanently locking escrowed funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron variant of the Intent Gateway settles cross-chain and same-chain order escrow by making a raw low-level `.call()` to the ERC20 `transfer` function and only checking that the *call itself* did not revert (`success`), never decoding and checking the boolean return value the ERC20 standard requires. This is the same broken invariant as the referenced report: a shallow, non-standard-compliant "did it succeed" check (checking only that something didn't blow up, rather than the actual success indicator) is treated as authoritative, letting the contract permanently finalize/mark state as settled while the actual asset movement silently failed.

### Finding Description
`withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` is the function that releases escrowed order inputs to a solver (`RedeemEscrow`) or refunds them to the user (`RefundEscrow`), and it is invoked from the trusted `onAccept` / `onGetResponse` entry points once an ISMP message has been authenticated: [1](#0-0) 

For every escrowed token it does:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
...
_orders[body.commitment][token] -= amount;
``` [2](#0-1) 

The same pattern appears in the fee-forwarding branch and in `SweepDust`: [3](#0-2) [4](#0-3) 

`token.call(...)` returns `success = true` whenever the callee executes without reverting — it says nothing about the ERC20 semantic result. Many ERC20-like tokens (and this is common on the TRC20/Tron ecosystem this file specifically targets) do not revert on failure; instead they return `false` from `transfer()` when the transfer could not be completed (e.g. paused token, blacklisted recipient, insufficient contract balance due to a fee-on-transfer/rebasing quirk, or a non-standard token that returns `false` instead of reverting). Because the code never decodes the returned `bytes` and checks it equals `true`, such a `false`-returning, non-reverting call is indistinguishable here from a real success.

This mirrors exactly the bug class in the external report: rather than checking the correct positive-success signal (`IERC721Receiver.onERC721Received.selector` in the report; the ERC20 boolean return value here), the code accepts a weaker, non-authoritative signal (non-zero revert-reason length in the report; "the outer call didn't revert" here) as proof of success.

Note that the rest of the codebase is aware of this exact class of bug and defends against it elsewhere — the sibling non-Tron implementation (`IntentsBase.sol`) and other apps in the repo use OpenZeppelin's `SafeERC20.safeTransfer`, which explicitly decodes and validates the return value: [5](#0-4) 
The Tron `IntentGatewayV2.sol` even imports `SafeERC20` and does `using SafeERC20 for IERC20;`, but the `withdraw()` and `SweepDust` paths bypass it entirely in favor of the manual, unchecked `.call()` pattern: [6](#0-5) 

### Impact Explanation
`withdraw()` unconditionally decrements `_orders[commitment][token]` and, for the finalizing call, sets `_filled[commitment] = beneficiary` regardless of whether the token actually moved: [1](#0-0) 
If the underlying token returns `false` instead of reverting, the order is marked filled/refunded and the escrow accounting is zeroed out, but the beneficiary never receives the tokens. Because `_filled[commitment]` is now non-zero, this order can never be retried, re-filled, or re-cancelled through any other code path (`Filled()`/`UnknownOrder()` guards elsewhere key off this same state), so the escrowed value is permanently stranded in the contract with no recovery path for the rightful beneficiary. This is a direct, unauthorized loss/lock of bridged user or solver funds via the standard settlement entrypoint (`onAccept`/`onGetResponse`), matching the bounty's "stealing or loss of funds" / "false ... acceptance of state" categories, without requiring any malicious relayer, prover, or admin — an ordinary interaction with a non-strictly-reverting ERC20 is enough to trigger it.

### Likelihood Explanation
This requires only that one of the escrowed input tokens used in an order is a non-standard ERC20 that returns `false` on failure rather than reverting (a well-known, common real-world pattern, especially on Tron/TRC20 tokens, which is precisely the deployment target of this file). No attacker privilege, relayer collusion, or governance action is needed — the failure mode is triggered by ordinary token behavior (e.g. a paused/blacklist-protected stablecoin, or a token that runs out of allowance/balance edge cases and returns `false`). Given the contract is specifically built for the Tron chain, where such tokens are common, likelihood is non-trivial.

### Recommendation
Replace every raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` in `withdraw()` and in the `SweepDust` branch of `onAccept()` with `SafeERC20.safeTransfer()` (which the contract already imports via `using SafeERC20 for IERC20`), so that a `false` return value from a non-reverting ERC20 causes the whole `onAccept`/`onGetResponse` call to revert instead of the state being finalized against a silently-failed transfer. If a bespoke low-level call must be kept for gas or Tron-VM compatibility reasons, decode the return data and require it to be either empty or ABI-decode to `true` before treating the transfer as successful, matching the mitigation pattern the report recommends (verify the specific success value, not just that the call didn't revert).

### Proof of Concept
1. Deploy `IntentGatewayV2.sol` (Tron variant) with an ERC20/TRC20 token `T` whose `transfer()` implementation returns `false` (without reverting) when, e.g., the recipient is blacklisted or the contract is paused — a legal, standards-noncompliant but common token behavior.
2. A user places an order (`placeOrder`) escrowing `T` as an input token; `_orders[commitment][T] = amount` is recorded.
3. Trigger the destination fill, so a legitimate `RedeemEscrow` (or `RefundEscrow`) ISMP message is delivered and authenticated by `onAccept()`, calling `withdraw(body, ...)`.
4. Inside `withdraw()`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` executes `T.transfer(beneficiary, amount)`, which returns `false` but does not revert, so `success == true`.
5. `_orders[commitment][T] -= amount` executes, `_filled[commitment] = beneficiary` is set, and `EscrowReleased`/`EscrowRefunded` is emitted — yet `beneficiary`'s `T` balance never increased.
6. The order can never be retried (guarded by `Filled()`/`_filled` checks elsewhere), so `amount` of `T` is permanently stuck in the `IntentGatewayV2` contract, unrecoverable by the intended beneficiary.

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
