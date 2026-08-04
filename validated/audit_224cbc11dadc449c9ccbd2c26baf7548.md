## Analysis

The Sudoswap bug's core primitive: a **shared, trusted executor contract can be triggered by an unauthorized party** to move funds it holds/has been approved to spend, because the guard that was supposed to restrict who can drive that executor doesn't actually restrict callers.

The Hyperbridge local analog is `CallDispatcher.dispatch()`, used as the shared arbitrary-call executor for `IntentGatewayV2` (predispatch/postdispatch) and `HyperFungibleTokenUpgradeable`/`WrappedHyperFungibleToken` (calldata execution) across the whole protocol on a chain: [1](#0-0) 

`dispatch(bytes memory encoded)` has **no access-control modifier whatsoever** — any address can call it directly, not just the gateway/token contracts that are meant to be its only callers, and it will forward arbitrary `to/value/data` from the dispatcher's own context. [2](#0-1) 

The sweep logic that is supposed to keep the dispatcher's balance at zero between calls is scoped only to the tokens explicitly declared in the order/message, not to "whatever balance the dispatcher actually ends up holding": [3](#0-2) [4](#0-3) 

And `HyperFungibleTokenUpgradeable.onAccept` dispatches attacker/user-supplied calldata to the same shared dispatcher with **no sweep at all** afterward: [5](#0-4) 

Documentation itself acknowledges the dispatcher is expected to hold tokens only "temporarily," and warns against unlimited allowances precisely because of this residual-balance/approval risk, but nothing in code enforces it: [6](#0-5) 

Yet the shipped Foundry test for postdispatch approves `type(uint256).max` from the dispatcher to an external router: [7](#0-6) 

Any token balance that ends up in the `CallDispatcher` and isn't covered by the caller's declared sweep list (e.g. an intermediate swap token not listed in `order.output.assets`/`order.inputs`, dust from an HFT calldata execution that has zero sweep, or leftover balance from a partially-consumed swap) stays there indefinitely, and because `dispatch()` is completely permissionless, **any unprivileged address can call it directly** to move that balance — or exploit any stale ERC20 approval the dispatcher still holds toward a router — to an address of their choosing. This is the same broken invariant as the Sudoswap report: a contract with access to funds (via balance or lingering approval) can be driven by an unauthorized caller because the mechanism meant to gate who triggers it was never actually enforced at the contract level.

### Title
Permissionless `CallDispatcher.dispatch()` lets any address drain residual token/ETH balances and stale approvals left by intent/HFT executions - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher.dispatch()` has no access control; it is meant to be invoked only by `IntentGatewayV2` and the `HyperFungibleToken`/`WrappedHyperFungibleToken` contracts as an internal execution helper for predispatch/postdispatch swaps, but it is a public external function callable by anyone.

### Finding Description
`IntentsBase._execute` and `IntentGatewayV2`'s predispatch flow only sweep the specific tokens listed in `order.output.assets`/`order.inputs` back out of the dispatcher after execution [3](#0-2) . Any token balance produced by the executed `Call[]` that is not in that declared list (extra swap-path token, reward token, rounding remainder, or a stale approval left on the dispatcher toward a router as shown in the test suite [7](#0-6) ) is never recovered by the protocol. `HyperFungibleTokenUpgradeable.onAccept` performs zero sweep after dispatching arbitrary calldata to the same shared dispatcher [5](#0-4) . Because `CallDispatcher.dispatch()` itself enforces no caller restriction [1](#0-0) , any account can invoke it directly with an arbitrary `Call[]` to transfer out whatever balance the dispatcher is holding at that moment, or to trigger `transferFrom` on a router that still has a live (potentially `type(uint256).max`) allowance from the dispatcher.

### Impact Explanation
This results in unauthorized execution and loss/theft of funds that accumulate in a shared, singleton contract used by every Hyperbridge intent order and hyper-fungible-token transfer on a chain — exactly the "stealing or loss of funds via unauthorized execution" class the bounty targets, and it requires no relayer, prover, or admin compromise; a plain unprivileged EOA can call `dispatch()`.

### Likelihood Explanation
Likelihood depends on residual balance/approvals actually accumulating in the dispatcher (e.g. swap paths that don't perfectly match declared output assets, or postdispatch calls that leave non-full-consuming approvals as demonstrated in the repo's own test). Given the dispatcher is address-shared across many independent orders/apps over time, some nonzero window of exploitable balance is realistic, and the permissionless `dispatch()` entrypoint removes any barrier once that balance exists.

### Recommendation
Restrict `CallDispatcher.dispatch()` to an allowlisted set of caller contracts (the specific `IntentGatewayV2`/HFT instances), and/or make sweep logic in `_execute`/predispatch handling exhaustively drain *all* token balances left on the dispatcher (not just the declared set), and add a sweep step to `HyperFungibleTokenUpgradeable.onAccept` as well.

### Proof of Concept
1. A legitimate order's `postdispatch` call swaps through a router, leaving a `type(uint256).max` approval from `CallDispatcher` to that router for token X (as in `IntentGatewayV2Test.testPostdispatchTokenSweep`), or leaves a balance of a token not present in `order.output.assets`.
2. `_execute`'s sweep only checks tokens in `order.output.assets`; the un-declared/residual token or approval is left on the dispatcher permanently.
3. An attacker calls `CallDispatcher.dispatch(encode([Call({to: router or token, value:0, data: transferFrom/transfer-style call moving the balance to attacker})]))` directly — no modifier blocks this call.
4. The attacker receives the dispatcher's stranded balance/approval-backed funds.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
```text
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
```

**File:** sdk/packages/core/contracts/interfaces/ICallDispatcher.sol (L26-37)
```text
/**
 * @title The ICallDispatcher
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @notice This interface is used to dispatch untrusted call(s)
 */
interface ICallDispatcher {
    /*
     * @dev Dispatch the encoded call(s)
     */
    function dispatch(bytes memory params) external;
}
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-474)
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

```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L407-440)
```text
            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L320-336)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({from: message.from, to: beneficiary, source: string(request.source), amount: message.amount});
    }
```

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L1219-1224)
```text
        // Call 1: Approve Uniswap router
        postdispatchCalls[0] = Call({
            to: address(usdc),
            value: 0,
            data: abi.encodeWithSelector(IERC20.approve.selector, uniswapRouter, type(uint256).max)
        });
```
