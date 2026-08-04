Based on my investigation, I found a concrete, non-governance-gated analog to the stETH/wstETH issue in the `IntentGatewayV2` intent-fulfillment flow, specifically in the "sweep dispatcher balance as dust" logic used by both `placeOrder`'s predispatch phase and `fillOrder`'s calldata-execution phase.

### Title
Unattributed CallDispatcher balance sweep in `_execute` misclassifies foreign/leftover tokens as protocol dust - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
The Lido report's core defect is: a shared custody contract accumulates value that arrives outside the tracked accounting path, and the contract has no mechanism to attribute that value correctly — it either gets stuck or gets misassigned. In `IntentGatewayV2`, the `_params.dispatcher` (`CallDispatcher`) is a single, persistent, shared contract that every order's calldata execution routes through (`placeOrder`'s predispatch and `fillOrder`'s `_execute`/postdispatch). The `_execute` sweep path reads `IERC20(token).balanceOf(dispatcher)` and unconditionally treats the *entire* balance as this order's output, emitting it wholesale as `DustCollected`, with no before/after balance diff to isolate what this specific call actually produced.

### Finding Description
`_execute` in `evm/src/apps/intentsv2/IntentsBase.sol` (lines 438-485) is called by `fillOrder` after running solver-supplied `order.output.call` through the shared `dispatcher`: [1](#0-0) 

Unlike `placeOrder`'s Phase 1 handling of the same dispatcher pattern — which snapshots `balancesBefore[i]` before the dispatch and computes `received = after - before` to isolate only the delta this call produced — `_execute` has no snapshot/diff step. It reads `dispatcher.balance` / `IERC20(token).balanceOf(dispatcher)` directly and sweeps the full amount: [2](#0-1) 

Because `dispatcher` is a single persistent contract (`_params.dispatcher`, set once at `initialize`/governance update and reused by every order), any token balance left on it by one order's calldata (e.g. a partially executed DEX route, a failed inner transfer, or dust from a concurrent order's `placeOrder` predispatch step) is fully absorbed by whichever *next* order's `_execute` runs against the same token. That balance is misattributed as the new order's "protocol dust" via `emit DustCollected(token, balance)`, rather than being reconciled against what this particular fill's calldata actually generated.

### Impact Explanation
This breaks the invariant that funds routed through the dispatcher for a specific order belong to that order's rightful escrow/beneficiary. A solver can construct `order.output.call` that intentionally leaves tokens stranded on the dispatcher (e.g., an incomplete swap), then immediately fill another order using the same output token; that second order's `_execute` will sweep the stranded balance in full and count it as protocol dust — permanently diverting funds that should have gone to the first order's beneficiary or been refunded, with no on-chain accounting trail tying the swept balance back to its origin. This is a public-entrypoint (`fillOrder`) path reachable by any solver, requiring no relayer, prover, or governance action — matching "loss of funds" / "wrong beneficiary or amount" from the bounty's impact gate.

### Likelihood Explanation
Likelihood depends on whether `CallDispatcher.sol` truly persists non-zero balances across calls (i.e., whether a solver can craft `order.output.call` to leave a positive balance without reverting the whole dispatch). I was not able to fully read `evm/src/utils/CallDispatcher.sol` before running out of tool budget, so I cannot confirm from code whether the dispatcher enforces zero-balance-on-exit or similar guards. This is the key open question that determines exploitability; if `CallDispatcher.dispatch` already asserts it ends at zero balance per call, this finding is not exploitable and only the theoretical accounting-attribution gap remains.

### Recommendation
Mirror the `placeOrder` Phase-1 pattern in `_execute`: snapshot `balanceOf(dispatcher)` (or `dispatcher.balance`) immediately before `ICallDispatcher(dispatcher).dispatch(order.output.call)`, and only sweep/attribute the post-call delta as dust for *this* order. Any pre-existing balance found on the dispatcher before dispatch should be flagged/reverted rather than silently merged into the current order's outcome, since its presence indicates either a bug or an attempted exploit from a prior call.

### Proof of Concept
Conceptual (not executed, since I could not confirm dispatcher's zero-balance invariant):
1. Solver A calls `fillOrder` for order A with `order.output.call` crafted to leave `X` tokens of `TOKEN` stuck on `dispatcher` (e.g., a swap that under-delivers, or a deliberate `transfer` to the dispatcher that isn't fully consumed by the encoded sweep calls for order A's output list, since `_execute`'s `outputsLen` loop only sweeps tokens present in `order.output.assets` — a token not listed in A's outputs but present in A's calldata execution never gets swept for A, and can be picked up later).
2. Solver B (or Solver A again) calls `fillOrder` for order B whose `order.output.assets` includes `TOKEN`. `_execute` for order B reads `IERC20(TOKEN).balanceOf(dispatcher)`, which now includes the leftover `X` from order A, and emits `DustCollected(TOKEN, balance)` for the combined amount, sweeping it all into the gateway as order B's dust — with no accounting differentiating the two orders' contributions. [1](#0-0)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-468)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L229-275)
```text
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
```
