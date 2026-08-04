## Analysis

The seed bug in the external report is: an app forwards `msg.value` to pay a third-party (Wormhole) delivery fee, that third party refunds any overpayment back to the *forwarding contract*, and the forwarding contract has no mechanism to return that native-token overpayment to the actual depositor — so excess funds sent due to normal fee/quote fluctuation are either stuck or (in Wormhole's case) revert the whole call.

The direct local analog is in `EvmHost.dispatch()` for both `PostRequest` and `GetRequest` variants.

`EvmHost.dispatch(DispatchPost memory post)` and `EvmHost.dispatch(DispatchGet memory get)` accept native token as fee payment and swap it for the exact `feeToken()` amount required (`post.fee` / `get.fee`) via the configured Uniswap V2-compatible router, with `recipient = address(this)`: [1](#0-0) [2](#0-1) 

Unlike `IntentGatewayV2.placeOrder`/`fillOrder` and `ExtrinsicIntents`, which explicitly refund any unspent native token back to `msg.sender` after the same kind of fee swap: [3](#0-2) [4](#0-3) 

`EvmHost.dispatch()` has **no such refund path**. Its own docstring only addresses the underpayment case ("Will revert if enough native tokens are not provided") and never addresses overpayment.

The wrapper contracts used behind `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(...)` do refund unspent ETH — but to whichever address called them, which is `EvmHost` itself (since `EvmHost` is the direct caller, passing `recipient = address(this)` only for the output token leg): [5](#0-4) [6](#0-5) 

`EvmHost` does have a `receive()` function, so it will not revert on refund like `SpokeVault` — but this means the ETH refund is silently absorbed into `EvmHost`'s own balance instead of being returned to the actual dispatcher (the original caller of `dispatch()`). The `GnosisUniswapV2Wrapper` variant is worse: it converts the entire `msg.value` into the fee token and sends **all** of it to `msg.sender` (`EvmHost`) regardless of the requested `amountOut`, so any overpayment beyond `post.fee`/`get.fee` becomes untracked fee-token balance sitting in `EvmHost`, never credited to the depositor and never refundable: [7](#0-6) 

### Title
EvmHost.dispatch() (POST/GET) never refunds excess native-token fee payment, causing silent loss of user funds - (`evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)` and `EvmHost.dispatch(DispatchGet)` swap any supplied `msg.value` for the exact `feeToken()` amount required to pay for a cross-chain request, but never return unspent native token to the caller. Because the caller must estimate `msg.value` off-chain (relative to a fee-token amount that fluctuates with the Uniswap pool price/gas), any overestimate results in the excess ETH being permanently absorbed by the `EvmHost` contract instead of refunded to the depositor.

### Finding Description
In `dispatch(DispatchPost memory post)` and `dispatch(DispatchGet memory get)`, when `msg.value > 0`, the full value is forwarded to `swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)`. This function is designed to consume only what's needed to obtain `post.fee`/`get.fee` output tokens and refund the rest — but the refund target is whichever address called the router/wrapper, which is `EvmHost`, not the original `dispatch()` caller (`_msgSender()`). `EvmHost` accepts the refund silently via its `receive()` function, and there is no code path in `dispatch()` that forwards this leftover balance back to `_msgSender()`, unlike the equivalent logic implemented in `IntentGatewayV2.placeOrder`/`fillOrder` and `ExtrinsicIntents`, which explicitly perform `msg.sender.call{value: msgValue}("")` for unspent native tokens after the same kind of fee-swap operation.

### Impact Explanation
Any legitimate, unprivileged caller of `dispatch()` who supplies slightly more native token than the exact amount needed for the fee-token swap (which is expected given price/gas fluctuation between quote time and execution time) permanently loses that excess — it becomes stranded in `EvmHost`'s balance with no on-chain accounting tying it back to the depositor. Under the `GnosisUniswapV2Wrapper`, the loss is worse: the entire `msg.value` is unconditionally converted and handed to `EvmHost` regardless of the exact fee required, so any overpayment is fully absorbed with no path to reclaim it. This is a direct, unauthorized-by-omission loss of user funds through the ordinary use of a core public entry point.

### Likelihood Explanation
High. `dispatch()` is a routine, frequently invoked public/external entry point used by every app/relayer paying fees in native token. Overpayment is a common and expected occurrence any time the caller's off-chain price/fee quote doesn't exactly match the executed swap price, which happens naturally due to normal market movement between quote and execution — no malicious actor or special conditions are required.

### Recommendation
Mirror the pattern already used in `IntentGatewayV2` and `ExtrinsicIntents`: after the fee-token swap in `EvmHost.dispatch(DispatchPost)` and `dispatch(DispatchGet)`, compute the actual native token spent and refund any leftover balance directly to `_msgSender()` via a `.call{value: refund}("")`, reverting the transaction if the refund transfer fails. For `GnosisUniswapV2Wrapper`, cap the swapped/transferred amount to the requested `amountOut` and return the unused portion to the caller rather than converting and forwarding the full `msg.value`.

### Proof of Concept
1. Caller estimates the Uniswap price for `1 feeToken` fee and computes `msg.value = X` off-chain.
2. Pool price shifts slightly downward before the transaction executes (or caller intentionally pads the value for safety), so only `X - Δ` is actually needed to buy `1 feeToken`.
3. Caller invokes `EvmHost.dispatch(post)` with `value: X`.
4. Internally, `swapETHForExactTokens{value: X}(1e18, path, address(this), block.timestamp)` spends `X - Δ` and refunds `Δ` — but to `EvmHost` (the caller of the router/wrapper), not back to the original `dispatch()` caller.
5. `dispatch()` returns normally; the request is dispatched successfully, but `Δ` wei of the caller's native token is now stuck inside `EvmHost` with no function returning it to them.

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

**File:** evm/src/utils/uniswapv2/GnosisUniswapV2Wrapper.sol (L39-54)
```text
    function swapETHForExactTokens(uint256 amountOut, address[] calldata, address, uint256)
        external
        payable
        returns (uint256[] memory)
    {
        if (amountOut > msg.value) revert MsgValueLessThanExactAmount();

        (bool sent,) = WETH().call{value: msg.value}("");
        if (!sent) revert DepositFailed();

        IERC20(WETH()).safeTransfer(msg.sender, msg.value);

        uint256[] memory out = new uint256[](1);
        out[0] = msg.value;
        return out;
    }
```
