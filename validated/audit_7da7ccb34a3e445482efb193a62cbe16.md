## Analysis

The UNCX report's broken invariant is: **a shared, protocol-operated execution proxy is invoked with attacker/user-controlled call targets and tokens, and the proxy itself has no restriction on who can trigger it — so value that lands on the proxy (intended for a legitimate flow) becomes grabbable by anyone.**

The direct local analog is `CallDispatcher.sol`, the shared contract used by `IntentGatewayV2`/`IntentsBase._execute()` to run user-supplied `predispatch`/`postdispatch` calldata for every intent order on a chain. [1](#0-0) 

### Title
Unrestricted `CallDispatcher.dispatch()` lets anyone drain residual/leftover token balances from the shared intent-order execution proxy - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher` is a single, shared, non-access-controlled contract used by every `IntentGatewayV2` order's `predispatch`/`postdispatch` calldata execution. Its `dispatch(bytes)` function has no caller restriction whatsoever - any external address can call it directly and force it to execute arbitrary `Call{to, value, data}` entries. Because the dispatcher is shared across *all* orders and its balance-sweeping logic in `IntentsBase._execute()` only sweeps tokens listed in `order.output.assets`, any token balance left on the dispatcher that is not one of the current order's declared output assets (e.g. an intermediate swap token, a partially-consumed approval target, or dust from a different order's calldata) is never recovered by the protocol and can be stolen by an unrelated, unprivileged caller simply by invoking `dispatcher.dispatch(...)` themselves.

### Finding Description
`IntentsBase._execute()` runs order-defined calldata through the shared dispatcher and then sweeps back only the tokens declared in that order's `output.assets`: [2](#0-1) 

The sweep loop iterates `outputsLen` (i.e. `order.output.assets.length`) and only checks/sweeps balances for those specific tokens. Any other ERC20/native balance that ends up on the dispatcher as a side effect of the arbitrary postdispatch calls (approvals, intermediate swap outputs, refunds from a DEX call, etc.) is left on the contract with no owner-only recovery path.

`CallDispatcher` itself enforces no caller restriction on `dispatch()`: [1](#0-0) 

There is no `restrict`/`onlyGateway`/`onlyHost` modifier — unlike every other privileged entrypoint in the codebase (`HostManager.onAccept` uses `restrict(_params.host)`, `IntentGatewayV2.onAccept` uses `onlyHost`/`_authenticate`). Any address, including a completely unrelated attacker with no relation to any order, can call the dispatcher and force it to `to.call{value}(data)` against any target of their choosing (only requiring `extcodesize(to) > 0`).

The codebase's own tests demonstrate the exact pattern that leaves state behind: postdispatch calldata that leaves a standing `approve(router, type(uint256).max)` on the dispatcher and swaps tokens through Uniswap inside the dispatcher's own context: [3](#0-2) 

Since `IntentGatewayV2` only sweeps tokens present in the *current* order's `output.assets`, any token that briefly touches the dispatcher (e.g., an intermediate hop token in a multi-hop swap route, or a token from an order whose author mis-declares `output.assets` versus what the calldata actually produces) is left unswept. Because `dispatch()` is public and unauthenticated, that balance is directly stealable — an attacker just calls `CallDispatcher.dispatch(abi.encode([Call({to: leftoverToken, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, leftoverToken.balanceOf(dispatcher))})]))`.

This mirrors the UNCX pattern precisely: a protocol-shared, automation-facing contract (the auto-collector / here the CallDispatcher) that (a) is reachable by anyone, and (b) executes against attacker-influenced targets/tokens, so value sitting in the shared execution context can be siphoned off by a party with no legitimate claim to it.

### Impact Explanation
Funds (ERC20 dust, or leftover value) that transiently or erroneously remain in the shared `CallDispatcher` after an order's postdispatch execution are not restricted to the gateway's own sweep call — they are unauthorized-execution-reachable by any external account. This is a direct loss-of-funds vector: value that should be routed back into the protocol's dust accounting (`DustCollected`) or ultimately reach the intended beneficiary can instead be drained by an unrelated attacker who has no relationship to the order that produced it.

### Likelihood Explanation
Likelihood is driven entirely by the presence of any residual balance on `CallDispatcher` at a moment when the attacker can call `dispatch()` — which is always, since it is unauthenticated. Any solver/user order using postdispatch/predispatch calldata with multi-hop swaps, partial consumption, or a mismatch between the declared `output.assets` list and what the executed calldata actually produces creates an opportunity; the codebase's own test fixtures already exercise flows (approve + swap + manual transfer) that are a hair's breadth from leaving exactly this kind of residue. No privileged role, relayer, or governance action is required — only a public call to a shared utility contract.

### Recommendation
Restrict `CallDispatcher.dispatch()` to only be callable by the registered `IntentGatewayV2`/gateway instance that owns it (e.g., an `onlyGateway`/`restrict(gateway)` modifier set at construction), and/or deploy a fresh, single-use dispatcher instance per fill (e.g., via `CREATE2`/minimal proxy) rather than sharing one long-lived contract across all orders, so no cross-order residue can accumulate for an unrelated caller to claim. Additionally, harden `_execute()`'s sweep to reconcile *all* token balances actually touched by the calldata (not just the tokens declared in `order.output.assets`), or require postdispatch calldata to fully account for every token it interacts with.

### Proof of Concept
1. A solver fills an order whose `PaymentInfo.call` executes a multi-hop swap through the shared `CallDispatcher` (as in `evm/tests/foundry/IntentGatewayV2Test.sol` lines 1205-1243: approve → `swapTokensForExactTokens` → manual `transfer`), where the intermediate/leftover token (or unspent approval target's eventual refund) is not one of `order.output.assets`.
2. `IntentsBase._execute()` runs the calldata, then sweeps only the tokens in `order.output.assets`; any other token balance left on `dispatcher` is not swept.
3. An attacker, with no relation to the order, calls `CallDispatcher.dispatch(abi.encode(Call[]))` directly with a `Call` targeting the leftover token's `transfer(attacker, balance)` (or draining native value via `to: attacker, value: dispatcher.balance`). This succeeds because `dispatch()` has no caller restriction, and the attacker's `to` (the leftover ERC20 contract) has code, satisfying the only check in `dispatch()`.
4. The attacker receives tokens that never belonged to them, funded entirely by prior legitimate order activity.

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-473)
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
