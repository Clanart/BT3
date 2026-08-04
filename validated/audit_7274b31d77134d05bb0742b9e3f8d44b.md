This confirms the finding. The main EVM `IntentGatewayV2.sol` at `evm/src/apps/IntentGatewayV2.sol:17-25` composes `IntentsBase`/`IntrinsicIntents`/`ExtrinsicIntents` and additionally imports `ReentrancyGuardTransient`, and its `_withdraw` in `IntentsBase.sol` decrements `_orders[body.commitment][token]` **before** the external call [1](#0-0) . The team also has a dedicated regression test (`IntrinsicIntentsReentrancyTest.sol`) proving they previously fixed a same-chain fee/escrow-theft reentrancy bug by moving `_filled[commitment] = msg.sender` to the top of the fill functions (CEI pattern) [2](#0-1) .

The Tron port, however, still contains the pre-fix pattern.

### Title
Tron `IntentGatewayV2.withdraw` mutates escrow accounting after the external transfer, violating CEI - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`IntentGatewayV2.withdraw` on the Tron deployment transfers escrowed tokens (native and ERC-20) via low-level `.call` **before** decrementing `_orders[commitment][token]`, exactly the "check happens, but bypassable via re-entrant low-level call" pattern from the external Wild Credit report. This is the identical bug class the team already found and fixed with a CEI rewrite in `IntentsBase._withdraw` on the main EVM `IntentGatewayV2` (evidenced by `IntrinsicIntentsReentrancyTest.sol`), but the fix was never ported to the Tron contract.

### Finding Description
In `withdraw()`, for each escrowed token the code does: [3](#0-2) 
The external interaction (`beneficiary.call{value: amount}("")` for native tokens, or `token.call(transfer(...))` for ERC-20s) happens before `_orders[body.commitment][token] -= amount;`. Both `beneficiary` and `token` are attacker-influenced: `placeOrder` places no allowlist restriction on `order.inputs[i].token`, so an attacker can create an order whose input "token" is a malicious contract that executes arbitrary code on `transfer()`/receiving ETH [4](#0-3) .

Compare this to the fixed version in the main EVM app, `IntentsBase._withdraw`, where the escrow debit occurs *before* any external call: [1](#0-0) 

While the outer guard `_filled[body.commitment] = beneficiary;` is (correctly) set at the top of `withdraw()` before the loop [5](#0-4) , which blocks a simple re-entrant `cancelOrder()` call on the *same* commitment via the `Filled()` check in `cancelOrder` [6](#0-5) , the per-token escrow balance (`_orders[commitment][token]`) itself remains stale/non-zeroed during the external call. This removes the defense-in-depth layer that the fixed `IntentsBase` version relies on, and — unlike the main EVM `IntentGatewayV2`, which imports `ReentrancyGuardTransient` [7](#0-6)  — the Tron contract has **no reentrancy guard at all** on `withdraw`, `cancelOrder`, or `onAccept`.

### Impact Explanation
I was not able to fully verify, within the available tool budget, a complete exploit chain that defeats the `_filled` top-level guard to achieve double-withdrawal or fund theft on this specific Tron contract — the only reachable call sites for `withdraw()` are `cancelOrder` (blocked on re-entry to the same commitment by `_filled`), and `onAccept`/`onGetResponse` (both `onlyHost`, and `onAccept` additionally requires `authenticate()` against a registered remote instance). This Tron `IntentGatewayV2` also appears to lack a `fillOrder` entry point altogether (unlike the main EVM `IntentGatewayV2`), which limits the attack surface compared to the bug class the team already fixed elsewhere.

Because I could not conclusively demonstrate a working double-spend/loss-of-funds path within the current investigation depth, and per the task's strict requirement to only report a *provable* exploit, I am not confident enough in fund-loss impact to assert full severity here.

### Likelihood Explanation
Low-to-unproven with current evidence. The vulnerable code pattern is real and clearly diverges from the team's own documented fix for the same bug class, but the specific reachability of a profitable reentrant call was not conclusively established against the existing `_filled` guard and `onlyHost`/`authenticate` restrictions in this file.

### Recommendation
Port the CEI fix from `IntentsBase._withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:400-409`) to the Tron `IntentGatewayV2.withdraw` (`evm/tron/contracts/apps/IntentGatewayV2.sol:688-705`): decrement `_orders[body.commitment][token]` before making the external `.call`, and consider adding a reentrancy guard (as the main EVM `IntentGatewayV2` does via `ReentrancyGuardTransient`) to `withdraw`, `cancelOrder`, and `onAccept` for defense in depth.

### Proof of Concept
Not conclusively demonstrated. A verified PoC would require confirming a call path that lets a malicious `order.inputs` token or beneficiary contract re-enter into a function that reads/uses the not-yet-decremented `_orders[commitment][token]` value for financial gain, before the top-level `_filled` guard or `onlyHost`/`authenticate` restrictions apply. This was not established with certainty in this investigation — flagging this as a code-pattern regression relative to the team's own fixed `IntentsBase._withdraw`, rather than a fully proven exploit.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L32-49)
```text
/**
 * @title ReentrantBeneficiary
 * @notice Malicious beneficiary contract that attempts to re-enter `fillOrder` during
 *         the ETH transfer made by `_fillSameChain` or `_fillCrossChain`.
 *
 * Attack window (pre-fix):
 *
 *   _fillSameChain / _fillCrossChain:
 *     beneficiary.call{value: ...}("")   ← RE-ENTRY HERE
 *     // _filled still == address(0) pre-fix, now set at the top (CEI)
 *
 * With the CEI fix in place, `_filled[commitment]` is set to `msg.sender` at the
 * very start of both fill functions. Any reentrant `fillOrder` call therefore hits
 * the `if (_filled[commitment] != address(0)) revert Filled()` guard and reverts.
 * That revert propagates through `receive()`, causing the outer ETH transfer to
 * return `(false, ...)`, which triggers `InsufficientNativeToken()` in the outer
 * call — rolling back all state changes atomically.
 */
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L445-463)
```text
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-512)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

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

**File:** evm/src/apps/IntentGatewayV2.sol (L17-25)
```text
import {IntentsBase} from "./intentsv2/IntentsBase.sol";
import {IntrinsicIntents} from "./intentsv2/IntrinsicIntents.sol";
import {ExtrinsicIntents} from "./intentsv2/ExtrinsicIntents.sol";

import {ICallDispatcher, Call} from "@hyperbridge/core/interfaces/ICallDispatcher.sol";
import {IDispatcher} from "@hyperbridge/core/interfaces/IDispatcher.sol";
import {IIntentPriceOracle} from "@hyperbridge/core/apps/IntentPriceOracle.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";
```
