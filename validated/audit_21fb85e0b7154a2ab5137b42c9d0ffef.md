## Analysis

The external report's core broken invariant: **when a contract accepts `msg.value` for a fee payment and internally swaps/consumes only part of it, any unused/leftover portion must be refunded to the original payer — not silently retained by an intermediate contract.**

Hyperbridge has this exact pattern in `evm/src/apps/intentsv2/ExtrinsicIntents.sol`. Every native-value-consuming function in the intents module (`placeOrder`, `_fillSameChain`, `_fillCrossChain`) explicitly tracks a local `msgValue` counter, decrements it as it is consumed, and refunds any remainder to `msg.sender` at the end: [1](#0-0) 

But the two cancellation entrypoints, `_cancelFromSource` and `_cancelFromDest`, break this pattern — they forward the entire `msg.value` straight into `IDispatcher(hostAddr).dispatch{value: msg.value}(...)` with **no leftover-refund logic afterward**: [2](#0-1) [3](#0-2) 

`EvmHost.dispatch(DispatchGet)` / `dispatch(DispatchPost)` only consume as much ETH as needed via `swapETHForExactTokens{value: msg.value}(fee, ...)`; Uniswap's router refunds unused ETH to whoever *called the router* — which is `EvmHost` itself, not the original canceller: [4](#0-3) 

So any excess native token a user attaches to `cancelOrder()` (routed to `_cancelFromSource`/`_cancelFromDest`) when a relayer fee is being paid natively ends up parked inside `EvmHost`'s balance rather than returned to the user — the exact "kept by the intermediate contract instead of refunded to the payer" bug class from the report.

### Title
Cross-chain order cancellation leaks unrefunded native fee overpayment into EvmHost - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`_cancelFromSource` and `_cancelFromDest` forward the caller's full `msg.value` into `IDispatcher.dispatch()` for native-fee payment but never track/refund any leftover, unlike every sibling function in the module (`placeOrder`, `_fillSameChain`, `_fillCrossChain`) which does.

### Finding Description
`IDispatcher.dispatch()` (implemented in `EvmHost.sol`) only spends as much native value as required to swap for the exact relayer fee via `swapETHForExactTokens{value: msg.value}(fee, ...)`. Uniswap's router refunds any unused ETH to the *caller of the router*, which in this call chain is `EvmHost`, not the user who initiated `cancelOrder`. All other native-payment call sites in `ExtrinsicIntents.sol`/`IntentGatewayV2.sol` are aware of this and explicitly capture the residual (`msgValue -= ...`) and send it back to `msg.sender` at the end of the function. `_cancelFromSource` (line 218) and `_cancelFromDest` (line 262) omit this step entirely — they pass `msg.value` wholesale and stop, so any amount above the actual fee is stranded inside `EvmHost`.

### Impact Explanation
Any unprivileged user calling the public `cancelOrder()` entrypoint who overestimates the relayer fee (which the docs and SDK explicitly encourage, since fee/gas estimation is imprecise and quoting on-chain is discouraged due to sandwich risk) permanently loses the overpaid native amount — it becomes indistinguishable protocol-owned ETH inside `EvmHost` with no code path returning it to the payer. This is a direct, unauthorized loss of user funds through a normal, unprivileged transaction, matching the bounty's "stealing or loss of funds" impact category.

### Likelihood Explanation
High: this triggers on ordinary usage of a public entrypoint (`cancelOrder`) by any user, with no malicious peer, relayer, or governance action required — simply attaching more native value than the actual swap consumes (a routine and encouraged safety margin) is sufficient.

### Recommendation
Apply the same `msgValue` tracking/refund pattern used in `placeOrder`/`_fillSameChain`/`_fillCrossChain` to `_cancelFromSource` and `_cancelFromDest`: capture `msg.value` in a local variable, pass only the required amount to `dispatch()`, and refund any remainder to `msg.sender` after the dispatch call.

### Proof of Concept
1. User calls `cancelOrder()` on an expired cross-chain order from the source chain, triggering `_cancelFromSource`.
2. User attaches `msg.value = X` where `X` is meaningfully larger than the actual native cost of the relayer fee (a normal safety margin against fee-token price volatility, as recommended practice).
3. `IDispatcher(hostAddr).dispatch{value: X}(request)` is called; internally `EvmHost.dispatch(DispatchGet)` swaps only enough ETH for `get.fee` via `swapETHForExactTokens{value: X}`, and the router refunds `X - amounts[0]` back to `EvmHost` (the router's caller), not to the user.
4. `_cancelFromSource` returns without any leftover check — the user's excess ETH is now permanently held in `EvmHost`'s balance with no mechanism to route it back to the user.
5. Repeat the assertion pattern from the existing test suite (e.g. `testPlaceOrder_FeeSwap_RefundsExcessNativeToken` in `evm/tests/foundry/IntentGatewayV2Test.sol`, which validates refunds for `placeOrder`) against `cancelOrder`/`_cancelFromSource` — it will fail, since no equivalent refund test or code path exists for the cancel flow.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L157-168)
```text
        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L217-223)
```text
        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L261-266)
```text
        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
```

**File:** evm/src/core/EvmHost.sol (L974-985)
```text
    function dispatch(DispatchGet memory get) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                get.fee, path, address(this), block.timestamp
            );
        } else if (get.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), get.fee);
        }
```
