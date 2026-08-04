## Analog Found: Unauthenticated `CallDispatcher.dispatch()` Lets Anyone Drain Value Transiting the Shared Dispatcher

### Title
Permissionless `CallDispatcher.dispatch()` allows theft of any ETH/ERC-20 balance transiently or residually held by the shared dispatcher - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
The external report's core invariant is "a contract that can hold value has no protected way to move that value out, so it either gets stuck or is exposed." In Hyperbridge, `CallDispatcher` is the shared, singleton contract that `IntentGatewayV2` (and its Tron variant) routes *all* predispatch and postdispatch order calldata through, and it explicitly accepts and forwards native value: [1](#0-0) 

`dispatch()` has **no access-control modifier whatsoever** — no `onlyOwner`, no `restrict(gateway)`, no reentrancy guard — despite unconditionally forwarding `address(this).balance`-derived value (`call.value`) and arbitrary calldata to attacker-chosen targets on command of *any* caller.

### Finding Description
`IntentGatewayV2.placeOrder` and `IntentsBase._execute`/`ExtrinsicIntents.fillOrder` both push assets into this same shared `dispatcher` address, then rely on a *second*, separate call to `dispatcher.dispatch(...)` to sweep the resulting balance back to the gateway: [2](#0-1) [3](#0-2) 

Because `_params.dispatcher` is one address shared by every order and every caller of the gateway, and `dispatch()` on that address is public with zero restriction on `msg.sender`, any address that holds a balance in the `CallDispatcher` at any point — whether from the `receive()` fallback (accepted from anyone, per the comment "Receive function to accept ETH transfers"), from an order's `predispatch.call` or `output.call` (both of which are **user/order-creator-controlled arbitrary calldata**, cryptographically bound into the order commitment but not restricted in *content*), or from any dust the "sweep back" step fails to fully clear — can have that balance redirected by *any third party* simply by calling `CallDispatcher.dispatch()` directly with a `Call[]` of their choosing. Nothing ties a `dispatch()` invocation to the gateway, to a specific commitment, or to the party that funded the transient balance.

The order-creator-controlled calldata is the sharpest exploitation vector: `order.output.call` (executed via `dispatcher.dispatch(order.output.call)` inside `_execute`) is defined by whoever places the order, yet it executes with the dispatcher's own authority against whatever balance the dispatcher happens to hold at that moment — including any balance from a concurrently in-flight predispatch/postdispatch leg of a *different* order that has not yet completed its own sweep call within the same block's mempool ordering, or simply any stray balance sent to `receive()`. Existing guards (`nonReentrant` on `placeOrder`/`fillOrder`, dust accounting via `DustCollected`) protect the *gateway* contract's own reentrancy and bookkeeping, but they provide **no protection at all on `CallDispatcher` itself**, since that contract's only defense (implicitly assumed by the design) is "nobody but the gateway will ever call `dispatch()`" — an assumption the code never enforces.

### Impact Explanation
This is a direct "stealing or loss of funds" / "unauthorized transaction or execution" primitive: any native ETH or ERC-20 balance that is, even transiently, present in the shared `CallDispatcher` (accepted unconditionally via its unrestricted `receive()`, or routed through it by the IntentGateway's predispatch/postdispatch flows) can be seized by an unrelated third party who simply calls `dispatch()` themselves with a `Call[]` sending that balance to their own address. Because the contract is shared across every order and every user of the gateway (and any future consumer of `_params.dispatcher`), a single stray unit of value sitting in it is up for grabs by anyone watching the chain, not just the gateway or the rightful order owner.

### Likelihood Explanation
The precondition is minimal and permissionless: the attacker needs no privileged role, no relayer/prover compromise, and no malicious peer — only a plain transaction calling `CallDispatcher.dispatch(bytes)` with an ABI-encoded `Call[]`. The `receive()` function actively invites third parties to leave balance in the contract, and the two-step "deposit-then-sweep" pattern used throughout `placeOrder`/`_execute` guarantees the dispatcher legitimately holds real user/solver funds during normal operation, which an unprivileged watcher can attempt to intercept.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a known, trusted caller (e.g., an immutable `gateway`/`host` address set at construction, or a `restrict(msg.sender == authorizedCaller)` modifier), or redesign it so it is deployed per-call/ephemeral rather than as one address shared indefinitely across every order and every gateway instance. At minimum, add a reentrancy guard and reject any `dispatch()` call whose caller is not the registered `IntentGatewayV2`/`IntentsBase` contract that owns the current escrow flow.

### Proof of Concept
1. Any order creator places an order via `IntentGatewayV2.placeOrder` with `order.output.call` (a postdispatch call executed later via the shared `dispatcher`) crafted to call `CallDispatcher.dispatch()` again, targeting whatever token/ETH balance the dispatcher currently holds and redirecting it to an attacker address, instead of only performing the intended DeFi routing.
2. Alternatively/simpler: an attacker sends ETH directly to `CallDispatcher`'s `receive()` (or waits for any transient balance from a normal `placeOrder`/`_execute` predispatch/postdispatch step), then in a following transaction calls `CallDispatcher.dispatch(encoded)` directly with `Call[]{ to: attacker, value: dispatcher.balance, data: "" }`.
3. Since `dispatch()` performs no `msg.sender` check, the call succeeds and the balance is transferred to the attacker, with no relationship enforced between the caller and the party who funded that balance. [4](#0-3)

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
