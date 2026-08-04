I found a clear local analog. Compare `EvmHost.dispatch(DispatchPost)` / `dispatch(DispatchGet)` / `fundRequest` in `evm/src/core/EvmHost.sol` against `IntentGatewayV2.placeOrder`/`fillOrder`, which explicitly track leftover `msgValue` and refund it to `msg.sender` [1](#0-0) . The `EvmHost` dispatch functions never do this.

### Title
Overpaid native ETH sent to `EvmHost.dispatch()`/`fundRequest()` is not refunded and becomes permanently stuck in the host contract - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` accept `msg.value` and swap the entire amount via `swapETHForExactTokens{value: msg.value}(fee, ...)`, but never capture or refund the leftover ETH that the router returns after satisfying the exact-output swap.

### Finding Description
In all three payable entrypoints, the full `msg.value` is forwarded to the Uniswap V2 router's `swapETHForExactTokens`: [2](#0-1) [3](#0-2) [4](#0-3) 

`swapETHForExactTokens` only needs to spend the amount required to buy `post.fee`/`get.fee`/`amount` fee-tokens; the standard Uniswap V2 router semantics refund any unused ETH to `msg.sender` of the swap call — which here is `EvmHost` (`address(this)`), not the original caller (`_msgSender()`). None of these three functions capture the router's return value or forward any residual ETH back to the caller. As a result, every wei of `msg.value` beyond the amount actually needed for the swap remains trapped in the `EvmHost` contract's balance, attributed to no specific user.

This is the same broken invariant described in the external report (`msg.value` not required/refunded to exactly match cost), but it is directly provable in this repo's own dispatch/fee-payment path — and the codebase's own `IntentGatewayV2` shows the correct pattern is well understood elsewhere (explicit `msgValue -= amounts[0]` tracking and refund), making its absence in `EvmHost` a real, exploitable regression rather than a design choice: [5](#0-4) 

### Impact Explanation
Any unprivileged user calling `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, or `fundRequest()` with `msg.value` greater than the ETH cost of the swap for the requested fee amount permanently loses the difference — it is swept into the `EvmHost` contract's own balance with no accounting entry tying it back to the payer, and no public function exists to reclaim it. Since these are the primary fee-payment entrypoints used by every app built on Hyperbridge (`IApp` integrators, bridged-token contracts, intents apps calling `dispatchWithFeeToken`/native dispatch), this is a direct, repeatable loss-of-funds bug reachable by any normal user with a single transaction, not requiring any privileged, relayer, or prover assumption.

### Likelihood Explanation
High. Any caller who doesn't compute the exact native cost via `quoteNative`/off-chain estimation (e.g., pads `msg.value` for safety margin, as commonly recommended in the docs' "1% buffer" guidance) will trigger this loss on every call. This is not a contrived edge case — it is the expected/likely behavior for any caller that overestimates gas/fee costs.

### Recommendation
Capture the `amounts` array returned by `swapETHForExactTokens` in all three functions and refund `msg.value - amounts[0]` back to `_msgSender()` (or `post.payer`), mirroring the pattern already implemented in `IntentGatewayV2.placeOrder`/`fillOrder`.

### Proof of Concept
1. Compute `feeToken` cost of `post.fee` via `quoteNative`, note it requires `X` wei of ETH.
2. Call `IDispatcher(host).dispatch{value: X * 2}(post)` (or any `msg.value > X`).
3. `swapETHForExactTokens{value: X*2}(post.fee, path, address(this), ...)` executes, spending only `X` wei and refunding `X` wei back to `EvmHost` itself (the caller of the router).
4. Observe `address(host).balance` increased by `X` wei with no corresponding storage entry crediting the original caller; the caller has no way to withdraw it back.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-368)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }

        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

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

**File:** evm/src/core/EvmHost.sol (L1031-1042)
```text
    function fundRequest(bytes32 commitment, uint256 amount) external payable notFrozen {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                amount, path, address(this), block.timestamp
            );
        } else {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), amount);
        }
```
