## Finding: Native fee overpayment gets permanently stuck in `EvmHost`/`IntentGatewayV2` instead of being refunded to the caller

### Title
Excess native ETH sent for fee-token swaps is silently locked in `EvmHost`/`IntentGatewayV2` instead of being refunded - (File: `evm/src/core/EvmHost.sol`, `evm/src/apps/IntentGatewayV2.sol`)

### Summary
The Balancer report's root cause is: a contract pulls the *maximum* amount a user is willing to spend, the downstream integration only consumes part of it, and the leftover stays trapped in the intermediary contract because nothing forwards it back to the depositor. The same pattern exists in Hyperbridge's native-fee-to-feeToken swap path used by `EvmHost.dispatch()` and `IntentGatewayV2.placeOrder()`.

### Finding Description
When a caller pays the dispatch fee in native token, `EvmHost.dispatch()` swaps the entire `msg.value` for an exact amount of fee tokens: [1](#0-0) 

`IUniswapV2Router02.swapETHForExactTokens` is called with `to = address(this)` (the `EvmHost`), and `msg.sender` from the router's perspective is `EvmHost` itself, not the original caller. The canonical Uniswap V2 router implementation refunds any unspent ETH (`msg.value - amountIn`) to `msg.sender` of the swap call — i.e., back to `EvmHost`, never to `_msgSender()` who originally called `dispatch()`. Since `EvmHost` never tracks its ETH balance before/after the swap and never forwards a refund to the caller, any native token sent above the exact amount required for `post.fee` is permanently absorbed by the `EvmHost` contract with no accounting or reclaim path.

The identical pattern exists in `IntentGatewayV2.placeOrder()`'s fee-escrow logic, which performs the same `swapETHForExactTokens{value: msgValue}(order.fees, path, address(this), block.timestamp)` call and likewise never refunds unspent ETH to `msg.sender`: [2](#0-1) 

This is structurally identical to the `BalancerRouter` bug: the contract pulls a "maximum" amount (`msg.value`), an external AMM decides how much of it is actually needed, and the difference is not guaranteed - nor coded - to return to the payer.

### Impact Explanation
Any user or integrating app that dispatches a request/order paying fees in native token and supplies `msg.value` even slightly larger than the exact swap requirement (which is nearly guaranteed in practice, since callers must estimate/quote the required native amount off-chain against a moving AMM price) permanently loses the difference — it becomes unrecoverable, non-withdrawable ETH sitting in `EvmHost`/`IntentGatewayV2`. This is direct loss of user funds through the intended, unprivileged, primary entrypoints (`dispatch()`, `placeOrder()`), matching the bounty's "stealing or loss of funds" category.

### Likelihood Explanation
High. Any caller quoting a native-fee amount ahead of a Uniswap V2 swap (via `quote()`/off-chain estimation) will almost always send an amount ≥ the exact on-chain requirement to account for slippage/price movement between quoting and execution — meaning dust loss occurs on essentially every native-fee dispatch/placeOrder call, not just an edge case.

### Recommendation
Snapshot `address(this).balance` before the swap and forward any residual ETH back to `_msgSender()` (in `EvmHost.dispatch()`) or to `msg.sender` (in `IntentGatewayV2.placeOrder()`) after the swap completes, mirroring the dust-refund pattern already used elsewhere in the codebase for ERC20 leftovers (e.g., `DustCollected` handling in `IntentGatewayV2`).

### Proof of Concept
1. Caller estimates required native amount to cover `post.fee` via a quote and calls `EvmHost.dispatch{value: quotedAmount}(post)` where `quotedAmount` includes reasonable slippage buffer.
2. `dispatch()` calls `swapETHForExactTokens{value: quotedAmount}(post.fee, path, address(this), block.timestamp)`.
3. The Uniswap V2 router only consumes `amountIn ≤ quotedAmount` and refunds `quotedAmount - amountIn` ETH to `msg.sender`, which is `EvmHost`.
4. `dispatch()` returns without ever checking or forwarding `address(this).balance` back to the caller; the refunded dust ETH remains stuck in `EvmHost` indefinitely. [1](#0-0)

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-482)
```text
        if (order.fees > 0) {
            // escrow fees
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```
