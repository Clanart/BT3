### Title
`EvmHost::dispatch` swallows the Uniswap router's excess-ETH refund, permanently trapping user overpayments - (File: `evm/src/core/EvmHost.sol`)

### Summary
When a user pays for a cross-chain `dispatch()` request with native tokens, `EvmHost` performs an exact-output swap (`swapETHForExactTokens`) to acquire the exact `feeToken` amount needed and forwards the *entire* `msg.value` as the input cap. The Uniswap V2 router refunds any unused ETH to its immediate caller (`msg.sender`), which is `EvmHost` itself, not the original user who called `dispatch()`. `EvmHost` never forwards, tracks, or returns that refund. Any ETH the user sent above the exact amount required by the swap is permanently stranded in the host contract.

### Finding Description
`dispatch()` in `EvmHost.sol` executes: [1](#0-0) 

`swapETHForExactTokens` is an *exact-output* swap: `post.fee` is the exact amount of `feeToken` to receive, and `msg.value` is only the ceiling for how much ETH may be spent. Standard `IUniswapV2Router02.swapETHForExactTokens` behavior computes the actual ETH cost (`amounts[0] <= msg.value`) and refunds the difference `msg.value - amounts[0]` back to `msg.sender` of the swap call — which here is `EvmHost`, not the end user who called `dispatch()`.

After the swap call, `dispatch()` proceeds directly to building the `PostRequest` and emitting the event, with no logic to capture, account for, or forward any refunded native token back to `_msgSender()`: [2](#0-1) 

Because on-chain `quote()`/`getAmountsIn` are explicitly documented as unsafe to call in-transaction (subject to sandwich attacks) and are meant only for off-chain estimation, users are expected to send an approximate (necessarily imprecise) `msg.value`: [3](#0-2) 

This guarantees that any user who sends more ETH than the router's spot price requires at execution time (which is the normal/expected case given price movement between estimation and execution) will have the excess silently absorbed into `EvmHost`'s own balance with no path back to them. There is no `msg.sender.call{value: refund}` or equivalent logic anywhere in `dispatch()`, unlike the wrapper contracts (`UniV3UniswapV2Wrapper.sol`) which explicitly track and refund dust ETH to the original caller: [4](#0-3) 

`EvmHost.dispatch()` has no analogous refund mechanism, so the excess ETH becomes indistinguishable from the contract's other funds (e.g., protocol fees), effectively lost to the depositing user.

### Impact Explanation
This is a direct, unprivileged, permanent loss of user funds on a public entrypoint. Every `dispatch()` call funded with native token that overshoots the exact router cost (the normal case, since users cannot safely pre-quote on-chain) loses the difference with no recovery path for that specific user. This matches the bounty's "loss of funds" impact category and requires no malicious relayer, prover, or governance actor — only normal usage of the documented native-payment flow.

### Likelihood Explanation
High. Any caller of `dispatch()` supplying `msg.value` (the documented and recommended UX path per the same docs page) will almost always send a `msg.value` slightly above the exact swap cost, since exact costs fluctuate between off-chain estimation and on-chain execution. This is not an edge case — it is the expected steady-state behavior of the native-payment code path.

### Recommendation
Track the ETH balance of `EvmHost` before and after the swap call (or read the returned `amounts[0]` where the router ABI supports it) and refund any residual `msg.value - amountSpent` to `_msgSender()` via a safe ETH transfer, mirroring the refund pattern already implemented in `UniV3UniswapV2Wrapper.swapETHForExactTokens`.

### Proof of Concept
1. Attacker/user calls `IDispatcher(host).dispatch{value: X}(post)` where `post.fee` requires only `Y < X` wei of ETH at current pool price to acquire via `swapETHForExactTokens`.
2. `EvmHost` calls `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: X}(post.fee, path, address(this), block.timestamp)`.
3. The router internally computes `amounts[0] = Y` (`Y <= X`), performs the swap, and refunds `X - Y` wei of ETH to its caller, `EvmHost`.
4. `dispatch()` continues without ever touching or forwarding this `X - Y` refund; it is absorbed into `EvmHost`'s balance.
5. The user who called `dispatch()` and paid `X` receives no ETH back and has no on-chain mechanism to reclaim the `X - Y` difference — it is permanently lost to them.

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

**File:** evm/src/core/EvmHost.sol (L933-959)
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
    }
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L236-238)
```text
<Callout type="warning" title="Estimate Fees Off-Chain">
Use the `quote()` view function from your frontend to estimate how much native token users need to send. **Do not call `quote()` in smart contract transactions.** It uses Uniswap's `getAmountsIn`, making it vulnerable to sandwich attacks. Only use it off-chain for frontend fee estimation
</Callout>
```

**File:** evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol (L140-149)
```text
        bytes[] memory results = IMulticallExtended(_params.swapRouter).multicall(deadline, data);
        uint256 spent = abi.decode(results[0], (uint256));

        if (spent < msg.value) {
            uint256 refund = msg.value - spent;
            IWETH(weth).withdraw(refund);

            (bool success,) = msg.sender.call{value: refund}("");
            if (!success) revert RefundFailed();
        }
```
