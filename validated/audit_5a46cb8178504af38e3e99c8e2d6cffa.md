### Title
Excess native-token payment on `dispatch()`/`fundRequest()` is refunded to `EvmHost` instead of the caller and becomes permanently stuck - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)` and `fundRequest()` are `payable` and, when `msg.value > 0`, forward the *entire* `msg.value` to a Uniswap-V2-style router's `swapETHForExactTokens` to buy an exact amount of fee tokens. [1](#0-0) [2](#0-1) [3](#0-2) 

None of these three functions check or use the return value of `swapETHForExactTokens`, and none of them forward any leftover ETH back to `_msgSender()`.

### Finding Description
`swapETHForExactTokens` only ever consumes the amount actually required to buy the exact output (`post.fee` / `get.fee` / `amount`); any ETH sent in excess of that requirement is refunded by the router/wrapper — but that refund goes to `msg.sender` **of the router call**, which is `EvmHost` itself, not the original user who called `dispatch()`/`fundRequest()`.

This is directly visible in the wrapper implementations used by the protocol:
- `UniV3UniswapV2Wrapper.swapETHForExactTokens` explicitly refunds the unspent ETH to `msg.sender` (the caller of the wrapper, i.e. `EvmHost`): [4](#0-3) 
- `UniV4UniswapV2Wrapper.swapETHForExactTokens` does the same: [5](#0-4) 
- The canonical `UniswapV2Router02.swapETHForExactTokens` behaves identically (refunds dust ETH to `msg.sender` of the call).

Because `EvmHost` is the direct caller of the router/wrapper, the refunded ETH lands on `EvmHost`'s own balance rather than being forwarded to `_msgSender()` (the end user). `EvmHost.sol` contains no logic after the `swapETHForExactTokens{value: msg.value}(...)` call to capture and re-forward this refund, and no user-facing withdraw/rescue function exists to later reclaim it — the same class of contract-level custody flaw described in the external report (`payable` entrypoint accepts more ETH than it can use, and the excess has no way back to the sender).

Contrast this with `IntentGatewayV2.placeOrder`, which explicitly implements the correct pattern — tracking `msgValue` consumption and refunding any unspent native token to `msg.sender` at the end of the function: [6](#0-5) 

`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` lack this equivalent refund step entirely.

### Impact Explanation
Any unprivileged user who calls `dispatch()` (POST or GET) or `fundRequest()` with `msg.value` greater than the exact ETH-equivalent cost of the requested fee will have the difference silently swept into `EvmHost`'s balance with no path to recovery — a direct, permanent loss of user funds through a normal, permissionless entrypoint. Since dApps/SDKs commonly compute `msg.value` with a slippage/buffer margin (a very common pattern for swap-based payments to tolerate price movement between quote and execution), overpayment is a realistic, not just accidental, occurrence, and the lost amount can be non-trivial in aggregate across many dispatches.

### Likelihood Explanation
High. This does not require any malicious peer, relayer, prover, or admin — a normal user calling the public `dispatch()`/`fundRequest()` functions with any reasonable ETH buffer above the exact required fee triggers the loss unconditionally. `IntentGatewayV2` in the very same repo demonstrates that the team is aware overpayment-refund handling is necessary for native-token flows, making its absence in `EvmHost` a genuine oversight rather than an intentional design choice.

### Recommendation
After the `swapETHForExactTokens{value: msg.value}(...)` calls in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, capture the actual ETH spent (via the returned `amounts[0]`, matching how `UniV3UniswapV2Wrapper`/`UniV4UniswapV2Wrapper` report `spent`), compute `msg.value - spent`, and refund that difference to `_msgSender()`, mirroring the pattern already used in `IntentGatewayV2.placeOrder`.

### Proof of Concept
1. User calls `EvmHost.dispatch(DispatchPost{ fee: 100, ... })` with `msg.value = 1 ether` (e.g., to cover expected slippage on the ETH→feeToken swap), while the actual ETH needed to buy `100` fee tokens is only `0.5 ether`.
2. `EvmHost` calls `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: 1 ether}(100, path, address(this), block.timestamp)`. [1](#0-0) 
3. The router spends `0.5 ether`, buys exactly `100` fee tokens for `EvmHost`, and refunds the remaining `0.5 ether` to `msg.sender` of the swap call — which is `EvmHost`, not the original user.
4. `EvmHost.dispatch` continues without inspecting the swap's return value or forwarding any refund; the `0.5 ether` remains permanently on `EvmHost`'s balance with no user-facing withdrawal mechanism.

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

**File:** evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol (L143-149)
```text
        if (spent < msg.value) {
            uint256 refund = msg.value - spent;
            IWETH(weth).withdraw(refund);

            (bool success,) = msg.sender.call{value: refund}("");
            if (!success) revert RefundFailed();
        }
```

**File:** evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol (L91-96)
```text
        uint256 refundETH = address(this).balance - balanceBefore;

        if (refundETH > 0) {
            (bool success,) = msg.sender.call{value: refundETH}("");
            require(success, "ETH refund failed");
        }
```

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
