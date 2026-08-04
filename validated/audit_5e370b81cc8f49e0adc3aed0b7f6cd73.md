## Finding [1](#0-0) 

The Tron variant of `IntentGatewayV2` imports and aliases `SafeERC20` for `IERC20` [2](#0-1)  and uses `safeTransferFrom` consistently for token *inflows* (escrow funding, predispatch transfers) [3](#0-2) . However, on the *outflow* paths — `withdraw()` and the `SweepDust` branch of the incoming-message handler — token payouts are made with a raw low-level `.call` to the ERC20 `transfer` selector, and only the outer call-success boolean is checked, never the ABI-decoded return value: [4](#0-3) [5](#0-4) 

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```

This is exactly the bug class from the external report: for any ERC20/TRC20 implementation that returns `false` on failure instead of reverting (non-compliant tokens are common on Tron/TRC20), `success` is `true` (the call itself didn't revert) while the actual transfer failed. The code proceeds as if the payout succeeded.

### Title
Unchecked ERC20 `transfer` return-data in `IntentGatewayV2.withdraw`/`onAccept` (Tron) causes escrow to be released/decremented without the beneficiary ever receiving funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`withdraw()` and the `SweepDust` handler in `onAccept()` release escrowed order funds and sweep dust using a raw `.call` to the `transfer` selector, checking only that the call did not revert, not the boolean value returned by the token. A token that returns `false` on a failed transfer (rather than reverting) will silently fail the payout while the contract still decrements/deletes escrow accounting and emits success events (`EscrowReleased`/`EscrowRefunded`/`DustSwept`), mirroring the exact false-success accounting bug described in the external report's unchecked `transferFrom` finding.

### Finding Description
In `withdraw(WithdrawalRequest memory body, bool isRefund)`, for each escrowed token the contract does: [6](#0-5) 
then unconditionally subtracts `amount` from `_orders[body.commitment][token]` and, on the final leg, deletes the transaction-fee escrow and transfers fees the same way: [7](#0-6) 

The same pattern appears in the `SweepDust` admin/host-driven flow inside `onAccept`: [8](#0-7) 

`(bool success,)` from `.call(...)` only reflects whether the callee reverted; it does not decode/validate the ABI-encoded `bool` a spec-compliant ERC20 `transfer` is supposed to return. Contrast this with the rest of the codebase (`IntentsBase.sol`, non-Tron `IntentGatewayV2.sol`, `HyperFungibleToken.sol`, etc.), which uniformly use OpenZeppelin's `safeTransfer`/`safeTransferFrom` — precisely because those wrappers decode and assert the boolean return value for non-reverting tokens. The Tron file imports `SafeERC20` and uses it for the escrow-funding (input) side [9](#0-8)  but reverts to the unchecked raw-call pattern specifically on the escrow-release (output) side.

### Impact Explanation
Because escrow state (`_orders[commitment][token]`) is decremented and `_filled[commitment]` is finalized regardless of whether the beneficiary actually received tokens, a failed-but-non-reverting transfer causes:
- The order to be marked filled/refunded (`EscrowReleased`/`EscrowRefunded` emitted) while the beneficiary receives nothing — a false-state acceptance that downstream indexers, solvers, and the cross-chain settlement logic will treat as a completed payout.
- The underlying tokens remain stuck in the `IntentGatewayV2` contract with no escrow accounting pointing to them, since `_orders[...]` has already been zeroed/decremented — a de facto loss/lock of user or solver funds with no path to reclaim them through the contract's own accounting.
- The same defect applies to `SweepDust`, which is reachable via a validated ISMP host message (`onAccept`), so it does not require a malicious peer to trigger — only a token whose `transfer` can return `false` without reverting.

This matches the bounty's "loss of funds" / "false proof or state acceptance" impact classes.

### Likelihood Explanation
Likelihood depends on which TRC20/ERC20 tokens are configured for use with `IntentGatewayV2` on Tron; many legacy or non-standard TRC20 tokens (and some ERC20 tokens ported without full compliance) return `false` on failure rather than reverting, so any order or dust-sweep denominated in such a token hits this path deterministically once the transfer condition (e.g., insufficient balance, blacklist, paused token) is met — no attacker collusion, privileged role, or race condition is needed.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `withdraw()` and the `SweepDust` branch of `onAccept()` with `IERC20(token).safeTransfer(beneficiary, amount)` (already imported and used elsewhere in the same file), which decodes and asserts the boolean return value and reverts on non-compliant tokens, exactly mirroring the C4 report's recommended `require(transferFrom(...) == true, ...)` mitigation.

### Proof of Concept
1. Register/escrow an order input in a TRC20 token whose `transfer` implementation returns `false` on failure instead of reverting (e.g., a token that silently no-ops when the contract balance is insufficient due to a prior partial sweep, or a blacklist/pausable token).
2. Trigger `withdraw()` (e.g., via `onGetResponse` cancellation path or the normal fill/redeem flow) for a commitment whose escrowed balance for that token is set, but where the token's `transfer` call to the beneficiary returns `false` (contract balance manipulated/insufficient, or beneficiary blacklisted).
3. Observe: `success` is `true` (call did not revert) so `withdraw` proceeds; `_orders[body.commitment][token] -= amount` executes; `_filled[body.commitment] = beneficiary` is set; `EscrowReleased`/`EscrowRefunded` is emitted — yet the beneficiary's token balance never increased.
4. The escrow record for that token/commitment is now gone even though the tokens remain locked in the `IntentGatewayV2` contract, with no further code path to redeem them.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L1-56)
```text
// Copyright (C) Polytope Labs Ltd.
// SPDX-License-Identifier: Apache-2.0

// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// 	http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
pragma solidity ^0.8.24;

import {DispatchPost, DispatchGet, IDispatcher, PostRequest} from "@hyperbridge/core/interfaces/IDispatcher.sol";
import {IncomingPostRequest, IncomingGetResponse} from "@hyperbridge/core/interfaces/IApp.sol";
import {HyperApp} from "@hyperbridge/core/apps/HyperApp.sol";
import {StateMachine} from "@hyperbridge/core/libraries/StateMachine.sol";
import {
    PaymentInfo,
    TokenInfo,
    DispatchInfo,
    Order,
    SweepDust,
    Params,
    ParamsUpdate,
    DestinationFee,
    WithdrawalRequest,
    FillOptions,
    SelectOptions,
    CancelOptions,
    NewDeployment
} from "@hyperbridge/core/apps/IntentGatewayV2.sol";
import {IIntentPriceOracle} from "@hyperbridge/core/apps/IntentPriceOracle.sol";

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L398-400)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-674)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

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
