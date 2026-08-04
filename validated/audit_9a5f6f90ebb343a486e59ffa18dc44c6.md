## Finding: Excess Native Token Not Refunded in `EvmHost.dispatch()` / `fundRequest()`

The exact bug class from the external report — payable fee functions that don't refund unused `msg.value` — has a real local analog in `EvmHost.sol`. Notably, the sibling contracts `IntentGatewayV2` and `ExtrinsicIntents` explicitly fixed this pattern, which highlights that `EvmHost` was missed.

### Title
Unrefunded Excess Native Token Permanently Locked in `EvmHost.dispatch()`/`fundRequest()` - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` accept `msg.value` and forward it entirely to `swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)`, discarding the returned `amounts` array without ever comparing it to `msg.value` or refunding the difference to the caller. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`swapETHForExactTokens` (standard UniswapV2Router02 semantics, referenced by the same ABI used throughout this repo) only guarantees the exact `amountOut` of tokens; any unspent ETH beyond the amount actually needed is refunded — but that refund is sent to `msg.sender` of the swap call, which here is `EvmHost` itself (since `EvmHost` is the direct caller of the router), not the original external caller who supplied `msg.value` to `dispatch()`/`fundRequest()`. [4](#0-3) 

Because `EvmHost` never captures the returned `amounts[0]` (actual ETH spent) and never forwards the difference (`msg.value - amounts[0]`) back to `_msgSender()`, any overpayment of native token is permanently stranded inside `EvmHost`. This is precisely the invariant the external report flags: a payable function that swaps/spends only part of `msg.value` but has no refund path for the remainder.

The codebase demonstrates the developers are aware of and actively guard against this exact pattern elsewhere — `IntentGatewayV2.placeOrder`, `ExtrinsicIntents.fillOrder`, and the `UniV3UniswapV2Wrapper`/`UniV4UniswapV2Wrapper` all explicitly track leftover `msgValue` from the swap call and refund it via `msg.sender.call{value: ...}("")`: [5](#0-4) [6](#0-5) [7](#0-6) 

`EvmHost.sol`'s own `dispatch`/`fundRequest` functions are the odd ones out — they are documented as the canonical, most-called entrypoints for every app built on Hyperbridge (per `IDispatcher` docs and `HyperApp` usage patterns), yet they lack the same refund logic. [8](#0-7) 

### Impact Explanation
Any unprivileged user or application contract calling `IDispatcher(host).dispatch{value: msg.value}(post)` — the exact pattern shown in Hyperbridge's own developer documentation — with `msg.value` even slightly greater than the fee-token cost of `post.fee`/`get.fee`/`amount` will have the excess permanently trapped in `EvmHost`. There is no refund mechanism and no user-facing withdrawal/rescue function found for stray native balance in `EvmHost.sol`. This is a direct, unconditional loss of user funds triggered through the most common, unprivileged, public entrypoints of the protocol (`dispatch`, `fundRequest`), matching the "stealing or loss of funds" impact category.

### Likelihood Explanation
High. `dispatch{value: msg.value}(post)` is the exact pattern documented for every `HyperApp` integrator sending native-token-funded POST/GET requests, and slippage/price movement in the Uniswap V2 pool between fee-estimation (off-chain quote) and execution routinely causes `amounts[0] < msg.value` when callers pad `msg.value` for safety margin (as the docs themselves recommend). No malicious actor, relayer, or admin is required — a normal user or integrating contract following the documented usage pattern triggers the loss.

### Recommendation
In `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, capture the `amounts` return value from `swapETHForExactTokens` and refund the difference (`msg.value - amounts[0]`) to `_msgSender()`, mirroring the pattern already used in `IntentGatewayV2.placeOrder` and `ExtrinsicIntents.fillOrder`.

### Proof of Concept
1. An integrator or user calls `IDispatcher(host).dispatch{value: 1 ether}(post)` where `post.fee` only requires `0.1 ether` worth of native token to swap into `feeToken` (a common scenario given users are told to pad for slippage, and fee-token prices are stable/predictable).
2. `EvmHost.dispatch()` forwards the full `1 ether` to `swapETHForExactTokens{value: 1 ether}(post.fee, path, address(this), block.timestamp)`.
3. The router spends only ~`0.1 ether`, refunding ~`0.9 ether` to `msg.sender` of the swap call — which is `EvmHost`, not the original caller.
4. `EvmHost.dispatch()` returns without checking the swap's returned `amounts[0]` or transferring any refund; the ~`0.9 ether` remains stuck in `EvmHost`'s balance indefinitely, with no function in `EvmHost.sol` to reclaim or return it to the original sender.

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

**File:** sdk/packages/sdk/src/abis/uniswapRouterV2.ts (L635-668)
```typescript
	{
		inputs: [
			{
				internalType: "uint256",
				name: "amountOut",
				type: "uint256",
			},
			{
				internalType: "address[]",
				name: "path",
				type: "address[]",
			},
			{
				internalType: "address",
				name: "to",
				type: "address",
			},
			{
				internalType: "uint256",
				name: "deadline",
				type: "uint256",
			},
		],
		name: "swapETHForExactTokens",
		outputs: [
			{
				internalType: "uint256[]",
				name: "amounts",
				type: "uint256[]",
			},
		],
		stateMutability: "payable",
		type: "function",
	},
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

**File:** evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol (L83-96)
```text
        // Snapshot standing balance (excluding inbound msg.value) so the refund is the swap-call delta only,
        // immune to any ETH that lands on the wrapper from outside the router (e.g., selfdestruct, coinbase).
        uint256 balanceBefore = address(this).balance - msg.value;

        IUniversalRouter(_params.universalRouter).execute{value: msg.value}(
            abi.encodePacked(bytes1(uint8(Commands.V4_SWAP))), inputs, deadline
        );

        uint256 refundETH = address(this).balance - balanceBefore;

        if (refundETH > 0) {
            (bool success,) = msg.sender.call{value: refundETH}("");
            require(success, "ETH refund failed");
        }
```

**File:** sdk/packages/core/contracts/interfaces/IDispatcher.sol (L121-146)
```text
     * @notice Payment for the request can be made with either the native token or the IHost.feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the IHost.feeToken.
     *
     * @param request - post request
     * @return commitment - the request commitment
     */
    function dispatch(DispatchPost memory request) external payable returns (bytes32 commitment);

    /**
     * @dev Dispatch a GET request to Hyperbridge
     *
     * @notice Payment for the request can be made with either the native token or the IHost.feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the IHost.feeToken.
     *
     * @param request - get request
     * @return commitment - the request commitment
     */
    function dispatch(DispatchGet memory request) external payable returns (bytes32 commitment);
```
