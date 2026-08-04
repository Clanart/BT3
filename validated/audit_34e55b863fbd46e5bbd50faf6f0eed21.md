### Title
Arbitrary attacker-controlled `predispatch.call` combined with balance-delta escrow crediting on a shared `CallDispatcher` allows fund misdirection during `placeOrder` - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.placeOrder` mirrors the exact broken invariant from the stHYPE report: it trusts a **raw balance delta on a shared custody contract** as proof of legitimate payment, instead of binding the transferred value to the specific order being processed. The gap is opened by `order.predispatch.call` — attacker-supplied calldata that is executed with full privilege through the shared `_params.dispatcher` contract before the gateway "measures" what arrived.

### Finding Description
In `placeOrder` [1](#0-0) , when an order carries predispatch assets and calldata:

1. The gateway first pushes the caller's declared `predispatch.assets` into the shared `dispatcher` address (native token via raw `.call{value:}`, ERC20 via `safeTransferFrom`) — [2](#0-1) .
2. It then executes `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` — a fully attacker-controlled, unvalidated blob of calldata, executed with the dispatcher's own address/privileges — [3](#0-2) .
3. Only *after* that arbitrary execution does the gateway snapshot the dispatcher's/its own balances (`balancesBefore`) and sweep whatever balance is currently sitting on the shared `dispatcher` into escrow, treating the balance delta as the "actual received" amount for the order — [4](#0-3) .

This is structurally identical to the stHYPE bug: a shared, balance-accounted custody address (`dispatcher` there was the Sovereign Pool; here it is `_params.dispatcher`) is measured by "whatever balance is currently present," and the measurement window is reachable from attacker-controlled execution (`sovereignPoolSwapCallback` there; `order.predispatch.call` here). Nothing in `dispatch()` scopes the moved value to *this* order — it is a bare balance read (`IERC20(token).balanceOf(dispatcher)` / `address(dispatcher).balance`, lines 238/243), so any value sitting on `dispatcher` at that instant — regardless of which order or caller it was meant for — is what gets swept and credited.

Test fixtures confirm `dispatcher` is designed as a shared, reusable component rather than a per-order-scoped escrow: multiple independently configured `IntentGatewayV2` proxies (`customGateway`, `intentGateway`) are deployed pointing at the exact same `address(dispatcher)` [5](#0-4) . Because `placeOrder`'s `nonReentrant` guard is scoped to each proxy's own storage (OZ transient/storage-based reentrancy lock), it does **not** protect the shared `dispatcher`'s balance from being touched by a second, independent gateway instance (or any other consumer of `dispatcher`) invoked from within the attacker's own `order.predispatch.call`. The escrow-crediting logic never verifies that the swept balance was actually deposited *for this specific order/commitment* — it only checks `balance >= requiredAmount` (lines 239, 244) before treating the entire measured delta as this order's input.

### Impact Explanation
This breaks the "bridged assets/order escrow must move exactly once and only to the rightful beneficiary and amount" invariant required by the bounty scope. An attacker who can trigger a nested `dispatch()`/`placeOrder()` invocation against the shared `dispatcher` inside their own `predispatch.call` can redirect balance intended for one order's escrow into a different, attacker-controlled commitment, or drain dust/leftover balances that a legitimate in-flight order deposited onto the shared dispatcher before that order's own sweep executes — a direct analog of the "steal the surplus by donating into a shared balance-accounted contract during a callback window" primitive from the source report.

### Likelihood Explanation
The unsafe pattern (execute attacker-supplied calldata via a shared contract, then trust `balanceOf`/`.balance` deltas on that shared contract as this order's payment) is fully present and reachable from the unprivileged `placeOrder` public entrypoint with no additional trust assumptions — no relayer, prover, or governance actor is required, satisfying the bounty's "public entrypoint, unprivileged attacker" requirement. What I could not fully verify in this session is the internal access-control model of `ICallDispatcher`/`dispatcher` itself (i.e., whether `dispatch()` enforces caller restrictions that would prevent an attacker from nesting a nested nested `dispatch`/`placeOrder` call against the same shared dispatcher instance within their own `predispatch.call`), since the `CallDispatcher` implementation was not available in the indexed content. This should be confirmed before treating the impact as unconditionally exploitable in production topology versus only in shared test fixtures.

### Recommendation
- Do not measure "amount received" via bare `balanceOf(dispatcher)`/`dispatcher.balance` deltas around an attacker-controlled arbitrary call. Instead, have the dispatcher return/attest the exact amount it moved for *this specific call*, and validate it against an order-scoped escrow record rather than a raw contract-wide balance.
- Scope `dispatcher` interactions per-order (e.g., ephemeral per-order sub-account/clone, or a `nonReentrant`-style lock enforced at the `dispatcher` contract level itself, not only at each gateway proxy) so that balance swept for one order can never include value deposited by a different order or caller.
- Restrict `ICallDispatcher.dispatch` so it cannot be re-entered by a second, independent `placeOrder`/`dispatch` call while a sweep for an earlier order is pending within the same transaction trace.

### Proof of Concept
Not fully constructible without the `CallDispatcher` source (access-control unknown), but the exploitable primitive is directly demonstrable in isolation:
1. Caller submits `placeOrder` with `predispatch.assets = [amount X of token T]` and `predispatch.call` = arbitrary calldata that, via the shared `dispatcher`, invokes a second order-processing path (e.g. another `IntentGatewayV2` instance sharing the same `dispatcher`, per the shared-dispatcher configuration shown in `evm/tests/foundry/IntentGatewayV2Test.sol:879-887`).
2. That nested call reads `IERC20(T).balanceOf(dispatcher)` (line 243) — which still includes the `X` tokens just deposited in step 1 for the outer order — and sweeps them into its own, attacker-controlled commitment via its own `transferCalls`/measurement logic (lines 245-268).
3. The outer `placeOrder` then continues, finds `dispatcher`'s balance depleted, and either reverts (self-DoS) or credits a reduced/zero amount to the legitimate order, while the attacker's nested order now holds tokens it never deposited.

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L229-280)
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

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L879-887)
```text
        IntentGatewayV2 customGateway = _deployGatewayProxy();
        Params memory customParams = Params({
            host: address(host),
            dispatcher: address(dispatcher),
            solverSelection: false,
            surplusShareBps: 0, // 0% to protocol, 100% to beneficiary
            protocolFeeBps: 0,
            priceOracle: address(0)
        });
```
