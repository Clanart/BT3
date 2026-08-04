### Title
`CallDispatcher.dispatch()` has no caller restriction and lets anyone drain any balance it is holding, mirroring the H‑13 unchecked-external-call pattern - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`GiantSavETHVaultPool.withdrawDETH()` was vulnerable because it made external calls to user-supplied contract addresses without whitelisting them and then moved funds based on the contract's own post-call balance, with no reentrancy defense. The local analog is `CallDispatcher.dispatch()`, the shared, ownerless utility contract that `IntentGatewayV2` (both `evm/src/apps/IntentGatewayV2.sol` and the Tron variant) routes escrow, predispatch, and output-call funds through. `dispatch()` is `external` with **no caller check whatsoever** and executes attacker-supplied `Call[]` targets using the dispatcher's *own* balance for `value`, exactly the "no whitelist check for user provided addresses" defect from the report.

### Finding Description
`CallDispatcher.dispatch()`: [1](#0-0) 

only checks that `call.to` has code (`extcodesize`) — it never checks `msg.sender`, never restricts `call.to`, and imposes no reentrancy guard. Anyone can call this function directly, at any time, supplying arbitrary `Call[]` structs, and it will forward `call.value` (drawn from `CallDispatcher`'s own ETH balance) and arbitrary calldata to any address.

`IntentGatewayV2` treats this contract as a trusted intermediate custodian of user funds during multiple flows:

- In `placeOrder`'s predispatch branch, tokens/ETH are sent to `dispatcher`, then `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` — **calldata fully controlled by the order creator** — is executed by the dispatcher, followed by a second `dispatch()` call to sweep the resulting balance back, using balance snapshots (`balancesBefore`) to compute `received`: [2](#0-1) 

- In `_execute` (used by same-chain and cross-chain fills), the solver-supplied `order.output.call` is dispatched through the same singleton `CallDispatcher`, after which the function reads the dispatcher's *live* balance (`dispatcher.balance` / `IERC20(token).balanceOf(dispatcher)`) directly — not a pre/post diff scoped to this call — and sweeps whatever it finds back to the gateway as "dust": [3](#0-2) 

Because `dispatch()` itself has no whitelist/ownership gate and no reentrancy lock, the same primitive from H‑13 applies here: an unprivileged actor can call `CallDispatcher.dispatch()` directly (bypassing `IntentGatewayV2` entirely) and instruct it to move out whatever native or ERC-20 balance is currently sitting on the dispatcher, to any address they choose. `IntentGatewayV2` relies on this contract holding funds transiently and safely between its own back-to-back `dispatch()` invocations, but nothing on-chain enforces that only `IntentGatewayV2` (or the party who deposited) may trigger `dispatch()` — the guard that exists (`nonReentrant` via `ReentrancyGuardTransient`) is scoped to `IntentGatewayV2`'s own storage/transient slot and does not protect the `CallDispatcher` singleton or block a second, independent top-level call to `dispatch()`.

### Impact Explanation
Any value that ends up resting on `CallDispatcher` — dust from a partially executed predispatch/output call, native ETH sent to it via its `receive()` (explicitly present "to accept ... swept balances"), or tokens left after a `Call` in the middle of a multi-step flow but before the gateway's own follow-up sweep call executes — is claimable by the first caller of `dispatch()`, with zero authorization check. This is unauthorized transaction/execution and direct loss of funds for whoever it belonged to (the gateway's dust pool, or a user/solver whose flow left a transient balance on this contract), matching the bounty's accepted impact categories (stealing/loss of funds, unauthorized execution).

### Likelihood Explanation
`dispatch()` is a plain external function with no modifiers, callable by anyone at any block, and the contract's own documentation/design (accepting and forwarding arbitrary `Call[]`) confirms it is meant to be triggered by `IntentGatewayV2`'s dispatcher role but is not restricted to it. Any transaction ordering that leaves the `CallDispatcher` briefly holding value (a normal, expected pattern per `IntentGatewayV2`'s multi-step predispatch/sweep design) creates a window that requires no privileged actor, relayer, or governance — only calling `dispatch()` first.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a whitelisted/authorized caller (e.g., `onlyOwner`/`onlyGateway` set at construction, or a per-call access-controlled variant scoped to the invoking `IntentGatewayV2` instance), and/or make it non-reentrant. Do not rely on "whatever balance is currently on the dispatcher" as the sweep/dust source in `_execute` and `placeOrder`; instead compute strict per-call balance deltas under an access-controlled dispatcher, or use a per-order ephemeral executor instead of one shared singleton.

### Proof of Concept
1. Observe that `CallDispatcher` is deployed once and referenced by `_params.dispatcher` in `IntentGatewayV2`.
2. Any actor sends `CallDispatcher.dispatch(encodedCalls)` directly (not through `IntentGatewayV2`) where `encodedCalls` decodes to `Call[]` with `to = attacker`, `value = address(CallDispatcher).balance` (or an ERC-20 `transfer` call moving `IERC20(token).balanceOf(dispatcher)` to `attacker`).
3. Because `dispatch()` never checks `msg.sender`, the call succeeds and moves out whatever balance `CallDispatcher` is holding at that moment — including balances legitimately in transit as part of an `IntentGatewayV2.placeOrder` predispatch flow or an `_execute` output-call sweep, if the attacker's transaction lands between the gateway's deposit-to-dispatcher call and its own sweep-back call (e.g., via a reentrant call embedded in the attacker's own `order.predispatch.call`/`order.output.call`, which is dispatched with dispatcher as the executing context and can itself invoke `dispatch()` again before the outer flow's sweep step runs). [1](#0-0) [4](#0-3) [3](#0-2)

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-480)
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
```
