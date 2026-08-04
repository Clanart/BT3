## Finding

The core issue in the HedgeUnit report — attacker-controllable value sitting in a shared contract getting swept/counted without any ownership isolation — has a direct, stronger local analog: `CallDispatcher.dispatch()` is a **completely permissionless, access-control-free** entrypoint, yet it is used by `IntentGatewayV2` as a **shared, persistent holding vessel** for user escrow funds during predispatch/postdispatch calldata execution. [1](#0-0) 

`dispatch()` has no `onlyGateway`/`onlyOwner` modifier — any address can call it directly and force the `CallDispatcher` to execute arbitrary `Call[]` against any contract with code, moving out anything the dispatcher currently holds.

Meanwhile, `IntentGatewayV2.placeOrder` routes real user funds through this shared dispatcher: predispatch assets are transferred into the dispatcher, an attacker/user-supplied arbitrary call is executed by the dispatcher, and only the tokens matching `order.inputs[i].token` are swept back out via a balance-delta sweep: [2](#0-1) 

The sweep only recovers the token types listed in `order.inputs` (or in `order.output.assets` for postdispatch, via `IntentsBase._execute`): [3](#0-2) 

Any predispatch/postdispatch call that doesn't fully convert 100% of its input into exactly the tracked output token (partial fills, exact-output swaps with input dust, ETH remainders sent to the dispatcher's `receive()`, or a call that produces a token not enumerated in `order.inputs`/`order.output.assets`) leaves genuine, real user-escrow-bound tokens/ETH sitting in the `CallDispatcher` contract with no per-order accounting. Because `dispatch()` is unauthenticated, that residue is immediately and permanently theft-able by any third party, at any time, by simply crafting a `Call[]` that instructs the dispatcher to transfer its balance to themselves.

### Title
Permissionless `CallDispatcher.dispatch()` allows theft of residual escrow funds left by IntentGatewayV2 predispatch/postdispatch swaps - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher.dispatch(bytes)` has no access control, yet `IntentGatewayV2` uses this single shared, permanently-deployed contract as a mid-flight holding vessel for real user escrow assets during predispatch (swap-then-escrow) and postdispatch (fill-then-act) order flows. Only the exact token types tracked in `order.inputs`/`order.output.assets` are swept back to the gateway via a balance-delta sweep; any other token/ETH residue left behind (from partial swap consumption, rounding, or exact-output routes) remains in the dispatcher indefinitely. Since `dispatch()` is callable by anyone, that residue can be drained directly by any attacker with no relationship to the affected order.

### Finding Description
`placeOrder`'s predispatch path (`evm/src/apps/IntentGatewayV2.sol:203-280`) transfers the order's predispatch assets into `_params.dispatcher`, then invokes `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` with attacker/user-controlled calldata, and finally sweeps only the tokens named in `order.inputs` back to the gateway using a `balanceOf` delta. `IntentsBase._execute` performs the symmetric operation for postdispatch calldata, sweeping only `order.output.assets` tokens (`evm/src/apps/intentsv2/IntentsBase.sol:438-450`).

Neither sweep guarantees full drainage of every asset the predispatch/postdispatch call could have touched — a partial-fill swap, an exact-output route with input-token dust, or any call producing an untracked token/native-ETH remainder leaves real value stranded in the dispatcher. Because `CallDispatcher.dispatch()` (`evm/src/utils/CallDispatcher.sol:44`) has zero caller restriction, that stranded value is directly reachable by anyone: an attacker just submits their own `dispatch()` call with `Call[]` = `{to: token, data: transfer(attacker, balance)}}` (or drains ETH via a call with `value: dispatcher.balance` to a controlled contract, since `receive()` is also unrestricted).

### Impact Explanation
This is a direct loss-of-funds vector on production escrow assets, not a griefing/DoS issue. Any dust or non-primary-token remainder left in the shared `CallDispatcher` by a legitimate order's predispatch/postdispatch execution is permanently and trivially stealable by an unrelated, unprivileged third party — no relayer, prover, admin, or front-running race condition required. Because the dispatcher is a single shared contract used by every order on the chain, this drains value belonging to the intent-gateway ecosystem generally (protocol dust intended for treasury sweep, or genuine escrow remainder), matching the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" categories.

### Likelihood Explanation
High. No special conditions are needed beyond a predispatch/postdispatch call that doesn't perfectly zero out every touched asset — a routine occurrence for exact-output swaps, multi-hop routes, or any DEX interaction with slippage/rounding. Once such residue exists, exploitation is a single unauthenticated call to `dispatch()` requiring no coordination, no timing race, and no privileged role.

### Recommendation
Add caller authentication to `CallDispatcher.dispatch()` (e.g., restrict to the configured `IntentGatewayV2`/`SimplexPaymaster` instances, or make the dispatcher single-use/ephemeral per call via `CREATE2`+`SELFDESTRUCT` or a minimal proxy pattern instead of a shared singleton). Additionally, sweep the *complete* set of assets touched by predispatch/postdispatch calldata (not just the tokens enumerated in `order.inputs`/`order.output.assets`) back to the gateway before returning control, so no value can be stranded in the dispatcher between transactions.

### Proof of Concept
1. A legitimate user places an order with predispatch calldata that swaps ETH → DAI via an exact-output Uniswap route, where the router refunds excess ETH to the caller (`CallDispatcher`) rather than to the gateway. The gateway's sweep loop only forwards DAI (`order.inputs[i].token`) back from the dispatcher; the refunded excess ETH remains on `CallDispatcher`.
2. Any attacker (unrelated to the order) calls `CallDispatcher.dispatch(abi.encode(calls))` directly, where `calls = [{to: attackerContract, value: address(dispatcher).balance, data: ""}]`, draining the stranded ETH — or for ERC-20 remainders, `calls = [{to: token, value: 0, data: abi.encodeCall(IERC20.transfer, (attacker, IERC20(token).balanceOf(dispatcher)))}]`.
3. The attacker now holds funds that were part of a user's/protocol's escrow flow, without ever interacting with `IntentGatewayV2` or being selected as a solver.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L203-280)
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

            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-450)
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
```
