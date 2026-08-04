## Finding

Hyperbridge does not use OpenZeppelin's legacy `safeApprove` anywhere — all internal fee-token/router approvals use `forceApprove` [1](#0-0) [2](#0-1) , so the exact Sherlock M-4 root cause is absent from protocol-internal code. However, the same *bug class* — a shared, persistent contract accumulating a non-zero token allowance that permanently blocks future users — reappears in the `IntentGatewayV2` / `CallDispatcher` design.

### Title
Attacker-controlled order calldata can leave a permanent dust allowance on the shared `CallDispatcher`, bricking predispatch/postdispatch swaps for that token/target pair for all future orders - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/utils/CallDispatcher.sol`)

### Summary
`IntentGatewayV2` routes all `predispatch` and `output.call` (postdispatch) calldata through a single, shared `CallDispatcher` instance stored in `_params.dispatcher` [3](#0-2) . This dispatcher is reused across every order placed on the gateway. `Order.predispatch.call` and `Order.output.call` are fully attacker-controlled — any user places their own order and encodes an arbitrary `Call[]` array that `CallDispatcher.dispatch` executes verbatim via raw low-level calls [4](#0-3) .

### Finding Description
A malicious order's calldata can include a raw `IERC20.approve(spender, amountIn - 1)` call against a token like USDT (which reverts on any approve that changes a non-zero allowance to another non-zero value — the same "reset to zero first" semantics as the legacy OpenZeppelin `safeApprove` in the original report). If the attacker crafts the accompanying swap/target call so that only `amountIn - 1` of the approved amount is actually consumed, the `CallDispatcher` is left holding a 1-wei residual allowance from itself to that `spender` for that `token`.

Because the `CallDispatcher` is the same contract instance reused for all orders (confirmed by `_params.dispatcher` being a single configured address [5](#0-4) , and by every test referencing the same `dispatcher` address across orders [6](#0-5) ), any subsequent, unrelated order — placed by a different, honest user — whose predispatch/postdispatch calldata tries to `approve(sameSpender, sameToken, newAmount)` for a swap (a documented, expected pattern; see `IntentGatewayV2Test.sol` using `IERC20.approve.selector` with the Uniswap V3 router, and `IntentGatewayV2SameChainTest.sol` approving the Uniswap V2 router with `type(uint256).max`) will have that `approve` call revert on-chain. Since `CallDispatcher.dispatch` reverts the entire batch if any call fails [7](#0-6) , the victim's entire `placeOrder`/`fillOrder` transaction reverts, permanently bricking that token/spender combination on the shared dispatcher.

Existing guards do not stop this path:
- There is no check anywhere in `IntentGatewayV2`/`IntentsBase` that the `CallDispatcher`'s allowance to any external target is zero before or after dispatch.
- The dust-sweeping logic only sweeps token *balances* left on the dispatcher, not *allowances* [8](#0-7) .
- The documentation acknowledges the general allowance-reset quirk only for user-to-gateway approvals, not for the shared dispatcher's persistent state: "Some ERC-20s require an allowance to be reset to zero before changing a non-zero allowance" [9](#0-8) .

### Impact Explanation
Any unprivileged user who can place an order (permissionless) can permanently deny predispatch/postdispatch swap composability for a given token/target pair to every other user of the gateway. Because full-fill calldata orders cannot be partially filled and the whole transaction reverts on `CallFailed`, this can strand escrowed input funds for legitimate orders that depend on that swap route (e.g., swap-then-escrow or fill-then-act patterns), forcing users into cancellation/refund flows and degrading solver fill capability for the affected asset — a direct fund-lock / broken-execution impact on the shared dispatcher shared by all users.

### Likelihood Explanation
High. The attack requires only placing one cheap order with crafted `predispatch.call` or `output.call` bytes — no privileged role, relayer, or prover is needed, and the target token (e.g. USDT) and popular router/spender addresses are public knowledge. The attacker only needs to under-consume their own approved allowance by 1 wei during their own swap execution, which is fully within their control since they supply the entire `Call[]`.

### Recommendation
- Have `CallDispatcher` (or `IntentsBase._execute`/predispatch handling) reset any token allowance the dispatcher grants back to zero after each `dispatch()` call, or require calls that grant allowances to use `forceApprove`-equivalent semantics enforced by the dispatcher itself rather than trusting attacker-supplied calldata.
- Alternatively, deploy a fresh, ephemeral `CallDispatcher` (e.g., via `CREATE2`/minimal proxy) per order/dispatch so no allowance state persists across unrelated orders.
- Add a post-dispatch invariant check that reverts if the dispatcher retains any non-zero allowance to a previously-untrusted target after `dispatch()` completes.

### Proof of Concept
1. Attacker places `orderA` with `output.call` (or `predispatch.call`) encoding `Call[]`:
   - `Call{ to: USDT, data: approve(ROUTER, 1000e6) }`
   - `Call{ to: ROUTER, data: <swap consuming only 999_999_999 of the 1000e6 allowance, leaving 1> }`
2. `fillOrder`/`placeOrder` succeeds; `CallDispatcher` now has `USDT.allowance(dispatcher, ROUTER) == 1`.
3. Victim places `orderB` with predispatch/postdispatch calldata that also does `Call{ to: USDT, data: approve(ROUTER, someAmount) }` to route a swap through the same `ROUTER`.
4. USDT's `approve` reverts because the existing allowance (1) is non-zero and the new amount is non-zero, `CallDispatcher.dispatch` reverts with `CallFailed`, and the victim's entire `placeOrder`/`fillOrder` transaction reverts — permanently, until the dispatcher's allowance to `ROUTER` for `USDT` is externally reset (which no function in the contract currently does). [10](#0-9) [11](#0-10) [12](#0-11)

### Citations

**File:** sdk/packages/core/contracts/apps/HyperApp.sol (L101-106)
```text
    function dispatchWithFeeToken(DispatchPost memory request) internal returns (bytes32) {
        address hostAddr = host();
        address feeToken = IDispatcher(hostAddr).feeToken();
        if (request.payer != address(this)) IERC20(feeToken).safeTransferFrom(request.payer, address(this), request.fee);
        IERC20(feeToken).forceApprove(hostAddr, request.fee);
        return IDispatcher(hostAddr).dispatch(request);
```

**File:** evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol (L178-179)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amountIn);
        IERC20(token).forceApprove(_params.swapRouter, amountIn);
```

**File:** evm/src/apps/IntentGatewayV2.sol (L203-227)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);
```

**File:** evm/src/utils/CallDispatcher.sol (L1-63)
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
pragma solidity ^0.8.17;

import {ICallDispatcher, Call} from "@hyperbridge/core/interfaces/ICallDispatcher.sol";

/**
 * @title The CallDispatcher
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @notice This contract is used to dispatch calls to other contracts.
 */
contract CallDispatcher is ICallDispatcher {
    /**
     * @dev error thrown when the target is not a contract.
     */
    error NotContract(address target);

    /**
     * @dev error thrown when a call fails.
     */
    error CallFailed(address target, bytes result);

    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
}
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L427-443)
```text
    /**
     * @dev Executes arbitrary calldata attached to an order's output via the CallDispatcher.
     * After dispatching the calls, any residual token balances left on the dispatcher
     * are swept back to this contract and accounted for as protocol dust.
     *
     * This enables composable order fulfillment — solvers can route through DEXes,
     * lending protocols, or other DeFi primitives as part of filling an order.
     *
     * @param order The order containing the output calldata to execute.
     * @param outputsLen The number of output assets to sweep after execution.
     */
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L444-468)
```text
        Call[] memory sweepCalls = new Call[](outputsLen);
        uint256 sweepCount = 0;

        for (uint256 i; i < outputsLen;) {
            address token = address(uint160(uint256(order.output.assets[i].token)));

            if (token == address(0)) {
                uint256 balance = dispatcher.balance;
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({to: address(this), value: balance, data: ""});
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            } else {
                uint256 balance = IERC20(token).balanceOf(dispatcher);
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1520-1525)
```text
        // No escrow released and calldata not executed.
        assertEq(
            dai.allowance(address(intentGateway.params().dispatcher), address(intentGateway)),
            0,
            "Calldata should not execute on rejected partial fill"
        );
```

**File:** docs/content/developers/evm/intent-gateway/placing-orders.mdx (L471-471)
```text
Some ERC-20s require an allowance to be reset to zero before changing a non-zero allowance. For those tokens, add `approve(sourceGateway, 0n)` before the exact approval in the same batch. Do not use this reset on tokens that do not require it unless your token integration specifies it.
```
