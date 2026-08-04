Found a concrete, locally-provable analog. The `ClaimFees` bug's core pattern — a function that blindly sweeps a contract's *entire current balance* instead of the delta attributable to the specific operation, letting unrelated funds get co-mingled and drained — reproduces in Hyperbridge's `IntentGatewayV2` / `CallDispatcher` design.

### Title
Unauthenticated `CallDispatcher.dispatch()` lets anyone drain token/native residue left by `IntentGatewayV2`'s predispatch and output-call sweeps - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`IntentGatewayV2` routes user escrow deposits and solver swap outputs through a single, permanently-deployed, **shared** `CallDispatcher` instance (`_params.dispatcher`) rather than an ephemeral per-order contract. `CallDispatcher.dispatch()` has zero access control — any address can call it with arbitrary `Call[]` data. The gateway's own sweep logic only recovers the *specific tokens declared in the order* (`order.inputs` / `order.output.assets`), not the full set of assets a predispatch/output call might actually produce. Any token balance left on the dispatcher outside that declared set is then permanently and trivially reachable by an unprivileged attacker calling `CallDispatcher.dispatch()` directly.

### Finding Description
`CallDispatcher.dispatch` has no `onlyOwner`/`restrict` modifier at all: [1](#0-0) 

`_params.dispatcher` is a single shared address used by every order, not deployed fresh per order: [2](#0-1) 

In `placeOrder`, when an order includes `predispatch` calldata (documented as e.g. "unwrapping LP tokens"), assets are transferred into the dispatcher, arbitrary `order.predispatch.call` is executed, and only the tokens listed in `order.inputs` are swept back: [3](#0-2) [4](#0-3) 

The same pattern repeats for output fills in `_execute`, which sweeps `dispatcher.balance` / `IERC20(token).balanceOf(dispatcher)` for each token in `order.output.assets` only: [5](#0-4) 

If a predispatch or output call (e.g., an LP-token unwrap that yields two underlying tokens when the order only declares one, or any swap/call path that leaves a token not enumerated in the order's asset list) leaves a balance on the dispatcher that the enumerated sweep loop never touches, that balance is stranded on the shared `CallDispatcher` contract. Because `dispatch()` is public and unauthenticated, **anyone** — with no relayer, prover, admin, or front-running requirement — can subsequently call `CallDispatcher.dispatch()` directly with a `Call` that transfers that stranded token/ETH balance to themselves. This is the same broken invariant as `GaugeCL._claimFees`: the code assumes "whatever balance sits at this address after my operation belongs to me," while the balance holder is a shared, permissionlessly-controllable location that any other actor can also read and drain.

### Impact Explanation
Any token or native balance stranded on the shared `CallDispatcher` — from LP-unwrap byproducts, partial swap outputs, rounding remainders, or any predispatch/output call whose produced assets don't exactly match the order's declared token list — is unauthorized-execution-reachable and stealable by an arbitrary unprivileged address. This is direct loss of user/protocol funds via unauthorized execution on a public entry point, matching the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" categories.

### Likelihood Explanation
Likelihood is driven purely by protocol usage, not by any privileged or adversarial infrastructure actor: any order that legitimately uses `predispatch.call` for multi-token unwrap flows (explicitly called out as a supported use case in the code comments) or any output-call swap that yields dust in a token outside `order.output.assets` creates exploitable residue. No relayer/prover compromise, no governance action, and no front-running is needed — the attacker simply calls `CallDispatcher.dispatch()` whenever residue exists, which can be checked on-chain at any time.

### Recommendation
- Add access control to `CallDispatcher.dispatch()` so only the registered `IntentGatewayV2` (or an explicitly authorized caller) can invoke it, closing the direct drain path.
- Deploy an ephemeral, per-order dispatcher (e.g., via CREATE2 salted by the order commitment) instead of one shared singleton, so no cross-order residue can accumulate.
- In the sweep logic, enumerate and sweep the dispatcher's *actual* post-call token balances (or require predispatch/output calls to declare every token they may produce) rather than only the tokens named in `order.inputs`/`order.output.assets`, mirroring the `_claimFees` fix pattern of measuring exact deltas instead of assuming balance ownership.

### Proof of Concept
1. A user places an order with `predispatch.call` that unwraps an LP token into `tokenA` and `tokenB`, declaring only `tokenA` in `order.inputs`.
2. `placeOrder` sweeps `tokenA` back via the declared-inputs loop; `tokenB`'s balance remains on the shared `CallDispatcher` contract indefinitely (`evm/src/apps/IntentGatewayV2.sol:229-256`).
3. An attacker, having observed `tokenB.balanceOf(dispatcherAddress) > 0` on-chain, calls `CallDispatcher.dispatch(abi.encode([Call({to: tokenB, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})]))` directly — no restriction exists on `evm/src/utils/CallDispatcher.sol:44` to stop this.
4. The `extcodesize` check on `tokenB` passes (it's a contract), the `.call` succeeds, and `tokenB`'s entire stranded balance is transferred to the attacker, with no interaction with `IntentGatewayV2` required.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L152-158)
```text
     *    the post-fee amounts.
     * 3. If the order includes predispatch calldata, executes it via the CallDispatcher
     *    (e.g., unwrapping LP tokens) before escrowing the resulting balances.
     * 4. Otherwise, transfers input tokens directly from the caller into escrow.
     * 5. If the order includes solver fees, collects them in the protocol
     *    fee token — swapping from native token via Uniswap V2 if necessary.
     *
```

**File:** evm/src/apps/IntentGatewayV2.sol (L203-211)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

```

**File:** evm/src/apps/IntentGatewayV2.sol (L229-258)
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
