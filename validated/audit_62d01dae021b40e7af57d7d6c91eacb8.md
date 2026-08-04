### Title
Shared `CallDispatcher` residual balances can be swept and self-credited as escrow by any user - ([File: evm/src/apps/IntentGatewayV2.sol], [File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
The external report's core defect is: an arbitrary-length, caller-supplied "path" is used in full for actual execution, but a downstream accounting step only looks at a fixed, bounded subset of that path, so tokens produced/consumed outside that subset are neither validated nor consistently attributed. The same broken pattern exists in the `IntentGatewayV2` intents flow around the shared `CallDispatcher`: `_execute()` only sweeps back tokens that appear in `order.output.assets`, so any other token balance left on the dispatcher by arbitrary `order.output.call` calldata is stranded there indefinitely. Separately, `placeOrder()`'s predispatch phase sweeps the dispatcher's **entire current balance** of a token (not merely what the current order deposited) and credits that full amount straight into the caller's own escrow. Any unprivileged user can therefore target a token address with stranded dispatcher dust and pull it into their own order's escrow for free.

### Finding Description
`_execute()` in `evm/src/apps/intentsv2/IntentsBase.sol` dispatches solver-supplied arbitrary calldata (`order.output.call`) through the shared `CallDispatcher`, then sweeps residual balances back — but only for tokens present in `order.output.assets`: [1](#0-0) 

Just like the `path` argument in the report — where `zap()`/`_zap()` accept an arbitrary-length path but `enter()` only consumes `path[0]`/`path[1]` — `order.output.call` can route through or leave behind balances of tokens that are never enumerated in `order.output.assets`. Those tokens are never swept and remain sitting on the `CallDispatcher` contract, a single shared, stateless contract address (`_params.dispatcher`) used by every order and every user: [2](#0-1) 

Separately, `placeOrder()`'s predispatch phase measures "what this order received" by reading the dispatcher's **total** balance of a token — not an amount scoped to this specific caller/order — and sweeps all of it into the gateway, then credits it directly to escrow: [3](#0-2) [4](#0-3) 

Because `IERC20(token).balanceOf(dispatcher)` is read fresh at sweep time with no notion of "only tokens belonging to my deposit," any balance stranded there by an unrelated prior order's `_execute()` dust (or any other stray transfer to the dispatcher) is fair game for the next caller who names that same token in their own `order.inputs`. That caller does not need to actually deposit the token — the loop only checks `balance < requiredAmount` (revert if too little), and otherwise transfers whatever is there and credits `received` into their own escrow (`_orders[commitment][token] = reducedInputs[i].amount`).

### Impact Explanation
This is a fund-theft primitive matching the bounty's "stealing or loss of funds" and "wrong beneficiary or amount" categories: an unprivileged attacker can identify a token that was stranded on the shared `CallDispatcher` (from any other user's or solver's `order.output.call` residue), then call `placeOrder` naming that token, and have the entire stranded balance transferred into their own order's escrow at zero cost. This is not merely funds becoming inaccessible (a lock), it is funds moving to a beneficiary who never contributed them, directly violating the "bridged assets... must move exactly once and only to the rightful beneficiary and amount" invariant.

### Likelihood Explanation
Likelihood is moderate: it requires a prior transaction (any order fill with attached `order.output.call` that doesn't fully account for every token it touches, e.g., a swap route producing an intermediate/leftover token not listed in `order.output.assets`) to first leave a stranded balance on the dispatcher. Given `order.output.call` is fully attacker/solver controlled arbitrary calldata, an attacker can trivially engineer this seeding step themselves in one transaction and then immediately harvest it in a second `placeOrder` call — no relayer, prover, or privileged role is needed, and both steps are reachable through fully public entry points (`fillOrder` and `placeOrder`).

### Recommendation
- Scope the predispatch sweep in `placeOrder()` to the balance delta actually produced by *this* transaction's `predispatch.call`, not the dispatcher's total current balance (e.g., snapshot balance immediately before the predispatch dispatch and only sweep the increase).
- In `_execute()`, either require `order.output.call` to leave zero residual balance in all tokens it can possibly touch, or generalize the sweep to reconcile against a manifest of tokens the call is allowed to touch, so no token balance can be permanently orphaned on the shared dispatcher.
- Consider making the `CallDispatcher` per-order/ephemeral (e.g., minimal proxy per invocation) instead of a single shared, stateful contract, eliminating cross-order balance bleed entirely.

### Proof of Concept
1. Attacker (or any solver) fills an order whose `order.output.call` performs a swap/route that leaves a small residual balance of token `X` on the shared `CallDispatcher` (token `X` is not listed in that order's `order.output.assets`, so `_execute()`'s sweep loop never picks it up — see `IntentsBase.sol:447-473`).
2. Attacker calls `placeOrder` with `order.predispatch.assets = []`/minimal, `order.predispatch.call` a no-op, and `order.inputs = [{token: X, amount: <stranded balance>}]`.
3. In `placeOrder`'s Phase 1 (`IntentGatewayV2.sol:242-251`), `balance = IERC20(X).balanceOf(dispatcher)` reads the full stranded amount left by step 1; `transferCalls[0]` sweeps that entire balance to the gateway.
4. `received = IERC20(X).balanceOf(address(this)) - balancesBefore[0]` equals the stranded amount; since `received <= order.inputs[0].amount`, `order.inputs[0].amount = received` (`IntentGatewayV2.sol:270-275`).
5. Phase 3 credits `_orders[commitment][X] = reducedInputs[0].amount` (`IntentGatewayV2.sol:333-343`) — the attacker's escrow is now funded with tokens they never deposited, and they can subsequently cancel the order to redeem `X` directly to themselves.

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L242-280)
```text
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

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```
