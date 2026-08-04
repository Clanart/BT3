## Analysis

The external report's core pattern is: **a contract holds a balance/authority to move funds, and any unprivileged caller can trigger that movement to themselves before the intended flow completes** — the swapRelayer transfers its own balance to `msg.sender` with no ownership check on *whose* funds those are.

The direct local analog is `CallDispatcher.dispatch()`, the shared helper contract used by `IntentGatewayV2` (and other apps like `HyperFungibleTokenUpgradeable`) to execute arbitrary predispatch/postdispatch calldata for orders. [1](#0-0) 

### Title
Unrestricted `CallDispatcher.dispatch()` Allows Any Address to Drain Stale Token Approvals/Balances Left by IntentGatewayV2 Orders - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher` is a single, long-lived, shared contract referenced by `IntentGatewayV2._params.dispatcher` for **every** order's predispatch/postdispatch calldata execution. Its `dispatch(bytes)` function is `external` with **no caller restriction whatsoever** — any address, not just the `IntentGateway`, can call it directly and force it to execute arbitrary `Call{to, value, data}` entries using whatever native ETH or ERC-20 approvals happen to currently sit on the dispatcher.

### Finding Description
`IntentGatewayV2.placeOrder`/`fillOrder` route user- and solver-supplied calldata (`order.predispatch.call`, `order.output.call`) through the shared dispatcher: assets are transferred to the dispatcher, `ICallDispatcher(dispatcher).dispatch(...)` executes the calldata (e.g., approve+swap on a router), and only afterward does the gateway sweep the dispatcher's resulting balance back to itself via a second `dispatch(...)` call. [2](#0-1) 

Because `CallDispatcher.dispatch` is public and unauthenticated, and the dispatcher persists across transactions/orders, any ERC-20 `approve` left standing on the dispatcher toward a router (e.g., from a predispatch swap call that approves more than the swap actually consumes, or from a partial/failed sweep leaving the approval un-revoked) remains usable indefinitely. An attacker can call `CallDispatcher.dispatch()` directly with a `Call` targeting that router's swap function, specifying the dispatcher as `msg.sender`/`from` (via the stale allowance) and their own address as the output recipient — completely bypassing `IntentGatewayV2` and its sweep/escrow accounting. [3](#0-2) 

The dispatcher's `receive()` and lack of an access-control modifier mean it functions exactly like the swapRelayer in the external report: it is a shared holding/execution point whose balance and delegated authority (approvals) can be pulled by whoever calls the public function first, not necessarily the party whose funds are actually resting there. [4](#0-3) 

### Impact Explanation
This matches the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" criteria: an unprivileged, non-owner, non-relayer caller can extract value that was meant to flow only through `IntentGatewayV2`'s escrow/settlement path, without needing a malicious peer, prover, or admin.

### Likelihood Explanation
Exploitability depends on a residual ERC-20 approval or balance existing on the dispatcher between transactions (e.g., partial-fill swaps, fee-on-transfer tokens causing under-consumption of an approved amount, or any predispatch/postdispatch calldata that approves more than it spends). Given calldata is fully attacker/solver-controlled per order and swap amounts are frequently exact-output/slippage-bounded, such excess approvals are a realistic byproduct of normal usage rather than a contrived edge case, and the exposure window persists indefinitely (approvals don't expire) until observed and exploited.

### Recommendation
Restrict `CallDispatcher.dispatch()` to only be callable by its configured owner/app contract (e.g., an `onlyCaller` modifier bound to `IntentGatewayV2`/`HyperFungibleToken` at construction), and/or have calling apps explicitly revoke (`forceApprove(spender, 0)`) any approvals granted to external routers immediately after each predispatch/postdispatch execution, so no standing spend authority survives past the single atomic order flow.

### Proof of Concept
1. A solver fills an order whose `output.call` approves a DEX router for `amountIn = 1000` tokens from the dispatcher, but the router's swap only consumes `600` (e.g., due to slippage-limited exact-output logic), leaving `400` approved and un-revoked on the dispatcher.
2. `IntentsBase._execute` sweeps only the dispatcher's post-swap token *balance* back to the gateway — it does not revoke the router's `400`-token allowance. [5](#0-4) 
3. An attacker calls `CallDispatcher.dispatch()` directly (no auth check) with `Call{to: router, data: swap(amountIn=400, ..., recipient=attacker)}`. The router calls `token.transferFrom(dispatcher, router, 400)` using the still-valid allowance, then sends the swap output to the attacker instead of the gateway. [6](#0-5)

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L36-62)
```text
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L203-258)
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

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-484)
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
```
