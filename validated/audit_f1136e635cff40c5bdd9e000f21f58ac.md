## Analysis

The core broken invariant in the external H-02 report is: a periphery "orchestrator" contract that moves value through intermediate/shared execution points assumes those intermediate points are only ever touched by the intended trusted flow, without actually enforcing that assumption on-chain — leaving the intermediate contract's state (balances/approvals) exploitable once real-world usage patterns diverge from the idealized happy path.

The direct Hyperbridge analog is `CallDispatcher`, the shared arbitrary-calldata executor used by `IntentGatewayV2` (predispatch/postdispatch execution) and by `HyperFungibleToken`/`WrappedHyperFungibleToken` (`onAccept` calldata execution). It is a single, reusable, unauthenticated multicall-style contract that routinely and, by the protocol's own design, sometimes indefinitely, holds ERC20 balances and grants ERC20 approvals as part of normal cross-chain and intent-fill flows. [1](#0-0) 

`dispatch()` has no caller restriction whatsoever — it is a plain `external` function with no modifier, no `onlyOwner`, no allow-list of authorized callers (gateway/host): [2](#0-1) 

It also has a public `receive()` that accepts ETH from anyone: [3](#0-2) 

The protocol's own test suite and docs confirm this contract legitimately ends up holding token balances and, in at least one documented pattern, an *unlimited* ERC20 approval to an external router that is not fully consumed by the swap it backs: [4](#0-3) 

The intent-gateway logic *does* sweep back whatever balance remains of the specific `order.output.assets`/`order.inputs` tokens after each calldata execution round [5](#0-4) , and the fee-on-transfer tests show the codebase is well aware that "actual received" can diverge from "expected" amounts [6](#0-5) . But this sweep is scoped only to the token addresses explicitly listed in that specific order's `inputs`/`output.assets`. Any other token balance, any left-over ERC20 allowance (e.g. the `type(uint256).max` router approval above, which the code never revokes), or stray ETH sent to the contract's `receive()`, is *not* tracked, *not* owned by anything, and is retrievable by **any address on Earth** by simply calling `CallDispatcher.dispatch()` directly with a `Call{to: token, data: transfer(attacker, balance)}` (or, for the leftover allowance case, whichever call spends it).

### Title
Unauthenticated `CallDispatcher.dispatch()` lets anyone drain residual balances/approvals left on the shared intent/cross-chain calldata executor - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher`, the single shared contract used by `IntentGatewayV2` and `HyperFungibleToken`/`WrappedHyperFungibleToken` to execute predispatch/postdispatch/onAccept calldata, exposes `dispatch(bytes)` as a fully public function with zero caller authentication and accepts arbitrary native value via a public `receive()`. Because this contract is deliberately designed to transiently (and, per the codebase's own dust/fee-on-transfer accounting, sometimes non-transiently) hold ERC20 balances and grant ERC20 approvals as part of normal operation, any residue that escapes the narrow per-order sweep logic is permanently claimable by an unrelated, unprivileged caller.

### Finding Description
`dispatch()` decodes and executes an arbitrary `Call[]` with the `CallDispatcher`'s own identity as `msg.sender`, without checking who invoked it: [7](#0-6) 
There is no `onlyHost`, `onlyGateway`, or any access-control equivalent to the `onlyHost` modifiers used elsewhere in the codebase's `HyperApp`-derived contracts (e.g. `onAccept` is gated by `onlyHost` in `HyperFungibleTokenUpgradeable.sol` and `WrappedHyperFungibleToken.sol`). `IntentsBase._execute` and the predispatch/escrow flows in `IntentGatewayV2`/`ExtrinsicIntents.sol` route real user/solver funds through this shared, address-stable contract before sweeping only the tokens named in the current order: [5](#0-4) 
Any token balance not enumerated in that sweep (e.g. a token accidentally received, dust from a fee-on-transfer token whose amount rounding leaves a sliver, or an approval granted to a spender but not fully consumed, as demonstrated by the `type(uint256).max` approval left in `testPostdispatchTokenSweep`) is not the gateway's to reclaim — it belongs, by construction of `dispatch()`, to whoever calls it next.

### Impact Explanation
Any unprivileged external account can call `CallDispatcher.dispatch()` (or directly exploit a stray leftover ERC20 approval) to move out any ERC20/ETH residue sitting on this shared contract, resulting in outright theft of funds that were never intended for that caller. Because the contract is shared across all orders/messages of the gateways that use it, this is not confined to the order/solver who caused the residue — the funds are up for grabs by any observer of the chain, satisfying the "stealing or loss of funds" / "unauthorized execution" impact categories.

### Likelihood Explanation
No privileged role, relayer, prover, or malicious peer is required — a plain EOA calling `dispatch()` (or the underlying token's `transferFrom`, if it exploits a stray approval) is a public entrypoint action available at any time the contract carries residue. The codebase's own fee-on-transfer and dust-accounting tests confirm that non-zero, unaccounted-for residue on this contract is a realistic and even expected occurrence, not a purely theoretical edge case.

### Recommendation
Restrict `CallDispatcher.dispatch()` to an allow-listed set of callers (the specific `IntentGatewayV2`/`HyperFungibleToken` instances that are configured to use it), or make each gateway deploy/own its own dispatcher instance so no cross-application/cross-order residue is reachable by a third party. Additionally, ensure any calldata that grants ERC20 approvals from the dispatcher is followed by an explicit revocation (`approve(spender, 0)` or `forceApprove` back to zero) once the intended spend completes, and sweep *all* token balances the dispatcher could plausibly hold (not just the order's declared token set) back to a safe, access-controlled owner after each execution round.

### Proof of Concept
1. Any order creator/filler executes a legitimate flow whose postdispatch/predispatch calldata approves a spender (e.g. a router) for `type(uint256).max` of a token, as shown in `IntentGatewayV2Test.sol::testPostdispatchTokenSweep` (`postdispatchCalls[0]` approves `uniswapRouter` for `type(uint256).max`), while the actual swap consumes less than the full allowance.
2. This approval is never revoked; it remains active on-chain against the fixed `CallDispatcher` address indefinitely.
3. In a completely separate later transaction, whenever the `CallDispatcher` legitimately (even momentarily, via any other user's order) holds a balance of that same token, or whenever any dust is left unaccounted for by the narrow per-order sweep in `IntentsBase._execute`, an unrelated attacker calls `CallDispatcher.dispatch(abi.encode([Call({to: token, value:0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})]))` directly — this call succeeds because `dispatch()` performs no caller check — and the attacker receives funds that were never intended for them. [8](#0-7)

### Citations

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

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L1219-1243)
```text
        // Call 1: Approve Uniswap router
        postdispatchCalls[0] = Call({
            to: address(usdc),
            value: 0,
            data: abi.encodeWithSelector(IERC20.approve.selector, uniswapRouter, type(uint256).max)
        });

        // Call 2: Exact output swap - swap USDC for exactly 1000 DAI
        postdispatchCalls[1] = Call({
            to: uniswapRouter,
            value: 0,
            data: abi.encodeWithSelector(
                bytes4(keccak256("swapTokensForExactTokens(uint256,uint256,address[],address,uint256)")),
                daiOutputAmount, // exact amount out
                type(uint256).max, // max amount in
                path,
                address(dispatcher), // tokens come back to dispatcher
                block.timestamp
            )
        });

        // Call 3: Transfer DAI to user
        postdispatchCalls[2] = Call({
            to: address(dai), value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, user, daiOutputAmount)
        });
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-485)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

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

            unchecked {
                ++i;
            }
        }

        if (sweepCount > 0) {
            Call[] memory finalCalls = new Call[](sweepCount);
            for (uint256 i; i < sweepCount;) {
                finalCalls[i] = sweepCalls[i];
                unchecked {
                    ++i;
                }
            }
            ICallDispatcher(dispatcher).dispatch(abi.encode(finalCalls));
        }
    }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2372-2434)
```text
    /// @notice Full round-trip: place with fee-on-transfer, fill, solver withdraws exact escrow.
    function testPlaceAndFill_FeeOnTransferToken_RoundTrip() public {
        FeeOnTransferToken fot = new FeeOnTransferToken(100); // 1% transfer fee
        fot.mint(user, 10000 * 1e18);

        uint256 inputAmount = 1000 * 1e18;
        uint256 receivedByGateway = inputAmount - (inputAmount * 100) / 10000; // 990
        uint256 outputAmount = 900 * 1e18;

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(fot)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: outputAmount});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 100,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        // Place order
        vm.startPrank(user);
        fot.approve(address(intentGateway), inputAmount);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        // Reconstruct order as placeOrder mutated it
        order.user = bytes32(uint256(uint160(user)));
        order.source = host.host();
        order.nonce = 0;
        order.inputs[0].amount = receivedByGateway; // actual received

        // Solver fills
        uint256 solverFotBefore = fot.balanceOf(solver);

        vm.startPrank(solver);
        dai.approve(address(intentGateway), outputAmount);

        TokenInfo[] memory solverOutputs = new TokenInfo[](1);
        solverOutputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: outputAmount});

        intentGateway.fillOrder(order, FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: solverOutputs}));
        vm.stopPrank();

        // Solver should receive the escrowed FOT (with transfer fee applied on the way out)
        uint256 solverFotReceived = fot.balanceOf(solver) - solverFotBefore;
        uint256 expectedSolverReceived = receivedByGateway - (receivedByGateway * 100) / 10000; // 990 - 1% fee
        assertEq(solverFotReceived, expectedSolverReceived, "Solver should receive escrowed FOT minus transfer fee");

        // Gateway should have zero FOT left
        assertEq(fot.balanceOf(address(intentGateway)), 0, "Gateway should have no FOT remaining");
    }
```
