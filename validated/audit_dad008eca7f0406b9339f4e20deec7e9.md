### Title
`EvmHost.dispatch()`/`fundRequest()` swap excess native ETH is refunded to the Host contract itself, not the caller, permanently locking overpayments - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept native ETH via `msg.value` and swap it for an exact amount of fee token using `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(...)`. Unlike the app-layer contracts in this same repo (`IntentGatewayV2.sol`, `IntrinsicIntents.sol`, `ExtrinsicIntents.sol`), which explicitly compute leftover `msgValue` after the swap and forward it back to `msg.sender`, `EvmHost` never checks for or refunds any leftover ETH after the swap call. This is the direct analog of the reported `presaleMint()` bug: the function accepts more ETH than required but does not return the excess to the payer.

### Finding Description
In `EvmHost.sol`, the three payable entrypoints all follow the same pattern: [1](#0-0) [2](#0-1) [3](#0-2) 

The Uniswap V2 canonical implementation of `swapETHForExactTokens` computes the required input, executes the swap, and refunds any unused ETH via `TransferHelper.safeTransferETH(msg.sender, msg.value - amounts[0])`. Critically, `msg.sender` in that refund is whoever called the router — which here is `EvmHost` itself, since `EvmHost` invokes the router with `{value: msg.value}`. The refund therefore lands back in `EvmHost`'s own balance, not the original caller who overpaid. `EvmHost` has no `receive()`/fallback function to accept stray ETH gracefully and, more importantly, no code path that forwards or accounts for that returned dust to the original `_msgSender()`/`post.payer`/`get.payer`.

This directly contrasts with the pattern already established elsewhere in the same repository, where the developers are clearly aware of this exact class of bug and defensively refund unspent native value to the caller after an identical `swapETHForExactTokens` call: [4](#0-3) [5](#0-4) 

`EvmHost.dispatch()` and `fundRequest()` lack this exact same "compute delta / refund unspent" step, meaning any user who overestimates the ETH needed to cover `post.fee`/`get.fee`/`amount` (a common occurrence since gas/price fluctuations make it hard to send the exact required amount) permanently loses the difference. No admin, governance, or sweep function exists in `EvmHost` to recover ETH that lands there via this refund path, so the funds are effectively stuck (not merely "donated" as the code intentionally allows for fee-token overpayment on delivered requests via `fundRequest`'s comment about "seen as a donation" — that comment applies only to fee-token overpayment on already-delivered requests, not to native-ETH dust from the swap).

### Impact Explanation
Any unprivileged user calling `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, or `fundRequest()` with native ETH loses any excess sent above what the Uniswap swap actually consumes. This is a direct, unprivileged loss-of-funds bug reachable through the most common entrypoints of the protocol (dispatching cross-chain requests and funding relayer fees) — a primary interaction surface for ordinary bridge users, not just intent solvers. Given that dispatch fees denominated in native token require off-chain estimation via `quote()`/`quoteNative()` helpers (see SDK), slight price movement or slippage between quoting and execution will regularly cause overpayment, and every such overpayment is silently absorbed by the `EvmHost` contract with no recovery mechanism for the payer.

### Likelihood Explanation
High. This triggers on completely benign, expected usage: any caller who doesn't send the exact wei amount required by the fee-swap math. Given asynchronous quote-then-execute UX (documented throughout the SDK/docs for both `dispatch` and `fundRequest`), even honest users following recommended flows are exposed. No malicious actor, relayer, or governance interaction is needed — a normal user calling `dispatch{value: X}(...)` where `X` slightly exceeds the swap's exact input is sufficient.

### Recommendation
Mirror the pattern already used in `IntentGatewayV2.sol`/`IntrinsicIntents.sol`/`ExtrinsicIntents.sol`: capture the `amounts[0]` (or `msg.value` minus actual consumed) returned by `swapETHForExactTokens` in `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, and forward the unspent remainder back to `_msgSender()` via a low-level call, reverting if the transfer fails.

### Proof of Concept
1. Attacker/user calls `EvmHost.dispatch{value: 2 ether}(DispatchPost{ fee: X, ... })` where the actual native ETH required to obtain `X` fee tokens via `swapETHForExactTokens` is only `0.5 ether`.
2. Inside `dispatch`, `IUniswapV2Router02.swapETHForExactTokens{value: 2 ether}(X, path, address(this), block.timestamp)` executes: it deposits/swaps only `0.5 ether` worth of WETH and refunds the remaining `1.5 ether` to `msg.sender` of the router call, which is `EvmHost`.
3. `dispatch()` returns without forwarding any of that `1.5 ether` back to the original caller.
4. The `1.5 ether` now sits in `EvmHost`'s balance permanently, with no function in `EvmHost.sol` to withdraw stray native ETH back to the payer or treasury — confirmed by the absence of any `receive()`/withdraw-ETH function in the contract (only ERC20 fee-token flows are handled via `IHostManager`/governance withdrawal paths, which do not cover native ETH dust from swaps). [6](#0-5)

### Citations

**File:** evm/src/core/EvmHost.sol (L921-1051)
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
    }

    /**
     * @dev Dispatch a GET request to Hyperbridge
     *
     * @notice Payment for the request can be made with either the native token or the feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the feeToken.
     *
     * @param get - get request
     * @return commitment - the request commitment
     */
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

        uint64 timeoutTimestamp = get.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(get.timeout);
        GetRequest memory request = GetRequest({
            source: host(),
            dest: get.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            timeoutTimestamp: timeoutTimestamp,
            keys: get.keys,
            height: get.height,
            context: get.context
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee});
        emit GetRequestEvent({
            source: string(request.source),
            dest: string(request.dest),
            from: request.from,
            keys: request.keys,
            nonce: request.nonce,
            height: request.height,
            context: request.context,
            timeoutTimestamp: request.timeoutTimestamp,
            fee: get.fee
        });
    }

    /**
     * @dev Increase the relayer fee for a previously dispatched request.
     * This is provided for use only on pending requests, such that when they timeout,
     * the user can recover the entire relayer fee.
     *
     * @notice Payment can be made with either the native token or the feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the feeToken.
     *
     * If called on an already delivered request, these funds will be seen as a donation to the hyperbridge protocol.
     * @param commitment - The request commitment
     * @param amount - The amount provided in `feeToken()`
     */
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

        FeeMetadata memory metadata = _requestCommitments[commitment];
        if (metadata.sender == address(0)) revert UnknownRequest();

        metadata.fee += amount;
        _requestCommitments[commitment] = metadata;

        emit RequestFunded({commitment: commitment, newFee: metadata.fee});
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
