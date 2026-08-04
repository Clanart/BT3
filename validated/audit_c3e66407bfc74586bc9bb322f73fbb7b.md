Found the analog. `CallDispatcher` is a **shared, unauthenticated custody contract**: it accepts native ETH from any address via an unguarded `receive()`, and its `dispatch()` function is a public entrypoint with no access control that spends the contract's *entire pooled balance* (via `call.value` in the attacker-decoded `Call[]`) to whatever `to`/`data` the caller supplies. This is the same "no earmarking, anyone can trigger custody movement" flaw as the Lido MEV vault.

### Title
Unauthenticated `CallDispatcher.dispatch()` lets anyone drain pooled ETH/tokens staged for cross-chain order execution - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher` is a single, shared contract used by `IntentGatewayV2`/`IntentsBase._execute` and the `HyperFungibleToken` family to execute arbitrary calldata attached to cross-chain orders/transfers. It accepts native ETH via an unguarded `receive()` [1](#0-0)  and exposes `dispatch(bytes memory encoded)` as a fully public function with **no caller restriction and no per-order/per-commitment binding** — it just decodes an arbitrary `Call[]` and executes `to.call{value: call.value}(call.data)` using the contract's own balance [2](#0-1) .

### Finding Description
`IntentsBase._execute` routes solver-fill calldata through the shared dispatcher, and — critically — pre-funds it before executing calls: for orders with output calldata, tokens/ETH intended for `order.output.call` are sent to the dispatcher, `dispatch()` is invoked to run the order's calls, and only afterward is any residual balance swept back [3](#0-2) . Because `dispatch()` is a standalone public function on a contract shared by *all* orders/transfers system-wide, and its execution draws from the dispatcher's **aggregate** balance rather than a balance scoped to the calling order, any external, unprivileged address can call `CallDispatcher.dispatch()` directly, between the legitimate funding step and the legitimate `_execute` call in the same block/mempool window, supplying its own `Call[]` that redirects the currently-held ETH/tokens to itself. The dispatcher has no notion of "whose value this is" — it is exactly the Lido vault problem: a shared pot that anyone can trigger a withdrawal from, with the withdrawal function taking no restriction on caller or on which value is spent.

### Impact Explanation
This directly matches the "bridge custody" and "unauthorized execution / theft of funds" impact classes: an attacker can front-run or race the intended `_execute`/`onAccept` flow to steal escrowed order assets or in-flight cross-chain transfer value that has been staged in the dispatcher, causing loss of funds for solvers/users and possible double-spend of the same pooled balance across unrelated orders that happen to route through the same dispatcher concurrently.

### Likelihood Explanation
`dispatch()` requires no privileged role, no relayer/prover compromise, and no malformed proof — it is callable by any EOA the moment the dispatcher holds a spendable balance, which happens routinely as part of the documented `_execute` composable-fill flow and the `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata-execution flow. The only requirement is timing a call to land while the dispatcher is holding value, which is observable on-chain (mempool or same-block).

### Recommendation
Scope every dispatched call's value to the specific order/commitment that funded it (e.g., pass an amount cap or a per-call commitment binding that only the calling gateway contract can authorize), restrict `dispatch()` to be callable only by registered gateway contracts (`onlyAuthorizedCaller`), and/or require value to be pushed atomically with the call (e.g., `dispatch{value: X}(...)`) rather than staged as a standing balance that any address can later claim via an unauthenticated call.

### Proof of Concept
1. A solver calls `IntentGatewayV2.fillOrder` for an order whose `output.call` requires ETH to complete a DEX swap; `_execute` first transfers ETH into `CallDispatcher`, planning to call `dispatch(order.output.call)` next [4](#0-3) .
2. Before that second transaction lands, an attacker observes the dispatcher's ETH balance increase and submits their own transaction directly to `CallDispatcher.dispatch()` with a `Call[]` of `{to: attacker, value: dispatcher.balance, data: ""}`.
3. Since `dispatch()` has no access control and spends the dispatcher's current balance regardless of which order funded it, the attacker's call executes first (or instead), draining the ETH meant for the solver's order fill.
4. The legitimate `_execute` call subsequently fails or executes against a drained balance, leaving the order's output call unfunded while the attacker walks away with the staged ETH.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L36-39)
```text
    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}
```

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
