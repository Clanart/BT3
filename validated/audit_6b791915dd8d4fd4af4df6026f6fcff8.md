## Analysis

**Core broken invariant (from the external report):** when a contract performs a native-token swap and only partially consumes the swap input, the leftover amount must be returned to the address that originally supplied it — not retained by an intermediate contract.

**Local analog found:** `EvmHost.dispatch(DispatchPost)` and `EvmHost.dispatch(DispatchGet)` perform exactly this pattern with Uniswap V2, but the leftover native token is *not* routed back to the caller.

### Title
Native fee-swap dust from `EvmHost.dispatch()` is refunded to the Host contract, not the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`dispatch(DispatchPost)` and `dispatch(DispatchGet)` both swap `msg.value` native tokens for the fee token via `swapETHForExactTokens`, but call the router directly from `EvmHost`, so any unused ETH the router refunds goes back to `EvmHost` itself rather than to the actual fee payer.

### Finding Description
When a caller sends native token to pay for a request, `EvmHost` swaps it for `feeToken` using Uniswap V2: [1](#0-0) 

`IUniswapV2Router02.swapETHForExactTokens(amountOut, path, to, deadline)` computes the exact input needed, and if `msg.value` exceeds that amount, the router refunds the difference to **its own caller** (`msg.sender` as seen by the router). Since `EvmHost` is the one invoking the router with `{value: msg.value}`, `EvmHost` is the router's `msg.sender` — meaning any dust ETH is sent back to `EvmHost`'s own balance, not to `_msgSender()` (the original transaction sender who funded the call). The same pattern repeats in `dispatch(DispatchGet)`: [2](#0-1) 

This is the exact analog of the LibSwap bug: `msg.value` (the "fromAmount") is not fully consumed by the swap, and the excess is left behind in the intermediary contract instead of being returned to the party that supplied it.

By contrast, the codebase's own `IntentGatewayV2` layer is careful to avoid this exact class of bug — it explicitly tracks and refunds unspent native tokens to the caller after every swap or fill: [3](#0-2) [4](#0-3) 

`IntentGatewayV2` avoids triggering the bug only because it always sends `dispatch{value: options.nativeDispatchFee}(...)` with an exact, pre-computed value rather than overpaying: [5](#0-4) 

But `EvmHost.dispatch()` is a public, permissionless entry point usable by any `IApp`/integrator, and the protocol's own documentation advertises that "unused native is refunded" for fee payments: [6](#0-5) 

Any caller relying on that documented behavior who sends `msg.value` with normal slippage/safety buffer (rather than the exact `getAmountsIn` value) will have their excess ETH permanently retained by `EvmHost` instead of refunded to them.

### Impact Explanation
Any unprivileged caller (a user, or any `IApp` contract built on Hyperbridge) that calls `dispatch()` with native token to cover the relayer fee and overestimates the required amount (which is normal, since exact `getAmountsIn` pricing is a moving target subject to slippage) permanently loses the excess ETH into `EvmHost`'s balance. There is no accounting entry crediting this dust to the payer, and no code path in `EvmHost.sol` that forwards it back — this is real, unrecoverable-by-the-user fund loss on a production entrypoint, matching the bounty's "stealing or loss of funds" category.

### Likelihood Explanation
High likelihood of triggering unintentionally: any integrator that doesn't compute the *exact* Uniswap `amountIn` before calling `dispatch{value: ...}` (which is the normal, expected usage pattern given typical slippage buffers) will lose ETH on every call. No malicious actor, relayer, or governance compromise is required — it is a direct consequence of normal usage of a public function.

### Recommendation
Track the ETH balance of `EvmHost` before and after the `swapETHForExactTokens` call (or capture the router's `amounts[0]` return value) and forward any unused `msg.value` back to `_msgSender()`, mirroring the refund pattern already used in `IntrinsicIntents._fillSameChain` and `ExtrinsicIntents._fillCrossChain`.

### Proof of Concept
1. An `IApp` contract calls `IDispatcher(host).dispatch{value: X}(post)` where `X` is intentionally padded above the exact swap cost to tolerate slippage (standard practice, and consistent with how `IntentGatewayV2`'s own `placeOrder` flow sizes native payments with headroom before calling internal swap helpers).
2. Inside `dispatch()`, `swapETHForExactTokens{value: X}(post.fee, path, address(this), block.timestamp)` is invoked by `EvmHost`.
3. The Uniswap V2 Router computes `amountIn < X`, performs the swap, and refunds `X - amountIn` to its caller — `EvmHost`.
4. `EvmHost` never forwards this refund to `_msgSender()`; the function returns normally with the dust sitting in `EvmHost`'s balance, permanently inaccessible to the original payer.

### Citations

**File:** evm/src/core/EvmHost.sol (L921-932)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L144-149)
```text
        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L157-162)
```text
        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L164-168)
```text
        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** docs/content/developers/evm/intent-gateway/placing-orders.mdx (L275-278)
```text
#### Native token

The placement transaction carries `nativeValue` extra wei, which the gateway swaps into the fee token through its configured router (unused native is refunded). Check the balance now; the placement step adds `nativeValue` to the transaction:

```
