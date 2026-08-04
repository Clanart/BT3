## Finding

### Title
Excess native ETH sent to `EvmHost.dispatch()`/`fundRequest()` is never refunded to the caller and becomes permanently stuck - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept `msg.value` and pipe the entire amount into `swapETHForExactTokens{value: msg.value}(fee, ...)` to buy the exact `feeToken` amount needed. Uniswap V2's router refunds unspent ETH to whoever called the swap function — but that caller is `EvmHost` itself, not the original `_msgSender()`. None of these three functions ever forward that refunded ETH back out to the caller, so any overpayment silently accumulates in `EvmHost` with no recovery path.

### Finding Description
In `EvmHost.dispatch(DispatchPost memory post)`: [1](#0-0) 

the swap consumes `post.fee` worth of `feeToken` using up to `msg.value` of ETH, and any leftover ETH from `swapETHForExactTokens` is refunded by the Uniswap router to `msg.sender` of that call — i.e. to `EvmHost`, not to `_msgSender()`. The function then proceeds straight to building the commitment and emitting the event with no leftover-ETH accounting or refund step: [2](#0-1) 

The same pattern (swap on full `msg.value`, no refund) recurs in `dispatch(DispatchGet)`: [3](#0-2) 

and in `fundRequest()`: [4](#0-3) 

This is the exact analog of the M-28 bug class: the developer implicitly assumed the ETH sent would be fully consumed by the swap (i.e. that the amount is pre-calculated exactly), but `swapETHForExactTokens` only spends up to `post.fee` worth and refunds the rest — and here, unlike downstream app code, nothing captures or re-forwards that refund.

Contrast this with the app-layer code in `IntentGatewayV2.sol`, which correctly captures the swap's return value and forwards the remainder to `msg.sender`: [5](#0-4) 

and with `ExtrinsicIntents._fillCrossChain`, which caps the value sent to `dispatch()` to exactly `options.nativeDispatchFee` and refunds any remainder itself: [6](#0-5) 

However, `ExtrinsicIntents._cancelFromSource` and `_cancelFromDest` do **not** apply this pattern — they forward the entire `msg.value` straight into `IDispatcher(hostAddr).dispatch{value: msg.value}(request)`: [7](#0-6) [8](#0-7) 

Any user calling `cancelOrder`/`cancelFromSource`/`cancelFromDest` (or any other `IApp` calling `IDispatcher.dispatch`/`fundRequest` directly) with `msg.value` greater than the ETH actually required to buy `post.fee` worth of `feeToken` will have the excess permanently trapped inside `EvmHost`, since `EvmHost` has no sweep/withdraw function for native ETH accumulated this way (only `IERC20(feeToken()).safeTransfer` paths exist for relayer/timeout refunds, never native ETH).

### Impact Explanation
This is a direct, unconditional loss-of-funds bug reachable by any unprivileged user through a public entry point (`dispatch`, `fundRequest`, or any `IApp` such as `IntentGatewayV2`'s cancel flows that forward `msg.value` to `dispatch`). Overpaid native ETH is not refunded to the payer and there is no mechanism in `EvmHost` to retrieve it — it is effectively burned into the contract's balance, exactly matching the "funds sent for fees are not refunded... no way to retrieve these funds" impact described in M-28.

### Likelihood Explanation
High. Any caller who does not compute the exact required native-ETH amount for the swap (e.g. due to slippage/price movement between quote and execution, or simply sending a safety margin like `msg.value` = order.deadline-based estimate) will trigger this every time. The existing test suite even demonstrates awareness of this exact overpayment scenario at the `IntentGatewayV2` app layer (`testPlaceOrder_FeeSwap_RefundsExcessNativeToken`), showing the refund pattern is known and applied inconsistently — `EvmHost.dispatch`/`fundRequest` and the `_cancelFromSource`/`_cancelFromDest` cross-chain cancel paths omit it entirely.

### Recommendation
In `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, capture the `amounts[0]` (ETH actually spent) returned by `swapETHForExactTokens` and refund `msg.value - amounts[0]` back to `_msgSender()` before returning, mirroring the pattern already used in `IntentGatewayV2.placeOrder`. Additionally, update `ExtrinsicIntents._cancelFromSource` and `_cancelFromDest` to cap the value forwarded to `dispatch()` and refund any leftover `msg.value`, consistent with `_fillCrossChain`.

### Proof of Concept
1. A user calls `EvmHost.fundRequest(commitment, amount)` (or any `IApp` triggers `dispatch(DispatchPost)`/`dispatch(DispatchGet)`, e.g. via `IntentGatewayV2._cancelFromSource`) with `msg.value` deliberately set higher than what is needed to buy `amount`/`post.fee` worth of `feeToken` (e.g. due to price fluctuation buffer).
2. Internally, `swapETHForExactTokens{value: msg.value}(amount, path, address(this), block.timestamp)` spends only `amounts[0] < msg.value` and the Uniswap V2 router refunds `msg.value - amounts[0]` ETH to `msg.sender` of the swap call, which is `EvmHost`.
3. `fundRequest`/`dispatch` finish execution without forwarding any ETH back to the original caller.
4. The excess ETH now sits in `EvmHost`'s balance permanently; there is no function in `EvmHost.sol` that lets the original payer, or anyone, reclaim it.

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

**File:** evm/src/core/EvmHost.sol (L934-958)
```text
        // adjust the timeout
        uint64 timeoutTimestamp = post.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(post.timeout);
        PostRequest memory request = PostRequest({
            source: host(),
            dest: post.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            to: post.to,
            timeoutTimestamp: timeoutTimestamp,
            body: post.body
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
        emit PostRequestEvent({
            source: string(request.source),
            dest: string(request.dest),
            from: _msgSender(),
            to: abi.encodePacked(request.to),
            nonce: request.nonce,
            timeoutTimestamp: request.timeoutTimestamp,
            body: request.body,
            fee: post.fee
        });
```

**File:** evm/src/core/EvmHost.sol (L974-986)
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
