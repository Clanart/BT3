### Title
Excess native fees paid to `HyperbridgeLzEndpoint.send()` are silently absorbed by `EvmHost` instead of being refunded to the paying OApp - ([File: sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol])

### Summary
`HyperbridgeLzEndpoint.send()` accepts `msg.value` from an OApp to pay for cross-chain messaging, explicitly discards the caller-supplied `_refundAddress` parameter, and forwards the *entire* `msg.value` to `EvmHost.dispatch()`. Unlike other fee-handling code paths in this same codebase, `send()` never captures or returns any unused native value to the original caller. Because `EvmHost.dispatch()` performs the Uniswap V2 swap itself (as `msg.sender` of the router call), any refund the router issues for unspent ETH goes to `EvmHost`, not to the `HyperbridgeLzEndpoint` or the OApp that originally paid. The result is that excess native fees paid through this LayerZero-compatible endpoint are permanently misdirected to the protocol host contract instead of the payer — the exact bug class described in the external report, reproduced locally.

### Finding Description
`send()` ignores the `_refundAddress` parameter entirely: [1](#0-0) 

It then dispatches with the full `msg.value` and never reconciles any leftover amount: [2](#0-1) 

Contrast this with `EvmHost.dispatch(DispatchPost)`, which is the function actually invoked, and which performs a `swapETHForExactTokens{value: msg.value}(post.fee, ...)` call where `EvmHost` itself is `msg.sender` of the router: [3](#0-2) 

Standard Uniswap V2 router semantics refund unused ETH to the caller of the swap (`msg.sender`), which here is `EvmHost`, not `HyperbridgeLzEndpoint` and not the original OApp that funded the `send()` call. `EvmHost.dispatch()` performs no accounting or return of this refund back up the call stack — the function has no `msgValue -= amounts[0]` style bookkeeping and no forwarding of leftover value to `_msgSender()`.

This is a genuine, locally-provable divergence from the rest of the codebase's own fee-handling convention. `IntentGatewayV2.placeOrder` explicitly captures the swap's actual cost and refunds the difference to `msg.sender`: [4](#0-3) 

And `ExtrinsicIntents.fillOrder` does the same for solver overpayment: [5](#0-4) 

`HyperbridgeLzEndpoint.send()` has no equivalent refund logic, and its own `quote()` function even documents that a "generous 2x buffer" is intentionally applied to native fee quotes "to absorb the legacy deployed host's per-byte protocol fee," with the comment claiming "Excess native is refunded by the uniswap router" — but that refund lands in `EvmHost`, not back at the OApp: [6](#0-5) 

### Impact Explanation
Any OApp/OFT that migrates to `HyperbridgeLzEndpoint` (the documented drop-in LayerZero replacement) and calls `send()` using the endpoint's own `quote()` value — which is deliberately 2x the real cost — will have up to ~50% of every native-fee payment permanently retained by `EvmHost` rather than refunded. This is a direct, protocol-level loss of user/OApp funds on every cross-chain send using native payment, not a hypothetical edge case; it is baked into the documented fee-quoting behavior. This matches the required impact class of "stealing or loss of funds" via incorrect refund routing.

### Likelihood Explanation
High. This triggers on the standard, documented happy path: any OApp calling `quote()` then `send{value: quotedFee}()` with native token payment (`payInLzToken = false`), which is presented in the docs as the normal usage pattern. No malicious actor, relayer, or admin is required — the loss occurs for every legitimate unprivileged caller using the endpoint as intended.

### Recommendation
`HyperbridgeLzEndpoint.send()` should either: (1) call `EvmHost.dispatch()` with an amount strictly equal to the quoted fee (removing the 2x buffer, or precisely computing the swap cost via `getAmountsIn`/`quote` before forwarding value) so no excess is ever sent, or (2) have `EvmHost.dispatch()` return the actual amount spent so the endpoint can compute and refund the difference to `msg.sender` (or the caller-supplied `_refundAddress`), mirroring the pattern already used in `IntentGatewayV2.placeOrder` and `ExtrinsicIntents.fillOrder`.

### Proof of Concept
1. OApp calls `HyperbridgeLzEndpoint.quote(params, sender)` with `payInLzToken = false`; receives `nativeFee = 2 * actualDispatchFee` per the documented 2x buffer. [7](#0-6) 
2. OApp calls `send{value: nativeFee}(params, refundAddress)`. The `refundAddress` parameter is discarded by the function signature. [1](#0-0) 
3. `send()` forwards the full `msg.value` to `IDispatcher(_host).dispatch{value: msg.value}(request)`. [8](#0-7) 
4. Inside `EvmHost.dispatch()`, only `post.fee` (the real, non-buffered fee) worth of tokens is swapped out via `swapETHForExactTokens{value: msg.value}(post.fee, ...)`; the router refunds the ~50% unused ETH to `EvmHost` (the caller), not to `HyperbridgeLzEndpoint` or the OApp. [3](#0-2) 
5. No code path in `HyperbridgeLzEndpoint` or `EvmHost.dispatch()` returns this refund to the OApp; the value is retained by `EvmHost`, resulting in permanent loss to the OApp/user for every native-paid `send()`.

### Citations

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L261-265)
```text
    /// @inheritdoc ILayerZeroEndpointV2
    function send(
        MessagingParams calldata _params,
        address /* _refundAddress */
    ) external payable override whenNotPaused returns (MessagingReceipt memory) {
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L296-313)
```text
        if (msg.value > 0) {
            IDispatcher(_host).dispatch{value: msg.value}(request);
        } else {
            // Fee tokens already transferred to this contract by OFT's _payLzToken.
            // The quoted lzTokenFee includes a buffer above the relayer fee so the
            // legacy deployed host's per-byte protocol fee can be paid out of it;
            // approve our full feeToken balance and let the host take what it needs.
            address feeToken = IDispatcher(_host).feeToken();
            IERC20(feeToken).forceApprove(_host, IERC20(feeToken).balanceOf(address(this)));
            IDispatcher(_host).dispatch(request);
        }

        return MessagingReceipt({
            guid: guid,
            nonce: nonce,
            fee: MessagingFee({nativeFee: msg.value, lzTokenFee: 0})
        });
    }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L337-345)
```text
        // Apply a generous 2x buffer to absorb the legacy deployed host's
        // per-byte protocol fee (the in-source host has no such markup). Excess
        // native is refunded by the uniswap router; excess feeToken approval is
        // simply unused.
        if (_params.payInLzToken) {
            return MessagingFee({nativeFee: 0, lzTokenFee: request.fee * 2});
        } else {
            return MessagingFee({nativeFee: quote(request) * 2, lzTokenFee: 0});
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
