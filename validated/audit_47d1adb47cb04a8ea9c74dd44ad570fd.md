Based on my research, I found a concrete local analog matching the report's core broken invariant: an execution-scoped permission (an ERC-20 `approve`) that is granted through a workflow-style contract but never expires or is revoked once that workflow step completes.

### Title
Dangling ERC-20 approvals left on the shared `CallDispatcher` via unrestricted order calldata - (File: `evm/src/utils/CallDispatcher.sol`, `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2` routes `predispatch`/`postdispatch` calldata through a single, shared, unowned `CallDispatcher` contract that blindly executes any attacker-supplied `Call[]` (`to.call{value}(data)`) with no restriction on target or selector. [1](#0-0) 
Because any order's calldata can include an `IERC20.approve(spender, amount)` call executed *as the `CallDispatcher`*, that allowance is written to the token contract's own storage and is never cleared by the gateway once the order finishes. This is confirmed directly by the test suite, which places an order whose postdispatch calldata approves the gateway for `1` wei of DAI and then asserts the allowance is still `1` on the `CallDispatcher` address after `fillOrder` completes: [2](#0-1) 

### Finding Description
`IntentGatewayV2.placeOrder`/`fillOrder` only sweep back the tokens *the order itself declares* (`order.inputs`, `predispatch.assets`, `output.assets`) after running the `CallDispatcher`: [3](#0-2) 
There is no mechanism that resets or revokes *arbitrary* approvals the calldata may have granted to *arbitrary* spenders for *arbitrary* tokens — the docs even describe this calldata path as intentionally open-ended ("Orders support arbitrary calldata execution... executed through the `CallDispatcher` contract"). [4](#0-3) 
This is structurally the same defect as the reported `Workflow`/`AccessController` bug: a shared, reusable execution contract is granted a standing permission as a side effect of legitimate use, and that permission is never scoped to expire when the calling operation finishes — it persists on-chain, callable by anyone who was granted it, independent of the order/workflow that created it.

### Impact Explanation
Because `CallDispatcher` is a single shared contract reused by every order from every user (and, per the `HyperFungibleToken` docs, is also the recipient/executor for cross-chain mint-then-swap flows), any address that is granted a dangling `approve()` on it retains a standing claim over whatever balance of that token the dispatcher ever holds in the future — for tokens that are not part of the current order's declared input/output set and therefore fall outside the gateway's post-execution sweep. An attacker who plants such an approval (via a cheap order with `predispatch.call`/`output.call` containing `approve(attacker, type(uint256).max)`) creates a persistent, unauthorized drain vector against a contract meant to be a stateless pass-through, matching the "unauthorized transaction/execution" and "loss of funds" impact classes.

### Likelihood Explanation
No privileged actor, relayer, or admin is required — any unprivileged user can place an order with calldata that plants the approval, since `CallDispatcher.dispatch` performs no allow-list or selector check on the calls it executes. [1](#0-0) 
The realistic drain window depends on the `CallDispatcher` later holding a non-zero balance of the exact approved token outside of the atomic transaction that created the approval (e.g., via another order's predispatch/postdispatch step routing the same token, or a cross-chain mint-and-swap flow that leaves dust). I was not able to fully verify within this investigation whether `HyperFungibleToken` and `IntentGatewayV2` are configured to share the exact same `CallDispatcher` instance in production deployments, or whether any current flow leaves a non-zero balance of a non-order-declared token on the dispatcher between transactions — this would need direct confirmation against deployment configuration and a live trace, which requires deeper access than the indexed context provides.

### Recommendation
Scope the `CallDispatcher`'s capability per invocation instead of leaving standing permissions: require the caller (`IntentGatewayV2`) to explicitly revoke any approvals it does not track immediately after `dispatch()` returns, or replace persistent `approve`-based spending with a pull-once/transient-approval pattern (e.g., `permit`-based single-use authorizations, or having `CallDispatcher` `approve(spender, 0)` for every token touched at the end of each `dispatch()` call) so no allowance survives past the order that created it.

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder` with a trivial 1-wei input and `output.call` (or `predispatch.call`) encoding a single `Call{ to: TOKEN, data: abi.encodeWithSelector(IERC20.approve.selector, attacker, type(uint256).max) }`.
2. `fillOrder`/`placeOrder` routes this through `ICallDispatcher(dispatcher).dispatch(...)`, executing the approve as the `CallDispatcher`, exactly as reproduced by the existing test that asserts the allowance persists after order completion: [5](#0-4) 
3. `TOKEN.allowance(CallDispatcher, attacker)` is now `type(uint256).max` permanently — no code path in `IntentGatewayV2` or `CallDispatcher` ever revokes it.
4. Whenever `CallDispatcher` subsequently holds a balance of `TOKEN` that falls outside the current order's declared/swept asset set, the attacker calls `TOKEN.transferFrom(CallDispatcher, attacker, balance)` directly, bypassing the gateway's escrow/settlement logic entirely.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-60)
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
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1520-1541)
```text
        // No escrow released and calldata not executed.
        assertEq(
            dai.allowance(address(intentGateway.params().dispatcher), address(intentGateway)),
            0,
            "Calldata should not execute on rejected partial fill"
        );

        // Full fill in a single transaction — calldata executes.
        vm.startPrank(solver);
        dai.approve(address(intentGateway), outputAmount);
        TokenInfo[] memory outputs2 = new TokenInfo[](1);
        outputs2[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: outputAmount});
        intentGateway.fillOrder(order, FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: outputs2}));
        vm.stopPrank();

        // Allowance should now be 1 (calldata executed)
        assertEq(
            dai.allowance(address(intentGateway.params().dispatcher), address(intentGateway)),
            1,
            "Calldata should execute after full fill"
        );
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L227-258)
```text
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

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L83-96)
```text
## Calldata

Orders support arbitrary calldata execution at two points in the lifecycle — before escrow (predispatch) and after fill (postdispatch). Both are executed through the `CallDispatcher` contract, which takes an ABI-encoded `Call[]` array:

```solidity
struct Call {
    address to;      // Target contract (must have code, reverts with NotContract otherwise)
    uint256 value;   // ETH to send with call
    bytes data;      // Calldata to execute
}
```

The `CallDispatcher` executes each call sequentially and reverts the entire batch if any call fails.

```
