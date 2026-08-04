## Analysis

The external report's core broken invariant is: **an endpoint blindly forwards an unauthenticated, unvalidated payload (arbitrary path/method/headers/body) into a privileged execution sink, with no check on caller identity or on what is actually being executed.**

The direct local analog is `CallDispatcher.dispatch(bytes)` in `evm/src/utils/CallDispatcher.sol`. It is a single, shared, unauthenticated contract used by `IntentGatewayV2`, `HyperFungibleToken`, `WrappedHyperFungibleToken`, and other apps as the generic "execute this batch of arbitrary calls" primitive: [1](#0-0) 

`dispatch()` has **no access-control modifier** — any address can call it directly — and it only checks that `call.to` has code, never who the caller is, never what protocol invariant the calls belong to, and it holds no state of its own to bind a call batch to a specific order/message. This is functionally identical to the reported `router-api` proxy: "no input validation, no path filtering, no method restrictions, no authentication," just raw forwarding of attacker-controlled targets/value/calldata to `.call{value: call.value}(call.data)`.

### Where this becomes exploitable

`IntentGatewayV2` escrows order inputs into the shared `dispatcher` (the `CallDispatcher`) *before* invoking `dispatch()` to run the order's `predispatch` calls: [2](#0-1) 

The loop transfers each input asset to `dispatcher` one at a time — native ETH via a raw `.call{value: amount}("")`, ERC-20 via `safeTransferFrom`. Because `order.inputs` (including the token address) is attacker-supplied at order-placement time, an attacker can place an order whose non-first input token is a malicious ERC-20 with a transfer hook. When `safeTransferFrom` invokes that hook, execution transfers to attacker-controlled code *while a prior loop iteration's native ETH (or another already-escrowed ERC-20) is already sitting in `dispatcher`'s balance* — before `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` ever runs.

Since `CallDispatcher.dispatch()` has no access restriction, the attacker's hook can call it directly (this does **not** reenter `IntentGatewayV2`, so the gateway's `nonReentrant` guards do not cover it) with a `Call[]` that sends the dispatcher's already-held native ETH/ERC-20 balance to an attacker address, stealing the escrow before the legitimate predispatch/sweep flow executes.

The same unauthenticated-forwarding weakness exists wherever `CallDispatcher` is used as the execution sink for cross-chain calldata (`HyperFungibleToken.onAccept`, `WrappedHyperFungibleToken.onAccept`, `IntentsBase._execute`) — all of these transiently move value into the shared, callerless `dispatch()` sink.

### Title
Unauthenticated `CallDispatcher.dispatch()` allows theft of transiently-escrowed funds via malicious-token reentrancy - (File: evm/src/utils/CallDispatcher.sol, evm/src/apps/IntentGatewayV2.sol)

### Summary
`CallDispatcher.dispatch()` is a public, unauthenticated, generic "execute arbitrary calls" endpoint shared across all Hyperbridge EVM apps. `IntentGatewayV2.placeOrder` moves order inputs into `CallDispatcher` sequentially before calling `dispatch()` on the order's `predispatch` calls. Because ERC-20 transfers can invoke attacker-controlled hooks, and `CallDispatcher.dispatch()` never checks the caller or binds a call batch to a specific order, an attacker-supplied malicious input token can hook mid-`safeTransferFrom` and call `dispatch()` directly to redirect already-escrowed native ETH/tokens sitting in `CallDispatcher` to an arbitrary address.

### Finding Description
`CallDispatcher.dispatch(bytes memory encoded)` decodes an ABI-encoded `Call[]` and executes each `to.call{value}(data)` with only an `extcodesize(to) != 0` check — no `msg.sender` restriction of any kind: [3](#0-2) 

`IntentGatewayV2.placeOrder`'s predispatch flow funds `dispatcher` iteratively across multiple inputs (native ETH via raw call, ERC-20 via `safeTransferFrom`) before it ever calls `dispatch()`: [2](#0-1) 

Because `order.inputs` (including which ERC-20 contracts are used) is chosen by the order creator, a malicious token contract placed as one of the inputs can execute attacker code during its own `transferFrom` call. At that point in the loop, native ETH or a previously-transferred ERC-20 from an earlier input may already sit in `dispatcher`'s balance. The attacker's hook calls `CallDispatcher.dispatch()` directly — a completely separate external call, not a reentry into `IntentGatewayV2` — so `IntentGatewayV2`'s `nonReentrant` modifiers (confirmed present in the file) do not protect this path at all. The attacker's `dispatch()` call sweeps the already-escrowed balance to an address of their choosing.

### Impact Explanation
This is a direct fund-loss primitive on a production bridge contract: user assets that are supposed to be locked into an order's escrow can instead be redirected to an attacker before the order is even finalized, because the shared execution sink has zero caller authentication and no linkage back to a specific order/commitment — matching "stealing or loss of funds" / "unauthorized transaction or execution" in the bounty scope.

### Likelihood Explanation
Medium-to-high: the attacker fully controls the malicious order they place (including which token contracts are listed as inputs), needs no relayer/prover/admin cooperation, and the vulnerable window (multi-input escrow loop before `dispatch()` of predispatch calls) exists on every `placeOrder` call that has more than one input asset with predispatch calldata.

### Recommendation
- Restrict `CallDispatcher.dispatch()` to a caller allowlist (e.g., only the specific app contracts authorized to use it), or make each app deploy/own its own `CallDispatcher` instance instead of sharing one global instance.
- Alternatively, bind each `dispatch()` invocation to a single-use, pre-committed call-hash so a stray/attacker call to `dispatch()` cannot execute an arbitrary, unrelated `Call[]`.
- In `IntentGatewayV2`, transfer all inputs for an order atomically (or use a pull-based accounting model) so no unspent balance is ever left sitting in `dispatcher` while token hooks can still run.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC-20 whose `transferFrom` calls back into `CallDispatcher.dispatch()` with a `Call[]` that transfers `address(this).balance` (or the ERC-20 balance) from `CallDispatcher` to the attacker.
2. Attacker calls `IntentGatewayV2.placeOrder` with `order.inputs = [{token: address(0), amount: X}, {token: EvilToken, amount: Y}]` and a non-empty `predispatch.call`.
3. During the input-escrow loop: the native-ETH transfer to `dispatcher` executes first, funding `dispatcher` with `X` wei; then `IERC20(EvilToken).safeTransferFrom(...)` is called, triggering `EvilToken`'s hook.
4. The hook calls `CallDispatcher.dispatch(maliciousCalls)` directly — unauthenticated — draining the `X` wei already sitting in `CallDispatcher` to the attacker's address, before `IntentGatewayV2`'s own `dispatch(order.predispatch.call)` and sweep logic ever run.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L39-62)
```text
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

**File:** evm/src/apps/IntentGatewayV2.sol (L211-227)
```text

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
